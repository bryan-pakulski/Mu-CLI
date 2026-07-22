"""Compact action records for compaction (spec #4/#5).

When conversation history is compacted, a tool-call/tool-result pair is
replaced by a *one-line action record* that preserves the decisions and
state the model needs to keep working — without replaying the full raw tool
output:

  * the decision: which tool + a short args digest
  * the outcome: a short status string (ok / error code)
  * state changes: modified_files
  * unresolved failures: error_code (when present)
  * validation: ok flag
  * the cache_key, so the full result is still retrievable via ``recall``

Dropped: superseded errors, repeated reads, successful logs, and the raw
output itself (it lives in the durable ResultStore / ToolResultCache and is
recoverable via the cache_key).

Used by the compaction renderers in ``mu/session/history.py`` and
``mu/session/messages.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _short_args(args: Any, max_chars: int = 80) -> str:
    """Compact args digest: sorted key=value pairs, truncated."""
    if not isinstance(args, dict) or not args:
        return ""
    try:
        parts = []
        for k in sorted(args.keys()):
            v = args[k]
            sv = str(v)
            if len(sv) > 24:
                sv = sv[:21] + "..."
            parts.append(f"{k}={sv}")
        s = " ".join(parts)
        return s[: max_chars - 1] + ("…" if len(s) >= max_chars else "")
    except Exception:  # noqa: BLE001
        return ""


def _extract_envelope(part: Dict[str, Any]) -> Dict[str, Any]:
    """Return the structured envelope dict if the tool_result part carries
    one, else an empty dict."""
    tr = part.get("tool_result")
    if isinstance(tr, dict):
        return tr
    return {}


def render_action_record(part: Dict[str, Any]) -> str:
    """Render a compact one-line action record for a ``tool_result`` part.

    Preserves tool+args (decision), ok/error_code (validation + unresolved
    failures), modified_files (state changes), summary (outcome), and
    cache_key (retrievability). Drops the raw output."""
    tool_name = part.get("tool_name", "tool")
    env = _extract_envelope(part)
    # The envelope's tool_name is authoritative (set by build_structured_tool_result).
    if env.get("tool_name"):
        tool_name = env["tool_name"]
    args = env.get("args") if env else None
    # The envelope's `args` is already shortened by _shorten_tool_args (a
    # string); use it directly. If absent, fall back to the part's raw
    # tool_args (a dict) and shorten it ourselves.
    if isinstance(args, str) and args:
        a = args[:80]
    elif isinstance(args, dict) and args:
        a = _short_args(args)
    else:
        a = _short_args(part.get("tool_args", {}))

    ok = env.get("ok")
    if ok is None:
        # No envelope → infer ok from absence of error_code.
        ok = env.get("error_code") is None
    error_code = env.get("error_code")
    summary = str(env.get("summary") or "").strip().replace("\n", " ")
    if len(summary) > 120:
        summary = summary[:117] + "..."

    modified_files = env.get("modified_files") or []
    if not modified_files:
        # Some parts stash modified files in data.
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        modified_files = data.get("modified_files") or []

    cache_key = part.get("cache_key")

    bits = [f"action: {tool_name}"]
    if a:
        bits.append(f"args={a}")
    bits.append(f"ok={'true' if ok else 'false'}")
    if error_code:
        bits.append(f"errors={error_code}")
    if modified_files:
        files = ",".join(str(f) for f in modified_files[:6])
        if len(modified_files) > 6:
            files += f",+{len(modified_files) - 6}"
        bits.append(f"files={files}")
    if summary:
        bits.append(f"outcome={summary}")
    if cache_key:
        # Preserve the [cache:KEY] tag format the summarizer prompt tells the
        # model to keep — so the full raw stays recallable via recall(KEY).
        bits.append(f"[cache:{cache_key}]")
    return "[" + " ".join(bits) + "]"


def is_action_record_eligible(part: Dict[str, Any]) -> bool:
    """True when a tool_result part should be rendered as an action record
    (it carries a cache_key OR a structured envelope) rather than as a prose
    clip. Parts without either keep the legacy prose rendering."""
    if part.get("cache_key"):
        return True
    return isinstance(part.get("tool_result"), dict)


__all__ = ["render_action_record", "is_action_record_eligible"]