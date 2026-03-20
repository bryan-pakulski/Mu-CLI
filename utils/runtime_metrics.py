def _max_int(value, fallback=1):
    return max(1, int(value or fallback))


def collect_runtime_metrics(session):
    hist_len = len(session.session_manager.history)
    anchor = session.session_manager.summary_anchor
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
        },
        "mode": {"name": str(session.variables.get("agent_mode", "default"))},
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
