"""What needs the operator, counted once for every surface (web sidebar badge, the
orchestration page, Android tab badge, the TUI's banner, toolbar and ``/orch``).

``counts(store)`` is the single definition. ``peek()`` reads it from the default
orchestration home *without* starting anything: it returns None when orchestration has
never been used on this machine (no store file), so a badge or a toolbar never creates
orchestration state or starts the scheduler.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

OFFLINE_AFTER_S = 90.0


def counts(store) -> Dict[str, Any]:
    proposed = store.list_runs(states=["proposed"], limit=500)
    paused = store.list_runs(states=["paused"], limit=500)
    in_review = store.list_versions(statuses=["in_review"])
    rb_review = store.list_runbook_versions(statuses=["in_review"])
    pending_nodes = store.list_nodes(statuses=["pending"])
    now = store.now()
    offline = [n for n in store.list_nodes(statuses=["active"])
               if n.last_seen is None or now - float(n.last_seen) > OFFLINE_AFTER_S]
    total = len(proposed) + len(paused) + len(in_review) + len(rb_review) + len(pending_nodes)
    return {"approvals": len(proposed), "paused_runs": len(paused), "reviews": len(in_review) + len(rb_review),
            "pending_nodes": len(pending_nodes), "offline_nodes": len(offline),
            "dispatch_paused": bool(store.get_setting("dispatch_paused", False)), "total": total}


def describe(att: Dict[str, Any]) -> str:
    """``"2 runs awaiting approval · 1 node awaiting acceptance"`` (empty when nothing waits)."""
    def n(count: int, one: str, many: str) -> str:
        return f"{count} {one if count == 1 else many}"
    parts = []
    if att.get("approvals"):
        parts.append(n(att["approvals"], "run awaiting approval", "runs awaiting approval"))
    if att.get("paused_runs"):
        parts.append(n(att["paused_runs"], "paused run", "paused runs"))
    if att.get("reviews"):
        parts.append(n(att["reviews"], "version in review", "versions in review"))
    if att.get("pending_nodes"):
        parts.append(n(att["pending_nodes"], "node awaiting acceptance", "nodes awaiting acceptance"))
    return " · ".join(parts)


_lock = threading.Lock()
_cache: Dict[str, Any] = {}


def peek(home: Optional[str] = None, *, max_age_s: float = 0.0) -> Optional[Dict[str, Any]]:
    """Attention counts for the orchestration home, or None when it has never been used.
    ``max_age_s`` reuses a recent answer (the TUI toolbar redraws often). Never raises."""
    from .store import OrchestrationStore, orchestration_home
    root = os.path.abspath(os.path.expanduser(home or orchestration_home()))
    if not os.path.exists(os.path.join(root, "orch.sqlite3")):
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(root)
        if hit and max_age_s and now - hit[0] < max_age_s:
            return dict(hit[1])
    try:
        result = counts(OrchestrationStore(root))
    except Exception:  # noqa: BLE001 - a badge must never break its host
        return None
    with _lock:
        _cache[root] = (now, result)
    return dict(result)
