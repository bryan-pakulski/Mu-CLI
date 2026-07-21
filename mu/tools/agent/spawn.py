"""Async sub-agent spawning (orchestrator pattern).

`spawn_agent` runs a fresh `Session` as a child of the current session on
a **background daemon thread** and returns immediately with a `task_id`.
The parent's agentic loop is NOT blocked — it can continue other work,
then poll the child via `poll_subagent` and cancel it via `kill_subagent`.

Per child:

  * **Isolated state** — own `SessionManager`, empty history, fresh memory +
    scratchpad stores. The parent's history is not polluted.
  * **Cloned provider** — `parent.provider.clone_for_child()` gives the child
    its own `model_name` slot while sharing the underlying thread-safe HTTP
    client. This removes the old race where concurrent children clobbered a
    single shared `model_name`.
  * **Shared folder context** — the child sees the same workspace folders.
  * **YOLO by default** — the user already approved the spawn; the child is
    trusted within its run. (Plan mode still blocks the spawn itself.)
  * **Depth-capped** — children may spawn grandchildren up to
    `MAX_SUBAGENT_DEPTH` (default 2). Beyond that `spawn_agent` is disabled
    in the child's tool surface.
  * **Role-stamped** — `session_role="child"`, `subagent_depth`,
    `subagent_parent_task_id` are set so LAYER 3B (Agent Role) injects
    sub-agent guidance into the child's system prompt. The parent is
    stamped `session_role="parent"` on its first spawn so it gets
    orchestrator guidance.
  * **Lifecycle-managed** — a `SubagentLifecycleManager` tracks tool calls,
    detects stuck/stall, and auto-kills on runtime limit. The parent
    decides kill/extend for stuck/stall via `poll_subagent`.
  * **Quiet UI** — the child has a `SubagentUI` that routes tool-call
    progress to the live tracker instead of flooding the parent terminal.

Result envelope: `ok=True` with `data.task_id` + `data.status="running"`.
The parent retrieves the final summary by polling.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from mu.memory.stores import ACTIVE
from mu.tools import tool


logger = logging.getLogger("mucli")

MAX_SUBAGENT_DEPTH = 2

_DEFAULT_MAX_ITERATIONS = 60
_MIN_MAX_ITERATIONS = 30


def _model_installed(model: str, installed: list) -> bool:
    """True if `model` is in the provider's available list. Matches exactly,
    or by base name (split on ``:``) so an elided ``:latest`` tag on Ollama
    still counts as installed. Mirrors OllamaProvider.is_model_installed."""
    if not model:
        return False
    if model in installed:
        return True
    base = model.split(":", 1)[0]
    return any(m.split(":", 1)[0] == base for m in installed)


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
        "Dispatch a child agent with an isolated session to perform a focused "
        "task ASYNCHRONOUSLY. Returns immediately with a task_id — the parent "
        "loop is NOT blocked. To wait for results without busy-polling, call "
        "`await_subagent(task_id, timeout=N)` — it blocks on a single tool call "
        "and wakes when the child finishes or the timer fires (no poll-loop "
        "warning). Use `poll_subagent(task_id)` for a quick non-blocking status "
        "check, and `kill_subagent(task_id)` to cancel a stuck or unneeded child. "
        "Use for long-horizon side quests (research, large refactors) so the "
        "parent context stays clean and the parent can keep working while "
        "children run."
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
                "description": (
                    "Optional per-call model override for the child — must be a "
                    "model installed on the active provider. Default: the "
                    "subagent_model session variable if set, else the parent's "
                    "model. An uninstalled name falls back to the parent model."
                ),
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

    # Lazy LAYER 3B: stamp the parent as orchestrator on its first spawn so
    # subsequent turns get orchestrator guidance. Cleared by Session's
    # send_message finally once no children remain active.
    if not str(parent.variables.get("session_role", "") or "").strip():
        parent.variables["session_role"] = "parent"

    # ------------------------------------------------------- build child Session
    # Local imports avoid a load-time cycle with `mu.session.session`.
    from mu.session.session import Session, SessionManager
    from mu.agent.lifecycle import SubagentLifecycleManager

    max_iterations = int(args.get("max_iterations") or _DEFAULT_MAX_ITERATIONS)
    if max_iterations <= 0:
        max_iterations = _DEFAULT_MAX_ITERATIONS
    if max_iterations < _MIN_MAX_ITERATIONS:
        max_iterations = _MIN_MAX_ITERATIONS

    # Cloned provider — own model_name slot, shared (thread-safe) client.
    # clone_for_child() shallow-copies the parent, so the child already
    # inherits the parent's model_name. We only reassign it when a valid
    # override is selected below. Resolution priority:
    #   1. `subagent_model` session variable (user-configured default)
    #   2. the agent's per-call `model` arg
    #   3. the inherited parent model (the clone's current model_name)
    # An uninstalled config/arg falls back to the parent model with a
    # warning, so a hallucinated name (e.g. "sonnet-3.5" on Ollama) no
    # longer crashes the child's first generate() call — the child just
    # runs on the parent's working model instead.
    child_provider = parent.provider.clone_for_child()
    parent_model = child_provider.model_name
    try:
        installed = list(parent.provider.get_available_models() or [])
    except Exception:  # noqa: BLE001 — never let listing block a spawn
        installed = []

    cfg_model = str((parent.variables or {}).get("subagent_model") or "").strip()
    arg_model = str(args.get("model") or "").strip()

    def _pick(candidate: str, source: str) -> str | None:
        if not candidate:
            return None
        if _model_installed(candidate, installed):
            return candidate
        logger.warning(
            "spawn_agent: %s model %r is not installed on %s (available: %s); "
            "falling back to parent model %r.",
            source,
            candidate,
            getattr(parent.provider, "name", "") or "provider",
            installed[:8] if installed else "<none>",
            parent_model,
        )
        return None

    resolved = _pick(cfg_model, "subagent_model") or _pick(arg_model, "requested") or parent_model
    child_provider.model_name = resolved

    child_session_name = f"__subagent__"
    child_sm = SessionManager(session_name=child_session_name)
    # No disk side-effects: a child run is in-memory only.
    child_sm.save_history = lambda *a, **kw: None

    remaining_depth = MAX_SUBAGENT_DEPTH - (current_depth + 1)
    child_depth = current_depth + 1

    # ── R5 / FM-5: curated parent→child context handoff ──────────────
    explicit_context = str(args.get("context") or "").strip()
    handoff = _build_handoff(parent, explicit_context)
    parent_findings_block = _format_parent_findings(handoff, explicit_context)

    # Build a child UI that forwards tool-call progress to the parent's
    # long-lived progress tracker (owned by the registry) so "Running tool:
    # X" updates become live-panel rows instead of terminal log spam.
    from mu.ui.subagent import SubagentUI

    registry = parent._subagent_registry
    # Wire the GUI live-push callback onto the registry (and its tracker) so
    # subagent_start/progress/end events reach the chat-feed status panel.
    # CLI/TUI UIs have no ``_publish`` → the registry stays silent (no
    # behaviour change). Walk nested SubagentUI wrappers to the root UI so
    # grandchild spawns route through the real WebUI too.
    _root_ui = parent.ui
    while isinstance(_root_ui, SubagentUI):
        _root_ui = _root_ui._parent
    if _root_ui is not None and hasattr(_root_ui, "_publish"):
        registry._publish = lambda ev: _root_ui._publish(ev)
    # Pre-open the tracker row so the child UI can be wired with its agent_id.
    tracker_agent_id = None
    try:
        tracker_agent_id = registry.tracker.open(depth=child_depth, task=task)
    except Exception:  # noqa: BLE001
        tracker_agent_id = None

    child_ui = (
        SubagentUI(
            parent.ui,
            depth=child_depth,
            tracker=registry.tracker if tracker_agent_id else None,
            agent_id=tracker_agent_id,
        )
        if parent.ui is not None
        else None
    )

    child = Session(
        provider=child_provider,
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
    # Subagent runs are short — never compact history mid-run.
    child.variables["compact_history"] = False
    # Skip the agent_mode-specific prompts (feature / loop) for subagent turns.
    child.variables["agent_mode"] = "default"
    # Disable collation for subagents (see rationale below).
    child.variables["collation_enabled"] = False
    # Role-stamp the child so LAYER 3B injects sub-agent guidance.
    child.variables["session_role"] = "child"
    child.variables["subagent_depth"] = child_depth
    # subagent_parent_task_id set after register (needs the new task_id).

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

    # Adaptive lifecycle manager. Thresholds inherited from the parent's
    # configured session variables (defaults: stuck=3, stall=5, runtime=300s).
    lifecycle = SubagentLifecycleManager(
        thresholds={
            "stuck_threshold": int(parent.variables.get("subagent_stuck_threshold", 3) or 3),
            "stall_threshold": int(parent.variables.get("subagent_stall_threshold", 5) or 5),
            "max_runtime_seconds": int(
                parent.variables.get("subagent_max_runtime_seconds", 300) or 300
            ),
            "enabled": bool(parent.variables.get("subagent_lifecycle_enabled", True)),
        }
    )
    child._subagent_lifecycle = lifecycle

    # Register the child in the parent's async registry (control plane).
    record = registry.register(
        child,
        task=task,
        depth=child_depth,
        lifecycle=lifecycle,
        tracker_agent_id=tracker_agent_id,
        model=resolved,
    )
    child.variables["subagent_parent_task_id"] = record.task_id
    # Bridge the child loop to its registry record so the child can report
    # live context-usage (context_pct / iter / tokens_in) each iteration.
    child._parent_registry = registry

    logger.info(
        "spawn_agent: dispatched task_id=%s depth=%d task=%s max_iter=%d disabled=%d",
        record.task_id,
        child._subagent_depth,
        task[:60],
        max_iterations,
        len(disabled),
    )

    # Announce the dispatch on the parent UI.
    task_preview = task if len(task) <= 100 else task[:97] + "..."
    if parent.ui is not None and hasattr(parent.ui, "show_info"):
        try:
            parent.ui.show_info(
                f"🤖 [bold]Spawning subagent[/bold] (d={child_depth}, "
                f"task_id={record.task_id}): {task_preview}"
            )
        except Exception:
            pass

    # Launch the background thread that runs the child to completion. The
    # thread wrapper captures the final summary / partial findings and
    # closes the tracker row; the parent retrieves results via poll_subagent.
    registry.launch(record, task)

    return _envelope(
        ok=True,
        message=(
            f"Dispatched sub-agent (task_id={record.task_id}, depth={child_depth}). "
            "It is running in the background. To wait for it to finish without "
            "burning iterations, call await_subagent(task_id, timeout=...) — it "
            "blocks until the child finishes or the timer fires. Call poll_subagent "
            "for a quick non-blocking status check; call kill_subagent to cancel it."
        ),
        data={
            "task_id": record.task_id,
            "status": "running",
            "depth": child_depth,
            "task": task,
        },
    )


__all__ = ["MAX_SUBAGENT_DEPTH", "spawn_agent"]