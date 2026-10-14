"""External agent harnesses for job stages: mucli (built-in), Claude Code and Codex.

Design: ``documentation/plans/agent_harnesses.md``. A job picks the agent per agent stage
(``execution.stage_agents[stage] = {"agent", "model", "effort"}``); unset stages fall back to
Settings → Agent harnesses. This module is host-side: it resolves the agent and model, finds the
CLI binaries, resolves the credential that will be copied into the stage sandbox (planos-style:
exactly one per harness) and reports setup status.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

AGENTS = ("mucli", "claude", "codex")
AGENT_LABELS = {"mucli": "mucli", "claude": "Claude Code", "codex": "Codex"}
STAGES = ("plan", "implement", "review")
MODEL_RE = re.compile(r"[\w.:/@+-]{0,120}")
EFFORTS = ("", "none", "minimal", "low", "medium", "high", "xhigh", "max")

# planos defaults: review deliberately uses a different model from implement.
DEFAULT_MODELS = {
    "claude": {"plan": "claude-opus-5-5", "implement": "claude-sonnet-5-5", "review": "claude-opus-5-5"},
    "codex": {"plan": "", "implement": "", "review": ""},  # "" = Codex's configured default
}
# Hosts a harness stage must reach (added to the stage's egress allowlist on every stage).
PROVIDER_HOSTS = {
    "claude": ("api.anthropic.com", "statsig.anthropic.com"),
    "codex": ("api.openai.com", "chatgpt.com", "auth.openai.com"),
}
SECRET_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


# ------------------------------------------------------------------ per-stage selection


def _rule(path: str, settings: Optional[Dict[str, Any]] = None) -> Any:
    from .job_settings import rule
    return rule(path, settings) if settings is not None else rule(path)


def clean_stage_agents(raw: Any) -> Dict[str, Dict[str, str]]:
    """Validate ``execution.stage_agents``: ``{stage: {agent, model?, effort?}}`` or ``{stage: agent}``."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("stage_agents must be an object of stage → agent")
    out: Dict[str, Dict[str, str]] = {}
    for stage, value in raw.items():
        if stage not in STAGES:
            raise ValueError(f"unknown agent stage {stage!r} (plan, implement or review)")
        if isinstance(value, str):
            value = {"agent": value}
        if not isinstance(value, dict):
            raise ValueError(f"stage_agents.{stage} must be an agent name or an object")
        agent = str(value.get("agent") or "").strip().lower()
        if agent not in AGENTS:
            raise ValueError(f"unknown agent {agent!r} for {stage}; choose mucli, claude or codex")
        model = str(value.get("model") or "").strip()
        if not MODEL_RE.fullmatch(model):
            raise ValueError(f"invalid model name for {stage}: {model!r}")
        effort = str(value.get("effort") or "").strip().lower()
        if effort not in EFFORTS:
            raise ValueError(f"invalid effort for {stage}: {effort!r}")
        entry = {"agent": agent}
        if model:
            entry["model"] = model
        if effort:
            entry["effort"] = effort
        out[stage] = entry
    return out


def parse_agent_spec(text: str) -> Dict[str, Dict[str, str]]:
    """CLI ``--agent claude`` (all stages) or ``--agent plan=claude,implement=codex:gpt-5.5``."""
    text = str(text or "").strip()
    if not text:
        return {}
    if "=" not in text:
        agent, _, model = text.partition(":")
        return clean_stage_agents({s: {"agent": agent, "model": model} for s in STAGES})
    raw: Dict[str, Dict[str, str]] = {}
    for item in text.split(","):
        stage, _, rest = item.partition("=")
        agent, _, model = rest.partition(":")
        raw[stage.strip()] = {"agent": agent.strip(), "model": model.strip()}
    return clean_stage_agents(raw)


