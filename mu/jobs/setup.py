"""Jobs Setup and Doctor (planos-style).

Setup is a checklist of independent steps. Each step checks the machine's actual state (so it is
safe to re-run any time) and offers the actions that fix it: ``run`` (do it now), ``prompts``
(ask, then do it), ``job`` (long-running, with a live log), ``terminal`` (a command the user runs
in a terminal: the web and Android show it to copy) and ``nav`` (open Settings). Doctor is the
quick pass/fail/warn report of everything a job run depends on; ``deep`` adds the sandbox
isolation self-tests. Shared by the web GUI, Android (REST) and the terminal (``/job setup``,
``/job doctor`` and the dashboard).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

STEPS: List[Dict[str, Any]] = [
    {"id": "docker", "title": "Docker", "why": "Every job stage runs in a locked-down container on your Docker engine."},
    {"id": "git", "title": "Git identity", "why": "mucli commits each job's changes as you before pushing."},
    {"id": "runtime", "title": "Job runtime image",
     "why": "The image stages run in. It carries mucli's worker code, so it must be rebuilt after mucli changes."},
    {"id": "controller", "title": "Job controller",
     "why": "The background service that runs queued jobs (the mucli GUI daemon)."},
    {"id": "models", "title": "mucli stage models",
     "why": "The provider and model mucli uses for each stage it runs (stages on Claude Code or Codex need none)."},
    {"id": "claude", "title": "Claude Code", "optional": True,
     "why": "Run job stages on Claude Code. A long-lived token (`claude setup-token`) is the most reliable for long runs."},
    {"id": "codex", "title": "Codex", "optional": True,
     "why": "Run job stages on OpenAI Codex, with your Codex login or an OpenAI API key."},
    {"id": "agents", "title": "Default agent per stage",
     "why": "Which agent plans, implements and reviews when a job doesn't choose (picked again at each launch)."},
    {"id": "sandbox", "title": "Sandbox self-test",
     "why": "Starts real stage containers for every ready agent and checks isolation from inside: capabilities, "
            "read-only paths, no network except the agent's own provider, credentials."},
    {"id": "jira", "title": "JIRA", "optional": True,
     "why": "Import Jira tickets as jobs. Optional writeback (off by default) comments on the ticket as the job "
            "progresses and can move it to statuses you choose."},
    {"id": "dind", "title": "Docker-in-Docker (Sysbox)", "optional": True,
     "why": "Lets a job's build environment run its own Docker engine (for projects whose tests use Docker)."},
    {"id": "gpu", "title": "GPUs", "optional": True, "why": "Lets build environments that request GPUs use them."},
]
STEP_IDS = [s["id"] for s in STEPS]
STEP_BY_ID = {s["id"]: s for s in STEPS}
STATES = ("ok", "todo", "warn", "skipped", "optional")


@dataclass
class StepStatus:
    state: str
    detail: str


# ------------------------------------------------------------------ persisted choices


def setup_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "jobs", "setup.json")


_file_lock = threading.Lock()


def read_setup() -> Dict[str, Any]:
    try:
        with open(setup_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_setup(**patch: Any) -> Dict[str, Any]:
    with _file_lock:
        data = {**read_setup(), **patch}
        path = setup_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".setup.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return data


def set_skipped(step: str, skipped: bool) -> None:
    current = set(read_setup().get("skipped") or [])
    current.add(step) if skipped else current.discard(step)
    write_setup(skipped=sorted(current))


def needs_setup() -> bool:
    """Cheap: should a jobs UI suggest Setup? (never finished)."""
    return not read_setup().get("completed_at")


# ------------------------------------------------------------------ checks


def _run(args: List[str], timeout: float = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def git_identity() -> Dict[str, str]:
    return {"name": _run(["git", "config", "user.name"]).stdout.strip(),
            "email": _run(["git", "config", "user.email"]).stdout.strip()}


def _image_state() -> StepStatus:
    from .sandbox.build import IMAGE, image_digest, source_digest
    try:
        label = image_digest(IMAGE)
    except (OSError, subprocess.SubprocessError):
        return StepStatus("todo", "Docker is not reachable")
    if label is None:
        return StepStatus("todo", f"{IMAGE} is not built yet")
    current = source_digest()
    if label == "":
        return StepStatus("todo", f"{IMAGE} was built before images carried a code fingerprint; rebuild it")
    if label != current:
        return StepStatus("todo", f"{IMAGE} was built from older mucli code ({label} ≠ {current}); rebuild it")
    return StepStatus("ok", f"{IMAGE} · code {current}")


def _controller_pid() -> Optional[int]:
    try:
        from mu.gui import daemon
        from .host import DEFAULT_CONTROLLER_PORT
        return daemon.is_running() or daemon.pid_for_port(DEFAULT_CONTROLLER_PORT)
    except Exception:  # noqa: BLE001
        return None


def _smoke_note(agent: str) -> str:
    smoke = (read_setup().get("smoke") or {}).get(agent) or {}
    if not smoke:
        return ""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(smoke.get("at") or 0)))
    return f" · test stage {'passed' if smoke.get('ok') else 'FAILED'} {when}" + ("" if smoke.get("ok") else f" ({smoke.get('detail', '')[:120]})")


def _harness_state(agent: str) -> StepStatus:
    from .harness import AGENT_LABELS, binary, credentials, version
    path = binary(agent)
    if not path:
        install = ("install it (https://docs.claude.com/claude-code), then log in or create a token below" if agent == "claude"
                   else "install it (`npm i -g @openai/codex` or the standalone release), then `codex login` below")
        return StepStatus("todo", f"{AGENT_LABELS[agent]} is not installed: {install}")
    creds = credentials(agent)
    if not creds:
        return StepStatus("todo", f"{AGENT_LABELS[agent]} {version(path)} is installed but has no credentials")
    note = _smoke_note(agent)
    if creds["kind"] == "login":
        return StepStatus("warn", f"{version(path)} · using your login session ({creds['source']}); "
                                  + ("a setup-token is more reliable for long runs" if agent == "claude" else
                                     "an API key avoids host-login refresh conflicts") + note)
    return StepStatus("ok", f"{version(path)} · {creds['kind']} from {creds['source']}" + note)


def _agents_state() -> StepStatus:
    from .harness import AGENT_LABELS, status
    st = status()
    defaults = st["defaults"]
    text = " · ".join(f"{s} {AGENT_LABELS.get(a, a)}" for s, a in defaults.items())
    broken = [f"{s} ({AGENT_LABELS[a]} not ready)" for s, a in defaults.items() if a != "mucli" and not st[a]["ready"]]
    return StepStatus("todo", f"{text}; " + ", ".join(broken)) if broken else StepStatus("ok", text)


def _sandbox_state() -> StepStatus:
    record = read_setup().get("selftest") or {}
    if not record:
        return StepStatus("todo", "not run yet")
    from .sandbox.build import IMAGE, image_digest
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(record.get("at") or 0)))
    if not record.get("ok"):
        return StepStatus("todo", f"FAILED {when}: {', '.join(record.get('failures') or [])[:300]}")
    try:
        label = image_digest(IMAGE)
    except (OSError, subprocess.SubprocessError):
        label = None
    if label != record.get("image"):
        return StepStatus("todo", f"passed {when}, but the runtime image changed since; run it again")
    return StepStatus("ok", f"passed {when} · {record.get('summary', '')}")


_jira_ok_for = {"sig": "", "detail": ""}


def _jira_state() -> StepStatus:
    from mu.integrations.jira_settings import public_settings, resolve_config
    info = public_settings()
    if not info.get("configured"):
        return StepStatus("optional", "not configured (base URL + email + API token, or a PAT)")
    cfg = resolve_config()
    sig = f"{cfg.base_url}|{cfg.email}|{(cfg.api_token or cfg.pat or '')[-6:]}"
    if _jira_ok_for["sig"] == sig:
        return StepStatus("ok", _jira_ok_for["detail"])
    try:
        from mu.integrations.jira import JiraClient
        me = JiraClient(cfg).myself()
    except Exception as exc:  # noqa: BLE001
        return StepStatus("warn", f"set, but JIRA rejected it: {str(exc).splitlines()[0][:200]}")
    try:
        kind = "Cloud" if JiraClient(cfg).deployment == "cloud" else "Data Center/Server"
    except Exception:  # noqa: BLE001
        kind = ""
    detail = f"{cfg.base_url} as {me.get('display_name') or cfg.email}" + (f" · {kind}" if kind else "") \
        + f" · writeback {'on' if cfg.writeback else 'off'}"
    _jira_ok_for.update(sig=sig, detail=detail)
    return StepStatus("ok", detail)


def check(step: str) -> StepStatus:
    if step == "docker":
        from .sandbox.cli import container_cli
        cli = container_cli()
        result = _run([cli, "version", "--format", "{{.Client.Version}}" if cli == "podman" else "{{.Server.Version}}"])
        if result.returncode == 0:
            return StepStatus("ok", f"{'Podman' if cli == 'podman' else 'Docker'} {result.stdout.strip()}")
        why = (result.stderr or result.stdout or "").strip().splitlines()[:1]
        why = why[0] if why else "is Docker installed and running?"
        if "permission denied" in why.lower():
            return StepStatus("todo", f"no permission: add yourself to the docker group (sudo usermod -aG docker "
                                      f"{os.environ.get('USER', '$USER')}) and open a new terminal")
        return StepStatus("todo", f"not reachable: {why}")
    if step == "git":
        ident = git_identity()
        return StepStatus("ok", f"{ident['name']} <{ident['email']}>") if ident["name"] and ident["email"] else \
            StepStatus("todo", "user.name / user.email not set")
    if step == "runtime":
        return _image_state()
    if step == "controller":
        pid = _controller_pid()
        return StepStatus("ok", f"running (pid {pid})") if pid else StepStatus("todo", "not running; queued jobs wait")
    if step == "models":
        from .job_settings import load_settings, missing_stages, resolve
        missing = missing_stages({})
        if missing:
            return StepStatus("todo", f"no mucli model for: {', '.join(missing)}")
        data = load_settings()
        from .harness import harness_stages
        from types import SimpleNamespace
        external = set(harness_stages(SimpleNamespace(execution={}), data))
        parts = [f"{s} {resolve({}, s, data)['provider']}/{resolve({}, s, data)['model']}" for s in ("plan", "implement", "review")
                 if s not in external]
        return StepStatus("ok", " · ".join(parts) or "every stage runs on Claude Code / Codex")
    if step in ("claude", "codex"):
        return _harness_state(step)
    if step == "agents":
        return _agents_state()
    if step == "sandbox":
        return _sandbox_state()
    if step == "jira":
        return _jira_state()
    if step == "dind":
        from .sandbox.cli import is_podman
        if is_podman():
            return StepStatus("optional", PODMAN_DIND_NOTE)
        from .sandbox.environments import sysbox_available
        return StepStatus("ok", "Sysbox runtime available") if sysbox_available() else \
            StepStatus("optional", "Sysbox is not installed and running (only needed for jobs whose builds use Docker)")
    if step == "gpu":
        from mu.container.hardware import detect_hardware
        from mu.container.docker_cli import CommandRunner
        gpu = dict(detect_hardware(CommandRunner()).get("gpu") or {})
        if gpu.get("supported"):
            devices = gpu.get("devices") or []
            return StepStatus("ok", f"{len(devices)} GPU(s) available to containers" if devices else "GPU support available")
        return StepStatus("optional", str(gpu.get("reason") or "no GPU support detected"))
    raise KeyError(step)


def check_step(step: str) -> StepStatus:
    try:
        status = check(step)
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - a check reports, never raises
        status = StepStatus("warn", f"check failed: {str(exc)[:300]}")
    if step in (read_setup().get("skipped") or []) and status.state != "ok":
        return StepStatus("skipped", f"skipped · {status.detail}")
    return status


# ------------------------------------------------------------------ self-tests (long-running)


def run_sandbox_selftest(log: Callable[[str], None]) -> bool:
    """Isolation probe for mucli and every ready external agent, plan and implement."""
    from .harness import status
    from .sandbox import selftest
    from .sandbox.build import IMAGE, image_digest
    agents = ["mucli"] + [a for a in ("claude", "codex") if status()[a]["ready"]]
    failures, total = [], 0
    for agent in agents:
        for stage in ("plan", "implement"):
            log(f"{agent} · {stage}")
            checks = selftest.isolation(agent, stage, log=log)
            total += len(checks)
            failures += [f"{agent} {stage}: {c.name}" for c in checks if not c.ok]
    ok = not failures
    try:
        image = image_digest(IMAGE)
    except (OSError, subprocess.SubprocessError):
        image = None
    write_setup(selftest={"at": time.time(), "ok": ok, "image": image, "failures": failures[:20],
                          "summary": f"{total - len(failures)}/{total} checks · {', '.join(agents)}"})
    log(f"{'PASS' if ok else 'FAIL'}: {total - len(failures)}/{total} checks")
    return ok


def run_agent_test(agent: str, log: Callable[[str], None]) -> bool:
    from .sandbox import selftest
    result = selftest.agent_smoke(agent, log=log)
    smoke = dict(read_setup().get("smoke") or {})
    smoke[agent] = {"at": time.time(), "ok": result.ok, "detail": result.detail}
    write_setup(smoke=smoke)
    return result.ok


# ------------------------------------------------------------------ actions


def _act(id: str, label: str, kind: str, **extra: Any) -> Dict[str, Any]:
    return {"id": id, "label": label, "kind": kind, **{k: v for k, v in extra.items() if v not in (None, "")}}


def actions(step: str, status: Optional[StepStatus] = None) -> List[Dict[str, Any]]:
    """What the UI offers for ``step`` (without the callables)."""
    skipped = step in (read_setup().get("skipped") or [])
    skip = []
    if STEP_BY_ID[step].get("optional"):
        skip = [_act("unskip", "Un-skip this step", "run")] if skipped else \
            [_act("skip", "Skip this step", "run", hint="you can come back to it any time")]
    if step == "docker":
        return [_act("recheck", "Check again", "run")]
    if step == "git":
        ident = git_identity()
        return [_act("identity", "Set name and email", "prompts", prompts=[
            {"label": "Your name (git user.name)", "initial": ident["name"]},
            {"label": "Your email (git user.email)", "initial": ident["email"]}])]
    if step == "runtime":
        return [_act("build", "Build the runtime image", "job", hint="a few minutes the first time",
                     command="python3 -m mu.jobs.sandbox.build")]
    if step == "controller":
        return [_act("start", "Start the job controller", "run", hint="the detached mucli GUI daemon on 127.0.0.1:30311")]
    if step == "models":
        return [_act("all-stages", "Use one provider/model for the unset stages", "prompts", prompts=[
            {"label": "Provider (anthropic, openai, gemini, ollama)", "initial": "anthropic"},
            {"label": "Model", "initial": "claude-sonnet-5-5"}]),
            _act("settings", "Open Job settings (per-stage models)", "nav", nav="settings")]
    if step == "claude":
        out = [_act("setup-token", "Create a long-lived token (`claude setup-token`)", "terminal", command="claude setup-token",
                    hint="opens Claude's login flow; copy the token it prints, then paste it with the next action"),
               _act("paste-token", "Paste a Claude OAuth token", "prompts",
                    prompts=[{"label": "CLAUDE_CODE_OAUTH_TOKEN (hidden)", "initial": "", "secret": True}],
                    hint="saved 0600 in ~/.mucli/secrets.env; never shown again")]
        if (status or check("claude")).state in ("ok", "warn"):
            out.append(_act("test", "Run a test stage on Claude Code", "job", hint="one small model request in a real sandbox"))
        return out + skip
    if step == "codex":
        out = [_act("login", "Log in to Codex (`codex login`)", "terminal", command="codex login"),
               _act("paste-key", "Paste an OpenAI API key for Codex", "prompts",
                    prompts=[{"label": "OPENAI_API_KEY (hidden)", "initial": "", "secret": True}],
                    hint="saved 0600 in ~/.mucli/secrets.env; Settings → Agent harnesses picks login vs key")]
        if (status or check("codex")).state in ("ok", "warn"):
            out.append(_act("test", "Run a test stage on Codex", "job", hint="one small model request in a real sandbox"))
        return out + skip
    if step == "agents":
        from .harness import status as harness_status
        defaults = harness_status()["defaults"]
        return [_act("defaults", "Choose the default agents", "prompts", prompts=[
            {"label": "Agents: plan=…,implement=…,review=… (mucli, claude or codex)",
             "initial": ",".join(f"{s}={a}" for s, a in defaults.items())}]),
            _act("settings", "Open Job settings (models per agent, credentials)", "nav", nav="settings")]
    if step == "sandbox":
        return [_act("selftest", "Run the sandbox self-test", "job", hint="starts real stage containers; no model calls")]
    if step == "jira":
        from mu.integrations.jira_settings import public_settings
        info = public_settings()
        events = (info.get("writeback_settings") or {}).get("events") or {}
        return [_act("details", "Enter JIRA Cloud details (email + API token)", "prompts", prompts=[
            {"label": "JIRA base URL", "initial": info.get("base_url") or ""},
            {"label": "Atlassian email", "initial": info.get("email") or git_identity()["email"]},
            {"label": "API token (hidden; blank keeps the current one)", "initial": "", "secret": True}],
            hint="id.atlassian.com → Security → API tokens"),
            _act("pat", "Enter JIRA Data Center / Server details (personal access token)", "prompts", prompts=[
                {"label": "JIRA base URL", "initial": info.get("base_url") or ""},
                {"label": "Personal access token (hidden; blank keeps the current one)", "initial": "",
                 "secret": True}],
                hint="Jira profile → Personal Access Tokens"),
            _act("writeback", "Ticket writeback (comments and status changes)", "prompts", prompts=[
                {"label": "Writeback on or off", "initial": "on" if info.get("writeback") else "off"},
                {"label": "Move ticket to this status when a job starts (blank = leave it)",
                 "initial": (events.get("created") or {}).get("jira_status") or ""},
                {"label": "Status when the change is pushed for review (blank = leave it)",
                 "initial": (events.get("pushed") or {}).get("jira_status") or ""},
                {"label": "Status when the job is done (blank = leave it)",
                 "initial": (events.get("done") or {}).get("jira_status") or ""}],
                hint="statuses are matched by name against the ticket's workflow transitions; "
                     "JIRA_WRITEBACK=1/0 overrides the on/off switch"),
            _act("test", "Test the connection", "run")] + skip
    if step == "dind":
        from .sandbox.environments import SYSBOX_GUIDANCE
        return [_act("recheck", "Check again", "run", hint=" ".join(SYSBOX_GUIDANCE))] + skip
    if step == "gpu":
        return [_act("recheck", "Check again", "run",
                     hint="install the NVIDIA Container Toolkit and restart Docker")] + skip
    return []


class Setup:
    """Live setup state shared by every UI in this process (one background job at a time)."""

    def __init__(self) -> None:
        self.status: Dict[str, StepStatus] = {}
        self.checking: set = set()
        self.lock = threading.RLock()
        self.job: Optional[Dict[str, Any]] = None
        self.notice = ""

    # -------------------------------------------------- checks

    def recheck(self, steps: Optional[List[str]] = None) -> None:
        steps = list(steps or STEP_IDS)
        with self.lock:
            self.checking.update(steps)
        threads = [threading.Thread(target=self._check_one, args=(s,), daemon=True) for s in steps]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)

    def _check_one(self, step: str) -> None:
        status = check_step(step)
        with self.lock:
            self.status[step] = status
            self.checking.discard(step)

    # -------------------------------------------------- view

    def vm(self) -> Dict[str, Any]:
        with self.lock:
            steps = []
            for meta in STEPS:
                st = self.status.get(meta["id"])
                steps.append({**meta, "optional": bool(meta.get("optional")), "checking": meta["id"] in self.checking or st is None,
                              "state": st.state if st else "", "detail": st.detail if st else "checking…",
                              "actions": actions(meta["id"], st) if st else []})
            checked = [s for s in steps if not s["checking"]]
            done = sum(1 for s in checked if s["state"] in ("ok", "skipped"))
            blocking = [s["title"] for s in checked if not s["optional"] and s["state"] not in ("ok", "skipped")]
            all_checked = len(checked) == len(steps)
            return {"steps": steps, "done": done, "total": len(steps), "all_checked": all_checked,
                    "blocking": blocking, "can_finish": all_checked and not blocking,
                    "completed_at": read_setup().get("completed_at"), "job": dict(self.job) if self.job else None,
                    "notice": self.notice}

    # -------------------------------------------------- actions

    def action(self, step: str, action_id: str, inputs: Optional[List[str]] = None, *, wait: bool = False,
               on_line: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """Run one action. ``job`` actions run in the background (``wait`` blocks, for the terminal)."""
        if step not in STEP_BY_ID:
            raise ValueError(f"unknown setup step {step!r}")
        offered = {a["id"]: a for a in actions(step, self.status.get(step))}
        if action_id not in offered:
            raise ValueError(f"{step}: no action {action_id!r} right now")
        spec = offered[action_id]
        inputs = [str(v or "") for v in (inputs or [])]
        if spec["kind"] == "prompts" and len(inputs) < len(spec.get("prompts") or []):
            raise ValueError(f"{spec['label']}: {len(spec['prompts'])} answers needed")
        if spec["kind"] == "terminal":
            # The command ran in the user's terminal; just re-check.
            self.recheck([step, "agents", "sandbox"])
            return {"ok": True, "message": "checked again"}
        if spec["kind"] == "nav":
            return {"ok": True, "nav": spec.get("nav")}
        if spec["kind"] == "job":
            return self._start_job(step, spec, wait=wait, on_line=on_line)
        message = self._do(step, action_id, inputs)
        self.recheck(list(dict.fromkeys([step, *self._also(step)])))
        with self.lock:
            self.notice = message
        return {"ok": True, "message": message}

    @staticmethod
    def _also(step: str) -> List[str]:
        return {"claude": ["agents", "models"], "codex": ["agents", "models"], "agents": ["models", "claude", "codex"],
                "models": ["agents"], "docker": STEP_IDS, "runtime": ["sandbox"]}.get(step, [])

    def _do(self, step: str, action_id: str, inputs: List[str]) -> str:
        from . import harness
        if action_id == "recheck":
            return "checked again"
        if action_id in ("skip", "unskip"):
            set_skipped(step, action_id == "skip")
            return "skipped" if action_id == "skip" else "un-skipped"
        if step == "git":
            name, email = (v.strip() for v in inputs[:2])
            if not name or not email:
                raise ValueError("name and email are both needed")
            for key, value in (("user.name", name), ("user.email", email)):
                result = _run(["git", "config", "--global", key, value])
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or f"git config {key} failed")
            return f"git identity set to {name} <{email}>"
        if step == "controller":
            from .host import ensure_controller_daemon
            host = ensure_controller_daemon()
            if not host.running:
                raise RuntimeError(host.detail)
            return host.detail or "controller running"
        if step == "models":
            from .job_settings import missing_stages, save_settings
            provider, model = (v.strip() for v in inputs[:2])
            if not provider or not model:
                raise ValueError("provider and model are both needed")
            stages = {s: {"provider": provider, "model": model} for s in missing_stages({})}
            save_settings({"stage_models": stages})
            return f"{provider}/{model} set for {', '.join(stages) or 'no stages (all were set)'}"
        if step == "claude" and action_id == "paste-token":
            if not inputs[0].strip():
                raise ValueError("paste the token printed by `claude setup-token`")
            harness.write_secret("CLAUDE_CODE_OAUTH_TOKEN", inputs[0].strip())
            return "Claude token saved"
        if step == "codex" and action_id == "paste-key":
            if not inputs[0].strip():
                raise ValueError("paste an OpenAI API key")
            harness.write_secret("OPENAI_API_KEY", inputs[0].strip())
            return "OpenAI API key saved"
        if step == "agents":
            from .job_settings import save_settings
            chosen = harness.parse_agent_spec(inputs[0])
            missing = [s for s in harness.STAGES if s not in chosen]
            if missing:
                raise ValueError(f"name an agent for every stage (missing: {', '.join(missing)})")
            save_settings({"harness": {"default": {s: cfg["agent"] for s, cfg in chosen.items()}}})
            return "default agents saved"
        if step == "jira" and action_id == "details":
            from mu.integrations.jira_settings import save_settings as save_jira
            url, email, token = (v.strip() for v in inputs[:3])
            save_jira({"base_url": url, "email": email, **({"api_token": token} if token else {})})
            _jira_ok_for.update(sig="")
            return "JIRA details saved"
        if step == "jira" and action_id == "pat":
            from mu.integrations.jira_settings import save_settings as save_jira
            url, pat = (v.strip() for v in inputs[:2])
            save_jira({"base_url": url, **({"pat": pat} if pat else {})})
            _jira_ok_for.update(sig="")
            return "JIRA Data Center details saved"
        if step == "jira" and action_id == "writeback":
            from mu.integrations.jira_settings import save_settings as save_jira, writeback_settings
            switch, started, pushed, done = (v.strip() for v in inputs[:4])
            if switch.lower() not in {"on", "off", "yes", "no", "true", "false", "1", "0"}:
                raise ValueError("answer on or off")
            events = dict(writeback_settings().get("events") or {})
            for name, status in (("created", started), ("pushed", pushed), ("done", done)):
                events[name] = {**dict(events.get(name) or {}), "jira_status": status}
            save_jira({"writeback": {"enabled": switch.lower() in {"on", "yes", "true", "1"}, "events": events}})
            _jira_ok_for.update(sig="")
            return f"JIRA writeback {'on' if switch.lower() in {'on', 'yes', 'true', '1'} else 'off'}"
        if step == "jira" and action_id == "test":
            _jira_ok_for.update(sig="")
            return "connection tested"
        raise ValueError(f"{step}: unsupported action {action_id}")

    def _start_job(self, step: str, spec: Dict[str, Any], *, wait: bool,
                   on_line: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        with self.lock:
            if self.job and self.job.get("running"):
                raise RuntimeError(f"{self.job['title']} is still running")
            self.job = {"step": step, "action": spec["id"], "title": spec["label"], "running": True, "ok": None,
                        "lines": [], "started_at": time.time()}

        def log(line: str) -> None:
            with self.lock:
                lines = self.job["lines"]
                lines.append(str(line)[:500])
                del lines[:-400]
            if on_line is not None:
                try:
                    on_line(str(line))
                except Exception:  # noqa: BLE001 - a display callback must not break the job
                    pass

        def work() -> None:
            ok = False
            try:
                if step == "runtime":
                    from .sandbox.build import build_image
                    build_image(log=log)
                    ok = True
                elif step == "sandbox":
                    ok = run_sandbox_selftest(log)
                elif step in ("claude", "codex"):
                    ok = run_agent_test(step, log)
            except Exception as exc:  # noqa: BLE001 - the log carries the failure
                log(f"✗ {exc}")
            finally:
                with self.lock:
                    self.job.update(running=False, ok=ok, finished_at=time.time())
                self.recheck(list(dict.fromkeys([step, *self._also(step)])))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        if wait:
            thread.join()
        return {"ok": True, "message": f"{spec['label']} started", "job": dict(self.job)}

    def finish(self) -> Dict[str, Any]:
        view = self.vm()
        if not view["can_finish"]:
            raise ValueError("still to do: " + ", ".join(view["blocking"] or ["checking…"]))
        write_setup(completed_at=time.time())
        return self.vm()


_setup: Optional[Setup] = None
_setup_lock = threading.Lock()


def get_setup() -> Setup:
    global _setup
    with _setup_lock:
        if _setup is None:
            _setup = Setup()
        return _setup


# ------------------------------------------------------------------ doctor

PODMAN_DIND_NOTE = ("Podman: job stage containers are supported, but the per-job Docker engine (Sysbox "
                    "Docker-in-Docker) is not; jobs whose build environment sets `docker` need Docker "
                    "(jobs.container_cli = docker)")
ROOTLESS_DOCKER_HINT = ("rootless Docker: stages run as --user <your uid>:<gid>, which rootless Docker maps to a "
                        "subordinate host uid, so writes to the bind-mounted worktree fail with \"Permission denied\". "
                        "Fix: use rootful Docker (sudo usermod -aG docker $USER), or Podman (jobs.container_cli = podman), "
                        "where mucli adds --userns=keep-id so the stage user is you")
SERVICE_TESTS_NOTE = ("Not yet supported: tests that start services or publish ports from inside a stage container "
                      "(testcontainers, docker compose port mapping, a database on localhost) fail there: stage containers "
                      "have no network and the per-job engine is reachable only through the policy proxy. Run such suites "
                      "outside the job, or mock the service.")


def sandbox_host_checks(run: Callable[[List[str]], subprocess.CompletedProcess] = None) -> List[tuple]:
    """``(name, status, detail)`` rows about the container host: CLI, rootless mode, Podman,
    SELinux, the engine's data-root disk, sandbox limits and known unsupported test setups."""
    from .sandbox import cli as sandbox_cli
    run = run or _run
    rows: List[tuple] = []
    name = sandbox_cli.container_cli()
    binary = shutil.which(name)
    configured = str(sandbox_cli.setting("jobs.container_cli", "auto") or "auto")
    if not binary:
        rows.append(("container_cli", "fail", f"{name} not found on PATH (jobs.container_cli = {configured}); install Docker or Podman"))
        podman = False
    else:
        podman = sandbox_cli.is_podman()
        rows.append(("container_cli", "ok", f"{'podman' if podman else name} · {binary} (jobs.container_cli = {configured})"))

    def info(fmt: str) -> Optional[str]:
        if not binary:
            return None
        result = run([binary, "info", "--format", fmt])
        return (result.stdout or "").strip() if result.returncode == 0 else None
    if binary and podman:
        rootless = (info("{{.Host.Security.Rootless}}") or "").lower() == "true"
        rows.append(("rootless", "ok", "rootless Podman: stages use --userns=keep-id so their user is you" if rootless
                     else "rootful Podman"))
    elif binary:
        security = info("{{json .SecurityOptions}}")
        if security is None:
            rows.append(("rootless", "warn", "cannot query the Docker daemon (docker info failed)"))
        elif "rootless" in security:
            rows.append(("rootless", "warn", ROOTLESS_DOCKER_HINT))
        else:
            rows.append(("rootless", "ok", "rootful Docker"))
    other = shutil.which("podman")
    if podman:
        rows.append(("podman", "warn", PODMAN_DIND_NOTE))
    else:
        rows.append(("podman", "ok", f"Podman installed at {other} (set jobs.container_cli = podman to use it)" if other
                     else "Podman not installed (optional)"))
    rows.append(("selinux", "ok", "SELinux enforcing: job bind mounts get the shared container label (:z / relabel=shared)"
                 if sandbox_cli.selinux_enforcing() else "SELinux not enforcing"))
    if binary:
        root = info("{{.Store.GraphRoot}}" if podman else "{{.DockerRootDir}}")
        if root:
            try:
                free = shutil.disk_usage(root).free / 1e9
                rows.append(("container_data_root", "ok" if free >= 10 else "warn",
                             f"{free:.1f} GB free under {root} (images, build environments and engine volumes live here)"))
            except OSError as exc:
                rows.append(("container_data_root", "warn", f"cannot measure {root}: {exc}"))
        else:
            rows.append(("container_data_root", "warn", "cannot read the container engine's data root"))
    from .sandbox.cache import cache_root, max_bytes, usage
    from .sandbox.policy import SandboxPolicy
    try:
        policy = SandboxPolicy.for_stage("checks")
        cap = max_bytes()
        used = usage()["bytes"]
        rows.append(("sandbox_limits", "ok",
                     f"per stage container: {policy.cpu:g} CPUs · memory {policy.memory} · {policy.pids_limit} processes · "
                     f"/tmp 512 MB · dependency cache {used / 1e9:.1f} GB of "
                     f"{'unlimited' if not cap else f'{cap / 1024 ** 3:g} GB'} ({cache_root()}) · "
                     "set sandbox.cpu / sandbox.memory / sandbox.pids_limit / sandbox.cache_max_gb"))
    except ValueError as exc:
        rows.append(("sandbox_limits", "fail", str(exc)))
    rows.append(("service_tests", "ok", SERVICE_TESTS_NOTE))
    return rows


