"""Sandboxed execution of one verified dispatch (plan §6.5).

``authorize`` is the gate: envelope signatures, deadline and replay, then parameter validation
against the manifest's schema, then node-local policy. Only an authorized envelope reaches
``Executor.execute``, which materializes the tool's verified blobs into a fresh work dir, builds
a clean environment, and runs it:

* ``sandbox="systemd"`` (production): through ``mucli-node-exec``, a fixed root helper reached
  via a scoped sudoers entry. The helper trusts nothing from the agent: it re-verifies the
  signed envelope with the root-owned public keys, re-checks the root-owned policy, copies the
  blobs into a root-owned work dir verifying each digest, and runs the tool as a transient
  systemd unit (``systemd-run --wait --pipe``) with ``User=``, ``MemoryMax``, ``CPUQuota``,
  ``RuntimeMaxSec``, ``NoNewPrivileges``, ``PrivateTmp`` and optional ``ProtectSystem`` /
  ``ReadWritePaths``. (A transient *service* rather than ``--scope``: scopes cannot carry the
  exec hardening properties.)
* ``sandbox="none"``: a plain subprocess in its own process group. Development and disposable
  test nodes only; the node reports it in its policy summary and the master shows it.

Parameters never touch script text: they arrive as ``$MUCLI_PARAMS`` (a JSON file) and typed
``MUCLI_P_<NAME>`` environment variables.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from . import collectors, schema
from .canonical import digest
from .envelope import ReplayCache, verify_dispatch
from .policy import NodePolicy, decide
from .redact import redact
from .store import NodeStore

HARD_MAX_OUTPUT_KB = 4096
HARD_MAX_TIMEOUT_S = 6 * 3600
MAX_RESULT_BYTES = 1024 * 1024
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BLOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RESERVED_ENV = ("MUCLI_", "LD_", "PYTHON", "BASH_ENV", "ENV", "PATH", "HOME", "IFS", "SHELLOPTS")
TERMINAL = ("succeeded", "failed", "timed_out", "cancelled", "refused")


@dataclass
class ExecutionResult:
    exec_id: str
    state: str
    reason: str = ""
    exit_code: Optional[int] = None
    output: Any = None
    output_errors: List[str] = field(default_factory=list)
    log: str = ""
    log_truncated: bool = False
    redactions: int = 0
    duration_s: float = 0.0
    output_digest: str = ""
    check_mode: bool = False
    sandbox: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Keys:
    signing: bytes
    test: Optional[bytes] = None


# --------------------------------------------------------------------- authorize

def prepare_params(manifest: Mapping[str, Any], params: Any) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(params, dict):
        return {}, ["params must be an object"]
    inputs = manifest.get("inputs") or {"type": "object", "additionalProperties": False}
    filled = schema.apply_defaults(params, inputs)
    errors = schema.validate(filled, inputs, "params")
    for name in filled:
        if not _PARAM_NAME.match(str(name)):
            errors.append(f"params.{name}: invalid parameter name")
    return filled, errors


def authorize(envelope: Mapping[str, Any], *, node_id: str, keys: Keys, policy: NodePolicy,
              replay: ReplayCache, check_mode_passed, now: Optional[float] = None
              ) -> Tuple[bool, str, Dict[str, Any]]:
    """(allowed, reason, params-with-defaults). Every refusal reason is human readable."""
    now = time.time() if now is None else now
    check = verify_dispatch(envelope, node_id=node_id, signing_key=keys.signing, test_key=keys.test,
                            accept_test=policy.canary, replay=replay, now=now)
    if not check.ok:
        return False, check.reason, {}
    manifest = envelope["manifest"]
    params, errors = prepare_params(manifest, envelope.get("params"))
    if errors:
        return False, "invalid parameters: " + "; ".join(errors[:5]), {}
    decision = decide(envelope, manifest, policy, now=now, check_mode_passed=check_mode_passed)
    if not decision.allowed:
        return False, decision.reason, {}
    return True, "", params


# --------------------------------------------------------------------- environment

def param_env(params: Mapping[str, Any]) -> Dict[str, str]:
    env = {}
    for name, value in params.items():
        key = "MUCLI_P_" + str(name).upper()
        if isinstance(value, bool):
            env[key] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            env[key] = repr(value) if isinstance(value, float) else str(value)
        elif isinstance(value, str):
            env[key] = value
        elif value is None:
            env[key] = ""
        else:
            env[key] = json.dumps(value, sort_keys=True)
    return env


def build_env(envelope: Mapping[str, Any], params: Mapping[str, Any], workdir: str,
              secrets: Mapping[str, str]) -> Dict[str, str]:
    manifest = envelope["manifest"]
    env = {"PATH": SAFE_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME": workdir,
           "TMPDIR": os.path.join(workdir, "tmp")}
    for name, value in (manifest.get("env") or {}).items():
        name = str(name)
        if _ENV_NAME.match(name) and not name.startswith(_RESERVED_ENV):
            env[name] = str(value)
    env.update(param_env(params))
    env.update({
        "MUCLI_PARAMS": os.path.join(workdir, "params.json"),
        "MUCLI_RESULT": os.path.join(workdir, "result.json"),
        "MUCLI_CHECK": "1" if envelope.get("check_mode") else "0",
        "MUCLI_EXEC_ID": str(envelope.get("exec_id") or ""),
        "MUCLI_RUN_ID": str(envelope.get("run_id") or ""),
        "MUCLI_TOOL": str(manifest.get("id") or ""),
        "MUCLI_TOOL_VERSION": str(manifest.get("version") or ""),
        "MUCLI_WORKDIR": workdir,
    })
    for name in envelope.get("secret_names") or []:
        if str(name) in secrets:
            env["MUCLI_SECRET_" + str(name).upper()] = str(secrets[str(name)])
    return env


def materialize(envelope: Mapping[str, Any], store: NodeStore, workdir: str) -> str:
    """Copy each verified blob into ``workdir/tool`` and return the entry path."""
    manifest = envelope["manifest"]
    impl = manifest.get("implementation") or {}
    tool_dir = os.path.join(workdir, "tool")
    os.makedirs(tool_dir, mode=0o755, exist_ok=True)
    os.makedirs(os.path.join(workdir, "tmp"), mode=0o700, exist_ok=True)
    for name, sha in (envelope.get("blobs") or {}).items():
        if not _BLOB_NAME.match(str(name)):
            raise ValueError(f"unsafe file name {name!r}")
        data = store.read_blob(str(sha).replace("sha256:", ""))
        if data is None:
            raise ValueError(f"blob {name} is missing or fails its digest")
        with open(os.path.join(tool_dir, name), "wb") as fh:
            fh.write(data)
        os.chmod(os.path.join(tool_dir, name), 0o755)
    entry = str(impl.get("entry") or "")
    if impl.get("runtime") != "builtin" and entry not in (envelope.get("blobs") or {}):
        raise ValueError(f"entry {entry!r} is not one of the signed files")
    return os.path.join(tool_dir, entry)


def interpreter_argv(runtime: str, entry_path: str) -> List[str]:
    if runtime == "bash":
        return ["/bin/bash", "--noprofile", "--norc", "-e", "-u", "-o", "pipefail", entry_path]
    if runtime == "python3":
        return [shutil.which("python3") or "/usr/bin/python3", "-I", "-B", entry_path]
    raise ValueError(f"runtime {runtime!r} has no interpreter")


# --------------------------------------------------------------------- output capture

class _Capture:
    """Redacts per line (suppressing PEM blocks), caps the stored log, forwards chunks."""

    def __init__(self, cap_bytes: int, literals, on_output: Optional[Callable[[str], None]]):
        self.cap = cap_bytes
        self.literals = list(literals)
        self.on_output = on_output
        self.parts: List[str] = []
        self.size = 0
        self.truncated = False
        self.redactions = 0
        self._in_pem = False
        self._pending: List[str] = []
        self._last_flush = time.monotonic()

    def line(self, text: str) -> None:
        if "-----BEGIN" in text and "PRIVATE KEY" in text:
            self._in_pem = True
        if self._in_pem:
            if "-----END" in text:
                self._in_pem = False
                text = "[REDACTED:PEM private key]\n"
                self.redactions += 1
            else:
                return
        clean, n = redact(text, self.literals)
        self.redactions += n
        if self.size < self.cap:
            room = self.cap - self.size
            piece = clean if len(clean.encode()) <= room else clean.encode()[:room].decode(errors="ignore")
            self.parts.append(piece)
            self.size += len(piece.encode())
            if piece != clean:
                self.truncated = True
        else:
            self.truncated = True
        if self.on_output and not self.truncated:
            self._pending.append(clean)
            if time.monotonic() - self._last_flush > 0.5 or sum(map(len, self._pending)) > 8192:
                self.flush()

    def flush(self) -> None:
        if self.on_output and self._pending:
            chunk = "".join(self._pending)
            self._pending = []
            self._last_flush = time.monotonic()
            try:
                self.on_output(chunk)
            except Exception:  # noqa: BLE001 - streaming is best effort
                pass

    @property
    def text(self) -> str:
        out = "".join(self.parts)
        return out + ("\n[output truncated]\n" if self.truncated else "")


def kill_process_group(pgid: int) -> None:
    """SIGTERM then SIGKILL a whole process group (mirrors ``verification._kill_process_group``)."""
    try:
        os.killpg(pgid, signal.SIGTERM)
        time.sleep(0.2)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# --------------------------------------------------------------------- executor

class Executor:
    def __init__(self, store: NodeStore, *, sandbox: str = "systemd",
                 helper: Optional[List[str]] = None):
        if sandbox not in ("systemd", "none"):
            raise ValueError("sandbox must be 'systemd' or 'none'")
        self.store = store
        self.sandbox = sandbox
        self.helper = list(helper or ["sudo", "-n", "/usr/local/libexec/mucli-node/mucli-node-exec"])
        self._procs: Dict[str, subprocess.Popen] = {}
        self._cancelled: set = set()
        self._lock = threading.Lock()

    def cancel(self, exec_id: str) -> bool:
        with self._lock:
            self._cancelled.add(exec_id)
            proc = self._procs.get(exec_id)
        if proc is not None and proc.poll() is None:
            kill_process_group(proc.pid)
            return True
        return False

    def execute(self, envelope: Mapping[str, Any], params: Mapping[str, Any], *,
                secrets: Optional[Mapping[str, str]] = None,
                on_output: Optional[Callable[[str], None]] = None) -> ExecutionResult:
        manifest = envelope["manifest"]
        exec_id = str(envelope.get("exec_id") or "")
        impl = manifest.get("implementation") or {}
        limits = manifest.get("limits") or {}
        timeout = min(float(limits.get("timeout_s") or 300), HARD_MAX_TIMEOUT_S)
        cap = min(int(limits.get("max_output_kb") or 512), HARD_MAX_OUTPUT_KB) * 1024
        secrets = dict(secrets or {})
        check_mode = bool(envelope.get("check_mode"))
        result = ExecutionResult(exec_id=exec_id, state="failed", check_mode=check_mode, sandbox=self.sandbox)
        started = time.monotonic()
        workdir = self.store.work_dir(exec_id)
        try:
            if impl.get("runtime") == "builtin":
                return self._builtin(result, str(impl.get("entry") or ""), params, started)
            entry = materialize(envelope, self.store, workdir)
            with open(os.path.join(workdir, "params.json"), "w", encoding="utf-8") as fh:
                json.dump(dict(params), fh, sort_keys=True)
            env = build_env(envelope, params, workdir, secrets)
            if self.sandbox == "systemd":
                spec_path = os.path.join(workdir, "spec.json")
                with open(spec_path, "w", encoding="utf-8") as fh:
                    os.fchmod(fh.fileno(), 0o600)
                    json.dump({"envelope": dict(envelope), "secrets": secrets,
                               "blob_dir": os.path.join(workdir, "tool"),
                               "result_out": os.path.join(workdir, "result.json")}, fh)
                argv = self.helper + [spec_path]
                env = {"PATH": SAFE_PATH, "LANG": "C.UTF-8"}
            else:
                argv = interpreter_argv(str(impl.get("runtime") or ""), entry)
            capture = _Capture(cap, secrets.values(), on_output)
            state, code = self._spawn(exec_id, argv, env, workdir, timeout, capture)
            capture.flush()
            result.exit_code = code
            result.log = capture.text
            result.log_truncated = capture.truncated
            result.redactions = capture.redactions
            if state != "exited":
                result.state = state
                result.reason = {"timed_out": f"timed out after {timeout:.0f}s",
                                 "cancelled": "cancelled"}.get(state, state)
            else:
                self._collect_output(result, manifest, os.path.join(workdir, "result.json"), secrets)
                if code != 0:
                    result.state = "failed"
                    if self.sandbox == "systemd" and code in SYSTEMD_EXIT:
                        result.reason = (f"the sandbox couldn't start the tool: systemd {SYSTEMD_EXIT[code]} "
                                         f"(exit {code})")
                    result.reason = result.reason or f"exit code {code}"
                elif result.output_errors:
                    result.state = "failed"
                    result.reason = "output does not match the declared schema"
                else:
                    result.state = "succeeded"
            if result.state == "succeeded" and check_mode:
                self.store.record_check_mode_pass(f"{manifest.get('id')}@{manifest.get('version')}")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reported result
            result.state = "failed"
            result.reason = f"node error: {exc}"
        finally:
            result.duration_s = round(time.monotonic() - started, 3)
            shutil.rmtree(workdir, ignore_errors=True)
            with self._lock:
                self._procs.pop(exec_id, None)
                self._cancelled.discard(exec_id)
        return result

    def _builtin(self, result: ExecutionResult, entry: str, params, started: float) -> ExecutionResult:
        fn = BUILTINS.get(entry)
        if fn is None:
            result.reason = f"unknown builtin {entry!r}"
            return result
        result.output = fn(params)
        result.output_digest = digest(result.output)
        result.exit_code = 0
        result.state = "succeeded"
        return result

    def _spawn(self, exec_id, argv, env, workdir, timeout, capture) -> Tuple[str, Optional[int]]:
        with self._lock:
            if exec_id in self._cancelled:
                return "cancelled", None
        proc = subprocess.Popen(argv, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        with self._lock:
            self._procs[exec_id] = proc
        timer_state = {"timed_out": False}

        def _on_timeout():
            timer_state["timed_out"] = True
            kill_process_group(proc.pid)

        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()
        try:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                capture.line(raw.decode("utf-8", errors="replace"))
            code = proc.wait()
        finally:
            timer.cancel()
        if timer_state["timed_out"]:
            return "timed_out", code
        with self._lock:
            if exec_id in self._cancelled:
                return "cancelled", code
        return "exited", code

    @staticmethod
    def _collect_output(result: ExecutionResult, manifest, path: str, secrets) -> None:
        if not os.path.exists(path):
            if (manifest.get("outputs") or {}).get("required"):
                result.output_errors = ["no result written to $MUCLI_RESULT"]
            return
        if os.path.getsize(path) > MAX_RESULT_BYTES:
            result.output_errors = ["result larger than 1 MiB"]
            return
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text, n = redact(fh.read(), secrets.values())
        result.redactions += n
        try:
            result.output = json.loads(text) if text.strip() else None
        except ValueError:
            result.output_errors = ["result is not valid JSON"]
            return
        outputs = manifest.get("outputs")
        if isinstance(outputs, dict) and outputs and result.output is not None:
            result.output_errors = schema.validate(result.output, outputs, "output")[:20]
        result.output_digest = digest(result.output)


def _builtin_facts(params):
    return collectors.collect_facts()


def _builtin_disks(params):
    return {"disks": collectors.disks()}


def _builtin_services(params):
    return {"services": collectors.services()}


BUILTINS: Dict[str, Callable[[Any], Any]] = {
    "facts": _builtin_facts,
    "disks": _builtin_disks,
    "services": _builtin_services,
}


# --------------------------------------------------------------------- root helper

ROOT_DIR = "/var/lib/mucli-node-root"
ETC_DIR = "/etc/mucli-node"


# systemd's own exit codes when it can't start the service (systemd.exec(5)): the tool never ran.
SYSTEMD_EXIT = {200: "couldn't enter the working directory (CHDIR)", 203: "couldn't execute the entry (EXEC)",
                216: "couldn't set the group (GROUP)", 217: "couldn't switch to the run_as user (USER)",
                226: "couldn't set up the namespaces (NAMESPACE)", 228: "couldn't apply the seccomp filter (SECCOMP)"}


def share_workdir(root: str, workdir: str, uid: Optional[int] = None, gid: Optional[int] = None) -> None:
    """Let a tool's (non-root) user reach its own working directory and nothing else.

    The root store and its ``work`` directory become traverse-only (0711: no listing), and the
    working directory, owned by the tool's user, is private to it (0700). Without this a tool
    running as ``nobody`` can't even enter its directory and systemd stops it before it starts."""
    os.chmod(root, 0o711)
    os.chmod(os.path.dirname(workdir), 0o711)
    if uid is not None:
        for dirpath, dirnames, filenames in os.walk(workdir):
            for name in dirnames + filenames + [""]:
                os.chown(os.path.join(dirpath, name), uid, gid if gid is not None else -1)
    os.chmod(workdir, 0o700)


