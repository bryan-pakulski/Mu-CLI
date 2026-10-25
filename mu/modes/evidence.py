"""Engine-run evidence: ``CheckRun`` and ``run_check`` (modes v2 §3.2).

A ``CheckRun`` records that the harness ran a command and what happened:
command, cwd, exit code, whether its predicate held, a ≤2 KB excerpt, the full
output (session log file + the run's result store), file hashes the command
depends on, and where it ran.

``run_check`` is the only new way for a mode to execute a command. It applies
the same controls as ``bash``: plan mode refuses it, the bash secret guard
inspects the command, there is no login shell (``bash -c``), the environment
is scrubbed of credentials, output is redacted, and runs are time-limited. The
tool that calls it is approval-gated and registers an approval preview so the
user sees the exact command (``mu.modes.preview``).

Predicates (``CheckSpec.predicate``):

* ``{"exit": 0}``                       exit code equals the value
* ``{"exit_nonzero": true}``            exit code is not 0
* ``{"markers": [...]}``                every marker appears in the output
* ``{"markers": [...], "absent": true}`` no marker appears
* ``{"junit": "report.xml"}``           the JUnit report (relative to cwd) has tests and no failures
* ``{"all": [p, ...]}`` / ``{"any": [p, ...]}``
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .state import ModeState, now, register_mode_state, state_for

PURPOSES = ("repro", "verify", "regression", "exit_criterion", "poc", "remediation",
            "grader", "suite", "explore", "probe")
EXCERPT_BYTES = 2048
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 1800
MAX_RUNS = 1000

# Environment variables that never reach a check: anything credential-shaped.
_SECRET_ENV_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|COOKIE|SESSION|AUTH|PRIVATE|_PAT$|^PAT_)", re.I
)
_KEEP_ENV = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TERM", "SHELL",
                       "TMPDIR", "TZ", "PWD", "VIRTUAL_ENV", "PYTHONPATH", "NODE_PATH",
                       "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME", "JAVA_HOME"})


class CheckRefused(Exception):
    """The harness will not run this command (plan mode, secret guard, bad cwd)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class CheckSpec:
    command: str
    purpose: str = "verify"
    predicate: Dict[str, Any] = field(default_factory=lambda: {"exit": 0})
    cwd: str = ""
    timeout_s: int = DEFAULT_TIMEOUT_S
    hash_paths: List[str] = field(default_factory=list)
    runner: str = "auto"        # auto | host | docker
    label: str = ""             # short human label ("Repro fails as expected")
    ref: str = ""               # owning record (case/task/finding id)
    network: bool = True        # docker runner only
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class CheckRun:
    id: str
    purpose: str
    command: str
    cwd: str
    exit_code: Optional[int]
    passed: bool
    predicate: Dict[str, Any]
    excerpt: str
    result_ref: Optional[str] = None
    log_path: Optional[str] = None
    started_at: float = 0.0
    duration_s: float = 0.0
    hashes: Dict[str, str] = field(default_factory=dict)
    runner: str = "host"
    label: str = ""
    ref: str = ""
    timed_out: bool = False
    tests: Optional[Dict[str, Any]] = None
    source: str = "engine"      # engine (run_check) | observed (a bash call the harness recognised)
    detail: str = ""            # why the predicate failed / passed

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CheckRun":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in (raw or {}).items() if k in known}
        data.setdefault("id", "")
        data.setdefault("purpose", "verify")
        data.setdefault("command", "")
        data.setdefault("cwd", "")
        data.setdefault("exit_code", None)
        data.setdefault("passed", False)
        data.setdefault("predicate", {})
        data.setdefault("excerpt", "")
        return cls(**data)

    def summary(self) -> str:
        mark = "passed" if self.passed else "failed"
        exit_txt = "timeout" if self.timed_out else f"exit {self.exit_code}"
        label = self.label or self.purpose
        return f"{self.id} {label}: {mark} ({exit_txt}, {self.duration_s:.1f}s) — {self.command[:120]}"

    def brief(self) -> Dict[str, Any]:
        """Compact form for tool results."""
        out = {
            "id": self.id, "purpose": self.purpose, "passed": self.passed,
            "exit_code": self.exit_code, "timed_out": self.timed_out,
            "command": self.command, "runner": self.runner,
            "duration_s": self.duration_s, "detail": self.detail,
            "excerpt": self.excerpt, "result_ref": self.result_ref,
        }
        if self.tests:
            out["tests"] = {k: v for k, v in self.tests.items() if k != "cases"}
        return out


