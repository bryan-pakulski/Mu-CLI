import glob
import json
import os
import time

from utils.config import AGENTIC_MODES, AGENTIC_SYSTEM_BASE, HISTORY_DIR

FEATURE_ACTIVE_STATUSES = {"running", "review"}


def feature_elapsed_thinking_seconds(feature_state: dict | None) -> int:
    if not isinstance(feature_state, dict):
        return 0
    accumulated = float(feature_state.get("thinking_seconds_accumulated", 0.0) or 0.0)
    status = str(feature_state.get("status", "") or "").strip().lower()
    started_at = float(feature_state.get("thinking_started_at", 0.0) or 0.0)
    if status in FEATURE_ACTIVE_STATUSES and started_at > 0:
        accumulated += max(0.0, time.time() - started_at)
    return max(0, int(accumulated))


def collect_feature_progress(session):
    feature_state = None
    session_manager = getattr(session, "session_manager", None)
    if session_manager and hasattr(session_manager, "get_feature_state"):
        feature_state = session_manager.get_feature_state()
    elif hasattr(session, "feature_state"):
        feature_state = getattr(session, "feature_state")

    if not isinstance(feature_state, dict):
        return None

    directory = str(feature_state.get("directory", "") or "").strip()
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not directory:
        return {"state": feature_state, "plan": None, "progress": None}

    try:
        from core.feature_mode import (
            refresh_and_persist_feature_plan,
            summarize_feature_plan,
        )

        if not metadata_path and directory:
            for candidate in glob.glob(
                os.path.join(HISTORY_DIR, "sessions", "*", "features", "*.json")
            ):
                try:
                    with open(candidate, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if str(data.get("directory", "")).strip() == directory:
                        metadata_path = candidate
                        break
                except (OSError, json.JSONDecodeError):
                    continue

        if metadata_path:
            plan = refresh_and_persist_feature_plan(
                getattr(session.session_manager, "current_session_name", directory),
                metadata_path=metadata_path,
            )
        else:
            plan = refresh_and_persist_feature_plan(directory)

        summary = summarize_feature_plan(plan)
        tasks = summary.get("phases", [])
        completed_tasks = sum(
            1 for task in tasks if str(task.get("status", "")) == "completed"
        )
        elapsed_seconds = feature_elapsed_thinking_seconds(feature_state)
        start_tokens = int(feature_state.get("start_tokens", 0) or 0)
        token_total = int(session.session_manager.token_counts.get("total", 0) or 0)
        token_delta = max(0, token_total - start_tokens)

        return {
            "state": feature_state,
            "plan": summary,
            "progress": {
                "completed_tasks": completed_tasks,
                "total_tasks": len(tasks),
                "next_phase": summary.get("next_task") or summary.get("next_phase"),
                "elapsed_seconds": elapsed_seconds,
                "token_delta": token_delta,
            },
        }
    except (FileNotFoundError, OSError, ValueError):
        return {"state": feature_state, "plan": None, "progress": None}


def _max_int(value, fallback=1):
    return max(1, int(value or fallback))


def _build_l1_directives_text(session) -> str:
    base_text = str(getattr(session, "system_instruction", "") or "")
    variables = getattr(session, "variables", {}) or {}
    agent_mode = str(variables.get("agent_mode", "default") or "default").strip().lower()
    default_mode_instruction = str(
        AGENTIC_MODES.get(agent_mode, AGENTIC_MODES.get("default", "")) or ""
    ).strip()
    mode_instruction = str(
        variables.get(f"agentic_mode_prompt_{agent_mode}", default_mode_instruction)
        or default_mode_instruction
    ).strip()
    if not mode_instruction:
        return base_text

    agentic_system_base = str(
        variables.get("agentic_system_base_override", AGENTIC_SYSTEM_BASE)
        or AGENTIC_SYSTEM_BASE
    ).strip()
    strategy_block = (
        f"{agentic_system_base}\n\n### CURRENT STRATEGY MODE: {agent_mode.upper()}\n{mode_instruction}".strip()
    )
    if strategy_block and strategy_block not in base_text:
        return f"{base_text}\n\n{strategy_block}".strip()
    return base_text


def collect_runtime_metrics(session):
    hist_len = len(session.session_manager.history)
    anchor = session.session_manager.summary_anchor
    if anchor > hist_len:
        anchor = 0
    active_turns = max(0, hist_len - anchor)
    context_limit = _max_int(session.variables.get("context_token_limit", 256000))
    if hasattr(session.session_manager, "estimate_runtime_history_tokens"):
        context_tokens = int(session.session_manager.estimate_runtime_history_tokens() or 0)
    else:
        serialized = json.dumps(session.session_manager.history, default=str)
        context_tokens = max(0, int(len(serialized) / 4))

    memory_limit = _max_int(
        session.variables.get(
            "memory_max_entries", getattr(session.task_memory, "max_entries", 1)
        )
    )
    scratch_limit = _max_int(
        session.variables.get(
            "scratchpad_max_entries", getattr(session.turn_scratchpad, "max_entries", 1)
        )
    )
    collation_limit = _max_int(getattr(session.collation_buffer, "max_bytes", 1))
    collation_bytes = sum(
        len(result or "") for _, _, result in session.collation_buffer.entries
    )

    return {
        "ctx": {"current": context_tokens, "maximum": context_limit},
        "ctx_turns": {"current": active_turns, "maximum": max(1, hist_len)},
        "mem": {
            "current": len(session.task_memory.entries),
            "maximum": memory_limit,
        },
        "scratch": {
            "current": len(session.turn_scratchpad.entries),
            "maximum": scratch_limit,
        },
        "queue": {
            "current": collation_bytes,
            "maximum": collation_limit,
        },
        "queue_items": len(session.collation_buffer.entries),
        "tokens": {
            "input": int(session.session_manager.token_counts.get("input", 0) or 0),
            "output": int(session.session_manager.token_counts.get("output", 0) or 0),
            "total": int(session.session_manager.token_counts.get("total", 0) or 0),
            "total_cost": float(
                session.session_manager.token_counts.get("total_cost", 0.0) or 0.0
            ),
        },
        "mode": {"name": str(session.variables.get("agent_mode", "default"))},
        "yolo": {"enabled": bool(session.variables.get("yolo", False))},
        "feature": collect_feature_progress(session),
    }


def collect_context_layers(session):
    system_limit = max(
        1, int(session.variables.get("system_prompt_char_limit", 12000) or 12000)
    )
    summary_limit = max(
        1, int(session.variables.get("conversation_summary_char_limit", 8000) or 8000)
    )
    tool_limit = max(
        1, int(session.variables.get("recent_tool_context_char_limit", 12000) or 12000)
    )
    retrieval_limit = max(
        1, int(session.variables.get("retrieval_context_char_limit", 5000) or 5000)
    )
    goal_limit = max(
        1, int(session.variables.get("active_goal_context_char_limit", 4000) or 4000)
    )
    summary_text = str(getattr(session.session_manager, "conversation_summary", "") or "")
    system_text = _build_l1_directives_text(session)
    goal_text = str(session._build_active_goal_context() or "")
    tool_text = str(session._build_recent_tool_context(max_chars=tool_limit) or "")
    retrieved_text = str(getattr(session, "_pending_retrieved_context", "") or "")
    current_turn = ""
    if session.session_manager.history:
        current_turn = json.dumps(session.session_manager.history[-1], default=str)

    layers = [
        {
            "layer": "L1",
            "name": "System directives",
            "current": min(len(system_text), system_limit),
            "maximum": system_limit,
            "description": "Primary system prompt and mode directives.",
        },
        {
            "layer": "L2",
            "name": "Conversation summary",
            "current": min(len(summary_text), summary_limit),
            "maximum": summary_limit,
            "description": "Long-horizon continuity summary.",
        },
        {
            "layer": "L3",
            "name": "Active goal",
            "current": min(len(goal_text), goal_limit),
            "maximum": goal_limit,
            "description": "Feature/task status + scratchpad snapshot.",
        },
        {
            "layer": "L4",
            "name": "Recent tool activity",
            "current": min(len(tool_text), tool_limit),
            "maximum": tool_limit,
            "description": "Compressed recent tool calls/results.",
        },
        {
            "layer": "L4B",
            "name": "Retrieved snippets",
            "current": min(len(retrieved_text), retrieval_limit),
            "maximum": retrieval_limit,
            "description": "Semantic workspace retrieval context.",
        },
        {
            "layer": "L5",
            "name": "Current turn",
            "current": len(current_turn),
            "maximum": max(1, int(session.variables.get("context_token_limit", 256000) or 256000)),
            "description": "Live request/response turn payload.",
        },
    ]
    return layers


def collect_context_layer_contents(session) -> dict[str, str]:
    summary_text = str(getattr(session.session_manager, "conversation_summary", "") or "")
    system_text = _build_l1_directives_text(session)
    goal_text = str(session._build_active_goal_context() or "")
    tool_limit = max(
        1, int(session.variables.get("recent_tool_context_char_limit", 12000) or 12000)
    )
    tool_text = str(session._build_recent_tool_context(max_chars=tool_limit) or "")
    retrieval_limit = max(
        1, int(session.variables.get("retrieval_context_char_limit", 5000) or 5000)
    )
    retrieved_text = str(getattr(session, "_pending_retrieved_context", "") or "")
    if len(retrieved_text) > retrieval_limit:
        retrieved_text = retrieved_text[:retrieval_limit].rstrip()
    current_turn = ""
    if session.session_manager.history:
        current_turn = json.dumps(session.session_manager.history[-1], default=str, indent=2)
    return {
        "L1": system_text,
        "L2": summary_text,
        "L3": goal_text,
        "L4": tool_text,
        "L4B": retrieved_text,
        "L5": current_turn,
    }


def build_inline_meter(label, current, maximum, width=8):
    maximum = _max_int(maximum)
    current = max(0, int(current or 0))
    ratio = min(current / maximum, 1.0)
    percent = int(round(ratio * 100))
    filled = min(width, int(round(width * ratio)))
    bar = "█" * filled + " " * (width - filled)
    return f"{label}:{percent:>3}% [{bar}] {current}/{maximum}"


def build_live_status_line(session):
    metrics = collect_runtime_metrics(session)
    parts = [
        f"yolo:{'on' if metrics['yolo']['enabled'] else 'off'}",
        build_inline_meter("ctx", metrics["ctx"]["current"], metrics["ctx"]["maximum"]),
        build_inline_meter("mem", metrics["mem"]["current"], metrics["mem"]["maximum"]),
        build_inline_meter(
            "scratch",
            metrics["scratch"]["current"],
            metrics["scratch"]["maximum"],
        ),
        build_inline_meter("queue", metrics["queue"]["current"], metrics["queue"]["maximum"]),
    ]

    feature_metrics = metrics.get("feature") or {}
    progress = feature_metrics.get("progress")
    if isinstance(progress, dict):
        total_tasks = max(1, int(progress.get("total_tasks", 1) or 1))
        completed_tasks = max(0, int(progress.get("completed_tasks", 0) or 0))
        overall_pct = int(round((completed_tasks / total_tasks) * 100))

        next_phase = progress.get("next_phase")
        phase_pct = 0
        if isinstance(next_phase, dict):
            counts = next_phase.get("task_counts", {}) if isinstance(next_phase.get("task_counts"), dict) else {}
            done = int(counts.get("completed", 0) or 0)
            total = int(
                (counts.get("completed", 0) or 0)
                + (counts.get("in_progress", 0) or 0)
                + (counts.get("not_started", 0) or 0)
            )
            phase_pct = int(round((done / max(1, total)) * 100))
        elif total_tasks > 0 and completed_tasks >= total_tasks:
            phase_pct = 100

        parts.extend([f"P:{phase_pct:>3}%", f"O:{overall_pct:>3}%"])

    return " ".join(parts)
