"""Inspector router — exposes the TUI's workspace / memory / stats /
variables surface to the GUI.

Read-only endpoints work without a loaded session (returning empty
structures), so the inspector drawer can render skeletons before the
user picks a session. Mutating endpoints require ``require_session``.

Variables are validated via ``utils.config.validate_and_cast`` so the
type contract matches what the TUI enforces.
"""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from utils.config import (
    DEFAULT_VARIABLES,
    VARIABLE_SCHEMA,
    calculate_cost,
    validate_and_cast,
)

from ..deps import require_session

router = APIRouter()
_logger = logging.getLogger(__name__)


def _load_variable_help() -> Dict[str, str]:
    """Build `{key: help_text}` for every VARIABLE_SCHEMA entry by
    scraping the trailing comments in ``utils/config.py``.

    The schema is currently maintained with the help docstring as a
    trailing Python comment after each entry's closing brace::

        "yolo": {"type": bool, "default": False},  # YOLO mode (no approvals)

    We parse the file's AST to locate the ``VARIABLE_SCHEMA`` dict
    literal, then for each key look at the source line where its value
    ends and pluck the trailing ``# …`` comment. Continuation lines
    (subsequent lines beginning with ``#``) are concatenated, so
    multi-line docstrings work.

    Best-effort. Failures degrade to empty help.
    """
    src_path = Path(__file__).resolve().parents[3] / "utils" / "config.py"
    try:
        src = src_path.read_text(encoding="utf-8")
    except OSError as exc:
        _logger.warning("inspector: could not read %s: %s", src_path, exc)
        return {}
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        _logger.warning("inspector: could not parse %s: %s", src_path, exc)
        return {}

    schema_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "VARIABLE_SCHEMA":
                    schema_node = node
                    break
            if schema_node is not None:
                break
    if schema_node is None or not isinstance(schema_node.value, ast.Dict):
        return {}

    lines = src.splitlines()

    def _trailing_comment(line_idx: int) -> str:
        """Read the trailing comment from `line_idx` (0-based), plus any
        immediately-following lines that are pure comment continuations."""
        if line_idx < 0 or line_idx >= len(lines):
            return ""
        first = lines[line_idx]
        hash_idx = first.find("#")
        if hash_idx < 0:
            return ""
        parts = [first[hash_idx + 1:].strip()]
        nxt = line_idx + 1
        while nxt < len(lines):
            stripped = lines[nxt].strip()
            if stripped.startswith("#"):
                parts.append(stripped.lstrip("#").strip())
                nxt += 1
                continue
            break
        return " ".join(p for p in parts if p)

    def _inner_comment(start_idx: int, end_idx: int) -> str:
        """Pull the first contiguous block of `# …` lines found within
        the value-dict body (between its opening `{` line + 1 and the
        closing `}` line). Used for entries that document themselves
        inside the dict instead of trailing it."""
        parts: list[str] = []
        i = start_idx + 1
        # Skip leading non-comment lines (typed fields like '"type": int,').
        while i < end_idx:
            stripped = lines[i].strip()
            if stripped.startswith("#"):
                parts.append(stripped.lstrip("#").strip())
                j = i + 1
                while j < end_idx and lines[j].strip().startswith("#"):
                    parts.append(lines[j].strip().lstrip("#").strip())
                    j += 1
                break
            i += 1
        return " ".join(p for p in parts if p)

    out: Dict[str, str] = {}
    for key_node, val_node in zip(schema_node.value.keys, schema_node.value.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            continue
        end_lineno = getattr(val_node, "end_lineno", None)
        start_lineno = getattr(val_node, "lineno", None)
        if end_lineno is None or start_lineno is None:
            continue
        # Prefer trailing comment ("},  # foo") since it tends to be the
        # most concise summary. Fall back to inline doc inside the dict.
        comment = _trailing_comment(end_lineno - 1)
        if not comment:
            comment = _inner_comment(start_lineno - 1, end_lineno - 1)
        if comment:
            out[key_node.value] = comment
    return out


# Computed once at import; cheap and avoids re-parsing per request.
_VARIABLE_HELP: Dict[str, str] = _load_variable_help()


# Curated grouping for the GUI settings tab. Keys not listed here drop
# into "other" at the end. Order here is the order shown in the UI.
_VARIABLE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "behavior",
        (
            "agent_mode",
            "session_goal",
            "yolo",
            "strict_mode",
            "verbose",
            "show_thinking",
            "show_thinking_explicit",
            "max_iterations",
            "compact_history",
            "streaming_enabled",
            "reflective_retry_enabled",
        ),
    ),
    (
        "memory",
        (
            "memory_enabled",
            "memory_max_entries",
            "memory_summary_limit",
            "scratchpad_enabled",
            "scratchpad_max_entries",
        ),
    ),
    (
        "context budgets",
        (
            "context_token_limit",
            "context_trim_threshold",
            "response_token_reserve",
            "conversation_summary_char_limit",
            "active_goal_context_char_limit",
            "recent_tool_context_char_limit",
            "retrieval_context_char_limit",
            "retrieval_top_k",
            "workspace_context_max_chars",
            "workspace_context_files",
            "skills_max_chars",
            "skills_mode",
            "tool_context_window",
            "collation_enabled",
            "structured_tool_results",
        ),
    ),
    (
        "provider retry",
        (
            "provider_max_retries",
            "provider_retry_base_delay",
            "provider_retry_max_delay",
            "provider_retry_max_total_wait_seconds",
        ),
    ),
    (
        "ollama",
        (
            "ollama_host",
            "ollama_num_ctx",
            "ollama_num_predict",
            "ollama_temperature",
            "ollama_top_p",
            "ollama_top_k",
            "ollama_repeat_penalty",
            "ollama_seed",
            "ollama_mirostat",
        ),
    ),
    (
        "loop mode",
        (
            "loop_active",
            "loop_features",
            "loop_detection_enabled",
            "loop_detection_repeat_threshold",
        ),
    ),
)