@register_mode_state
class ChecksState(ModeState):
    """Every ``CheckRun`` of the session, addressed ``r<n>``."""

    name = "checks"

    def __init__(self) -> None:
        super().__init__()
        self.runs: Dict[str, CheckRun] = {}
        self.counter = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"counter": self.counter, "runs": [r.to_dict() for r in self.runs.values()]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        self.runs = {}
        for raw in data.get("runs") or []:
            if isinstance(raw, dict) and raw.get("id"):
                run = CheckRun.from_dict(raw)
                self.runs[run.id] = run
        self.counter = int(data.get("counter") or len(self.runs))

    def next_id(self) -> str:
        with self.lock:
            self.counter += 1
            return f"r{self.counter}"

    def record(self, run: CheckRun) -> CheckRun:
        with self.lock:
            if not run.id:
                run.id = self.next_id()
            self.runs[run.id] = run
            while len(self.runs) > MAX_RUNS:
                self.runs.pop(next(iter(self.runs)))
            self.touch()
            return run

    def get(self, run_id: str) -> Optional[CheckRun]:
        return self.runs.get(str(run_id or "").strip())

    def require(self, run_id: str) -> CheckRun:
        run = self.get(run_id)
        if run is None:
            raise KeyError(f"no check run {run_id!r}")
        return run

    def list(self, *, purpose: Optional[str] = None, ref: Optional[str] = None) -> List[CheckRun]:
        runs = list(self.runs.values())
        if purpose:
            runs = [r for r in runs if r.purpose == purpose]
        if ref is not None:
            runs = [r for r in runs if r.ref == ref]
        return runs

    def latest(self) -> Optional[CheckRun]:
        return next(reversed(self.runs.values()), None) if self.runs else None


def checks_state(owner: Any) -> ChecksState:
    return state_for(owner, "checks")


# ------------------------------------------------------------------ helpers


