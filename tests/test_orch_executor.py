"""Node execution (plan §6.5): parameter safety, clean environment, caps, timeouts, cancel, redaction."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import pytest

from mu.orchestration.node.executor import Executor, build_env, param_env, prepare_params
from mu.orchestration.node.store import NodeStore

NASTY = [
    "; rm -rf / #", "$(touch /tmp/pwned)", "`id`", "a && b || c", "\"quoted\" 'single'", "$HOME ${PATH}",
    "line1\nline2", "*?[glob]", "> /etc/passwd", "| cat /etc/shadow", "\\backslash\\", "' OR 1=1 --",
    "ünïcødé ✓", "-n -e", "$((1+1))", "{a,b}", "!!", "~root",
]


def _envelope(store: NodeStore, files, *, runtime="python3", entry="main.py", inputs=None, outputs=None,
              limits=None, check_mode=False, secrets=(), exec_id="x_test"):
    blobs = {}
    for name, text in files.items():
        data = text.encode()
        sha = hashlib.sha256(data).hexdigest()
        store.put_blob(sha, data)
        blobs[name] = sha
    manifest = {"id": "demo.tool", "version": 1, "effect": "read_only", "category": "diagnostics",
                "implementation": {"runtime": runtime, "entry": entry, "check_mode": True},
                "inputs": inputs or {"type": "object", "properties": {"value": {"type": "string"}},
                                     "additionalProperties": False},
                "outputs": outputs or {}, "limits": limits or {"timeout_s": 30, "max_output_kb": 64},
                "secrets": list(secrets)}
    return {"exec_id": exec_id, "run_id": "r_1", "manifest": manifest, "blobs": blobs, "check_mode": check_mode,
            "secret_names": list(secrets)}


@pytest.fixture
def store(tmp_path):
    return NodeStore(str(tmp_path / "node"))


ECHO_PARAM_PY = (
    "import json, os, sys\n"
    "p = json.load(open(os.environ['MUCLI_PARAMS']))\n"
    "json.dump({'from_file': p['value'], 'from_env': os.environ['MUCLI_P_VALUE']}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
)
ECHO_PARAM_SH = (
    "#!/bin/bash\n"
    "printf '%s' \"$MUCLI_P_VALUE\" > \"$MUCLI_WORKDIR/value.txt\"\n"
    "python3 -c 'import json,os; v=open(os.environ[\"MUCLI_WORKDIR\"]+\"/value.txt\").read(); "
    "json.dump({\"from_env\": v}, open(os.environ[\"MUCLI_RESULT\"],\"w\"))'\n"
)


@pytest.mark.parametrize("value", NASTY)
def test_parameters_with_shell_metacharacters_arrive_literally_python(store, value):
    env = _envelope(store, {"main.py": ECHO_PARAM_PY})
    result = Executor(store, sandbox="none").execute(env, {"value": value})
    assert result.state == "succeeded", result.reason + result.log
    assert result.output == {"from_file": value, "from_env": value}


@pytest.mark.parametrize("value", NASTY)
def test_parameters_with_shell_metacharacters_arrive_literally_bash(store, value, tmp_path):
    canary = "/tmp/pwned"
    if os.path.exists(canary):
        os.unlink(canary)
    env = _envelope(store, {"main.sh": ECHO_PARAM_SH}, runtime="bash", entry="main.sh")
    result = Executor(store, sandbox="none").execute(env, {"value": value})
    assert result.state == "succeeded", result.reason + result.log
    assert result.output == {"from_env": value}
    assert not os.path.exists(canary)


def test_typed_param_env():
    assert param_env({"n": 3, "f": 1.5, "b": True, "s": "x", "l": ["a", 1], "z": None}) == {
        "MUCLI_P_N": "3", "MUCLI_P_F": "1.5", "MUCLI_P_B": "true", "MUCLI_P_S": "x",
        "MUCLI_P_L": '["a", 1]', "MUCLI_P_Z": ""}


def test_prepare_params_applies_defaults_and_rejects_unknown():
    manifest = {"inputs": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "default": 7}},
                           "additionalProperties": False}}
    assert prepare_params(manifest, {}) == ({"days": 7}, [])
    assert prepare_params(manifest, {"days": 0})[1]
    assert prepare_params(manifest, {"days": 2, "x": 1})[1]
    assert prepare_params(manifest, "not an object")[1]


def test_environment_is_clean(store, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("LD_PRELOAD", "/evil.so")
    script = "import json, os\njson.dump(dict(os.environ), open(os.environ['MUCLI_RESULT'], 'w'))\n"
    env = _envelope(store, {"main.py": script})
    env["manifest"]["env"] = {"GOOD": "1", "LD_LIBRARY_PATH": "/bad", "MUCLI_CHECK": "0", "PATH": "/bad"}
    result = Executor(store, sandbox="none").execute(env, {"value": "v"}, secrets={})
    seen = result.output
    assert "AWS_SECRET_ACCESS_KEY" not in seen and "LD_PRELOAD" not in seen and "LD_LIBRARY_PATH" not in seen
    assert seen["GOOD"] == "1"
    assert seen["PATH"].startswith("/usr/local/sbin")
    assert seen["MUCLI_CHECK"] == "0"
    allowed = {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR", "GOOD", "PWD", "LC_CTYPE", "SHLVL", "_"}
    assert {k for k in seen if not k.startswith("MUCLI_")} <= allowed


def test_check_mode_flag_and_history(store):
    script = "import json, os\njson.dump({'check': os.environ['MUCLI_CHECK']}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
    env = _envelope(store, {"main.py": script}, check_mode=True)
    result = Executor(store, sandbox="none").execute(env, {})
    assert result.output == {"check": "1"} and result.check_mode
    assert "demo.tool@1" in store.check_mode_passed()


def test_timeout_kills_the_whole_process_group(store, tmp_path):
    marker = tmp_path / "child-alive"
    script = (f"import subprocess, time\n"
              f"subprocess.Popen(['sh', '-c', 'sleep 3; touch {marker}'])\n"
              f"time.sleep(30)\n")
    env = _envelope(store, {"main.py": script}, limits={"timeout_s": 1})
    started = time.time()
    result = Executor(store, sandbox="none").execute(env, {})
    assert result.state == "timed_out"
    assert time.time() - started < 10
    time.sleep(3.5)
    assert not marker.exists(), "a child process survived the timeout"


def test_cancel_stops_a_running_execution(store):
    env = _envelope(store, {"main.py": "import time\nprint('started', flush=True)\ntime.sleep(30)\n"})
    executor = Executor(store, sandbox="none")
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("r", executor.execute(env, {})))
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not executor._procs:
        time.sleep(0.05)
    time.sleep(0.3)
    assert executor.cancel("x_test")
    thread.join(timeout=10)
    assert out["r"].state == "cancelled"


def test_output_cap_and_streaming(store):
    script = "for i in range(20000):\n    print('x' * 50)\n"
    env = _envelope(store, {"main.py": script}, limits={"timeout_s": 30, "max_output_kb": 4})
    chunks = []
    result = Executor(store, sandbox="none").execute(env, {}, on_output=chunks.append)
    assert result.state == "succeeded"
    assert result.log_truncated
    assert len(result.log.encode()) <= 4 * 1024 + 64
    assert chunks and sum(len(c) for c in chunks) <= 4 * 1024 + 51


def test_redaction_of_secret_literals_and_patterns(store):
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\nxyz\n-----END RSA PRIVATE KEY-----"
    script = ("import json, os\n"
              "s = os.environ['MUCLI_SECRET_DB_PASSWORD']\n"
              "print('password is', s)\n"
              f"print({pem!r})\n"
              "print('key AKIAABCDEFGHIJKLMNOP')\n"
              "json.dump({'echo': s}, open(os.environ['MUCLI_RESULT'], 'w'))\n")
    env = _envelope(store, {"main.py": script}, secrets=["db_password"])
    streamed = []
    result = Executor(store, sandbox="none").execute(env, {}, secrets={"db_password": "s3cret-value"},
                                                     on_output=streamed.append)
    text = result.log + "".join(streamed) + json.dumps(result.output)
    assert "s3cret-value" not in text
    assert "MIIabc" not in text and "AKIAABCDEFGHIJKLMNOP" not in text
    assert result.redactions >= 3


def test_output_schema_mismatch_fails(store):
    script = "import json, os\njson.dump({'n': 'not a number'}, open(os.environ['MUCLI_RESULT'], 'w'))\n"
    env = _envelope(store, {"main.py": script}, outputs={"type": "object", "properties": {"n": {"type": "integer"}}})
    result = Executor(store, sandbox="none").execute(env, {})
    assert result.state == "failed" and result.output_errors


def test_nonzero_exit_and_invalid_json(store):
    result = Executor(store, sandbox="none").execute(_envelope(store, {"main.py": "raise SystemExit(3)\n"}), {})
    assert result.state == "failed" and result.exit_code == 3
    bad = "import os\nopen(os.environ['MUCLI_RESULT'], 'w').write('{nope')\n"
    result = Executor(store, sandbox="none").execute(_envelope(store, {"main.py": bad}), {})
    assert result.state == "failed" and "valid JSON" in result.output_errors[0]


def test_tampered_blob_in_the_cache_is_refused(store):
    env = _envelope(store, {"main.py": "print('hi')\n"})
    sha = env["blobs"]["main.py"]
    with open(store.blob_path(sha), "wb") as fh:
        fh.write(b"print('evil')\n")
    result = Executor(store, sandbox="none").execute(env, {})
    assert result.state == "failed" and "digest" in result.reason
    assert not os.path.exists(store.blob_path(sha))


def test_entry_must_be_a_signed_file_and_names_are_safe(store):
    env = _envelope(store, {"main.py": "print(1)\n"}, entry="other.py")
    assert "signed files" in Executor(store, sandbox="none").execute(env, {}).reason
    env = _envelope(store, {"main.py": "print(1)\n"})
    env["blobs"] = {"../escape.py": env["blobs"]["main.py"]}
    assert "unsafe" in Executor(store, sandbox="none").execute(env, {}).reason


def test_builtin_collectors(store):
    env = _envelope(store, {}, runtime="builtin", entry="facts")
    result = Executor(store, sandbox="none").execute(env, {})
    assert result.state == "succeeded" and result.output["system"] == "linux" and "provides" in result.output


def test_work_dirs_are_removed(store):
    Executor(store, sandbox="none").execute(_envelope(store, {"main.py": "print(1)\n"}), {})
    assert os.listdir(os.path.join(store.root, "work")) == []


def test_systemd_sandbox_goes_through_the_root_helper(store, tmp_path):
    """The agent never runs a tool itself in systemd mode: it hands a spec to the helper."""
    helper = tmp_path / "fake-helper"
    seen = tmp_path / "seen.json"
    helper.write_text(f"#!/bin/sh\ncp \"$1\" {seen}\necho helper-ran\n")
    helper.chmod(0o755)
    env = _envelope(store, {"main.py": "print('direct run')\n"})
    result = Executor(store, sandbox="systemd", helper=[str(helper)]).execute(env, {"value": "v"},
                                                                            secrets={"x": "y"})
    assert result.state == "succeeded" and "helper-ran" in result.log and "direct run" not in result.log
    spec = json.loads(seen.read_text())
    assert spec["envelope"]["exec_id"] == "x_test" and spec["secrets"] == {"x": "y"}


def test_root_helper_dirs_let_the_tool_user_in_and_no_one_else(tmp_path):
    """Regression: tools running as nobody failed with systemd exit 200 (CHDIR) because the
    root store was 0700 all the way down."""
    import stat
    from mu.orchestration.node.executor import share_workdir
    from mu.orchestration.node.store import NodeStore
    root = tmp_path / "root"
    store = NodeStore(str(root))
    work = store.work_dir("x_1")
    share_workdir(str(root), work)
    mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
    assert mode(root) == 0o711 and mode(root / "work") == 0o711   # traverse, never list
    assert mode(work) == 0o700                                      # only the tool's own user
    assert mode(root / "blobs") == 0o700


def test_systemd_start_failures_say_what_happened(store, tmp_path):
    helper = tmp_path / "fake-helper"
    helper.write_text("#!/bin/sh\nexit 200\n")
    helper.chmod(0o755)
    env = _envelope(store, {"main.py": "print(1)\n"})
    result = Executor(store, sandbox="systemd", helper=[str(helper)]).execute(env, {}, secrets={})
    assert result.state == "failed" and result.exit_code == 200
    assert "couldn't enter the working directory" in result.reason


def test_systemd_argv_carries_the_hardening():
    from mu.orchestration.node.executor import systemd_argv
    manifest = {"implementation": {"runtime": "bash"}, "limits": {"timeout_s": 60, "memory_mb": 128, "cpu": 0.5},
                "privilege": {"run_as": "nobody"}, "paths": {"protect_system": True, "write": ["/var/log/nginx"]}}
    argv = systemd_argv(manifest, "mucli-exec-x", "/w", "/w/tool/main.sh", {"MUCLI_P_A": "1"})
    joined = " ".join(argv)
    for expected in ("--wait", "--pipe", "User=nobody", "NoNewPrivileges=yes", "PrivateTmp=yes", "MemoryMax=128M",
                     "CPUQuota=50%", "RuntimeMaxSec=60", "ProtectSystem=strict", "ReadWritePaths=/var/log/nginx",
                     "--setenv=MUCLI_P_A=1"):
        assert expected in joined
    assert argv[argv.index("--") + 1] == "/bin/bash"


def test_build_env_injects_only_declared_secrets(tmp_path):
    env = {"exec_id": "x", "run_id": "r", "check_mode": False, "secret_names": ["db"],
           "manifest": {"id": "t.t", "version": 1, "env": {}}}
    built = build_env(env, {}, str(tmp_path), {"db": "pw", "other": "nope"})
    assert built["MUCLI_SECRET_DB"] == "pw" and "MUCLI_SECRET_OTHER" not in built