@dataclass
class DoctorCheck:
    name: str
    status: str  # ok | warn | fail
    detail: str = ""


def doctor(*, deep: bool = False, log: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    """Quick report of everything job runs depend on; ``deep`` adds the sandbox isolation self-test."""
    from . import harness
    out: List[DoctorCheck] = []

    def add(name: str, fn: Callable[[], Any]) -> None:
        try:
            value = fn()
            status, detail = value if isinstance(value, tuple) else ("ok", str(value))
        except Exception as exc:  # noqa: BLE001
            status, detail = "fail", str(exc).splitlines()[0][:400] if str(exc) else type(exc).__name__
        out.append(DoctorCheck(name, status, detail))
        if log:
            log(f"{'✓' if status == 'ok' else '!' if status == 'warn' else '✗'} {name}: {detail}")

    def step(step_id: str, *, optional: bool = False):
        def run():
            st = check(step_id)
            if st.state == "ok":
                return "ok", st.detail
            if st.state in ("optional",) or optional:
                return "warn", st.detail
            return ("warn" if st.state == "warn" else "fail"), st.detail
        return run

    add("docker", step("docker"))
    add("git_identity", step("git"))
    add("runtime_image", step("runtime"))

    def settings_check():
        from .job_settings import load_settings, settings_path, validate_rules
        data = load_settings()
        validate_rules(data)
        return "ok", f"{settings_path()} valid"
    add("job_settings", settings_check)
    add("controller", step("controller"))
    add("stage_models", step("models"))
    defaults = harness.status()["defaults"]
    for agent in ("claude", "codex"):
        used = agent in defaults.values()

        def binary_check(agent=agent, used=used):
            path = harness.binary(agent)
            if not path:
                return ("fail" if used else "warn"), "not installed" + (" (a default agent!)" if used else "")
            kind = harness.binary_kind(path)
            return "ok", f"{harness.version(path)} · {path}" + (" · glibc (Debian/Ubuntu images only)" if kind == "glibc" else "")

        def creds_check(agent=agent, used=used):
            creds = harness.credentials(agent)
            if not creds:
                return ("fail" if used else "warn"), "no credentials" + (" (a default agent!)" if used else "")
            expires = creds.get("expires_at")
            if expires and expires - time.time() < 12 * 3600:
                return "warn", f"{creds['kind']}; token expires in {max(0, int((expires - time.time()) / 60))} min"
            return ("warn" if creds["kind"] == "login" else "ok"), f"{creds['kind']} from {creds['source']}"
        add(f"{agent}_binary", binary_check)
        add(f"{agent}_credentials", creds_check)
        smoke = (read_setup().get("smoke") or {}).get(agent)
        if smoke:
            add(f"{agent}_test_stage", lambda smoke=smoke: ("ok" if smoke.get("ok") else "fail",
                                                           f"{'passed' if smoke.get('ok') else 'failed'} "
                                                           f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(smoke['at']))} · {smoke.get('detail', '')}"))
    add("agent_defaults", step("agents"))
    if deep:
        def deep_selftest():
            lines: List[str] = []
            ok = run_sandbox_selftest(lines.append)
            return ("ok" if ok else "fail"), lines[-1] if lines else ""
        add("sandbox_selftest", deep_selftest)
    else:
        add("sandbox_selftest", step("sandbox"))
    try:
        host_rows = sandbox_host_checks()
    except Exception as exc:  # noqa: BLE001 - a check reports, never raises
        host_rows = [("container_host", "warn", f"check failed: {str(exc)[:300]}")]
    for row_name, row_status, row_detail in host_rows:
        add(row_name, lambda st=row_status, de=row_detail: (st, de))
    add("docker_in_docker", step("dind", optional=True))
    add("gpus", step("gpu", optional=True))
    add("jira", step("jira", optional=True))

    def disk():
        root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
        os.makedirs(root, exist_ok=True)
        free = shutil.disk_usage(root).free / 1e9
        return ("ok" if free >= 5 else "warn"), f"{free:.1f} GB free under {root}"
    add("disk_space", disk)
    return [asdict(c) for c in out]