def systemd_argv(manifest: Mapping[str, Any], unit: str, workdir: str, entry: str,
                 env: Mapping[str, str]) -> List[str]:
    impl = manifest.get("implementation") or {}
    limits = manifest.get("limits") or {}
    run_as = str((manifest.get("privilege") or {}).get("run_as") or "root")
    timeout = min(int(limits.get("timeout_s") or 300), HARD_MAX_TIMEOUT_S)
    argv = ["systemd-run", "--wait", "--pipe", "--collect", "--quiet", f"--unit={unit}",
            f"--property=User={run_as}", f"--property=WorkingDirectory={workdir}",
            f"--property=RuntimeMaxSec={timeout}", "--property=NoNewPrivileges=yes",
            "--property=PrivateTmp=yes", "--property=KillMode=control-group"]
    if limits.get("memory_mb"):
        argv.append(f"--property=MemoryMax={int(limits['memory_mb'])}M")
    if limits.get("cpu"):
        argv.append(f"--property=CPUQuota={int(float(limits['cpu']) * 100)}%")
    paths = manifest.get("paths") or {}
    if paths.get("protect_system"):
        argv.append("--property=ProtectSystem=strict")
        for path in list(paths.get("write") or []) + [workdir]:
            argv.append(f"--property=ReadWritePaths={path}")
    for key, value in env.items():
        argv.append(f"--setenv={key}={value}")
    argv.append("--")
    argv += interpreter_argv(str(impl.get("runtime") or ""), entry)
    return argv


