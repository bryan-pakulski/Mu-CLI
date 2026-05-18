import glob
import json
import os
import time

from utils.config import HISTORY_DIR


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
        from mu.feature.engine import (
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
        started_at = float(feature_state.get("started_at", 0) or 0)
        elapsed_seconds = max(0, int(time.time() - started_at)) if started_at else 0
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
            "cached": int(session.session_manager.token_counts.get("cached", 0) or 0),
            "reasoning": int(
                session.session_manager.token_counts.get("reasoning", 0) or 0
            ),
            "total_cost": float(
                session.session_manager.token_counts.get("total_cost", 0.0) or 0.0
            ),
        },
        "mode": {"name": str(session.variables.get("agent_mode", "default"))},
        "yolo": {"enabled": bool(session.variables.get("yolo", False))},
        "plan": {"enabled": bool(session.variables.get("plan_mode", False))},
        "feature": collect_feature_progress(session),
    }


def _chars_to_tokens(text: str, model: str) -> int:
    """Convert a char-budgeted text body into a token count using the
    same estimator that drives the compactor."""
    if not text:
        return 0
    from utils.token_estimator import estimate_tokens

    return estimate_tokens(text, model)


def _current_time_prelude() -> str:
    """A one-line time-awareness banner prepended to every system prompt.

    Without this the model has to guess at "is this commit recent?" /
    "schedule X for next Tuesday" / "the doc says deprecated as of
    2024-12 — are we past that?" type questions. ~25 tokens of overhead
    per turn for genuine grounding.
    """
    import datetime

    now = datetime.datetime.now().astimezone()
    return (
        f"Current date/time: {now.strftime('%Y-%m-%d %H:%M %Z')} "
        f"(weekday: {now.strftime('%A')})."
    )


def compose_base_system_prompt(session) -> str:
    """Reconstruct the base system prompt the provider receives —
    everything *before* the LAYER 1+ blocks get appended.

    Mirrors the composition in `Session.send_message`:
      * a one-line current-date/time prelude (so the model isn't
        guessing at the wall clock)
      * `system_instruction` (user-set persona/role)
      * feature/loop mode prefixes when those modes are active
      * the agentic harness base + mode workflow text (when agentic
        mode is on — adds ~3–5k tokens that previously hid from the
        per-layer table)

    This lets L0 in `/memory` reflect the *real* system-prompt cost
    instead of just the user's persona string.
    """
    if session is None:
        return ""
    parts: list = [_current_time_prelude()]
    base = str(getattr(session, "system_instruction", "") or "")
    if base:
        parts.append(base)

    variables = getattr(session, "variables", {}) or {}
    active_mode = str(variables.get("agent_mode", "default") or "default").lower()
    if active_mode == "feature":
        parts.append("[FEATURE MODE SYSTEM PROMPT — Feature Plan Engine instructions]")
    elif active_mode == "loop":
        loop_goal = str(variables.get("loop_goal", "") or "").strip()
        parts.append(
            "[LOOP MODE SYSTEM PROMPT — Long-Horizon Loop instructions]"
            + (f"\nLocked loop goal: {loop_goal}" if loop_goal else "")
        )

    # The agentic harness adds AGENTIC_SYSTEM_BASE + the current mode's
    # workflow prompt to every turn (see Session.send_message). That's
    # the bulk of L0 — without it the table under-counts by thousands
    # of tokens.
    agentic = bool(getattr(session, "agentic", False))
    if agentic:
        try:
            from utils.config import AGENTIC_MODES, AGENTIC_SYSTEM_BASE

            agentic_base = str(
                variables.get("agentic_system_base_override", AGENTIC_SYSTEM_BASE)
                or AGENTIC_SYSTEM_BASE
            )
            default_mode = AGENTIC_MODES.get(active_mode, AGENTIC_MODES.get("default", ""))
            mode_instruction = str(
                variables.get(f"agentic_mode_prompt_{active_mode}", default_mode)
                or default_mode
            )
            parts.append(
                f"{agentic_base}\n\n### CURRENT STRATEGY MODE: "
                f"{active_mode.upper()}\n{mode_instruction}"
            )
        except Exception:
            pass

    return "\n\n".join(p for p in parts if p)


