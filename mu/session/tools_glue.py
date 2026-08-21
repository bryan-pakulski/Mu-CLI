"""Tool-execution glue between the session loop and the dispatcher.

Three functions, all taking the live `session` as their first argument
so they can read/mutate per-turn state (hook abort flag, feature-state
syncs, the `_loop_blocker_raised` watchdog signal, etc.):

  * `execute_tool_with_memory(session, name, args)` — fires the
    `pre_tool` / `post_tool` hooks around `execute_tool`, honors
    short-circuit (plan-mode, secret-guard, custom hooks) and abort
    return values, and runs the feature-mode "no writes outside docs"
    check.

  * `build_structured_tool_result(session, name, args, raw_result)` —
    wraps a raw tool result string in the structured envelope the
    history stores (summary, args, raw, error_code, modified_files,
    telemetry). Per-tool data-extraction branches handle the cases
    where the model wants typed access (read_file → char_count, etc.).

  * `sync_feature_state_for_tool(session, name, args, raw, structured)`
    — when the just-executed tool was a feature-mode mutator or
    `raise_blocker`, write its result back into the session's feature
    state so the next turn sees the updated plan / blocker.

Tests covering these paths live in `tests/test_mu_agent_session_integration.py`
(pre/post_tool hooks, plan-mode block, abort flag), `tests/test_session.py`
(structured-result shape), and `tests/test_loop_blocker_halts_watchdog.py`
(the raise_blocker → `_loop_blocker_raised` interlock).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, Optional


# ---------------------------------------------------------------- extension tool dispatch

# Pending extension tool calls: call_id → {result, error, done}
# Agent thread publishes SSE event, waits on threading.Event for result.
# Extension plugin POSTs result to /api/extensions/{ext_id}/tool_result.
_EXTENSION_PENDING: Dict[str, Dict[str, Any]] = {}
_EXTENSION_LOCK = threading.Lock()
_EXTENSION_TTL_SECONDS = 90.0
_EXTENSION_PATH_KEYS = frozenset({"file_path", "filename", "path"})


def _extension_is_fresh(data: dict) -> bool:
    last_seen = data.get("last_seen")
    if not last_seen:
        return True
    try:
        return time.time() - float(last_seen) <= _EXTENSION_TTL_SECONDS
    except (TypeError, ValueError):
        return False


def extension_system_prompts(session: Any) -> list[str]:
    """Return prompt blocks belonging to live extension clients only."""

    prompts = []
    for _extension_id, data in sorted(
        (getattr(session, "extensions", {}) or {}).items()
    ):
        prompt = str(data.get("system_prompt") or "").strip()
        if prompt and _extension_is_fresh(data):
            prompts.append(prompt)
    return prompts


def extension_tool_definitions(session: Any) -> list:
    """Return validated provider tool definitions from live extensions.

    Registration data is untrusted input from an HTTP client.  The router
    validates it, and this seam validates again before it reaches a provider.
    Built-in names always win and duplicate extension names are ignored.
    """

    from providers.base import ToolDefinition
    from mu.tools.descriptors import TOOLS

    claimed = {tool.name for tool in TOOLS}
    definitions = []
    extensions = getattr(session, "extensions", {}) or {}
    for _extension_id, data in sorted(extensions.items()):
        if not _extension_is_fresh(data):
            continue
        prefix = str(data.get("tool_prefix") or "")
        for raw in data.get("tools", []) or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            parameters = raw.get("parameters") or {}
            execution_kind = str(raw.get("execution_kind") or "read")
            if (
                not name
                or name in claimed
                or (prefix and not name.startswith(prefix))
                or not isinstance(parameters, dict)
                or execution_kind not in {"read", "mutate"}
            ):
                continue
            if (
                execution_kind == "mutate"
                and bool(getattr(session, "variables", {}).get("plan_mode"))
            ):
                continue
            claimed.add(name)
            definitions.append(
                ToolDefinition(
                    name=name,
                    description=str(raw.get("description") or "")[:4000],
                    parameters=parameters,
                    # Editor-side mutation tools perform their own preview and
                    # approval.  A second server prompt would deadlock the call.
                    requires_approval=False,
                )
            )
    return definitions


def _extension_tool_is_mutating(data: dict, tool_name: str) -> bool:
    for tool in data.get("tools", []) or []:
        if isinstance(tool, dict) and tool.get("name") == tool_name:
            return str(tool.get("execution_kind") or "read") == "mutate"
    return False


def _extension_secret_reason(session: Any, value: Any, depth: int = 0) -> str:
    """Return a reason when extension arguments/results reference a denied path."""

    if depth > 6:
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _EXTENSION_PATH_KEYS and isinstance(item, str) and item:
                try:
                    from mu.security.secret_paths import is_denied_path

                    denied, reason = is_denied_path(
                        item, getattr(session, "variables", {}) or {}
                    )
                    if denied:
                        return str(reason or "denied secret path")
                except Exception:
                    pass
            nested = _extension_secret_reason(session, item, depth + 1)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value[:256]:
            nested = _extension_secret_reason(session, item, depth + 1)
            if nested:
                return nested
    return ""


def _serialize_extension_result(result: Any) -> str:
    if isinstance(result, str):
        output = result
    else:
        try:
            output = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            output = str(result)
    try:
        from mu.tools._scrub import scrub_and_annotate

        return scrub_and_annotate(output)
    except Exception:
        return output


def _find_extension_for_tool(session: Any, tool_name: str) -> Optional[tuple[str, dict]]:
    """Return the extension that explicitly registered ``tool_name``.

    Prefix-only interception allowed a client to hijack an unadvertised tool.
    Exact membership keeps dispatch aligned with the provider schema.
    """

    extensions = getattr(session, "extensions", {})
    for ext_id, data in extensions.items():
        prefix = data.get("tool_prefix", "")
        names = {
            str(tool.get("name") or "")
            for tool in (data.get("tools", []) or [])
            if isinstance(tool, dict)
        }
        if tool_name in names and (not prefix or tool_name.startswith(prefix)):
            return ext_id, data
    return None


def _dispatch_extension_tool(
    session: Any,
    extension_id: str,
    extension_data: dict,
    tool_name: str,
    tool_args: dict,
) -> str:
    """Publish an extension_tool_call SSE event and wait for the plugin to respond.

    The agent thread calls this when a registered extension's tool_prefix
    matches the invoked tool name. We publish the event via the session's
    EventBus (thread-safe), then block on a threading.Event until the
    extension plugin POSTs the result to /api/extensions/{ext_id}/tool_result.

    Returns the tool result string, or an error message on timeout.
    """
    session_name = ""
    try:
        session_name = session.session_manager.current_session_name
    except Exception:
        pass

    ui = getattr(session, "ui", None)
    bus = getattr(ui, "_bus", None) if ui else None
    if bus is None:
        return f"Error: extension {extension_id} is not connected to an event bus"
    if not _extension_is_fresh(extension_data):
        return f"Error: extension {extension_id} is no longer connected"
    secret_reason = _extension_secret_reason(session, tool_args)
    if secret_reason:
        return f"Error: extension tool {tool_name} blocked: {secret_reason}"

    call_id = str(uuid.uuid4())
    done = threading.Event()
    client_id = str(extension_data.get("client_id") or "")
    with _EXTENSION_LOCK:
        _EXTENSION_PENDING[call_id] = {
            "result": None,
            "error": None,
            "done": done,
            "extension_id": extension_id,
            "client_id": client_id,
            "session_name": session_name,
        }

    bus.publish_threadsafe({
        "kind": "extension_tool_call",
        "extension_id": extension_id,
        "client_id": client_id,
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "call_id": call_id,
        "session_name": session_name,
    })

    timeout = 300.0
    try:
        timeout = float(
            getattr(session, "variables", {}).get(
                "extension_tool_timeout_seconds", timeout
            )
        )
    except (TypeError, ValueError):
        timeout = 300.0
    timeout = max(5.0, min(timeout, 600.0))

    deadline = time.monotonic() + timeout
    resolved = False
    disconnected = False
    while time.monotonic() < deadline:
        if done.wait(timeout=min(5.0, max(0.0, deadline - time.monotonic()))):
            resolved = True
            break
        if not _extension_is_fresh(extension_data):
            disconnected = True
            break

    if resolved:
        with _EXTENSION_LOCK:
            entry = _EXTENSION_PENDING.pop(call_id, {})
        error = entry.get("error")
        if error:
            return f"Error: {error}"
        result = entry.get("result")
        if result is None:
            return "Error: extension returned no result"
        secret_reason = _extension_secret_reason(session, result)
        if secret_reason:
            return f"Error: extension tool {tool_name} blocked: {secret_reason}"
        return _serialize_extension_result(result)

    with _EXTENSION_LOCK:
        _EXTENSION_PENDING.pop(call_id, None)
    if disconnected:
        return f"Error: extension tool {tool_name} disconnected while the call was pending"
    return (
        f"Error: extension tool {tool_name} timed out after {timeout:g}s — "
        "the editor did not respond"
    )


def resolve_extension_tool_result(
    call_id: str,
    result: Any = None,
    error: str = "",
    *,
    extension_id: str = "",
    client_id: str = "",
    session_name: str = "",
) -> bool:
    """Resolve a pending extension tool call.

    Called by the /api/extensions/{ext_id}/tool_result endpoint.
    Returns True if the call_id was found and resolved, False if unknown.
    """
    with _EXTENSION_LOCK:
        entry = _EXTENSION_PENDING.get(call_id)
        if entry is None:
            return False
        if extension_id and entry.get("extension_id") != extension_id:
            return False
        if client_id and entry.get("client_id") != client_id:
            return False
        if session_name and entry.get("session_name") != session_name:
            return False
        entry["result"] = result
        entry["error"] = error or ""
        done: Optional[threading.Event] = entry.get("done")

    if done is not None:
        done.set()
    return True


def cancel_extension_calls(
    extension_id: str,
    session_name: str,
    *,
    client_id: Optional[str] = None,
    reason: str = "Editor extension disconnected",
) -> int:
    """Wake calls owned by a disconnected/replaced editor client."""

    cancelled = 0
    with _EXTENSION_LOCK:
        entries = list(_EXTENSION_PENDING.values())
        for entry in entries:
            if entry.get("extension_id") != extension_id:
                continue
            if session_name and entry.get("session_name") != session_name:
                continue
            if client_id and entry.get("client_id") != client_id:
                continue
            entry["error"] = reason
            done = entry.get("done")
            if done is not None:
                done.set()
            cancelled += 1
    return cancelled


# ---------------------------------------------------------------- hook-fire dispatch


def execute_tool_with_memory(
    session: Any,
    tool_name: str,
    tool_args: dict,
    *,
    invocation_source: str = "session",
) -> Any:
    """Fire pre_tool/post_tool hooks around the dispatcher.

    Returns whatever the tool produced (string or envelope dict),
    unless a `pre_tool` hook short-circuited (in which case the hook's
    payload is returned) or fired abort (synthetic
    `error_code=hook_aborted` envelope returned + the iteration loop
    sees `session._hook_abort_requested == True` next time around).
    """
    # Local imports to dodge cold-import overhead — these modules are
    # not always loaded when tools_glue itself is imported.
    from mu.tools._dispatcher import execute_tool
    from mu.session.helpers import _hook_abort_envelope
    from mu.agent.hooks import HookContext, default_registry

    pre_ctx = HookContext(
        point="pre_tool",
        session=session,
        variables=session.variables,
        tool_name=tool_name,
        tool_args=tool_args,
    )
    _, short, abort = default_registry.fire_with_signals("pre_tool", pre_ctx)
    if short is not None:
        return short.payload
    if abort is not None:
        session._record_hook_abort("pre_tool", abort)
        return _hook_abort_envelope(tool_name, session._hook_abort_reason)

    feature_violation = session._feature_doc_tool_violation(tool_name, tool_args)
    if feature_violation:
        return f"Error: {feature_violation}"

    # --- Pre-write snapshot for workspace diff tracking ---
    # Write tools (write_file, apply_diff, search_and_replace_file) modify
    # files.  We snapshot the file's CURRENT content BEFORE the tool runs
    # so that folder_context.get_context_diff_xml() can later produce a
    # real diff (original vs modified).  Without this, lazy-loading would
    # read the already-modified content as the "original", producing no diff.
    if tool_name in {"write_file", "apply_diff", "search_and_replace_file"}:
        _filename = tool_args.get("filename", "")
        if _filename and session.folder_context:
            try:
                import os as _os
                _full = _os.path.abspath(_filename)
                _fc = session.folder_context
                # Only snapshot if not already tracked (first write this turn)
                if _full not in _fc.initial_snapshots and _fc._is_text_file(_full):
                    try:
                        _content = _fc._load_file_content(_full)
                    except Exception:
                        _content = None
                    _fc.initial_snapshots[_full] = _content
            except Exception:
                pass
    # --- End pre-write snapshot ---

    # --- Extension tool interception ---
    # When a registered extension's tool_prefix matches the invoked tool
    # name, dispatch via SSE to the extension plugin instead of local execute.
    extension = _find_extension_for_tool(session, tool_name)
    if extension is not None:
        ext_id, ext_data = extension
        if bool(session.variables.get("plan_mode")) and _extension_tool_is_mutating(
            ext_data, tool_name
        ):
            result = (
                f"Error: plan mode is active; extension mutation tool "
                f"{tool_name} was blocked"
            )
        else:
            result = _dispatch_extension_tool(
                session, ext_id, ext_data, tool_name, tool_args
            )
    else:
        # Memory and scratchpad tools used to short-circuit here; they now
        # route through the normal dispatcher to the `@tool`-registered
        # handlers in `mu/tools/memory/handlers.py`, which resolve the
        # stores from `context.session`.
        result = execute_tool(
            tool_name,
            tool_args,
            session.folder_context,
            session.ui,
            session.variables,
            invocation_source=invocation_source,
            session=session,
        )

    post_ctx = HookContext(
        point="post_tool",
        session=session,
        variables=session.variables,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=result,
        metadata=pre_ctx.metadata,
    )
    _, _, abort = default_registry.fire_with_signals("post_tool", post_ctx)
    if abort is not None:
        session._record_hook_abort("post_tool", abort)
    return result


# ---------------------------------------------------------------- structured result


_FEATURE_MODE_TOOL_NAMES = frozenset(
    {
        "create_feature",
        "create_phases",
        "create_task",
        "get_execution_state",
        "block_task",
        "resume_task",
        "review_completed_tasks",
        "review_all_completed_tasks",
        "propose_task_diff",
        "decide_task_diff",
        "archive_task",
        "create_feature_task",
        "update_feature_task",
        "approve_feature_task",
        "get_current_task",
        "get_tasks",
        "update_task_status",
        "raise_blocker",
    }
)


_MEMORY_TOOL_NAMES = frozenset(
    {
        "save_memory",
        "search_memory",
        "list_memory",
        "save_scratchpad",
        "search_scratchpad",
        "list_scratchpad",
        "clear_scratchpad",
        "flush",
    }
)


def build_structured_tool_result(
    session: Any,
    tool_name: str,
    tool_args: dict,
    raw_result: Any,
    *,
    execution_source: str = "session",
    cache_key: Optional[str] = None,
) -> dict:
    """Wrap a raw tool result in the structured envelope the history
    stores (summary, args, raw, error_code, modified_files, telemetry).

    Per-tool branches add typed `data` fields when callers want
    structured access (`read_file` → `char_count`, `list_dir` → parsed
    tree, etc.). For un-recognized tools, `data` is left empty and the
    raw text-preview lives in `summary`.

    Spec #1/#2/#10 — budget-thresholded observation: when the raw result
    exceeds its inline token budget, the full ``raw`` is dropped from the
    in-context envelope and replaced by a compact observation (excerpt,
    diagnostics, counts) plus ``stored_ref`` pointing at the durable
    ResultStore. Small results stay verbatim. ``cache_key`` (the durable
    store key) must be supplied so the observation can embed the
    reference; when it is None the observation degrades to keeping the
    raw inline (no store backing)."""
    from mu.tools._envelope import infer_tool_error_code
    from mu.session.helpers import _shorten_tool_args
    from mu.session.messages import clip_preview

    envelope, unwrapped_raw = session._unwrap_tool_envelope(raw_result)
    raw_text = str(unwrapped_raw)
    error_code = (
        envelope.get("error_code")
        if isinstance(envelope, dict)
        else infer_tool_error_code(tool_name, raw_text)
    )
    is_error = error_code is not None
    structured = {
        "tool_name": tool_name,
        "ok": (
            bool(envelope.get("ok"))
            if isinstance(envelope, dict)
            else error_code is None
        ),
        "summary": clip_preview(raw_text, 220),
        "args": _shorten_tool_args(tool_args),
        "raw": raw_text,
        "error_code": error_code,
        "error": (
            None
            if error_code is None
            else {
                "code": error_code,
                "message": clip_preview(raw_text, 220),
            }
        ),
        "data": {},
        "modified_files": [],
        "artifacts": [],
        "telemetry": {
            "execution_source": execution_source,
            "delivery_mode": "structured",
            "raw_char_count": len(raw_text),
            "raw_line_count": len(raw_text.splitlines()),
        },
    }
    if isinstance(envelope, dict):
        structured["telemetry"]["tool_envelope"] = envelope
        envelope_artifacts = envelope.get("artifacts")
        if isinstance(envelope_artifacts, list):
            structured["artifacts"] = [
                dict(item) for item in envelope_artifacts if isinstance(item, dict)
            ]
        envelope_data = envelope.get("data")
        if isinstance(envelope_data, dict):
            structured["data"] = dict(envelope_data)

    if tool_name == "read_file":
        structured["data"] = {
            "filename": tool_args.get("filename", ""),
            "char_count": len(raw_text),
            "line_count": len(raw_text.splitlines()),
            "preview": clip_preview(raw_text, 240),
        }
    elif tool_name == "get_chunk":
        structured["data"] = {
            "file": tool_args.get("file", ""),
            "start_line": tool_args.get("start_line"),
            "end_line": tool_args.get("end_line"),
            "line_count": len(raw_text.splitlines()),
            "preview": clip_preview(raw_text, 240),
        }
    elif tool_name == "search_for_string":
        structured["data"] = {
            "query": tool_args.get("string", ""),
            **session._parse_search_results(raw_text),
        }
    elif tool_name == "list_dir":
        structured["data"] = session._parse_list_dir(
            raw_text, tool_args.get("path", "")
        )
    elif tool_name == "get_workspace_details":
        structured["data"] = session._parse_workspace_details(raw_text)
    elif tool_name in {"write_file", "apply_diff", "search_and_replace_file"}:
        filename = tool_args.get("filename", "")
        structured["data"] = {
            "filename": filename,
            "changed_file": filename,
        }
        if filename:
            structured["modified_files"] = [filename]
    elif tool_name in {"upload_artifact", "list_artifacts"}:
        if isinstance(envelope, dict):
            descriptor = envelope.get("artifact")
            if isinstance(descriptor, dict):
                structured["data"]["artifact"] = dict(descriptor)
    elif tool_name in _FEATURE_MODE_TOOL_NAMES:
        structured["data"] = session._parse_json_result(raw_text)
    elif tool_name in _MEMORY_TOOL_NAMES:
        structured["data"] = {"preview": clip_preview(raw_text, 220)}

    # ---- Budget-thresholded observation (spec #1/#2/#10) -----------------
    _apply_observation_transform(session, structured, raw_text, cache_key, is_error)
    return structured


def _apply_observation_transform(
    session: Any,
    structured: dict,
    raw_text: str,
    cache_key: Optional[str],
    is_error: bool,
) -> None:
    """Drop the full ``raw`` from the in-context envelope when it exceeds the
    inline token budget, replacing it with a compact observation + stored_ref.
    Small results stay verbatim. Best-effort: any failure leaves ``raw`` in
    place (the safe, pre-change behaviour)."""
    try:
        from mu.tools._observe import build_observation, resolve_inline_budget, RETRIEVABLE_VIA
        from utils.token_estimator import estimate_tokens

        variables = getattr(session, "variables", {}) or {}
        tool_name = structured["tool_name"]
        raw_tokens = int(estimate_tokens(raw_text) or 0)
        budget = resolve_inline_budget(tool_name, is_error, variables)
        structured["telemetry"]["raw_token_count"] = raw_tokens
        structured["telemetry"]["inline_budget"] = budget
        if raw_tokens <= budget:
            structured["data"]["omitted"] = False
            structured["telemetry"]["injected_token_count"] = raw_tokens
            return
        if not cache_key:
            # No store backing → keep raw inline (can't offer a stored_ref).
            structured["data"]["omitted"] = False
            structured["telemetry"]["injected_token_count"] = raw_tokens
            return
        obs, note = build_observation(
            tool_name, None, raw_text, structured["data"],
            budget_tokens=budget, is_error=is_error,
        )
        structured["data"] = obs
        structured["data"]["stored_ref"] = cache_key
        structured["data"]["retrievable_via"] = RETRIEVABLE_VIA
        structured["data"]["omission_note"] = note
        structured["raw"] = None  # full raw NOT in context (spec #1/#11)
        structured["telemetry"]["delivery_mode"] = "observed"
        # Injected token estimate: the observation dict (no raw) + summary.
        injected = int(estimate_tokens(str(structured["data"])) or 0) + int(
            estimate_tokens(structured.get("summary") or "") or 0
        )
        structured["telemetry"]["injected_token_count"] = injected
        structured["telemetry"]["compression_ratio"] = round(
            (raw_tokens - injected) / max(1, raw_tokens), 3
        )
    except Exception:  # noqa: BLE001 — never break the loop over a budget bug
        structured.setdefault("data", {})["omitted"] = False


# ---------------------------------------------------------------- feature-state sync


def sync_feature_state_for_tool(
    session: Any,
    tool_name: str,
    tool_args: dict,
    raw_result: Any,
    structured_result: Any,
) -> None:
    """When the just-executed tool was a feature-mode mutator or
    `raise_blocker`, write its result back into the session's feature
    state so the next turn sees the updated plan / blocker.

    Mutates `session._loop_blocker_raised` when `raise_blocker` fires
    so the loop-mode watchdog knows the pause was intentional and
    skips its "continue!" prod that would otherwise burn iterations
    re-raising the same blocker."""
    if tool_name in {
        "create_feature",
        "create_phases",
        "create_task",
        "get_execution_state",
        "block_task",
        "resume_task",
        "review_completed_tasks",
        "review_all_completed_tasks",
        "propose_task_diff",
        "decide_task_diff",
        "archive_task",
        "create_feature_task",
        "get_tasks",
        "get_current_task",
        "approve_feature_task",
        "update_feature_task",
        "update_task_status",
    }:
        data = {}
        if isinstance(structured_result, dict):
            data = structured_result.get("data", {}) or {}
            if isinstance(data.get("plan"), dict):
                data = data["plan"]
        if not isinstance(data, dict) or "feature_id" not in data:
            data = session._parse_json_result(raw_result)
            if isinstance(data.get("plan"), dict):
                data = data["plan"]
        if isinstance(data, dict) and data.get("feature_id"):
            is_plan_summary = any(
                key in data
                for key in (
                    "metadata_path",
                    "directory",
                    "review_status",
                    "phases",
                    "tasks",
                    "next_task",
                    "next_phase",
                )
            )
            if is_plan_summary:
                session._set_feature_state(feature_plan=data)
            elif tool_name in {"get_current_task", "get_tasks"}:
                metadata_path = str(
                    (session.session_manager.get_feature_state() or {}).get(
                        "metadata_path", ""
                    )
                    or ""
                ).strip()
                if metadata_path:
                    session._refresh_feature_state(metadata_path)
        return

    if tool_name == "raise_blocker":
        data = {}
        if isinstance(structured_result, dict):
            data = structured_result.get("data", {}) or {}
        if not isinstance(data, dict) or not data.get("kind"):
            data = session._parse_json_result(raw_result)
        if isinstance(data, dict):
            session._set_feature_state(status="awaiting_input", blocker=data)
        # Signal the loop-mode watchdog that this pause is intentional
        # — without this it would re-prompt the model with LOOP WATCHDOG
        # every iteration, forcing repeated re-raises until budget is
        # exhausted.
        session._loop_blocker_raised = True
        return


__all__ = [
    "execute_tool_with_memory",
    "build_structured_tool_result",
    "sync_feature_state_for_tool",
    "extension_tool_definitions",
    "extension_system_prompts",
    "resolve_extension_tool_result",
    "cancel_extension_calls",
]