def scrubbed_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = {}
    # Non-login bash still sources BASH_ENV. Loader hooks can execute before the
    # reviewed command, so neither inherited nor caller-supplied hooks survive.
    startup = {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"}
    def safe_key(key):
        return key not in startup and not key.startswith(("BASH_FUNC_", "LD_", "DYLD_", "GIT_CONFIG_"))
    for key, value in os.environ.items():
        if not safe_key(key):
            continue
        if key in _KEEP_ENV or key.startswith("LC_"):
            env[key] = value
        elif not _SECRET_ENV_RE.search(key) and not key.startswith(("MUCLI_", "OPENAI", "ANTHROPIC",
                                                                       "GEMINI", "GOOGLE", "OLLAMA",
                                                                       "AWS_", "AZURE_", "GH_", "GITHUB_")):
            env[key] = value
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["MUCLI_CHECK"] = "1"
    for key, value in (extra or {}).items():
        if safe_key(str(key)) and not _SECRET_ENV_RE.search(str(key)):
            env[str(key)] = str(value)
    return env


def hash_files(paths: Iterable[str], cwd: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in paths or []:
        path = str(raw or "").strip()
        if not path:
            continue
        full = path if os.path.isabs(path) else os.path.join(cwd, path)
        try:
            with open(full, "rb") as fh:
                out[path] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[path] = "missing"
    return out


def workspace_root(owner: Any) -> str:
    folder_context = getattr(owner, "folder_context", None)
    if folder_context is None:
        folder_context = getattr(getattr(owner, "session", None), "folder_context", None)
    for folder in getattr(folder_context, "folders", None) or []:
        if folder:
            return os.path.abspath(folder)
    return os.getcwd()


def _variables(owner: Any) -> Dict[str, Any]:
    for candidate in (owner, getattr(owner, "session", None)):
        variables = getattr(candidate, "variables", None)
        if isinstance(variables, dict):
            return variables
    return {}


def _session(owner: Any) -> Any:
    session = getattr(owner, "session", None)
    return session if session is not None else owner


def _markers(predicate: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if isinstance(predicate.get("markers"), list):
        out.extend(str(m) for m in predicate["markers"] if str(m))
    for sub in (predicate.get("all") or []) + (predicate.get("any") or []):
        if isinstance(sub, dict):
            out.extend(_markers(sub))
    return out


def make_excerpt(output: str, markers: Iterable[str] = (), limit: int = EXCERPT_BYTES) -> str:
    text = output or ""
    if len(text) <= limit:
        return text
    for marker in markers:
        pos = text.find(marker)
        if pos >= 0:
            start = max(0, pos - limit // 2)
            return ("…" if start else "") + text[start:start + limit - 1]
    return "…" + text[-(limit - 1):]


_PYTEST_SUMMARY_RE = re.compile(r"=+ (?P<body>[^=]*?\b(?:passed|failed|error|errors|no tests ran)\b[^=]*?) in [\d.]+s")
_PYTEST_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)


def parse_test_output(output: str) -> Optional[Dict[str, Any]]:
    """Counts and failing ids from a pytest run's summary line, if present."""
    match = None
    for match in _PYTEST_SUMMARY_RE.finditer(output or ""):
        pass
    if match is None:
        return None
    counts: Dict[str, Any] = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
    for num, word in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)", match.group("body")):
        key = "failed" if word.startswith("error") else word
        counts[key] = counts.get(key, 0) + int(num)
    counts["total"] = sum(int(counts.get(k, 0)) for k in ("passed", "failed", "skipped", "xfailed", "xpassed"))
    counts["failing"] = _PYTEST_FAILED_RE.findall(output or "")[:50]
    return counts


def evaluate(predicate: Dict[str, Any], exit_code: Optional[int], output: str, cwd: str,
             timed_out: bool = False) -> tuple:
    """Return (passed, detail, tests) for a predicate."""
    if not isinstance(predicate, dict) or not predicate:
        predicate = {"exit": 0}
    if "all" in predicate or "any" in predicate:
        subs = [p for p in (predicate.get("all") or predicate.get("any") or []) if isinstance(p, dict)]
        results = [evaluate(p, exit_code, output, cwd, timed_out) for p in subs]
        tests = next((r[2] for r in results if r[2]), None)
        if "all" in predicate:
            failed = [r[1] for r in results if not r[0]]
            return (not failed and bool(results)), "; ".join(failed) or "all conditions held", tests
        held = [r[1] for r in results if r[0]]
        return bool(held), ("; ".join(held) or "no condition held"), tests
    if timed_out:
        return False, "timed out", None
    tests = None
    checks: List[tuple] = []
    if "exit" in predicate:
        want = int(predicate["exit"])
        checks.append((exit_code == want, f"exit {exit_code} (wanted {want})"))
    if predicate.get("exit_nonzero"):
        checks.append((exit_code not in (0, None), f"exit {exit_code} (wanted non-zero)"))
    markers = [str(m) for m in (predicate.get("markers") or []) if str(m)]
    if markers:
        found = [m for m in markers if m in (output or "")]
        if predicate.get("absent"):
            checks.append((not found, f"markers present: {found}" if found else "markers absent"))
        else:
            missing = [m for m in markers if m not in found]
            checks.append((not missing, f"markers missing: {missing}" if missing else "markers present"))
    if predicate.get("junit"):
        report = os.path.join(cwd, str(predicate["junit"]))
        try:
            from mu.jobs.checks import parse_junit

            with open(report, "r", encoding="utf-8", errors="replace") as fh:
                tests = parse_junit(fh.read())
            ok = tests["total"] > 0 and tests["failed"] == 0
            checks.append((ok, f"junit {tests['passed']}/{tests['total']} passed, {tests['failed']} failed"))
        except Exception as exc:  # noqa: BLE001
            checks.append((False, f"junit report unreadable: {exc}"))
    if not checks:
        checks.append((exit_code == 0, f"exit {exit_code}"))
    if tests is None:
        tests = parse_test_output(output)
    failed = [d for ok, d in checks if not ok]
    return (not failed), ("; ".join(failed) if failed else "; ".join(d for _, d in checks)), tests


def describe_predicate(predicate: Dict[str, Any]) -> str:
    if not predicate:
        return "exit 0"
    if "all" in predicate:
        return " and ".join(describe_predicate(p) for p in predicate["all"])
    if "any" in predicate:
        return " or ".join(describe_predicate(p) for p in predicate["any"])
    parts = []
    if "exit" in predicate:
        parts.append(f"exit {predicate['exit']}")
    if predicate.get("exit_nonzero"):
        parts.append("non-zero exit")
    if predicate.get("markers"):
        word = "without" if predicate.get("absent") else "printing"
        parts.append(f"{word} {', '.join(repr(m) for m in predicate['markers'])}")
    if predicate.get("junit"):
        parts.append(f"JUnit {predicate['junit']} all passing")
    return " and ".join(parts) or "exit 0"


def _log_dir(owner: Any) -> str:
    from utils.config import HISTORY_DIR

    manager = getattr(_session(owner), "session_manager", None)
    name = str(getattr(manager, "current_session_name", "") or "") or "unsaved"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:128] or "unsaved"
    return os.path.join(os.path.expanduser(str(HISTORY_DIR)), "checks", name)


def _store_output(owner: Any, run: CheckRun, output: str) -> None:
    try:
        directory = _log_dir(owner)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{run.id}-{int(run.started_at)}.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(output)
        run.log_path = path
    except OSError:
        pass
    store = getattr(_session(owner), "result_store", None)
    if store is not None:
        try:
            key = f"check-{run.id}-{int(run.started_at * 1000)}"
            if store.put(key, "run_check", {"command": run.command, "purpose": run.purpose}, output):
                run.result_ref = key
        except Exception:  # noqa: BLE001 — the log file is the durable copy
            pass


def read_output(run: CheckRun) -> str:
    if run.log_path and os.path.isfile(run.log_path):
        with open(run.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return run.excerpt


def _redact(text: str) -> str:
    from mu.security.secret_paths import redact_secrets

    return redact_secrets(text)[0]


def _run_host(spec: CheckSpec, cwd: str, timeout: int) -> tuple:
    proc = subprocess.Popen(
        ["bash", "-c", spec.command], cwd=cwd, env=scrubbed_env(spec.env),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        out, _ = proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace")
    return (None if timed_out else proc.returncode), text, timed_out


def resolve_runner(spec: CheckSpec, owner: Any) -> str:
    from mu.tools.capabilities import normalize_session_type

    session_type = normalize_session_type(_variables(owner).get("session_type"))
    if session_type == "container":
        # The whole worker already runs inside the session container.
        return "container"
    if spec.runner == "docker":
        return "docker"
    return "host"


def preflight(spec: CheckSpec, owner: Any) -> str:
    """Raise ``CheckRefused`` when the command may not run; return the cwd."""
    variables = _variables(owner)
    command = str(spec.command or "").strip()
    if not command:
        raise CheckRefused("No command to run.", "invalid_args")
    if variables.get("plan_mode"):
        raise CheckRefused(
            "Plan mode is active — commands cannot run. Ask the user to /plan off.",
            "plan_mode_blocked",
        )
    from mu.agent.secret_guard import command_refusal

    reason = command_refusal(command, variables)
    if reason:
        raise CheckRefused(f"Secret-guard blocked the command: {reason}.", "secret_guard_blocked")
    root = workspace_root(owner)
    cwd = spec.cwd or root
    if not os.path.isabs(cwd):
        cwd = os.path.join(root, cwd)
    cwd = os.path.abspath(cwd)
    if not os.path.isdir(cwd):
        raise CheckRefused(f"Working directory does not exist: {cwd}", "invalid_args")
    return cwd


def run_check(spec: CheckSpec, owner: Any, *, record: bool = True) -> CheckRun:
    """Run ``spec`` under the harness controls and record a ``CheckRun``.

    ``owner`` is a session, a tool context, or anything with ``variables`` /
    ``folder_context`` / ``session_manager``. Raises ``CheckRefused``.
    """
    cwd = preflight(spec, owner)
    timeout = max(1, min(int(spec.timeout_s or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
    runner = resolve_runner(spec, owner)
    hashes = hash_files(spec.hash_paths, cwd)
    started = now()
    clock = time.monotonic()
    if runner == "docker":
        from .sandbox import run_in_docker

        exit_code, output, timed_out = run_in_docker(spec, cwd, timeout)
    else:
        exit_code, output, timed_out = _run_host(spec, cwd, timeout)
    output = _redact(output)
    passed, detail, tests = evaluate(spec.predicate, exit_code, output, cwd, timed_out)
    state = checks_state(owner)
    run = CheckRun(
        id=state.next_id() if record else "r0",
        purpose=spec.purpose if spec.purpose in PURPOSES else "verify",
        command=spec.command, cwd=cwd, exit_code=exit_code, passed=bool(passed),
        predicate=dict(spec.predicate or {"exit": 0}),
        excerpt=make_excerpt(output, _markers(spec.predicate or {})),
        started_at=started, duration_s=round(time.monotonic() - clock, 2),
        hashes=hashes, runner=runner, label=spec.label, ref=spec.ref,
        timed_out=timed_out, tests=tests, detail=detail,
    )
    _store_output(owner, run, output)
    if record:
        state.record(run)
    return run


# Commands the harness recognises as checks when the model runs them through ``bash``.
_CHECK_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|(])(?:python3? -m pytest|pytest|py\.test|npm (?:run )?test|yarn test|pnpm (?:run )?test|"
    r"npx (?:jest|vitest|tsc|eslint)|jest|vitest|cargo (?:test|check|clippy)|go (?:test|vet)|"
    r"ruff(?: check)?|mypy|tsc|eslint|flake8|pylint|make (?:test|check|lint)|gradle(?:w)? test|"
    r"mvn test|dotnet test|ctest|bundle exec rspec|rspec|phpunit|node --test)\b"
)


def looks_like_check(command: str, validation_commands: Iterable[str] = ()) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    for declared in validation_commands or []:
        declared = str(declared or "").strip()
        if declared and declared in text:
            return True
    return bool(_CHECK_COMMAND_RE.search(text))


def record_observed(owner: Any, *, command: str, exit_code: Optional[int], output: str,
                    purpose: str = "verify", cwd: str = "", duration_s: float = 0.0,
                    ref: str = "") -> CheckRun:
    """Record a command the model ran itself (``bash``) as an observed check."""
    output = str(output or "")
    passed, detail, tests = evaluate({"exit": 0}, exit_code, output, cwd or workspace_root(owner))
    state = checks_state(owner)
    run = CheckRun(
        id=state.next_id(), purpose=purpose if purpose in PURPOSES else "verify",
        command=command, cwd=cwd or workspace_root(owner), exit_code=exit_code,
        passed=bool(passed), predicate={"exit": 0}, excerpt=make_excerpt(output),
        started_at=now(), duration_s=round(float(duration_s or 0.0), 2), runner="host",
        tests=tests, source="observed", detail=detail, ref=ref,
    )
    _store_output(owner, run, output)
    return state.record(run)


__all__ = [
    "CheckRefused",
    "CheckRun",
    "CheckSpec",
    "ChecksState",
    "PURPOSES",
    "checks_state",
    "describe_predicate",
    "evaluate",
    "hash_files",
    "looks_like_check",
    "make_excerpt",
    "parse_test_output",
    "preflight",
    "read_output",
    "record_observed",
    "run_check",
    "scrubbed_env",
    "workspace_root",
]