def _budget_chars_to_tokens(char_budget: int) -> int:
    """Approximate a char-budget cap as a token cap. Used only for the
    UI-side denominator on layers whose user-facing limit is configured
    in chars (workspace files, skills, summary, goal, tool activity,
    retrieval). Conservative: 1 token ≈ 4 chars, rounded down."""
    return max(1, int(char_budget) // 4)


def estimate_non_l5_context_tokens(session) -> int:
    """Token count of all non-history layers (L1, L1B, L2, L3, L4, L4B).

    Used by the compactor to subtract from the global cap before
    deciding how much room L5 (conversation history) actually has.
    Cheap to call — the underlying `_build_*` methods on Session are
    backed by per-turn caching where it matters.
    """
    layers = collect_context_layers(session)
    return sum(int(layer["current"] or 0) for layer in layers if layer["layer"] != "L5")


def estimate_active_context_tokens(session) -> int:
    """Token count of the entire active context — every layer summed.

    This is what the user actually pays for on each turn. Matches the
    number rendered in the splash banner and `/memory`'s "Total" row.
    """
    layers = collect_context_layers(session)
    return sum(int(layer["current"] or 0) for layer in layers)


def collect_context_layers(session):
    """Per-layer breakdown of the active context, in **tokens**.

    Every layer matches the system-prompt assembly in
    `core/session.py:_inject_hierarchical_context` so the table in
    `/memory` reflects the real prompt cost (not just a slice of it).
    The estimator (`utils.token_estimator.estimate_tokens`) is the
    same one the compactor uses, so the numbers here, the splash
    banner, and the trim-trigger all agree.
    """
    model = ""
    try:
        provider_config = getattr(session.session_manager, "provider_config", None) or {}
        model = str(provider_config.get("model") or "")
    except Exception:
        model = ""

    # --- char-budgeted layers (the variable schema names them in chars) ---
    workspace_limit_chars = max(
        1, int(session.variables.get("workspace_context_max_chars", 8192) or 8192)
    )
    skills_limit_chars = max(
        1, int(session.variables.get("skills_max_chars", 6144) or 6144)
    )
    summary_limit_chars = max(
        1, int(session.variables.get("conversation_summary_char_limit", 8000) or 8000)
    )
    goal_limit_chars = max(
        1, int(session.variables.get("active_goal_context_char_limit", 4000) or 4000)
    )
    tool_limit_chars = max(
        1, int(session.variables.get("recent_tool_context_char_limit", 12000) or 12000)
    )
    retrieval_limit_chars = max(
        1, int(session.variables.get("retrieval_context_char_limit", 5000) or 5000)
    )

    # --- materialize the layer bodies the same way the prompt builder does ---
    try:
        workspace_text = str(session._build_workspace_context_files() or "")
    except Exception:
        workspace_text = ""
    try:
        skills_text = str(session._build_skills_block() or "")
    except Exception:
        skills_text = ""
    summary_text = str(getattr(session.session_manager, "conversation_summary", "") or "")
    try:
        goal_text = str(session._build_active_goal_context() or "")
    except Exception:
        goal_text = ""
    try:
        tool_text = str(session._build_recent_tool_context(max_chars=tool_limit_chars) or "")
    except Exception:
        tool_text = ""
    retrieved_text = str(getattr(session, "_pending_retrieved_context", "") or "")

    # --- L5 / history: total tokens for everything *not* covered by the
    # other layers — i.e. the conversation messages themselves.
    history_tokens = 0
    try:
        history_tokens = int(
            session.session_manager.estimate_runtime_history_tokens() or 0
        )
    except Exception:
        history_tokens = 0

    history_limit_tokens = max(
        1, int(session.variables.get("context_token_limit", 256000) or 256000)
    )

    # L0 — base system prompt (persona + agentic harness + mode workflow).
    # Not user-budgeted — its cap is the global context limit.
    system_prompt_text = compose_base_system_prompt(session)

    layers = [
        {
            "layer": "L0",
            "name": "System prompt",
            "current": _chars_to_tokens(system_prompt_text, model),
            "maximum": history_limit_tokens,
            "description": "Base system prompt — persona + agentic harness + mode workflow.",
        },
        {
            "layer": "L1",
            "name": "Workspace files",
            "current": _chars_to_tokens(workspace_text, model),
            "maximum": _budget_chars_to_tokens(workspace_limit_chars),
            "description": "AGENTS.md / CLAUDE.md / .mu/CONTEXT.md per attached folder.",
        },
        {
            "layer": "L1B",
            "name": "Installed skills",
            "current": _chars_to_tokens(skills_text, model),
            "maximum": _budget_chars_to_tokens(skills_limit_chars),
            "description": "Compact index + auto-expanded skill bodies.",
        },
        {
            "layer": "L2",
            "name": "Conversation summary",
            "current": _chars_to_tokens(summary_text, model),
            "maximum": _budget_chars_to_tokens(summary_limit_chars),
            "description": "Long-horizon continuity summary.",
        },
        {
            "layer": "L3",
            "name": "Active goal",
            "current": _chars_to_tokens(goal_text, model),
            "maximum": _budget_chars_to_tokens(goal_limit_chars),
            "description": "Feature/task status + scratchpad snapshot.",
        },
        {
            "layer": "L4",
            "name": "Recent tool activity",
            "current": _chars_to_tokens(tool_text, model),
            "maximum": _budget_chars_to_tokens(tool_limit_chars),
            "description": "Compressed recent tool calls/results.",
        },
        {
            "layer": "L4B",
            "name": "Retrieved snippets",
            "current": _chars_to_tokens(retrieved_text, model),
            "maximum": _budget_chars_to_tokens(retrieval_limit_chars),
            "description": "Semantic workspace retrieval context.",
        },
        {
            "layer": "L5",
            "name": "Conversation history",
            "current": history_tokens,
            "maximum": history_limit_tokens,
            "description": "All non-summarized messages sent to the provider.",
        },
    ]
    return layers


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
    parts = []
    # Plan mode comes first so its presence is unmissable when active.
    if metrics["plan"]["enabled"]:
        parts.append("🔒 PLAN")
    parts += [
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
