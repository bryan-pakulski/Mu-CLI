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

from typing import Any


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
    from mu.session.session import _hook_abort_envelope
    from mu.agent.hooks import HookContext, default_registry

    # Side-effect imports — ensure built-in hooks (plan_mode, compactor,
    # usage_tracker, secret_guard) have registered before we fire.
    import mu.agent.plan_mode  # noqa: F401
    import mu.agent.compactor  # noqa: F401
    import mu.agent.secret_guard  # noqa: F401

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
) -> dict:
    """Wrap a raw tool result in the structured envelope the history
    stores (summary, args, raw, error_code, modified_files, telemetry).

    Per-tool branches add typed `data` fields when callers want
    structured access (`read_file` → `char_count`, `list_dir` → parsed
    tree, etc.). For un-recognized tools, `data` is left empty and the
    raw text-preview lives in `summary`."""
    from mu.tools._envelope import infer_tool_error_code
    from mu.session.messages import clip_preview, _shorten_tool_args

    envelope, unwrapped_raw = session._unwrap_tool_envelope(raw_result)
    raw_text = str(unwrapped_raw)
    error_code = (
        envelope.get("error_code")
        if isinstance(envelope, dict)
        else infer_tool_error_code(tool_name, raw_text)
    )
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
    elif tool_name in {"write_file", "apply_diff"}:
        filename = tool_args.get("filename", "")
        structured["data"] = {
            "filename": filename,
            "changed_file": filename,
        }
        if filename:
            structured["modified_files"] = [filename]
    elif tool_name in _FEATURE_MODE_TOOL_NAMES:
        structured["data"] = session._parse_json_result(raw_text)
    elif tool_name in _MEMORY_TOOL_NAMES:
        structured["data"] = {"preview": clip_preview(raw_text, 220)}

    return structured


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
]