# ----- workspace ----------------------------------------------------------


@router.get("/workspace")
async def get_workspace(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {"folders": [], "cwd": os.getcwd()}
    fc = session.folder_context
    return {
        "folders": list(getattr(fc, "folders", []) or []),
        "cwd": os.getcwd(),
    }


@router.post("/workspace")
async def add_workspace(
    request: Request,
    payload: Dict[str, Any],
    session=Depends(require_session),
) -> Dict[str, Any]:
    raw = str(payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    folder = os.path.expanduser(raw)
    if not os.path.isdir(folder):
        raise HTTPException(
            status_code=400,
            detail=f"not a directory: {folder!r}",
        )
    with request.app.state.session_lock_for():
        session.folder_context.add_folder(folder)
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {
        "ok": True,
        "added": folder,
        "folders": list(session.folder_context.folders),
    }


@router.delete("/workspace")
async def remove_workspace(
    request: Request,
    path: str,
    session=Depends(require_session),
) -> Dict[str, Any]:
    folder = os.path.expanduser(path.strip())
    with request.app.state.session_lock_for():
        try:
            session.folder_context.remove_folder(folder)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {
        "ok": True,
        "removed": folder,
        "folders": list(session.folder_context.folders),
    }


# ----- memory -------------------------------------------------------------


def _entry_dict(entry) -> Dict[str, Any]:
    return {
        "id": entry.id,
        "content": entry.content,
        "tags": list(entry.tags or []),
        "source": entry.source or "",
        "hits": entry.hits,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


@router.get("/memory")
async def list_memory(request: Request) -> Dict[str, Any]:
    """Return all task_memory entries plus a separate view of the
    per-turn scratchpad. Both are surfaced because the user can't
    inspect them from chat without typing /memory commands."""
    session = request.app.state.session_by_name()
    if session is None:
        return {"task_memory": [], "scratchpad": []}
    sm = session.session_manager
    task = [_entry_dict(e) for e in sm.task_memory.list_entries(limit=200)]
    scratch = [_entry_dict(e) for e in sm.turn_scratchpad.list_entries(limit=200)]
    return {"task_memory": task, "scratchpad": scratch}


@router.get("/memory/search")
async def search_memory(
    request: Request,
    q: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {"results": []}
    sm = session.session_manager
    results = sm.task_memory.search(q, limit=max(1, min(limit, 100)))
    return {"results": [_entry_dict(e) for e in results]}


@router.delete("/memory/{entry_id}")
async def delete_memory(
    entry_id: int,
    request: Request,
    session=Depends(require_session),
) -> Dict[str, Any]:
    sm = session.session_manager
    store = sm.task_memory
    before = len(store.entries)
    store.entries = [e for e in store.entries if e.id != int(entry_id)]
    if len(store.entries) == before:
        raise HTTPException(status_code=404, detail=f"memory #{entry_id} not found")
    try:
        sm.save_history(session.folder_context)
    except Exception:
        pass
    return {"ok": True, "removed": entry_id}


@router.post("/memory/clear")
async def clear_memory(
    request: Request,
    session=Depends(require_session),
) -> Dict[str, Any]:
    sm = session.session_manager
    sm.task_memory.clear()
    try:
        sm.save_history(session.folder_context)
    except Exception:
        pass
    return {"ok": True}


# ----- stats --------------------------------------------------------------


@router.get("/stats")
async def get_stats(request: Request) -> Dict[str, Any]:
    session = request.app.state.session_by_name()
    if session is None:
        return {"active": False}
    sm = session.session_manager
    tokens = dict(sm.token_counts)
    model = session.provider.model_name if session.provider else ""
    cost = 0.0
    if model:
        try:
            cost = calculate_cost(
                model,
                int(tokens.get("input", 0) or 0),
                int(tokens.get("output", 0) or 0),
            )
        except Exception:
            cost = 0.0
    return {
        "active": True,
        "name": sm.current_session_name,
        "provider": session.provider.name if session.provider else "",
        "model": model,
        "tokens": tokens,
        "history_length": len(sm.history),
        "estimated_cost_usd": cost,
        "task_memory_size": len(sm.task_memory.entries),
        "scratchpad_size": len(sm.turn_scratchpad.entries),
        "agent_mode": session.variables.get("agent_mode", "default"),
    }


# ----- variables ----------------------------------------------------------


def _schema_meta(key: str) -> Dict[str, Any]:
    """Schema metadata for a single variable: type name + default."""
    info = VARIABLE_SCHEMA.get(key, {})
    target_type = info.get("type")
    if target_type is bool:
        type_name = "bool"
    elif target_type is int:
        type_name = "int"
    elif target_type is float:
        type_name = "float"
    elif target_type is str:
        type_name = "str"
    else:
        type_name = "any"
    return {
        "type": type_name,
        "default": info.get("default"),
    }


def _build_variable_entry(key: str, value: Any) -> Dict[str, Any]:
    meta = _schema_meta(key)
    return {
        "key": key,
        "value": value,
        "type": meta["type"],
        "default": meta["default"],
        "is_default": value == meta["default"],
        "help": _VARIABLE_HELP.get(key, ""),
    }


@router.get("/variables")
async def list_variables(request: Request) -> Dict[str, Any]:
    """Return session variables grouped by category, in display order.

    Anything in VARIABLE_SCHEMA but not in the curated grouping falls
    into a trailing "other" group, so a new schema key is never silently
    dropped from the UI.
    """
    session = request.app.state.session_by_name()
    variables: Dict[str, Any] = (
        dict(session.variables) if session is not None else dict(DEFAULT_VARIABLES)
    )

    groups: list[Dict[str, Any]] = []
    placed: set[str] = set()

    for group_name, keys in _VARIABLE_GROUPS:
        entries: list[Dict[str, Any]] = []
        for key in keys:
            if key not in variables:
                continue
            entries.append(_build_variable_entry(key, variables[key]))
            placed.add(key)
        if entries:
            groups.append({"name": group_name, "variables": entries})

    leftover = sorted(k for k in variables if k not in placed)
    if leftover:
        groups.append(
            {
                "name": "other",
                "variables": [_build_variable_entry(k, variables[k]) for k in leftover],
            }
        )

    return {"groups": groups}


@router.post("/variables/{key}")
async def set_variable(
    key: str,
    request: Request,
    payload: Dict[str, Any],
    session=Depends(require_session),
) -> Dict[str, Any]:
    if key not in VARIABLE_SCHEMA:
        raise HTTPException(status_code=404, detail=f"unknown variable: {key}")
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="value is required")
    try:
        casted = validate_and_cast(key, payload["value"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with request.app.state.session_lock_for():
        session.variables[key] = casted
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {"ok": True, "key": key, "value": casted}


@router.delete("/variables/{key}")
async def unset_variable(
    key: str,
    request: Request,
    session=Depends(require_session),
) -> Dict[str, Any]:
    if key not in VARIABLE_SCHEMA:
        raise HTTPException(status_code=404, detail=f"unknown variable: {key}")
    default = VARIABLE_SCHEMA[key].get("default")
    with request.app.state.session_lock_for():
        session.variables[key] = default
        try:
            session.session_manager.save_history(session.folder_context)
        except Exception:
            pass
    return {"ok": True, "key": key, "value": default}
