"""Body of the agentic turn loop.

`run_turn(session, text)` is the function that drives one user turn
end-to-end: prepares the user message, assembles the system prompt
through every hierarchical layer, runs the agentic loop until the
model emits a final response or hits `max_iterations`, dispatches
tool calls (including parallel batches), and returns a structured
turn-response dict.

For the long history of this body see `core/session.py` — it lived
on `Session.send_message` until Phase 4 of the refactor, where it
was extracted verbatim. `Session.send_message` is now a 3-line
forwarder to here; the body itself uses `session.` rather than
`self.` for every state access.

The control flow:

  1. Reset per-turn state (paused_execution_text, hook abort flag,
     loop blocker flag).
  2. Build the new user message (apply feature/loop mode prompt
     transforms; attach staged files).
  3. Compose the system prompt: agentic harness + mode-specific text
     + workspace context (retrieval-first when available) + L1–L5
     hierarchical layers.
  4. Roll history under the compaction budget.
  5. Loop: pre-turn hook abort check → provider stream → dispatch
     tool calls (serial or parallel) → post-process structured
     results → repeat until no tool calls OR hit max_iterations OR
     hook aborts OR user interrupts.
  6. Collect the turn response and return.

Hooks fire from this body via `session._execute_tool_with_memory`
(pre_tool/post_tool) and `session._provider_generate_with_retry`
(pre_provider_call/post_provider_call/on_stop). The body itself
doesn't fire hooks directly — see `mu/agent/hooks.py` for the points.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import traceback
from typing import Any

from mu.agent.approval import ApprovalPlan, build_approval_prompt, collect_approval_plans
from mu.feature.engine import refresh_and_persist_feature_plan, summarize_feature_plan
from mu.tools._dispatcher import execute_tool
from mu.tools._envelope import infer_tool_error_code
from mu.tools.descriptors import COLLATED_TOOLS, TOOLS
from providers.base import FileReference, ImageData, Message, MessagePart
from utils.config import (
    AGENTIC_MODES,
    AGENTIC_SYSTEM_BASE,
    NUDGE_EMPTY_RESPONSE,
    calculate_cost,
)
from utils.helpers import display_image_in_terminal, get_safe_mime_type
from utils.logger import logger
from utils.runtime_metrics import build_live_status_line


# Three symbols in `core/session.py` are still consumed by the body
# (`_HookAbort`, `_shorten_tool_args`, `_hook_abort_envelope`). The
# obvious `from mu.session.session import …` would force a circular import
# at module-load time — core/session.py imports `run_turn` from us via
# `Session.send_message`. Instead, we bind them on first call to
# `run_turn`; by then core/session.py has fully loaded because the
# `from mu.agent.loop_body import run_turn` lives inside the
# `send_message` body, not at module top.
_HookAbort = None  # bound by _bind_session_symbols on first run_turn
_shorten_tool_args = None
_hook_abort_envelope = None
_sanitize_for_log = None


def _bind_session_symbols():
    global _HookAbort, _shorten_tool_args, _hook_abort_envelope, _sanitize_for_log
    if _HookAbort is not None:
        return
    from mu.session import session as _session

    _HookAbort = _session._HookAbort
    _shorten_tool_args = _session._shorten_tool_args
    _hook_abort_envelope = _session._hook_abort_envelope
    _sanitize_for_log = _session._sanitize_for_log


def run_turn(session, text):
    _bind_session_symbols()
    logger.info(f"Sending message: {text[:100]}...")
    session.paused_execution_text = None
    session._loop_blocker_raised = False  # fresh turn — last turn's pause doesn't apply
    session._hook_abort_requested = False
    session._hook_abort_reason = None
    session.sync_runtime_state()
    if session.variables.get("scratchpad_enabled", True):
        session.turn_scratchpad.max_entries = max(
            1,
            int(
                session.variables.get(
                    "scratchpad_max_entries", session.turn_scratchpad.max_entries
                )
            ),
        )
        session.turn_scratchpad.clear()

    parts = list(session.staged_files)
    effective_text = text
    active_mode = str(session.variables.get("agent_mode", "default")).lower()
    if text and active_mode == "feature":
        effective_text = session._build_feature_mode_prompt(text)
    elif text and active_mode == "loop":
        effective_text = session._build_loop_mode_prompt(text)
    if active_mode == "loop":
        session._ensure_loop_goal_persistence()
    if effective_text:
        parts.append({"type": "text", "text": effective_text})

    new_user_message = {"role": "user", "parts": parts}

    if text and session.ui:
        session.ui.render_message("user", text)

    workspace_context = ""
    session._pending_retrieved_context = ""

    if session.folder_context.folders:
        retrieval_query = effective_text or text
        session._pending_retrieved_context = session._build_retrieved_workspace_context(
            retrieval_query
        )
        # Let tools auto discover workspace content as needed
        if session.agentic:
            active_tools = [t for t in TOOLS if t.name not in session.disabled_tools]
            tool_desc_str = "\n".join(
                [f"{t.name} - {t.description}" for t in active_tools]
            )

            agent_mode = str(session.variables.get("agent_mode", "default")).lower()
            default_mode_instruction = AGENTIC_MODES.get(
                agent_mode, AGENTIC_MODES["default"]
            )
            mode_instruction = str(
                session.variables.get(
                    f"agentic_mode_prompt_{agent_mode}",
                    default_mode_instruction,
                )
                or default_mode_instruction
            )
            agentic_system_base = str(
                session.variables.get(
                    "agentic_system_base_override", AGENTIC_SYSTEM_BASE
                )
                or AGENTIC_SYSTEM_BASE
            )

            # Providers automatically generated tool prompts so don't need to be embedded into the system prompt
            workspace_context = f"{agentic_system_base}\n\n### CURRENT STRATEGY MODE: {agent_mode.upper()}\n{mode_instruction}"
        else:
            logger.debug(
                f"Using agent_mode={session.variables.get('agent_mode', 'default')}"
            )

            if session.ui:
                with session.ui.show_status(
                    "Scanning monitored folders for changes..."
                ):
                    if session._pending_retrieved_context:
                        workspace_context = (
                            "### RETRIEVAL-FIRST WORKSPACE CONTEXT\n"
                            "Ranked snippets were selected from semantic index scoring.\n"
                            f"{session._pending_retrieved_context}"
                        )
                    else:
                        folder_initial_xml = (
                            session.folder_context.get_initial_context_xml()
                        )
                        folder_diff_xml = session.folder_context.get_context_diff_xml()
                        workspace_context = f"{folder_initial_xml}\n\n{folder_diff_xml}"

    base_system_prompt = session.system_instruction
    if active_mode == "feature":
        base_system_prompt += (
            "\n\nFEATURE MODE SYSTEM PROMPT\n"
            "You are in Feature Plan Engine mode. "
            "Use the staged feature-task engine for this request. Start with create_feature, then create_phases, then create_task for each ticket. "
            "Do not create alternate planning documents and do not begin code implementation until the user has reviewed and approved the plan. "
            "Every task must include explicit EXIT CRITERIA and tasks can be marked completed only after all exit criteria are verified. "
            "Continuously update verified_exit_criteria via update_task_status as each criterion is met so progress remains explicit. "
            "Step through one task at a time until completion; never work multiple tasks simultaneously. "
            "Use get_execution_state to choose the next actionable phase/task, use block_task if external input is required, and resume_task when user unblock context arrives. "
            "Use review_all_completed_tasks/review_completed_tasks/propose_task_diff/decide_task_diff/archive_task for review-and-archive flow after implementation completes. "
            "gather read-only context first, use save_scratchpad for temporary phase notes, call flush before acting on collected context, and call raise_blocker when blocked on user input. "
            "You must use save_memory for durable facts/decisions and reuse search_memory/list_memory before re-deriving context in long loops. "
            "You must use save_scratchpad/list_scratchpad within each turn to track in-flight plans as context grows. "
            "Do not stall on status-only updates: unless blocked or awaiting explicit approval/decision, continue implementation autonomously until all phases and tasks are completed."
        )
    elif active_mode == "loop":
        loop_goal = str(session.variables.get("loop_goal", "") or "").strip()
        base_system_prompt += (
            "\n\nLOOP MODE SYSTEM PROMPT\n"
            "You are executing a long-horizon autonomous loop. "
            "Work continuously in increments (plan -> execute -> verify -> continue) until stopped by the user. "
            "Maintain a visible task list via `todo_write` and `todo_set_status` so the user can see your plan at any time. "
            "Exactly one todo should be in_progress at a time. "
            "At each increment, provide a concise timeline update: attempted action, outcome, evidence, and next step. "
            "Use save_memory for durable findings and save_scratchpad for short-lived planning. "
            "For focused side-quests that would clutter loop context (deep research, isolated refactors), delegate via `spawn_agent` with a tight tools whitelist."
        )
        if loop_goal:
            base_system_prompt += f"\nLocked loop goal: {loop_goal}"
    if workspace_context:
        base_system_prompt += f"\n\n{workspace_context}"
    session.session_manager.roll_history_summary_to_token_budget(
        session._compaction_token_budget(),
        keep_recent=4,
    )
    # Tell the auto-compaction hook we've already rolled this turn so it
    # doesn't double-roll inside the iteration loop. Cleared in
    # `_collect_turn_response` when the turn finishes.
    session._history_rolled_this_turn = True
    session._pending_user_text = effective_text or text or ""
    base_system_prompt = session._inject_hierarchical_context(base_system_prompt)

    recent_history = session._prepare_runtime_history()
    messages = session._build_messages_from_history(recent_history, new_user_message)

    initial_history_len = len(session.session_manager.history)
    session.session_manager.history.append(new_user_message)
    session.session_manager.save_history()
    session.staged_files = []
    turn_start_index = len(session.session_manager.history) - 1

    max_iterations = session.variables.get("max_iterations", 50)
    iteration = 0
    active_tools = [t for t in TOOLS if t.name not in session.disabled_tools]

    total_in = 0
    total_out = 0
    total_cost = 0.0

    logger.info(f"Starting agentic loop (max_iterations={max_iterations})")
    provider_bad_request_retried = False
    exact_tool_sequence_history: list[str] = []
    pattern_tool_sequence_history: list[str] = []
    loop_detection_enabled = bool(
        session.variables.get("loop_detection_enabled", True)
    )
    loop_detection_repeat_threshold = max(
        2,
        int(session.variables.get("loop_detection_repeat_threshold", 3) or 3),
    )

    while iteration < max_iterations:
        iteration += 1
        logger.debug(f"Agentic loop iteration {iteration}/{max_iterations}")
        # Honor a hook abort raised in the previous iteration. The
        # in-flight provider call / tool dispatch from that iteration
        # has already finished and stored its results in history; we
        # exit cleanly here with status="hook_aborted".
        if session._hook_abort_requested:
            logger.info(
                f"Agentic loop exiting on hook abort: {session._hook_abort_reason}"
            )
            if session.session_manager.get_feature_state():
                session._set_feature_state(status="hook_aborted")
            return session._collect_turn_response(
                initial_history_len,
                status="hook_aborted",
                total_in=total_in,
                total_out=total_out,
                total_cost=total_cost,
                error=(
                    session._hook_abort_reason
                    or "A lifecycle hook requested abort."
                ),
            )
        current_tool_name = None
        current_tool_args = None
        iteration_tool_exact_fingerprints: list[str] = []
        iteration_tool_pattern: list[str] = []

        try:
            dynamic_system_prompt = base_system_prompt
            if session.variables.get("memory_enabled", True):
                session.task_memory.max_entries = max(
                    1,
                    int(
                        session.variables.get(
                            "memory_max_entries", session.task_memory.max_entries
                        )
                    ),
                )
                memory_summary = session.task_memory.render_summary(
                    limit=int(session.variables.get("memory_summary_limit", 8))
                )
                if memory_summary:
                    dynamic_system_prompt += (
                        "\n\nLAYER 3 — Persisted working memory snapshot:\n"
                        f"{memory_summary}"
                    )
            if session.variables.get("scratchpad_enabled", True):
                scratchpad_summary = session.turn_scratchpad.render_summary(limit=8)
                if scratchpad_summary:
                    dynamic_system_prompt += (
                        "\n\nLAYER 3 — Turn scratchpad snapshot:\n"
                        f"{scratchpad_summary}"
                    )

            if session.ui and hasattr(session.ui, "build_live_status"):
                status_msg = session.ui.build_live_status(
                    session,
                    session.provider.model_name,
                    iteration,
                    max_iterations,
                )
            else:
                status_msg = (
                    f"Generating ({session.provider.model_name}) it {iteration}/{max_iterations}"
                    f" | {build_live_status_line(session)}"
                )
            if session.ui:
                with session.ui.show_status(status_msg):
                    response = session._provider_generate_with_retry(
                        messages=messages,
                        system_prompt=dynamic_system_prompt,
                        thinking=session.thinking,
                        tools=active_tools
                        if (session.folder_context.folders and session.agentic)
                        else None,
                    )
            else:
                response = session._provider_generate_with_retry(
                    messages=messages,
                    system_prompt=dynamic_system_prompt,
                    thinking=session.thinking,
                    tools=active_tools
                    if (session.folder_context.folders and session.agentic)
                    else None,
                )

            logger.debug(
                f"Provider response received. Tokens: In {response.input_tokens}, Out {response.output_tokens}"
            )

            ai_parts_archive = []
            has_tool_call = False
            has_text = False

            for part in response.parts:
                if part.type == "text" and part.text:
                    has_text = True
                    if session.ui:
                        session.ui.render_message(
                            "assistant", part.text, session.provider.model_name
                        )
                    logger.debug(f"Assistant text: {part.text[:200]}...")
                    ai_parts_archive.append({"type": "text", "text": part.text})

                elif part.type == "image_inline" and part.inline_data:
                    display_image_in_terminal(session.session_manager.current_session_name, part.inline_data, save=True)
                    ai_parts_archive.append(
                        {
                            "type": "text",
                            "text": "[Image Generated and Saved locally]",
                        }
                    )

                elif part.type == "tool_call":
                    has_tool_call = True
                    ai_parts_archive.append(
                        {
                            "type": "tool_call",
                            "tool_name": part.tool_name,
                            "tool_args": part.tool_args,
                            "thought_signature": part.thought_signature,
                        }
                    )
                    if session.ui and active_mode != "loop":
                        session.ui.show_info(
                            f"🔨 Running tool: {part.tool_name}({_shorten_tool_args(part.tool_args)})"
                        )
                    logger.info(
                        f"Tool call: {part.tool_name} with args {part.tool_args}"
                    )

            if ai_parts_archive:
                session.session_manager.history.append(
                    {
                        "role": "assistant",
                        "parts": ai_parts_archive,
                    }
                )

            session.session_manager.token_counts["input"] += response.input_tokens
            session.session_manager.token_counts["output"] += response.output_tokens
            session.session_manager.token_counts["total"] += response.total_tokens
            session.session_manager.token_counts["cached"] = (
                session.session_manager.token_counts.get("cached", 0)
                + getattr(response, "cached_tokens", 0)
            )
            session.session_manager.token_counts["reasoning"] = (
                session.session_manager.token_counts.get("reasoning", 0)
                + getattr(response, "reasoning_tokens", 0)
            )

            total_in += response.input_tokens
            total_out += response.output_tokens

            est_cost = calculate_cost(
                session.provider.model_name,
                response.input_tokens,
                response.output_tokens,
            )
            cost_str = ""
            if est_cost is not None:
                total_cost += est_cost
                session.session_manager.token_counts["total_cost"] += est_cost
                cost_str = (
                    f"| Est. Cost: ${est_cost:.5f} (Total: ${total_cost:.5f})"
                )

            if session.ui:
                session.ui.show_info(
                    f"Tokens: In {response.input_tokens} | Out {response.output_tokens} | Total {response.total_tokens} {cost_str}"
                )

            if not has_tool_call:
                if not has_text:
                    logger.warning("Assistant provided empty response. Nudging.")

                    nudge_msg = {
                        "role": "user",
                        "parts": [
                            {"type": "text", "text": NUDGE_EMPTY_RESPONSE}
                        ],
                    }
                    session.session_manager.history.append(nudge_msg)
                    messages = session._build_messages_from_history(
                        session._prepare_runtime_history(),
                        {"role": "system", "parts": []},
                    )[:-1]
                    continue

                if active_mode == "loop" and iteration < max_iterations:
                    if session._loop_blocker_raised:
                        # The agent already raised a blocker this
                        # turn — pausing intentionally. Don't poke
                        # it; let the loop finalize so the user can
                        # respond. Without this gate the watchdog
                        # would re-prompt every iteration, burning
                        # tokens while the model repeats the
                        # blocker message.
                        logger.info(
                            "Loop mode: blocker raised; skipping watchdog continue."
                        )
                        if session.ui:
                            session.ui.show_info(
                                "Loop paused — blocker raised. "
                                "Provide the requested input, set a new loop goal, or /mode default."
                            )
                        # Fall through to the normal finalize path
                        # below (no `continue`).
                    else:
                        logger.info(
                            "Loop mode watchdog: assistant stopped without tool calls; issuing autonomous continue nudge."
                        )
                        watchdog_msg = {
                            "role": "user",
                            "parts": [
                                {
                                    "type": "text",
                                    "text": (
                                        "LOOP WATCHDOG: Continue autonomous loop execution now. "
                                        "Re-plan the next increment, execute concrete actions with tools, "
                                        "verify outcomes, and proceed without waiting for user confirmation. "
                                        "Only pause if blocked, and if blocked call raise_blocker with exact unblock requirements."
                                    ),
                                }
                            ],
                        }
                        session.session_manager.history.append(watchdog_msg)
                        messages = session._build_messages_from_history(
                            session._prepare_runtime_history(),
                            {"role": "system", "parts": []},
                        )[:-1]
                        continue

                if session.ui:
                    session.ui.show_info(
                        f"Final session tokens: In {total_in} | Out {total_out} | Total {total_in + total_out} | Total Est. Cost: ${total_cost:.5f}"
                    )

                logger.info("Agentic loop finished (no tool calls).")

                if (
                    str(session.variables.get("agent_mode", "default")).lower()
                    == "feature"
                    and session.session_manager.get_feature_state()
                ):
                    session._set_feature_state()

                if session.variables.get("compact_history", False):
                    if session.ui:
                        session.ui.show_info(
                            "[dim]Compacting turn history (removing tool metadata)...[/dim]"
                        )
                        session.session_manager.compact_completed_turn()
                    logger.debug("History compacted.")

                session.session_manager.save_history(session.folder_context)
                # If a hook aborted during this final iteration, surface
                # that as the turn status — the abort fired, the user
                # should see why the loop stopped.
                final_status = (
                    "hook_aborted"
                    if session._hook_abort_requested
                    else "completed"
                )
                final_error = (
                    session._hook_abort_reason
                    if session._hook_abort_requested
                    else None
                )
                return session._collect_turn_response(
                    initial_history_len,
                    status=final_status,
                    total_in=total_in,
                    total_out=total_out,
                    total_cost=total_cost,
                    error=final_error,
                )

            strict_mode = session.variables.get("strict_mode", False)
            tool_result_parts = []
            tool_calls = [p for p in response.parts if p.type == "tool_call"]

            approval_plans = collect_approval_plans(
                tool_calls,
                session.folder_context,
                strict_mode=strict_mode,
                yolo=session.variables.get("yolo", False),
            )

            # Show bulk diffs if multiple
            if len(approval_plans) > 1:
                if session.ui:
                    session.ui.show_info(
                        f"\n[bold yellow]Turn contains {len(approval_plans)} modifications requiring approval.[/bold yellow]"
                    )
                for approval_plan in approval_plans.values():
                    for modification in approval_plan.modifications:
                        if modification.can_render_diff:
                            if session.ui:
                                session.ui.show_diff(
                                    modification.filename,
                                    modification.original_content,
                                    modification.modified_content,
                                )

            # --- PHASE 1: approval + decision (serial, in input order) ----
            # Walk every tool call once. For each, record either an
            # `early_result` (denied / preview-failed / etc.) OR mark it
            # `pending` so the parallel execution phase below will run it.
            pending_executions: list[int] = []  # indices to execute
            early_results: dict[int, Any] = {}  # i -> pre-resolved result string

            for i, part in enumerate(tool_calls):
                current_tool_name = part.tool_name
                current_tool_args = part.tool_args
                if session._track_tool_for_loop_detection(
                    part.tool_name, part.tool_args
                ):
                    iteration_tool_exact_fingerprints.append(
                        session._tool_call_fingerprint(part.tool_name, part.tool_args)
                    )
                    iteration_tool_pattern.append(
                        session._tool_call_fingerprint(
                            part.tool_name, part.tool_args, pattern_only=True
                        )
                    )
                approval_plan = approval_plans.get(i)
                needs_approval = approval_plan is not None
                if needs_approval:
                    result = None
                    auto_approved = bool(
                        session.variables.get("yolo", False)
                        and approval_plan.can_approve
                    )

                    if approval_plan.preview_error and session.ui:
                        for modification in approval_plan.modifications:
                            if modification.preview_error:
                                session.ui.show_error(
                                    f"Cannot show diff for {modification.filename}: {modification.preview_error}"
                                )
                                logger.error(
                                    f"Diff error for {modification.filename}: {modification.preview_error}"
                                )
                                break

                    if (
                        approval_plan.error_code == "preview_failed"
                        and approval_plan.preview_error
                    ):
                        if session.ui:
                            session.ui.show_info(
                                f"  [yellow]Auto-retrying malformed patch for {part.tool_name}...[/yellow]"
                            )
                        result = (
                            "Error: Malformed patch detected. Please ensure your diff is correctly "
                            f"formatted. Check hunk headers and context.\n{approval_plan.preview_error}"
                        )
                        logger.warning(
                            f"Malformed patch detected for {part.tool_name}: {approval_plan.preview_error}"
                        )

                    # Show diffs if not already shown in bulk pre-calculation
                    if result is None and not auto_approved and len(approval_plans) <= 1:
                        for modification in approval_plan.modifications:
                            if modification.can_render_diff:
                                if session.ui:
                                    session.ui.show_diff(
                                        modification.filename,
                                        modification.original_content,
                                        modification.modified_content,
                                    )

                    # Shorten args for display
                    display_args = _shorten_tool_args(part.tool_args)

                    # Add count info to prompt if multiple
                    count_info = (
                        f" ({i + 1}/{len(tool_calls)})"
                        if len(tool_calls) > 1
                        else ""
                    )

                    if result is None and not auto_approved:
                        choice, reason = session._request_tool_approval(
                            approval_plan=approval_plan,
                            display_args=display_args,
                            count_info=count_info,
                        )
                        if choice == "n":
                            result = "User denied this tool call."
                            logger.info(
                                f"Tool call {part.tool_name} denied by user."
                            )
                        elif choice == "e":
                            result = f"User denied this tool call. Reason: {reason}"
                            logger.info(
                                f"Tool call {part.tool_name} denied by user with explanation: {reason}"
                            )
                        else:
                            auto_approved = True  # user said yes — defer to exec phase

                    if result is not None:
                        early_results[i] = result
                    elif auto_approved:
                        pending_executions.append(i)
                else:
                    # No approval needed — defer to exec phase.
                    pending_executions.append(i)

            # --- PHASE 2: execute pending calls (parallel for safe tools, serial for others) ---
            exec_results: dict[int, Any] = {}
            if pending_executions:
                from mu.agent.parallel import (
                    PARALLEL_SAFE_TOOLS,
                    ToolCall as _ParTC,
                    execute_calls as _exec_calls,
                )

                parallel_indices: list[int] = []
                serial_indices: list[int] = []
                for i in pending_executions:
                    part = tool_calls[i]
                    # `flush` must be serial (it reads the collation
                    # buffer, which is populated by the post-processing
                    # phase below for each preceding call).
                    if part.tool_name == "flush":
                        serial_indices.append(i)
                    elif part.tool_name in PARALLEL_SAFE_TOOLS:
                        parallel_indices.append(i)
                    else:
                        serial_indices.append(i)

                # Parallel batch — preserves input-order results.
                if parallel_indices:
                    par_calls = [
                        _ParTC(
                            tool_name=tool_calls[i].tool_name,
                            tool_args=tool_calls[i].tool_args or {},
                            tool_call_id=str(i),
                            thought_signature=tool_calls[i].thought_signature,
                        )
                        for i in parallel_indices
                    ]
                    max_concurrency = max(
                        1,
                        int(
                            session.variables.get("parallel_tool_concurrency", 4) or 4
                        ),
                    )

                    # If two or more of the parallel calls are sub-agent
                    # spawns, replace the streaming per-call logs with a
                    # live progress panel. Hooked up via
                    # `session._subagent_progress`, which `spawn_agent` reads
                    # to register/update/close its row.
                    spawn_count = sum(
                        1
                        for i in parallel_indices
                        if tool_calls[i].tool_name == "spawn_agent"
                    )
                    live_progress_ctx = None
                    live = None
                    rich_console = getattr(session.ui, "console", None) if session.ui else None
                    if spawn_count >= 2 and rich_console is not None:
                        try:
                            from mu.ui.progress import SubagentProgressTracker
                            from rich.live import Live

                            tracker = SubagentProgressTracker()
                            session._subagent_progress = tracker
                            live = Live(
                                get_renderable=tracker.render_panel,
                                console=rich_console,
                                refresh_per_second=4,
                                transient=False,
                            )
                            live.start()
                            live_progress_ctx = tracker
                        except Exception as _exc:  # pragma: no cover — defensive
                            logger.debug(
                                "Live progress panel unavailable: %s", _exc
                            )
                            live = None

                    if len(par_calls) > 1 and session.ui and live is None:
                        # Only show the one-liner when we're NOT using the
                        # live panel (otherwise it pollutes the panel area).
                        session.ui.show_info(
                            f"⚡ Dispatching {len(par_calls)} tool call(s) in "
                            f"parallel (max_concurrency={max_concurrency})."
                        )

                    try:
                        par_results = _exec_calls(
                            par_calls,
                            lambda tc: session._execute_tool_with_memory(
                                tc.tool_name, tc.tool_args
                            ),
                            max_concurrency=max_concurrency,
                        )
                    finally:
                        if live is not None:
                            try:
                                live.stop()
                            except Exception:
                                pass
                        session._subagent_progress = None
                    for idx, pr in zip(parallel_indices, par_results):
                        if pr.error is not None:
                            logger.warning(
                                "Parallel tool %s raised %s",
                                tool_calls[idx].tool_name,
                                pr.error,
                            )
                            exec_results[idx] = f"Error: {pr.error}"
                        else:
                            exec_results[idx] = pr.result

                # Serial calls (executed in their original input order).
                for idx in serial_indices:
                    part = tool_calls[idx]
                    if part.tool_name == "flush":
                        # Flush is finalised inside the post-processing
                        # phase below so it can read the freshly written
                        # collation buffer. Placeholder result here.
                        exec_results[idx] = None
                        continue
                    exec_results[idx] = session._execute_tool_with_memory(
                        part.tool_name, part.tool_args
                    )

            # --- PHASE 3: post-processing (serial, in input order) -----------
            for i, part in enumerate(tool_calls):
                current_tool_name = part.tool_name
                current_tool_args = part.tool_args
                if i in early_results:
                    result = early_results[i]
                else:
                    result = exec_results.get(i)

                source_result = result
                raw_result = source_result
                logger.debug(
                    f"Tool result ({part.tool_name}): {_sanitize_for_log(raw_result)}"
                )
                # Surface retryable failures to the live UI with the
                # registered hint. The model already sees the structured
                # envelope in its next turn; this is for the human.
                session._announce_retryable_failure(part.tool_name, raw_result)
                # --- Collation Logic ---
                is_flush = part.tool_name == "flush"
                should_collate = (
                    part.tool_name in COLLATED_TOOLS
                    and session.variables.get("collation_enabled", True)
                    and len(tool_calls) > 1
                )

                if is_flush:
                    collated_data = session.collation_buffer.flush()
                    if not collated_data:
                        raw_result = "No data in collation buffer to flush."
                    else:
                        raw_result = "--- Flushed Context ---\n" + "\n\n".join(
                            collated_data
                        )
                    if session.ui:
                        session.ui.show_info(
                            f"  [Flushed {len(collated_data)} items from buffer]"
                        )
                elif should_collate:
                    # Don't collate if there was an error
                    if raw_result and not str(raw_result).startswith("Error"):
                        session.collation_buffer.add(
                            part.tool_name, part.tool_args, raw_result
                        )
                        count = len(session.collation_buffer.entries)
                        raw_result = (
                            f"Stored '{part.tool_name}' result in collation buffer. "
                            f"{count} item(s) currently pending. "
                            "Continue gathering or call 'flush' when ready to receive all context."
                        )
                    if session.ui and active_mode != "loop":
                        session.ui.show_info(f"  [Collated: {part.tool_name}]")
                    else:
                        # If it's an error, don't collate it, let the model see the error immediately
                        if session.ui:
                            session.ui.show_tool_result(
                                session._render_tool_result(raw_result)
                            )
                else:
                    if session.ui and active_mode != "loop":
                        session.ui.show_tool_result(
                            session._render_tool_result(raw_result)
                        )

                if session.ui and hasattr(session.ui, "emit_tool_trace"):
                    session.ui.emit_tool_trace(
                        part.tool_name,
                        part.tool_args,
                        source_result,
                        raw_result,
                    )

                # --- End Collation Logic ---
                if session.variables.get("structured_tool_results", True):
                    if raw_result != source_result:
                        _, unwrapped_source = session._unwrap_tool_envelope(
                            source_result
                        )
                        source_text = str(unwrapped_source)
                        result = session._build_structured_tool_result(
                            part.tool_name,
                            part.tool_args,
                            raw_result,
                            execution_source="session",
                        )
                        result["data"] = {
                            "collated": True,
                            "pending_items": len(session.collation_buffer.entries),
                            "source_char_count": len(source_text),
                            "source_line_count": len(source_text.splitlines()),
                        }
                        result["telemetry"].update(
                            {
                                "delivery_mode": "collated",
                                "visible_char_count": len(str(raw_result)),
                            }
                        )
                    else:
                        result = session._build_structured_tool_result(
                            part.tool_name,
                            part.tool_args,
                            source_result,
                            execution_source="session",
                        )
                else:
                    result = raw_result

                session._sync_feature_state_for_tool(
                    part.tool_name,
                    part.tool_args,
                    source_result,
                    result,
                )
                tool_result_parts.append(
                    {
                        "type": "tool_result",
                        "tool_name": part.tool_name,
                        "tool_result": result,
                        "thought_signature": part.thought_signature,
                    }
                )
                current_tool_name = None
                current_tool_args = None

            tool_result_msg = {"role": "tool", "parts": tool_result_parts}
            session.session_manager.history.append(tool_result_msg)
            session.session_manager.save_history(session.folder_context)

            if loop_detection_enabled and iteration_tool_exact_fingerprints:
                exact_seq = " -> ".join(iteration_tool_exact_fingerprints)
                pattern_seq = " -> ".join(iteration_tool_pattern)
                exact_tool_sequence_history.append(exact_seq)
                pattern_tool_sequence_history.append(pattern_seq)

                exact_loop_detected = session._is_repeated_tool_sequence(
                    exact_tool_sequence_history,
                    repeat_threshold=loop_detection_repeat_threshold,
                )
                pattern_loop_detected = session._is_repeated_tool_sequence(
                    pattern_tool_sequence_history,
                    repeat_threshold=loop_detection_repeat_threshold,
                )

                if exact_loop_detected or pattern_loop_detected:
                    loop_kind = "exact" if exact_loop_detected else "pattern"
                    warning_text = (
                        "Loop detection triggered: repeated tool-call sequence "
                        f"detected {loop_detection_repeat_threshold}x ({loop_kind})."
                    )
                    if session.ui:
                        session.ui.show_error(warning_text)
                    logger.warning(warning_text)
                    loop_break_msg = {
                        "role": "user",
                        "parts": [
                            {
                                "type": "text",
                                "text": (
                                    "LOOP DETECTED: You repeated the same tool-call sequence three times. "
                                    "You MUST break out now. Do NOT repeat this sequence again. "
                                    "Take a materially different action: apply a concrete code change, "
                                    "run a different validation path, or raise_blocker with exact missing requirements. "
                                    "Then explain what changed and why this breaks the loop."
                                ),
                            }
                        ],
                    }
                    session.session_manager.history.append(loop_break_msg)
                    session.session_manager.save_history(session.folder_context)
                    messages = session._build_messages_from_history(
                        session._prepare_runtime_history(turn_start_index),
                        {"role": "system", "parts": []},
                    )[:-1]
                    continue

            messages = session._build_messages_from_history(
                session._prepare_runtime_history(turn_start_index),
                {"role": "system", "parts": []},
            )[:-1]

        except _HookAbort as abort_exc:
            # A `pre_provider_call` hook aborted the turn. The flag was
            # already set in `_provider_generate_with_retry`; just close
            # the turn cleanly without surfacing an "API Error" banner.
            reason = abort_exc.reason or "Hook requested abort"
            logger.info(f"Agentic loop aborted by hook: {reason}")
            if session.session_manager.get_feature_state():
                session._set_feature_state(status="hook_aborted")
            return session._collect_turn_response(
                initial_history_len,
                status="hook_aborted",
                total_in=total_in,
                total_out=total_out,
                total_cost=total_cost,
                error=reason,
            )
        except KeyboardInterrupt:
            if session.ui:
                session.ui.show_info("\nAgentic loop interrupted by user.")
            logger.warning("Agentic loop interrupted by user.")
            session.paused_execution_text = str(text or "")
            session.session_manager.history.append(
                {
                    "role": "tool",
                    "parts": [
                        {
                            "type": "tool_result",
                            "tool_name": "system",
                            "tool_result": "User interrupted execution.",
                        }
                    ],
                }
            )
            session.session_manager.save_history(session.folder_context)
            if session.session_manager.get_feature_state():
                session._set_feature_state(status="interrupted")
            return session._collect_turn_response(
                initial_history_len,
                status="interrupted",
                total_in=total_in,
                total_out=total_out,
                total_cost=total_cost,
                error="User interrupted execution.",
            )
        except Exception as e:
            traceback_text = traceback.format_exc()
            tool_context = ""
            if current_tool_name:
                tool_context = (
                    f" | Last tool: {current_tool_name}("
                    f"{_shorten_tool_args(current_tool_args or {})})"
                )
            if session.ui:
                session.ui.show_error(f"API Error during agentic loop: {e}{tool_context}")
                session.ui.show_error(
                    "Traceback (most recent call last):\n"
                    + "\n".join(traceback_text.strip().splitlines()[-8:])
                )
            logger.error(f"Error in agentic loop: {e}", exc_info=True)

            status_code = session._extract_http_status_code(str(e).lower())
            if (
                not provider_bad_request_retried
                and not current_tool_name
                and status_code is not None
                and 400 <= status_code < 500
                and status_code not in {408, 409, 425, 429}
            ):
                provider_bad_request_retried = True
                session.session_manager.history = session.session_manager.history[:initial_history_len]
                session.session_manager.summary_anchor = min(
                    session.session_manager.summary_anchor,
                    len(session.session_manager.history),
                )
                session.session_manager.history.append(new_user_message)
                session.session_manager.save_history(session.folder_context)
                messages = session._build_messages_from_history(
                    session._prepare_runtime_history(),
                    new_user_message,
                )
                iteration -= 1
                if session.ui:
                    session.ui.show_info(
                        f"Provider returned HTTP {status_code}. Rolled back the current turn and retrying once."
                    )
                continue

            choice = session._provider_error_recovery_choice()
            if choice == "rollback_retry":
                session.session_manager.history = session.session_manager.history[: turn_start_index + 1]
                session.session_manager.summary_anchor = min(
                    session.session_manager.summary_anchor,
                    len(session.session_manager.history),
                )
                session.session_manager.save_history(session.folder_context)
                messages = session._build_messages_from_history(
                    session._prepare_runtime_history(turn_start_index),
                    {"role": "system", "parts": []},
                )[:-1]
                iteration -= 1
                continue
            if choice == "retry":
                iteration -= 1  # Decrement so the next loop run tries the same step
                continue

            session.session_manager.save_history(session.folder_context)
            if session.session_manager.get_feature_state():
                session._set_feature_state(status="error")
            return session._collect_turn_response(
                initial_history_len,
                status="error",
                total_in=total_in,
                total_out=total_out,
                total_cost=total_cost,
                error=f"{e}{tool_context}",
            )

    session.session_manager.save_history(session.folder_context)
    session.paused_execution_text = None
    if session.session_manager.get_feature_state():
        session._set_feature_state(status="max_iterations_reached")
    return session._collect_turn_response(
        initial_history_len,
        status="max_iterations_reached",
        total_in=total_in,
        total_out=total_out,
        total_cost=total_cost,
        error=(
            f"Reached maximum iterations ({max_iterations}) without a final "
            "assistant response."
        ),
    )
