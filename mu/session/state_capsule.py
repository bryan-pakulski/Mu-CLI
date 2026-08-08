"""Deterministic runtime-state projection for long-horizon agent context.

The capsule is derived from structures MuCLI already owns: tool-result envelopes,
feature/task state, durable memory, scratchpad todos and the pinned session goal.
No model call is required to refresh it.

The existing conversation summary remains available as a small semantic residue
for genuinely unstructured information and overflow compaction. Periodic
``force_progress_checkpoint`` LLM summarization is disabled for sessions once
this projector is active; current state is refreshed deterministically instead.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


_MAX_FILES = 24
_MAX_FAILURES = 8
_MAX_VALIDATIONS = 8
_MAX_ACTIONS = 8
_MAX_MEMORY = 8
_MAX_TODOS = 8


def _empty_projection() -> Dict[str, Any]:
    return {
        "cursor": 0,
        "modified_files": [],
        "failures": {},
        "validations": [],
        "actions": [],
    }


def _append_unique_recent(values: List[str], value: str, limit: int) -> None:
    value = str(value or "").strip()
    if not value:
        return
    try:
        values.remove(value)
    except ValueError:
        pass
    values.append(value)
    if len(values) > limit:
        del values[: len(values) - limit]


def _bounded_append(values: List[Dict[str, Any]], value: Dict[str, Any], limit: int) -> None:
    values.append(value)
    if len(values) > limit:
        del values[: len(values) - limit]


def _as_envelope(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except (TypeError, ValueError):
                pass
    return {}


def _extract_modified_files(env: Dict[str, Any]) -> List[str]:
    values = env.get("modified_files") or []
    if not values and isinstance(env.get("data"), dict):
        values = env["data"].get("modified_files") or []
    if isinstance(values, str):
        values = [values]
    return [str(value) for value in values if str(value).strip()]


def _short(value: Any, limit: int = 180) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _target_hint(env: Dict[str, Any]) -> str:
    args = env.get("args")
    if isinstance(args, str):
        return _short(args, 100)
    if isinstance(args, dict):
        for key in ("filename", "file", "path", "command", "query"):
            if args.get(key):
                return _short(args[key], 100)
    data = env.get("data")
    if isinstance(data, dict):
        for key in ("filename", "file", "path"):
            if data.get(key):
                return _short(data[key], 100)
    return ""


def _is_validation(tool_name: str, env: Dict[str, Any]) -> bool:
    name = str(tool_name or "").lower()
    summary = str(env.get("summary") or "").lower()
    args = env.get("args")
    args_text = (
        args.lower()
        if isinstance(args, str)
        else json.dumps(args or {}, default=str).lower()
    )
    if any(token in name for token in ("test", "lint", "typecheck", "verify")):
        return True
    commandish = f"{args_text} {summary}"
    return any(
        token in commandish
        for token in (
            "pytest",
            "make test",
            "npm test",
            "pnpm test",
            "yarn test",
            "cargo test",
            "go test",
            "ruff ",
            "mypy",
            "tsc ",
        )
    )


def _process_tool_result(
    projection: Dict[str, Any],
    part: Dict[str, Any],
) -> None:
    env = _as_envelope(part.get("tool_result"))
    tool_name = str(
        env.get("tool_name") or part.get("tool_name") or "tool"
    ).strip()
    if not env:
        return

    ok = env.get("ok")
    error_code = str(env.get("error_code") or "").strip()
    summary = _short(
        env.get("summary")
        or env.get("message")
        or (env.get("data") if isinstance(env.get("data"), str) else ""),
        220,
    )
    target = _target_hint(env)
    cache_key = str(part.get("cache_key") or env.get("cache_key") or "").strip()

    for path in _extract_modified_files(env):
        _append_unique_recent(projection["modified_files"], path, _MAX_FILES)

    action = {
        "tool": tool_name,
        "ok": ok,
        "target": target,
        "summary": summary,
        "error_code": error_code,
        "cache_key": cache_key,
    }
    if _extract_modified_files(env) or ok is False or _is_validation(tool_name, env):
        _bounded_append(projection["actions"], action, _MAX_ACTIONS)

    failure_key = f"{tool_name}|{target or '*'}"
    if ok is False or error_code:
        projection["failures"][failure_key] = {
            "tool": tool_name,
            "target": target,
            "error_code": error_code or "tool_failed",
            "summary": summary,
            "cache_key": cache_key,
        }
        while len(projection["failures"]) > _MAX_FAILURES:
            first = next(iter(projection["failures"]))
            projection["failures"].pop(first, None)
    elif ok is True:
        projection["failures"].pop(failure_key, None)

    if _is_validation(tool_name, env):
        _bounded_append(
            projection["validations"],
            {
                "tool": tool_name,
                "ok": bool(ok),
                "target": target,
                "summary": summary,
                "error_code": error_code,
                "cache_key": cache_key,
            },
            _MAX_VALIDATIONS,
        )


def _refresh_projection(session: Any) -> Dict[str, Any]:
    sm = getattr(session, "session_manager", None)
    if sm is None:
        return _empty_projection()
    projection = getattr(sm, "_state_capsule_projection", None)
    if not isinstance(projection, dict):
        projection = _empty_projection()

    history = list(getattr(sm, "history", []) or [])
    cursor = int(projection.get("cursor", 0) or 0)
    if cursor < 0 or cursor > len(history):
        projection = _empty_projection()
        cursor = 0

    for message in history[cursor:]:
        for part in message.get("parts", []) or []:
            if part.get("type") == "tool_result":
                _process_tool_result(projection, part)

    projection["cursor"] = len(history)
    sm._state_capsule_projection = projection
    return projection


def _install_deterministic_checkpoint_policy(session: Any) -> None:
    """Replace periodic L2 checkpoint summarization on this SessionManager."""
    sm = getattr(session, "session_manager", None)
    if sm is None or getattr(sm, "_state_capsule_checkpoint_installed", False):
        return
    original = getattr(sm, "force_progress_checkpoint", None)
    if not callable(original):
        return
    sm._state_capsule_legacy_checkpoint = original

    def _deterministic_checkpoint(
        provider: Any = None,
        *,
        min_new_entries: int = 6,
    ) -> bool:
        sm._checkpoint_anchor = len(getattr(sm, "history", []) or [])
        sm._state_capsule_checkpoint_count = int(
            getattr(sm, "_state_capsule_checkpoint_count", 0) or 0
        ) + 1
        return False

    sm.force_progress_checkpoint = _deterministic_checkpoint
    sm._state_capsule_checkpoint_installed = True


def _memory_lines(session: Any) -> List[str]:
    memory = getattr(getattr(session, "session_manager", None), "task_memory", None)
    if memory is None:
        return []
    entries = [
        entry
        for entry in getattr(memory, "entries", []) or []
        if str(getattr(entry, "status", "")) in {"active", "done"}
        and str(getattr(entry, "kind", "")) in {"decision", "finding", "goal"}
    ]
    priority = {"decision": 0, "goal": 1, "finding": 2}
    entries.sort(
        key=lambda entry: (
            priority.get(str(getattr(entry, "kind", "")), 9),
            -float(getattr(entry, "updated_at", 0.0) or 0.0),
        )
    )
    return [
        f"- [{getattr(entry, 'kind', 'finding')}] "
        f"{_short(getattr(entry, 'content', ''), 320)}"
        for entry in entries[:_MAX_MEMORY]
    ]


def _todo_lines(session: Any) -> List[str]:
    scratch = getattr(getattr(session, "session_manager", None), "turn_scratchpad", None)
    if scratch is None:
        return []
    values = []
    for entry in getattr(scratch, "entries", []) or []:
        tags = {str(tag).lower() for tag in getattr(entry, "tags", []) or []}
        if "todo" not in tags:
            continue
        status = str(getattr(entry, "status", "active") or "active")
        values.append(f"- [{status}] {_short(getattr(entry, 'content', ''), 240)}")
        if len(values) >= _MAX_TODOS:
            break
    return values


def _feature_lines(session: Any) -> List[str]:
    sm = getattr(session, "session_manager", None)
    if sm is None:
        return []
    try:
        state = sm.get_feature_state()
    except Exception:
        state = getattr(sm, "feature_state", None)
    if not isinstance(state, dict) or not state:
        return []

    lines = []
    for label, key in (("feature", "feature_name"), ("status", "status"), ("next_phase", "next_phase")):
        value = state.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"- {label}: {_short(value, 220)}")
    blocker = state.get("blocker")
    if blocker:
        lines.append(f"- blocker: {_short(blocker, 320)}")

    plan = state.get("feature_plan")
    if isinstance(plan, dict):
        for key in ("current_task", "next_task", "active_task"):
            value = plan.get(key)
            if value:
                lines.append(f"- {key}: {_short(value, 300)}")
    return lines[:8]


def build_state_capsule(
    session: Any,
    *,
    max_chars: int = 12000,
    include_goal: bool = True,
) -> str:
    """Build the authoritative deterministic current-state capsule."""
    if session is None:
        return ""
    _install_deterministic_checkpoint_policy(session)
    variables = getattr(session, "variables", None) or {}
    if not str(variables.get("compact_focus", "") or "").strip():
        variables["compact_focus"] = (
            "Preserve only unstructured conversational facts, user constraints, "
            "rationale, and unresolved semantic context that cannot be recovered "
            "from the deterministic state capsule. Do not restate routine tool "
            "outcomes, modified-file lists, verification status, or structured "
            "task state already projected by the harness."
        )
    projection = _refresh_projection(session)

    sections: List[str] = []

    goal = str((getattr(session, "variables", None) or {}).get("session_goal", "") or "").strip()
    if goal and include_goal:
        sections.append("### Goal\n" + _short(goal, 1600))

    feature = _feature_lines(session)
    if feature:
        sections.append("### Structured task state\n" + "\n".join(feature))

    files = projection.get("modified_files") or []
    if files:
        sections.append("### Modified files\n" + "\n".join(f"- {path}" for path in files[-_MAX_FILES:]))

    failures = list((projection.get("failures") or {}).values())
    if failures:
        lines = []
        for item in failures[-_MAX_FAILURES:]:
            locator = f" target={item.get('target')}" if item.get("target") else ""
            cache = f" [cache:{item.get('cache_key')}]" if item.get("cache_key") else ""
            lines.append(
                f"- {item.get('tool')} error={item.get('error_code')}"
                f"{locator}: {item.get('summary') or 'failed'}{cache}"
            )
        sections.append("### Unresolved tool failures\n" + "\n".join(lines))

    validations = projection.get("validations") or []
    if validations:
        lines = []
        for item in validations[-_MAX_VALIDATIONS:]:
            verdict = "PASS" if item.get("ok") else "FAIL"
            cache = f" [cache:{item.get('cache_key')}]" if item.get("cache_key") else ""
            lines.append(
                f"- {verdict} {item.get('tool')}: "
                f"{item.get('summary') or item.get('target') or ''}{cache}"
            )
        sections.append("### Recent verification\n" + "\n".join(lines))

    memory = _memory_lines(session)
    if memory:
        sections.append("### Durable decisions and findings\n" + "\n".join(memory))

    todos = _todo_lines(session)
    if todos:
        sections.append("### Open work ledger\n" + "\n".join(todos))

    if not sections:
        return ""
    header = (
        "Deterministic state capsule — authoritative for structured current "
        "state. Derived from tool envelopes/stores; no LLM summarization used."
    )
    rendered = header + "\n\n" + "\n\n".join(sections)
    return rendered[: max(256, int(max_chars or 12000))]


__all__ = ["build_state_capsule"]
