"""Default mode's turn receipt (modes v2 §4.1).

The harness records what a turn did — files changed, checks run after the last
edit and whether they passed, work items touched, open blockers — so "verify
with evidence" is checkable rather than prose.

* ``bash`` takes an optional ``purpose`` (verify | repro | explore). A run
  with purpose verify/repro, or one the harness recognises as a check
  (pytest, npm test, cargo test, ruff, mypy, tsc, … and the project's
  ``.mucli/jobs.toml`` check commands), is recorded as an observed
  ``CheckRun``.
* At turn end, when files changed and no passing check ran after the last
  edit, the model gets one more iteration with a short nudge
  (``verify_nudge``, on by default; never a block). The receipt says
  *unverified* if it still doesn't verify.
* The receipt is stored on the session, shown as one line in the terminal and
  published to the GUI / mobile (``turn_receipt`` event).
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

from .state import ModeState, register_mode_state, state_for

PURPOSES = ("verify", "repro", "explore")
MAX_RECEIPTS = 60
_EXIT_RE = re.compile(r"Exit code:\s*(-?\d+)\s*$", re.M)


@register_mode_state
class ReceiptState(ModeState):
    name = "receipts"

    def __init__(self) -> None:
        super().__init__()
        self.receipts: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"receipts": self.receipts[-MAX_RECEIPTS:]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        self.receipts = [r for r in (data or {}).get("receipts") or [] if isinstance(r, dict)][-MAX_RECEIPTS:]

    def add(self, receipt: Dict[str, Any]) -> None:
        with self.lock:
            self.receipts.append(receipt)
            self.receipts = self.receipts[-MAX_RECEIPTS:]
            self.touch()

    def latest(self) -> Optional[Dict[str, Any]]:
        return self.receipts[-1] if self.receipts else None


def receipt_state(owner: Any) -> ReceiptState:
    return state_for(owner, "receipts")


_VALIDATION_CACHE: Dict[str, tuple] = {}


def validation_commands(root: str) -> List[str]:
    """Check commands the project declares in ``.mucli/jobs.toml`` ([checks] suites)."""
    path = os.path.join(root or "", ".mucli", "jobs.toml")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _VALIDATION_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        try:
            import tomllib  # type: ignore
        except ImportError:  # pragma: no cover - Python 3.10
            import tomli as tomllib  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            data = tomllib.loads(fh.read())
        commands = [str(s.get("command") or s.get("cmd") or "").strip()
                    for s in (data.get("checks") or {}).get("suites") or [] if isinstance(s, dict)]
    except Exception:  # noqa: BLE001
        commands = []
    commands = [c for c in commands if c]
    _VALIDATION_CACHE[path] = (mtime, commands)
    return commands


def _result_text(result: Any) -> str:
    """The tool's output text from a raw string or a (JSON) envelope."""
    if isinstance(result, str) and result.lstrip().startswith("{"):
        import json

        try:
            result = json.loads(result)
        except ValueError:
            return result
    if isinstance(result, dict):
        return str(result.get("message") or result.get("output") or "")
    return str(result or "")


def record_bash(session: Any, args: Dict[str, Any], result: Any) -> Optional[Any]:
    """Record a bash run as an observed CheckRun when it is a check."""
    from .evidence import looks_like_check, record_observed, workspace_root

    command = str((args or {}).get("command") or "").strip()
    purpose = str((args or {}).get("purpose") or "").strip().lower()
    if not command or purpose == "explore":
        return None
    root = workspace_root(session)
    if purpose not in ("verify", "repro") and not looks_like_check(command, validation_commands(root)):
        return None
    text = _result_text(result)
    match = None
    for match in _EXIT_RE.finditer(text or ""):
        pass
    if match is None:
        return None  # timed out / failed to start: not evidence either way
    exit_code = int(match.group(1))
    return record_observed(session, command=command, exit_code=exit_code, output=text,
                           purpose=purpose or "verify", cwd=str((args or {}).get("cwd") or root))


def cite_run(result: Any, run: Any) -> Any:
    """Tell the model the check run id its bash call was recorded as, so it can
    cite it as evidence (work items, increments, done-when)."""
    word = "passed" if run.passed else "failed"
    note = (f"Recorded as check run {run.id} ({word}). Cite it as evidence: evidence=[\"{run.id}\"]."
            if run.passed else f"Recorded as check run {run.id} ({word}); a failing run is not evidence of done.")
    if isinstance(result, str):
        return f"{result.rstrip()}\n{note}"
    if isinstance(result, dict):
        return {**result, "check_run": {"id": run.id, "passed": bool(run.passed)}, "check_run_note": note}
    return result


def _turn_start(session: Any) -> float:
    return float(getattr(session, "_turn_started_at", 0.0) or 0.0)


