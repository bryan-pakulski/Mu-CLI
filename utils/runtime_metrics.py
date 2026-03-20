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
        return {"state": feature_state, "plan": None}

    try:
        from core.feature_mode import refresh_and_persist_feature_plan, summarize_feature_plan

        plan = refresh_and_persist_feature_plan(
            directory,
            metadata_path=metadata_path or None,
        )
        return {
            "state": feature_state,
            "plan": summarize_feature_plan(plan),
        }
    except (FileNotFoundError, OSError, ValueError):
        return {"state": feature_state, "plan": None}


def _max_int(value, fallback=1):
    return max(1, int(value or fallback))


def collect_runtime_metrics(session):
    hist_len = len(session.session_manager.history)
    anchor = session.session_manager.summary_anchor
    if anchor > hist_len:
        anchor = 0
    active_turns = max(0, hist_len - anchor)
    context_limit = _max_int(getattr(session, "active_context_window", 1))

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
    collation_bytes = sum(len(result or "") for _, _, result in session.collation_buffer.entries)

    return {
        "ctx": {"current": active_turns, "maximum": context_limit},
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
    return " ".join(
        [
            f"yolo:{'on' if metrics['yolo']['enabled'] else 'off'}",
            build_inline_meter(
                "ctx", metrics["ctx"]["current"], metrics["ctx"]["maximum"]
            ),
            build_inline_meter(
                "mem", metrics["mem"]["current"], metrics["mem"]["maximum"]
            ),
            build_inline_meter(
                "scratch",
                metrics["scratch"]["current"],
                metrics["scratch"]["maximum"],
            ),
            build_inline_meter(
                "queue", metrics["queue"]["current"], metrics["queue"]["maximum"]
            ),
        ]
    )
