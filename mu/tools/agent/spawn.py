"""Sub-agent spawning.

`spawn_agent` runs a fresh `Session` as a child of the current session,
with:

  * **Isolated state** — the child has its own `SessionManager`, empty
    history, fresh memory + scratchpad stores. The parent's history is
    not polluted.
  * **Shared provider** — same LLM client object; model name can be
    temporarily overridden via the `model` arg.
  * **Shared folder context** — the child sees the same workspace folders
    so it can read/edit project files.
  * **YOLO by default** — the user already approved the spawn; the child
    is trusted within the rest of this turn. (Plan mode still blocks
    the spawn itself — see WRITE_TOOLS in `mu/agent/plan_mode.py`.)
  * **Depth-capped** — children may spawn grandchildren up to
    `MAX_SUBAGENT_DEPTH` (default 2). Beyond that, `spawn_agent` is
    disabled in the child's tool surface.
  * **Hook-aware** — every tool call the child makes still fires the
    parent's `pre_tool` / `post_tool` hooks (plan mode is inherited via
    the `plan_mode` variable propagated below).
  * **Quiet UI** — the child has `ui=None` so it doesn't render to the
    parent's terminal. The final `assistant_text` is returned to the
    parent as the tool result.
  * **No disk side-effects** — the child's `save_history` is patched to
    a no-op, so no orphan session files land in HISTORY_DIR.

Result envelope: `ok=True` with `message` = the child's final assistant
text and `data.tokens` showing the child's token usage so the parent
can budget appropriately.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from mu.memory.stores import ACTIVE
from mu.tools import tool


logger = logging.getLogger("mucli")

MAX_SUBAGENT_DEPTH = 2

_DEFAULT_MAX_ITERATIONS = 60
_MIN_MAX_ITERATIONS = 30


def _extract_partial_summary(child_sm) -> str:
    """Mine a child SessionManager's history for partial findings.

    When a sub-agent hits max_iterations or errors before producing a
    final assistant response, its history still contains every tool
    result and every intermediate assistant message it generated. This
    function extracts those into a formatted summary so the parent gets
    *something* useful instead of a placeholder that discards all work.

    Returns a non-empty string if any assistant text or tool results
    were found; empty string if the child did nothing at all.
    """
    if not child_sm or not child_sm.history:
        return ""

    parts: list[str] = []

    for turn in child_sm.history:
        role = turn.get("role", "")
        for p in turn.get("parts", []):
            ptype = p.get("type", "")
            if ptype == "text" and p.get("text"):
                text = p["text"].strip()
                if not text:
                    continue
                # Skip pure tool-call narration lines ("Running tool: X")
                if text.startswith("🔨 Running tool:"):
                    continue
                # Skip per-iteration token chatter
                if text.startswith("Tokens:") or text.startswith("Final session tokens:"):
                    continue
                parts.append(text)
            elif ptype == "tool_result" and p.get("tool_result"):
                raw = p["tool_result"]
                if isinstance(raw, (dict, list)):
                    import json as _json
                    raw = _json.dumps(raw, default=str)[:500]
                else:
                    raw = str(raw)[:500]
                tool_name = p.get("tool_name", "?")
                parts.append(f"[{tool_name}] {raw}")

    if not parts:
        return ""

    # Cap to avoid flooding the parent context with a giant partial dump.
    # Keep the last 8 entries (most recent findings are most relevant).
    if len(parts) > 8:
        parts = parts[-8:]

    header = "⚠️ Sub-agent hit iteration limit. Partial findings recovered:\n\n"
    return header + "\n---\n".join(parts)


_SUBAGENT_SYSTEM_TEMPLATE = """\
You are a focused sub-agent spawned by a parent agent. Your single \
responsibility is the task below — do not chat, do not propose; act with \
the tools available and return a concise final summary when done.

Sub-agent task:
{task}