def helper_main(argv: List[str]) -> int:
    """``mucli-node-exec SPEC``: the root side of a privileged execution. Trusts nothing."""
    from .policy import load_policy_file
    from .envelope import b64d

    if os.geteuid() != 0:
        print("mucli-node-exec must run as root", file=sys.stderr)
        return 126
    if len(argv) != 1:
        print("usage: mucli-node-exec SPEC", file=sys.stderr)
        return 2
    with open(argv[0], "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    with open(os.path.join(ETC_DIR, "master.json"), "r", encoding="utf-8") as fh:
        trust = json.load(fh)
    os.makedirs(ROOT_DIR, mode=0o700, exist_ok=True)
    root_store = NodeStore(ROOT_DIR)
    keys = Keys(b64d(trust["signing_key"]), b64d(trust["test_key"]) if trust.get("test_key") else None)
    policy = load_policy_file(os.path.join(ETC_DIR, "policy.yaml"))
    replay = root_store.replay_cache()
    if "update" in spec:
        from .update import install
        ok, message = install(spec["update"], str(spec.get("pyz") or ""), node_id=str(trust["node_id"]),
                              signing_key=keys.signing, policy=policy, replay=replay)
        root_store.save_replay_cache(replay)
        print(message if ok else f"mucli-node-exec refused the update: {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 125
    envelope = spec["envelope"]
    ok, reason, params = authorize(envelope, node_id=str(trust["node_id"]), keys=keys, policy=policy,
                                   replay=replay, check_mode_passed=root_store.check_mode_passed())
    root_store.save_replay_cache(replay)
    if not ok:
        print(f"mucli-node-exec refused: {reason}", file=sys.stderr)
        return 125
    # Copy the agent-supplied files into the root store, verifying every digest.
    for name, sha in (envelope.get("blobs") or {}).items():
        sha = str(sha).replace("sha256:", "")
        with open(os.path.join(spec["blob_dir"], os.path.basename(str(name))), "rb") as fh:
            root_store.put_blob(sha, fh.read())
    workdir = root_store.work_dir(str(envelope.get("exec_id") or "x"))
    entry = materialize(envelope, root_store, workdir)
    with open(os.path.join(workdir, "params.json"), "w", encoding="utf-8") as fh:
        json.dump(params, fh, sort_keys=True)
    env = build_env(envelope, params, workdir, spec.get("secrets") or {})
    manifest = envelope["manifest"]
    run_as = str((manifest.get("privilege") or {}).get("run_as") or "root")
    if run_as != "root":
        import pwd
        pw = pwd.getpwnam(run_as)
        share_workdir(ROOT_DIR, workdir, pw.pw_uid, pw.pw_gid)
    else:
        os.chmod(workdir, 0o700)
    unit = "mucli-exec-" + re.sub(r"[^A-Za-z0-9_-]", "-", str(envelope.get("exec_id")))[:48]

    def _stop(signum, frame):
        subprocess.run(["systemctl", "kill", "--signal=SIGKILL", f"{unit}.service"],
                       capture_output=True, timeout=10)
        sys.exit(143)

    signal.signal(signal.SIGTERM, _stop)
    try:
        code = subprocess.call(systemd_argv(manifest, unit, workdir, entry, env))
        if code in SYSTEMD_EXIT:
            print(f"mucli-node-exec: systemd {SYSTEMD_EXIT[code]} for run_as {run_as}; the tool did not run "
                  f"(exit {code})", file=sys.stderr)
        result_path = os.path.join(workdir, "result.json")
        if os.path.exists(result_path) and os.path.getsize(result_path) <= MAX_RESULT_BYTES:
            shutil.copyfile(result_path, spec["result_out"])
            try:
                import pwd
                agent = pwd.getpwnam("mucli-node")
                os.chown(spec["result_out"], agent.pw_uid, agent.pw_gid)
            except KeyError:
                pass
        if code == 0 and envelope.get("check_mode"):
            root_store.record_check_mode_pass(f"{manifest.get('id')}@{manifest.get('version')}")
        return code
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
