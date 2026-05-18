"""Loop-detection helpers for the agentic loop.

Three primitives:

  * `coarse_tool_args(tool_args)` — produce a stable, recursive,
    digest-friendly representation of tool arguments. Strings are
    hashed to a fixed-length SHA1 prefix so that fingerprints don't
    blow up on long content while still distinguishing different
    payloads.

  * `tool_call_fingerprint(name, args, pattern_only=False)` — combine
    `(name, args)` into a compact string token. With
    `pattern_only=True`, the args are first coarsened so the
    fingerprint groups *similar* calls (same shape, different content)
    together; without it, the fingerprint is exact.

  * `track_tool_for_loop_detection(name)` — boolean filter excluding
    bookkeeping tools that can legitimately repeat during a feature
    progression (`update_task_status`, `get_tasks`, ...).

  * `is_repeated_tool_sequence(history, threshold)` — true when the
    last `threshold` fingerprints in `history` are all identical and
    non-empty. The session's iteration loop calls this each turn to
    break out of stuck-in-a-rut patterns.

Tests: `tests/test_loop_detection.py`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List


# Tools whose repeated invocation during normal feature progression
# should NOT count toward loop detection (they're bookkeeping calls).
_BOOKKEEPING_TOOLS = frozenset(
    {
        "create_feature",
        "create_phases",
        "create_task",
        "update_task",
        "update_phases",
        "review_task",
        "review_all_completed_tasks",
        "review_completed_tasks",
        "update_task_status",
        "get_execution_state",
        "get_tasks",
        "get_current_task",
    }
)


def coarse_tool_args(tool_args: Any) -> Any:
    """Build a stable, coarse-grained representation of tool args for
    loop pattern checks. Strings are SHA1-prefixed to keep the result
    bounded; ints/floats/bools/None pass through; nested dicts/lists
    recurse; unknown types collapse to their type name.

    The output is JSON-serializable, suitable for feeding into
    `tool_call_fingerprint(..., pattern_only=True)`.
    """
    if isinstance(tool_args, dict):
        coarse = {}
        for key in sorted(tool_args.keys()):
            val = tool_args.get(key)
            if isinstance(val, str):
                coarse[key] = (
                    f"str:{hashlib.sha1(val.encode('utf-8')).hexdigest()[:10]}"
                )
            elif isinstance(val, (int, float, bool)) or val is None:
                coarse[key] = val
            elif isinstance(val, list):
                coarse[key] = [coarse_tool_args(item) for item in val[:8]]
            elif isinstance(val, dict):
                coarse[key] = coarse_tool_args(val)
            else:
                coarse[key] = type(val).__name__
        return coarse
    if isinstance(tool_args, list):
        return [coarse_tool_args(item) for item in tool_args[:8]]
    if isinstance(tool_args, str):
        return f"str:{hashlib.sha1(tool_args.encode('utf-8')).hexdigest()[:10]}"
    if isinstance(tool_args, (int, float, bool)) or tool_args is None:
        return tool_args
    return type(tool_args).__name__


def tool_call_fingerprint(
    tool_name: str, tool_args: Any, *, pattern_only: bool = False
) -> str:
    """Compact fingerprint of a `(name, args)` tool call. The exact
    variant uses the raw args; the `pattern_only` variant first
    coarsens via `coarse_tool_args` so that two calls with different
    string content but the same shape collide on the same fingerprint."""
    name = str(tool_name or "").strip().lower() or "tool"
    payload_source = (
        coarse_tool_args(tool_args or {}) if pattern_only else (tool_args or {})
    )
    try:
        payload = json.dumps(
            payload_source,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        payload = str(payload_source)
    digest = hashlib.sha1(f"{name}|{payload}".encode("utf-8")).hexdigest()[:12]
    return f"{name}:{digest}" if not pattern_only else f"{name}~{digest}"


def track_tool_for_loop_detection(tool_name: str, tool_args: Any = None) -> bool:
    """Return False for bookkeeping tools (feature-mode mutators, task
    inspectors) that legitimately repeat across iterations. The agent
    loop uses this to filter `tool_args` before adding to the
    fingerprint history."""
    name = str(tool_name or "").strip().lower()
    return name not in _BOOKKEEPING_TOOLS


def is_repeated_tool_sequence(
    sequence_history: List[str], repeat_threshold: int = 3
) -> bool:
    """True when the last `repeat_threshold` entries of
    `sequence_history` are all identical and non-empty — i.e. the
    agent has been firing the same tool call repeatedly and is
    probably stuck."""
    if len(sequence_history) < repeat_threshold:
        return False
    tail = sequence_history[-repeat_threshold:]
    if not all(tail):
        return False
    return len(set(tail)) == 1


__all__ = [
    "coarse_tool_args",
    "tool_call_fingerprint",
    "track_tool_for_loop_detection",
    "is_repeated_tool_sequence",
]