Operating rules:
- Use read/search tools first to ground yourself, then act.
- Issue independent reads in parallel within a single turn.
- Read-only tool results are injected directly into your context -- \
no need to call `flush`.
- For your own internal task tracking within this delegation, use \
`todo_write` / `todo_set_status` / `todo_list`. They are scoped to this \
sub-session and do not leak back to the parent.
- You have NO access to the parent's prior conversation history. Treat the \
task above as the full briefing. HOWEVER, durable findings the parent \
handed off (decisions, root causes, file locations) ARE available to you \
both in your own task memory and in the Parent findings section below \
when present — use them, and supersede any that you find to be wrong.
- When the task is complete, produce a SINGLE clear text response \
summarising what you did, what you found, and any caveats. This text is \
what gets returned to the parent — make it self-contained.
- Do not spawn more than {remaining_depth} additional level(s) of sub-agents.
- The user is NOT in the loop; tool approvals are auto-granted.
- You have a LIMITED iteration budget of {max_iterations} tool calls. When \
you notice you are running low on iterations (e.g. you have used more than \
half), start consolidating your findings into a summary. If you cannot \
complete the full task, save what you have found so far as your final \
response — partial results are far more useful than no results.
{parent_findings}
"""


def _build_system_prompt(
    task: str,
    remaining_depth: int,
    max_iterations: int = 60,
    parent_findings: str = "",
) -> str:
    return _SUBAGENT_SYSTEM_TEMPLATE.format(
        task=task,
        remaining_depth=max(0, remaining_depth),
        max_iterations=max_iterations,
        parent_findings=("\n" + parent_findings) if parent_findings else "",
    )


# ── R5 / FM-5: curated parent→child context handoff ─────────────────────
# The child gets isolated history (unchanged) but inherits a CURATED slice
# of the parent's durable memory so it doesn't re-derive hard-won findings
# (root causes, decisions, file locations). At depth 2 the grandchild
# accumulates grandparent→parent→child findings via `_subagent_handoff`.

_HANDOFF_MAX_ENTRIES = 8
_HANDOFF_MINE_LIMIT = 6


def _mine_parent_memory(parent) -> list:
    """Select the parent's most valuable durable entries to hand off.

    Priority: decisions > goals > findings; active before done; recency
    as the final tiebreaker. Returns a list of `MemoryEntry` objects
    (empty when the parent has no task memory)."""
    mem = getattr(getattr(parent, "session_manager", None), "task_memory", None)
    if mem is None:
        return []
    priority = {"decision": 0, "goal": 1, "finding": 2}
    entries = [
        e
        for e in mem.entries
        if e.kind in priority and e.status in ("active", "done")
    ]
    entries.sort(
        key=lambda e: (priority.get(e.kind, 9), 0 if e.status == "active" else 1, -e.updated_at)
    )
    return entries[:_HANDOFF_MINE_LIMIT]


def _build_handoff(parent, explicit_context: str) -> list:
    """Assemble the curated handoff list (dicts with content/kind/tags),
    combining (a) findings inherited by the parent from its own parent,
    (b) freshly mined parent-memory entries, deduped by content and capped
    at `_HANDOFF_MAX_ENTRIES`."""
    inherited = list(getattr(parent, "_subagent_handoff", []) or [])
    mined = _mine_parent_memory(parent)

    handoff: list = []
    seen: set[str] = set()

    def _add(content: str, kind: str, tags: list) -> None:
        if len(handoff) >= _HANDOFF_MAX_ENTRIES:
            return
        content = str(content or "").strip()
        if not content or content in seen:
            return
        seen.add(content)
        handoff.append({"content": content, "kind": kind or "finding", "tags": list(tags or [])})

    for item in inherited:
        _add(item.get("content", ""), item.get("kind", "finding"), item.get("tags", []))
    for entry in mined:
        _add(entry.content, entry.kind, entry.tags)
    return handoff


def _format_parent_findings(handoff: list, explicit_context: str) -> str:
    """Render the Parent findings block for the child system prompt."""
    blocks: list[str] = []
    ctx = str(explicit_context or "").strip()
    if ctx:
        blocks.append("Parent-provided context (free-form):\n" + ctx)
    if handoff:
        lines = [
            "Parent findings (durable memory handed off via your task "
            "memory; you may supersede any entry that turns out to be wrong):"
        ]
        for item in handoff:
            lines.append(f"- [{item.get('kind') or 'finding'}] {item['content']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _envelope(
    *,
    ok: bool,
    message: str,
    error_code=None,
    data=None,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "error_code": error_code,
        "message": message,
        "data": data or {},
        "artifacts": [],
        "telemetry": {"tool_name": "spawn_agent"},
    }


@tool(
    name="spawn_agent",
    description=(
        "Spawn a child agent with an isolated session to perform a focused "
        "task and return its final result. Use for long-horizon side quests "
        "(research, large refactors) so the parent context stays clean."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the child agent should do — a single focused goal.",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional whitelist of tools the child may use. Default: all.",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Cap on the child's tool-call loop. Default: 50.",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for the child. Default: parent's model.",
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional free-form briefing handed to the child (hard-won "
                    "facts, constraints, or partial answers the child should not "
                    "re-derive). In addition, the parent's durable task-memory "
                    "decisions/findings are auto-handed-off; this text is layered "
                    "on top. Keep it terse."
                ),
            },
        },
        "required": ["task"],
    },
    requires_approval=True,
    execution_kind="io",
    result_mode="json",
)
def spawn_agent(args: Dict[str, Any], context) -> Dict[str, Any]:
    task = str(args.get("task") or "").strip()
    if not task:
        return _envelope(
            ok=False,
            error_code="invalid_args",
            message="spawn_agent requires non-empty 'task'.",
        )

    parent = getattr(context, "session", None)
    if parent is None:
        return _envelope(
            ok=False,
            error_code="no_session",
            message=(
                "spawn_agent requires a parent session. The tool may only be "
                "invoked from inside an agent turn."
            ),
        )

    # Depth check first — refuse cleanly rather than spinning up state.
    current_depth = int(getattr(parent, "_subagent_depth", 0) or 0)
    if current_depth >= MAX_SUBAGENT_DEPTH:
        return _envelope(
            ok=False,
            error_code="depth_exceeded",
            message=(
                f"spawn_agent depth limit reached (current depth={current_depth}, "
                f"max={MAX_SUBAGENT_DEPTH}). Refusing to spawn further children."
            ),
            data={"depth": current_depth, "max_depth": MAX_SUBAGENT_DEPTH},
        )

    # Plan mode is inherited so a child cannot escape read-only enforcement.
    if (getattr(parent, "variables", None) or {}).get("plan_mode"):
        return _envelope(
            ok=False,
            error_code="plan_mode_blocked",
            message=(
                "spawn_agent is blocked while plan_mode is active. Disable "
                "plan mode with /plan off if you want sub-agents to run."
            ),
            data={"plan_mode": True},
        )

    # ------------------------------------------------------- build child Session
    # Local imports avoid a load-time cycle with `mu.session.session` (which itself
    # imports from `mu.*`).
    from mu.session.session import Session, SessionManager

    max_iterations = int(args.get("max_iterations") or _DEFAULT_MAX_ITERATIONS)
    if max_iterations <= 0:
        max_iterations = _DEFAULT_MAX_ITERATIONS
    # Enforce a minimum floor so the parent agent can't starve the child
    # with an unreasonably low budget. Values below 30 are clamped to 30.
    if max_iterations < _MIN_MAX_ITERATIONS:
        max_iterations = _MIN_MAX_ITERATIONS

    parent_provider = parent.provider
    original_model = parent_provider.model_name
    model_override = args.get("model")
    if model_override:
        parent_provider.model_name = str(model_override)

    child_session_name = f"__subagent_{uuid.uuid4().hex[:8]}__"
    child_sm = SessionManager(session_name=child_session_name)
    # No disk side-effects: a child run is in-memory only.
    child_sm.save_history = lambda *a, **kw: None

    remaining_depth = MAX_SUBAGENT_DEPTH - (current_depth + 1)
    child_depth = current_depth + 1

    # ── R5 / FM-5: curated parent→child context handoff ──────────────
    # Mine the parent's durable task memory for decisions/findings (plus any
    # inherited handoff the parent itself received, so grandparent→parent→
    # child findings accumulate at depth 2) and any explicit `context` text.
    explicit_context = str(args.get("context") or "").strip()
    handoff = _build_handoff(parent, explicit_context)
    parent_findings_block = _format_parent_findings(handoff, explicit_context)

    # Build a child UI that forwards tool-call / status messages to the
    # parent's UI with a clear `[subagent d=N]` prefix so the user can see
    # what's happening instead of staring at a frozen terminal.
    #
    # If the parent session has installed a `SubagentProgressTracker` for
    # the current parallel batch, hook the child UI into it so "Running
    # tool: X" updates become live-panel updates instead of terminal log
    # spam.
    from mu.ui.subagent import SubagentUI

    progress_tracker = getattr(parent, "_subagent_progress", None)
    progress_agent_id: Optional[str] = None
    if progress_tracker is not None:
        try:
            progress_agent_id = progress_tracker.open(depth=child_depth, task=task)
        except Exception:
            progress_agent_id = None

    child_ui = (
        SubagentUI(
            parent.ui,
            depth=child_depth,
            tracker=progress_tracker if progress_agent_id else None,
            agent_id=progress_agent_id,
        )
        if parent.ui is not None
        else None
    )

    child = Session(
        provider=parent_provider,
        thinking=parent.thinking,
        system_instruction=_build_system_prompt(
            task,
            remaining_depth,
            max_iterations,
            parent_findings=parent_findings_block,
        ),
        session_manager=child_sm,
        ui=child_ui,
        debug=getattr(parent, "debug", False),
    )

    # Seed the child's durable task memory with the handed-off entries so
    # the child can search/supersede them like its own findings (source is
    # tagged "parent_handoff" so a later `supersede` cleanly replaces them).
    # History stays isolated — only the curated slice crosses the boundary.
    if handoff and getattr(child_sm, "task_memory", None) is not None:
        for item in handoff:
            try:
                child_sm.task_memory.save(
                    content=item["content"],
                    tags=item["tags"],
                    source="parent_handoff",
                    kind=item["kind"],
                    status=ACTIVE,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "spawn_agent: failed to seed child memory with %r",
                    item["content"][:60],
                )

    # Inherit the folder context — the child reads/writes within the same workspace.
    child.folder_context = parent.folder_context
    child.session_manager.folder_context = parent.folder_context

    # Auto-approve so the child runs to completion without blocking the parent.
    child.variables["yolo"] = True
    child.variables["max_iterations"] = max_iterations
    # Marker for loop_body.py: fire wrap-up reminder 3 iterations before cap.
    child._subagent_wrap_up_iter = max(1, max_iterations - 3)
    # Subagent runs are short — never compact history mid-run.
    child.variables["compact_history"] = False
    # Skip the agent_mode-specific prompts (feature / loop) for subagent turns.
    child.variables["agent_mode"] = "default"
    # Disable collation for subagents: each read→flush cycle wastes 2
    # iterations (one for the read, one for the flush).  Subagent iteration
    # budget is already tight; collation is designed for the parent's
    # multi-turn context management, not short delegation runs.  With
    # collation off, read-only results are injected directly into history
    # so the subagent sees them immediately and can act in the same turn.
    child.variables["collation_enabled"] = False

    # Tools whitelist (if any). Always keep `flush` so collation works,
    # and disable `spawn_agent` if we're at the depth cap for the child.
    requested_tools = args.get("tools")
    disabled: list = []
    if requested_tools:
        from mu.tools.descriptors import TOOLS

        all_tool_names = {t.name for t in TOOLS}
        allowed = {str(name) for name in requested_tools} | {"flush"}
        disabled = sorted(all_tool_names - allowed)
    if remaining_depth <= 0 and "spawn_agent" not in disabled:
        disabled.append("spawn_agent")
    child.disabled_tools = disabled

    # Tag the depth on the child so a grandchild sees the running count.
    child._subagent_depth = child_depth
    # Carry the curated handoff forward so a grandchild (depth 2) inherits
    # grandparent→parent→child findings rather than re-deriving them.
    child._subagent_handoff = handoff

    logger.info(
        "spawn_agent: depth=%d, task=%s, max_iter=%d, disabled=%d",
        child._subagent_depth,
        task[:60],
        max_iterations,
        len(disabled),
    )

    # Announce on the parent UI so the user can see the spawn happen.
    task_preview = task if len(task) <= 100 else task[:97] + "..."
    if parent.ui is not None and hasattr(parent.ui, "show_info"):
        try:
            parent.ui.show_info(
                f"🤖 [bold]Spawning subagent[/bold] (d={child_depth}): {task_preview}"
            )
        except Exception:
            pass

    try:
        try:
            result = child.send_message(task)
        finally:
            parent_provider.model_name = original_model
    except Exception as exc:  # noqa: BLE001
        logger.warning("spawn_agent: child raised %s", exc)
        if progress_tracker is not None and progress_agent_id is not None:
            try:
                progress_tracker.close(
                    progress_agent_id,
                    tool_count=0,
                    summary="",
                    error=str(exc),
                )
            except Exception:
                pass
        if parent.ui is not None and hasattr(parent.ui, "show_error"):
            try:
                parent.ui.show_error(
                    f"🤖 Subagent (d={child_depth}) FAILED: {exc}"
                )
            except Exception:
                pass
        return _envelope(
            ok=False,
            error_code="subagent_error",
            message=f"Sub-agent failed: {exc}",
            data={"depth": child._subagent_depth, "task": task},
        )

    final_text = str(result.get("assistant_text") or "").strip()
    if not final_text:
        # When the child hits max_iterations or errors before producing a
        # final assistant response, mine its history for partial findings
        # so the parent gets *something* useful instead of a placeholder
        # that discards every tool result and every intermediate thought.
        partial = _extract_partial_summary(child_sm)
        if partial:
            final_text = partial
        else:
            final_text = "(sub-agent finished without producing a final text response)"

    child_ok = bool(result.get("ok", True))
    tokens = result.get("tokens") or {}
    tool_call_count = len(result.get("tool_calls") or [])

    # Close the live-panel row for this sub-agent (if a tracker is active).
    # The tracker keeps the row visible after close so the final "✓ done /
    # ✗ error" state is observable until the batch finishes.
    if progress_tracker is not None and progress_agent_id is not None:
        try:
            close_summary = final_text if child_ok else str(result.get("error") or final_text)
            progress_tracker.close(
                progress_agent_id,
                tool_count=tool_call_count,
                summary=close_summary,
                error=None if child_ok else str(result.get("error") or "subagent error"),
            )
        except Exception:
            pass

    # Announce completion on the parent UI — separate banners for success vs.
    # graceful failure so the user can tell at a glance whether the child
    # finished cleanly or hit `status=error` / `status=max_iterations_reached`.
    if parent.ui is not None:
        try:
            if child_ok:
                if hasattr(parent.ui, "show_info"):
                    parent.ui.show_info(
                        f"🤖 Subagent (d={child_depth}) done — "
                        f"{tool_call_count} tool call(s), "
                        f"{tokens.get('total', 0)} tokens, "
                        f"{len(final_text)} chars returned."
                    )
            else:
                if hasattr(parent.ui, "show_error"):
                    err_text = str(result.get("error") or final_text or "unknown")
                    err_status = str(result.get("status") or "error")
                    parent.ui.show_error(
                        f"🤖 Subagent (d={child_depth}) FAILED [{err_status}]: {err_text[:200]}"
                    )
        except Exception:
            pass

    return _envelope(
        ok=child_ok,
        message=final_text,
        error_code=None if child_ok else "subagent_failed",
        data={
            "status": result.get("status"),
            "tokens": result.get("tokens"),
            "depth": child._subagent_depth,
            "task": task,
            "tool_calls": tool_call_count,
            "history_length": len(child_sm.history),
        },
    )


__all__ = ["MAX_SUBAGENT_DEPTH", "spawn_agent"]