def build_receipt(session: Any) -> Dict[str, Any]:
    from .blockers import blocker_state
    from .edits import edits_state
    from .evidence import checks_state
    from .work import work_state

    started = _turn_start(session)
    edits = edits_state(session).since(started) if started else []
    files = sorted({e["path"] for e in edits if e.get("path")})
    last_edit = max([float(e.get("at") or 0) for e in edits] or [0.0])
    runs = [r for r in checks_state(session).runs.values() if r.started_at >= started] if started else []
    after = [r for r in runs if r.started_at >= last_edit] if files else runs
    verified = any(r.passed for r in after)
    work = work_state(session)
    touched = sorted({e.ref for e in work.events if e.at >= started and e.ref}) if started else []
    if not files:
        status = "no_changes"
    elif verified:
        status = "verified"
    else:
        status = "unverified"
    root = None
    try:
        from .evidence import workspace_root

        root = workspace_root(session)
    except Exception:  # noqa: BLE001
        pass
    return {
        "turn": getattr(session, "_current_turn_start_index", None),
        "at": round(time.time(), 3),
        "status": status,
        "files": [os.path.relpath(f, root) if root and os.path.isabs(f) else f for f in files],
        "checks": [{"id": r.id, "passed": r.passed, "command": r.command[:200], "purpose": r.purpose,
                    "after_edit": r.started_at >= last_edit} for r in runs],
        "work_items": touched,
        "blockers": [b.id for b in blocker_state(session).open_blockers()],
        "nudged": bool(getattr(session, "_verify_nudged", False)),
    }


def receipt_line(receipt: Dict[str, Any]) -> str:
    files = receipt.get("files") or []
    if receipt.get("status") == "no_changes":
        checks = receipt.get("checks") or []
        if not checks:
            return ""
        passed = sum(1 for c in checks if c["passed"])
        return f"Receipt · no files changed · {passed} of {len(checks)} checks passed"
    head = f"{len(files)} file{'s' if len(files) != 1 else ''} changed"
    if receipt.get("status") == "verified":
        return f"Receipt · {head} · checks passed"
    return f"Receipt · {head} · unverified"


def verify_nudge(session: Any, active_mode: str) -> str:
    """One extra iteration's nudge when files changed without a passing check after the edit."""
    variables = getattr(session, "variables", None) or {}
    if active_mode != "default" or not variables.get("verify_nudge", True):
        return ""
    if getattr(session, "_verify_nudged", False):
        return ""
    receipt = build_receipt(session)
    if receipt["status"] != "unverified":
        return ""
    session._verify_nudged = True
    names = ", ".join(receipt["files"][:4]) + ("…" if len(receipt["files"]) > 4 else "")
    return (f"You changed {names} without a passing check after the edit. Run one (tests, a linter, a "
            "smoke command — bash with purpose='verify'), or say plainly why it can't be checked.")


def finalize(session: Any, active_mode: str) -> Optional[Dict[str, Any]]:
    """Store and surface the turn's receipt (default mode)."""
    if active_mode != "default" or not _turn_start(session):
        return None
    receipt = build_receipt(session)
    line = receipt_line(receipt)
    if not line:
        return None
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    if states is not None:
        states.get("receipts").add(receipt)
    ui = getattr(session, "ui", None)
    publish = getattr(ui, "publish_event", None) if ui is not None else None
    if callable(publish):
        try:
            publish({"kind": "turn_receipt", "receipt": receipt, "line": line})
        except Exception:  # noqa: BLE001
            pass
    elif ui is not None and hasattr(ui, "show_info"):
        try:
            ui.show_info(line)
        except Exception:  # noqa: BLE001
            pass
    return receipt


def capsule(session: Any) -> str:
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    state = states.peek("receipts") if states is not None else None
    latest = state.latest() if state is not None else None
    if not latest or latest.get("status") != "unverified":
        return ""
    return (f"Last turn changed {', '.join(latest.get('files', [])[:4])} and ended unverified; verify before "
            "building on it.")


def _install_hook() -> None:
    from mu.agent.hooks import HookSpec, default_registry

    def _hook(ctx):
        if (ctx.tool_name or "") != "bash" or ctx.session is None:
            return None
        if getattr(getattr(ctx.session, "session_manager", None), "mode_state", None) is None:
            return None
        try:
            run = record_bash(ctx.session, ctx.tool_args or {}, ctx.tool_result)
            if run is not None:
                ctx.tool_result = cite_run(ctx.tool_result, run)
        except Exception:  # noqa: BLE001 — evidence capture must never break bash
            pass
        return None

    default_registry.remove("mode_bash_checks")
    default_registry.add(HookSpec(name="mode_bash_checks", point="post_tool", priority=51, handler=_hook))


_install_hook()


__all__ = ["PURPOSES", "ReceiptState", "build_receipt", "capsule", "cite_run", "finalize", "receipt_line",
           "receipt_state", "record_bash", "validation_commands", "verify_nudge"]