def stage_agent(job: Any, stage: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """``{"agent", "model", "effort", "source"}`` for one stage of ``job``."""
    execution = dict(getattr(job, "execution", None) or {})
    chosen = (execution.get("stage_agents") or {}).get(stage) or {}
    chosen = {"agent": chosen} if isinstance(chosen, str) else dict(chosen) if isinstance(chosen, dict) else {}
    agent = str(chosen.get("agent") or "").lower()
    source = "job" if agent else "settings"
    if agent not in AGENTS:
        agent = str(_rule(f"harness.default.{stage}", settings) or "mucli").lower()
    if agent not in AGENTS:
        agent = "mucli"
    model = str(chosen.get("model") or "")
    if not model and agent != "mucli":
        model = str(_rule(f"harness.{agent}.models.{stage}", settings) or "")
    effort = str(chosen.get("effort") or "") or str(_rule(f"jobs.stage_effort.{stage}", settings) or "")
    return {"agent": agent, "model": model, "effort": effort, "source": source}


def stage_agents(job: Any, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    return {stage: stage_agent(job, stage, settings) for stage in STAGES}


def harness_stages(job: Any, settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """Agent stages run by Claude Code / Codex (they need no mucli stage model)."""
    return [s for s, cfg in stage_agents(job, settings).items() if cfg["agent"] != "mucli"]


# ------------------------------------------------------------------ binaries


def binary(agent: str, settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if agent not in ("claude", "codex"):
        return None
    configured = str(_rule(f"harness.{agent}.binary", settings) or "").strip()
    candidates = [os.path.expanduser(configured)] if configured else [
        os.path.expanduser(f"~/.local/bin/{agent}"), shutil.which(agent) or ""]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.realpath(candidate)
    return None


_version_cache: Dict[str, str] = {}


def version(path: Optional[str]) -> str:
    if not path:
        return ""
    if path not in _version_cache:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
            _version_cache[path] = (out.stdout or out.stderr).strip().splitlines()[0][:120] if (out.stdout or out.stderr) else ""
        except (OSError, subprocess.SubprocessError, IndexError):
            _version_cache[path] = ""
    return _version_cache[path]


def binary_kind(path: Optional[str]) -> str:
    """``static`` binaries run in any image; ``glibc`` ones need a glibc-based image."""
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read(1 << 20)
    except OSError:
        return ""
    return "glibc" if b"/lib64/ld-linux" in data or b"ld-linux-x86-64" in data else "static"


# ------------------------------------------------------------------ secrets (planos secrets.env)


def secrets_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "secrets.env")


_secrets_lock = threading.Lock()


def read_secrets() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(secrets_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key in SECRET_KEYS:
                    out[key] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def write_secret(key: str, value: Optional[str]) -> None:
    """Store (or with an empty value, remove) one harness secret; 0600, atomic."""
    if key not in SECRET_KEYS:
        raise ValueError(f"unknown secret {key!r}")
    value = str(value or "").strip()
    if any(ch in value for ch in "\n\r'\""):
        raise ValueError("secret values cannot contain quotes or newlines")
    with _secrets_lock:
        data = read_secrets()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
        path = secrets_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".secrets.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("".join(f"{k}='{v}'\n" for k, v in sorted(data.items())))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _secret(key: str) -> str:
    return os.environ.get(key) or read_secrets().get(key, "")


def mask(value: str) -> str:
    """Never echo any part of a secret: only whether it is set."""
    return "set" if str(value or "") else ""


# ------------------------------------------------------------------ credentials


def _claude_config_dir() -> str:
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def _codex_home() -> str:
    return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")


def credentials(agent: str, settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The one credential copied into the stage sandbox for ``agent`` (planos order), or None.

    ``{"kind", "source", "env": {NAME: value}, "files": {relative path: content},
    "expires_at": epoch|None, "warning": str}``."""
    mode = str(_rule(f"harness.{agent}.auth", settings) or "auto").lower() if agent in ("claude", "codex") else "auto"
    if agent == "claude":
        key, token = _secret("ANTHROPIC_API_KEY"), _secret("CLAUDE_CODE_OAUTH_TOKEN")
        if mode in ("auto", "api_key") and key:
            return {"kind": "api_key", "source": "ANTHROPIC_API_KEY", "env": {"ANTHROPIC_API_KEY": key}, "files": {},
                    "expires_at": None, "warning": ""}
        if mode in ("auto", "oauth_token") and token:
            return {"kind": "oauth_token", "source": "CLAUDE_CODE_OAUTH_TOKEN", "env": {"CLAUDE_CODE_OAUTH_TOKEN": token},
                    "files": {}, "expires_at": None, "warning": ""}
        path = os.path.join(_claude_config_dir(), ".credentials.json")
        if mode in ("auto", "login") and os.path.isfile(path):
            try:
                content = open(path, encoding="utf-8").read()
                expires = (json.loads(content).get("claudeAiOauth") or {}).get("expiresAt")
            except (OSError, ValueError):
                return None
            expires_at = float(expires) / (1000 if expires and float(expires) > 1e11 else 1) if expires else None
            return {"kind": "login", "source": path, "env": {}, "files": {".credentials.json": content},
                    "expires_at": expires_at,
                    "warning": "Using your Claude login session. If the sandboxed agent refreshes the token, your "
                               "host login can be invalidated; `claude setup-token` is more reliable."}
        return None
    if agent == "codex":
        # Codex itself prefers its login (auth.json) over OPENAI_API_KEY; mirror that in auto mode.
        key = _secret("OPENAI_API_KEY")
        path = os.path.join(_codex_home(), "auth.json")
        if mode in ("auto", "login") and os.path.isfile(path):
            try:
                content = open(path, encoding="utf-8").read()
                auth_mode = str(json.loads(content).get("auth_mode") or "")
            except (OSError, ValueError):
                content, auth_mode = "", ""
            if content:
                return {"kind": "login", "source": path, "env": {}, "files": {"auth.json": content}, "expires_at": None,
                        "auth_mode": auth_mode,
                        "warning": "Using your Codex login (auth.json). Token refreshes inside the sandbox are not "
                                   "written back and can invalidate the host login; an API key is more reliable."
                                   + (" A ChatGPT login only allows some models." if auth_mode == "chatgpt" else "")}
        if mode in ("auto", "api_key") and key:
            return {"kind": "api_key", "source": "OPENAI_API_KEY", "env": {"OPENAI_API_KEY": key}, "files": {},
                    "expires_at": None, "warning": ""}
        return None
    return None


# ------------------------------------------------------------------ status (setup / doctor)


def status(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Setup view for each harness: binary, version, credential kind, warnings, readiness."""
    out: Dict[str, Any] = {"mucli": {"agent": "mucli", "label": AGENT_LABELS["mucli"], "ready": True, "problems": [],
                                     "warnings": []}}
    for agent in ("claude", "codex"):
        path = binary(agent, settings)
        creds = credentials(agent, settings)
        problems: List[str] = []
        warnings: List[str] = []
        if not path:
            problems.append(f"{AGENT_LABELS[agent]} is not installed (no `{agent}` in ~/.local/bin or PATH; "
                            f"set Settings → Agent harnesses → binary).")
        if not creds:
            problems.append("No credentials: " + ("set ANTHROPIC_API_KEY, or run `claude setup-token` and save the "
                                                  "token, or log in with `claude`." if agent == "claude" else
                                                  "set OPENAI_API_KEY or run `codex login`."))
        else:
            if creds.get("warning"):
                warnings.append(creds["warning"])
            if creds.get("expires_at") and creds["expires_at"] - time.time() < 12 * 3600:
                warnings.append("The login token expires within 12 hours; long stages may fail.")
        kind = binary_kind(path)
        if agent in READ_ONLY_NOTE:
            warnings.append(READ_ONLY_NOTE[agent])
        if kind == "glibc":
            warnings.append("This binary needs a glibc-based job image (Debian/Ubuntu); Alpine build environments can't run it.")
        out[agent] = {
            "agent": agent, "label": AGENT_LABELS[agent], "binary": path or "", "version": version(path),
            "binary_kind": kind, "credential": (creds or {}).get("kind", ""), "credential_source": (creds or {}).get("source", ""),
            "expires_at": (creds or {}).get("expires_at"), "ready": bool(path and creds), "problems": problems,
            "warnings": warnings, "default_models": dict(DEFAULT_MODELS[agent]),
            "models": {s: str(_rule(f"harness.{agent}.models.{s}", settings) or "") for s in STAGES},
        }
    out["defaults"] = {s: str(_rule(f"harness.default.{s}", settings) or "mucli") for s in STAGES}
    stored = read_secrets()
    out["secrets"] = {k: ("environment" if os.environ.get(k) else "secrets.env" if stored.get(k) else "")
                      for k in SECRET_KEYS}
    out["setup"] = {
        "claude": ["Install Claude Code (https://docs.claude.com/claude-code), then either set ANTHROPIC_API_KEY, "
                   "or run `claude setup-token` and paste the token here (most reliable for long jobs)."],
        "codex": ["Install Codex (`npm i -g @openai/codex` or the standalone release), then set OPENAI_API_KEY or "
                  "run `codex login`."],
    }
    return out


# ------------------------------------------------------------------ stage container plumbing (host side)


def binary_mounts(agent: str, settings: Optional[Dict[str, Any]] = None) -> List[tuple]:
    """``(src, dst, readonly)`` mounts that make the host CLI available in the stage container."""
    path = binary(agent, settings)
    if not path:
        raise FileNotFoundError(f"{AGENT_LABELS.get(agent, agent)} is not installed; see Settings → Agent harnesses")
    if agent == "claude":
        return [(path, "/opt/harness/claude", True)]
    # The standalone Codex release ships bin/codex with codex-path/ (rg) and codex-resources/.
    release = os.path.dirname(os.path.dirname(path))
    if os.path.basename(os.path.dirname(path)) == "bin" and os.path.isfile(os.path.join(release, "codex-package.json")):
        return [(release, "/opt/harness/codex", True)]
    return [(path, "/opt/harness/codex/bin/codex", True)]


def stage_credentials(agent: str, directory: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write the one credential for ``agent`` into ``directory`` (env.json + files/), 0700/0600."""
    creds = credentials(agent, settings)
    if not creds:
        raise PermissionError(f"No {AGENT_LABELS.get(agent, agent)} credentials; see Settings → Agent harnesses")
    os.makedirs(os.path.join(directory, "files"), mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    target = os.path.join(directory, "env.json")
    with open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        json.dump(creds.get("env") or {}, handle)
    for name, content in (creds.get("files") or {}).items():
        path = os.path.join(directory, "files", os.path.basename(name))
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
            handle.write(content)
    return {k: v for k, v in creds.items() if k not in ("env", "files")}


STATE_LIMIT_BYTES = 256 * 1024 * 1024


def copy_state(src: str, dst: str, *, limit: int = STATE_LIMIT_BYTES) -> int:
    """Copy a CLI session store, regular files only (no symlinks, no special files), up to ``limit``."""
    total = 0
    if not os.path.isdir(src) or os.path.islink(src):
        return 0
    for directory, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(directory, d))]
        rel = os.path.relpath(directory, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for name in files:
            path = os.path.join(directory, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if total + size > limit:
                continue
            shutil.copyfile(path, os.path.join(dst, rel, name))
            total += size
    return total


def provider_hosts(agent: str) -> tuple:
    return PROVIDER_HOSTS.get(agent, ())


READ_ONLY_NOTE = {
    "codex": "Codex's own sandbox (bubblewrap) cannot run inside the job container, so Codex implement stages "
             "without auto-approve route every command through Codex's automatic approval review, which may refuse "
             "to run project code such as tests. Enable auto-approve writes for Codex implement stages, or use "
             "Claude Code / mucli for implement.",
}
