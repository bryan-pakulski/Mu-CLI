"""Job sandbox self-tests (Setup → Sandbox, Doctor, ``/job setup``).

* ``isolation(agent)`` starts a real stage container built by the production policy code
  (``SandboxPolicy`` + ``container_args`` + the agent's mounts and egress bridge) and runs a probe
  inside it: capabilities, seccomp, writable paths, network and the egress allowlist.
* ``agent_smoke(agent)`` runs a real read-only plan stage on a scratch repository through
  ``run_stage_in_container`` (mucli, Claude Code or Codex), proving the whole path works end to
  end. It makes one small model request.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .cli import container_cli

PROBE = textwrap.dedent('''\
    import json, os, socket
    r = {}
    status = open("/proc/self/status").read().splitlines()
    field = lambda k: next(l.split(":", 1)[1].strip() for l in status if l.startswith(k + ":"))
    r.update(uid=os.getuid(), gid=os.getgid(), cap_eff=field("CapEff"), cap_prm=field("CapPrm"),
             no_new_privs=field("NoNewPrivs"), seccomp=field("Seccomp"))
    def can_write(path):
        try:
            with open(path, "w") as handle:
                handle.write("x")
            os.unlink(path)
            return True
        except OSError:
            return False
    for name, path in (("rootfs", "/usr/probe"), ("etc", "/etc/probe"), ("tmp", "/tmp/probe"),
                       ("workspace", "/workspace/.probe"), ("artifacts", "/artifacts/probe"),
                       ("relay", "/relay/probe"), ("creds", "/harness/creds/probe"),
                       ("claude_bin", "/opt/harness/probe"), ("codex_bin", "/opt/harness/codex/probe"),
                       ("git_pointer", "/workspace/.git")):
        r["write_" + name] = os.path.isdir(os.path.dirname(path)) and can_write(path) if name != "git_pointer" else \\
            os.access(path, os.W_OK)
    r["ifaces"] = sorted(os.listdir("/sys/class/net"))
    def direct(host, port):
        s = socket.socket()
        s.settimeout(3)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False
        finally:
            s.close()
    r["direct_internet"] = direct("1.1.1.1", 443)
    try:
        socket.getaddrinfo("example.com", 443)
        r["dns"] = True
    except OSError:
        r["dns"] = False
    def connect(host):
        s = socket.create_connection(("127.0.0.1", 3128), timeout=15)
        try:
            s.sendall(("CONNECT %s:443 HTTP/1.1\\r\\nHost: %s:443\\r\\n\\r\\n" % (host, host)).encode())
            return s.recv(200).decode(errors="replace").split("\\r\\n")[0]
        finally:
            s.close()
    for host in ("example.com", "api.anthropic.com", "api.openai.com", "chatgpt.com"):
        try:
            r["proxy:" + host] = connect(host)
        except OSError as exc:
            r["proxy:" + host] = "refused: %s" % exc
    r["docker_sock"] = os.path.exists("/var/run/docker.sock")
    r["secret_env"] = sorted(k for k in os.environ if k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"))
    for home in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        if os.environ.get(home):
            r["cred_modes"] = sorted(oct(os.stat(os.path.join(os.environ[home], f)).st_mode & 0o777)
                                     for f in os.listdir(os.environ[home]) if f in (".credentials.json", "auth.json"))
    print("PROBE:" + json.dumps(r), flush=True)
''')


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


ALLOWED_HOSTS = {"claude": {"api.anthropic.com"}, "codex": {"api.openai.com", "chatgpt.com"}}
PROBE_HOSTS = ("example.com", "api.anthropic.com", "api.openai.com", "chatgpt.com")


def check_probe(probe: dict, agent: str, stage: str, *, secret_env: Optional[List[str]] = None) -> List[Check]:
    """Evaluate a probe report against the sandbox contract for ``agent``/``stage``."""
    out: List[Check] = []
    add = lambda name, ok, detail="": out.append(Check(name, bool(ok), detail))  # noqa: E731
    add("runs as your user, not root", probe["uid"] == os.getuid() and probe["gid"] == os.getgid() and probe["uid"] != 0,
        f"uid {probe['uid']} gid {probe['gid']}")
    add("no Linux capabilities", int(probe["cap_eff"], 16) == 0 and int(probe["cap_prm"], 16) == 0,
        f"CapEff {probe['cap_eff']}")
    add("no privilege escalation", probe["no_new_privs"] == "1", f"NoNewPrivs {probe['no_new_privs']}")
    add("seccomp filter active", probe["seccomp"] == "2", f"Seccomp {probe['seccomp']}")
    add("system files read-only", not probe["write_rootfs"] and not probe["write_etc"])
    add("scratch space writable", probe["write_tmp"])
    add("workspace " + ("writable (implement)" if stage == "implement" else "read-only"),
        probe["write_workspace"] is (stage == "implement"))
    add("inputs, relay and credentials read-only",
        not probe["write_artifacts"] and not probe["write_relay"] and not probe["write_creds"])
    add("agent binary and .git pointer read-only",
        not probe["write_claude_bin"] and not probe["write_codex_bin"] and not probe["write_git_pointer"])
    add("no network interfaces besides loopback", probe["ifaces"] == ["lo"], ", ".join(probe["ifaces"]))
    add("no direct internet or DNS", not probe["direct_internet"] and not probe["dns"])
    add("no Docker socket", not probe["docker_sock"])
    allowed = ALLOWED_HOSTS.get(agent, set())
    for host in PROBE_HOSTS:
        answer = str(probe.get("proxy:" + host) or "")
        if host in allowed:
            add(f"egress allows {host}", " 200" in answer, answer)
        else:
            add(f"egress blocks {host}", " 200" not in answer, answer or "no proxy")
    if secret_env is None:
        secret_env = {"claude": ["ANTHROPIC_API_KEY"], "codex": ["OPENAI_API_KEY"]}.get(agent, [])
    add("only the agent's own credential is visible", probe["secret_env"] == secret_env,
        ", ".join(probe["secret_env"]) or "none")
    modes = probe.get("cred_modes") or []
    if modes:
        add("copied login files are private (0600)", all(m == "0o600" for m in modes), ", ".join(modes))
    return out


def parse_probe(text: str) -> Optional[dict]:
    for line in str(text or "").splitlines():
        if line.startswith("PROBE:"):
            try:
                return json.loads(line[len("PROBE:"):])
            except ValueError:
                continue
    return None


Log = Callable[[str], None]


def isolation(agent: str = "mucli", stage: str = "plan", *, log: Optional[Log] = None) -> List[Check]:
    """Run the probe in a stage container assembled exactly as a real ``agent`` stage would be."""
    from dataclasses import replace
    from .. import harness
    from .egress import EgressBridge
    from .policy import SandboxPolicy, container_args
    log = log or (lambda line: None)
    policy = replace(SandboxPolicy.for_stage(stage), egress_extra=())
    with tempfile.TemporaryDirectory(prefix="mucli-selftest-") as tmp:
        root = Path(tmp)
        dirs = {name: root / name for name in ("workspace", "input", "output", "relay", "creds", "state")}
        for path in dirs.values():
            path.mkdir()
        mounts, secret_env = [], []
        if agent != "mucli":
            mounts = harness.binary_mounts(agent)
            # A placeholder credential with the right *name*: the probe checks names, never values.
            creds = harness.credentials(agent) or {}
            names = sorted((creds.get("env") or {}).keys())
            (dirs["creds"] / "env.json").write_text(json.dumps({n: "selftest-placeholder" for n in names}))
            os.chmod(dirs["creds"], 0o700)
            secret_env = names
            mounts += [(str(dirs["creds"]), "/harness/creds", True), (str(dirs["state"]), "/harness/state", False)]
            policy = replace(policy, egress_extra=harness.provider_hosts(agent))
        bridge = EgressBridge(dirs["relay"], policy.egress_extra).start() if policy.egress_extra else None
        name = f"mucli-selftest-{agent}-{stage}-{os.getpid()}-{int(time.time())}"
        try:
            args = container_args(name, stage, str(dirs["workspace"]), str(dirs["input"]), str(dirs["output"]),
                                  str(dirs["relay"]), policy, "selftest", harness_mounts=tuple(mounts),
                                  module="mu.jobs.sandbox.harness_worker" if agent != "mucli" else "mu.jobs.sandbox.worker")
            # Same container, but the entrypoint runs the probe instead of a stage worker: start the
            # container's egress proxy (as both workers do), load the credential env, then probe.
            script = ("import json, os\nfrom mu.jobs.sandbox.egress import start_container_proxy\nstart_container_proxy()\n"
                      "p='/harness/creds/env.json'\nos.environ.update(json.load(open(p)) if os.path.exists(p) else {})\n" + PROBE)
            args = [*args[:-2], "-c", script]
            log(f"starting {agent} {stage} sandbox ({policy.image})")
            result = subprocess.run([container_cli(), "run", "--rm", *args[1:]], capture_output=True, text=True, timeout=120)
        finally:
            if bridge is not None:
                bridge.close()
        probe = parse_probe(result.stdout)
        if not probe:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["no output"]
            return [Check(f"{agent} sandbox starts", False, detail[0][:300])]
        checks = [Check(f"{agent} sandbox starts", True, policy.image)] + check_probe(probe, agent, stage, secret_env=secret_env)
        for c in checks:
            log(("  ✓ " if c.ok else "  ✗ ") + c.name + (f" — {c.detail}" if c.detail else ""))
        return checks


def agent_smoke(agent: str, *, log: Optional[Log] = None, model: str = "") -> Check:
    """One real read-only plan stage on a scratch repository (a small model request)."""
    from mu.jobs import JobService, JobSpec, JobStore
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.worktree import JobWorktreeManager
    from .runner import run_stage_in_container
    log = log or (lambda line: None)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mucli-selftest-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)
        git("init", "-b", "main")
        git("config", "user.name", "mucli self-test")
        git("config", "user.email", "selftest@localhost")
        (repo / "README.md").write_text("mucli self-test repository\n")
        git("add", ".")
        git("commit", "-m", "self-test")
        service = JobService(JobStore(str(root / "jobs.db")))
        execution = {"pipeline": "staged"}
        if agent != "mucli":
            execution["stage_agents"] = {"plan": {"agent": agent, **({"model": model} if model else {}), "effort": "low"}}
        job = service.create(JobSpec(title=f"self-test {agent}", repository=str(repo), execution=execution))
        try:
            JobWorktreeManager(service, root=str(root / "worktrees")).prepare(job)
            log(f"running a read-only plan stage on {agent} …")
            result = run_stage_in_container(service, service.get(job.id), "plan",
                                            "This is a setup self-test. Do not use any tools. Reply with exactly: READY")
            seconds = time.monotonic() - started
            text = str(result.get("text") or "")
            ok = result.get("kind") == "completed" and "READY" in text.upper()
            detail = (f"{seconds:.0f}s · ${float(result.get('cost_usd') or 0):.4f}" + ("" if ok else
                      f" · {result.get('kind')}: {(result.get('error') or text)[:300]}"))
            log(("  ✓ " if ok else "  ✗ ") + detail)
            return Check(f"{agent} completes a stage", ok, detail)
        except Exception as exc:  # noqa: BLE001 - a self-test reports, never raises
            log(f"  ✗ {exc}")
            return Check(f"{agent} completes a stage", False, str(exc)[:300])
        finally:
            try:
                artifacts_for(job).remove()
            except Exception:  # noqa: BLE001
                pass
            shutil.rmtree(root / "worktrees", ignore_errors=True)
