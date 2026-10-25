"""Resume an orchestration chat when the runs it proposed finish.

An ``orchestration`` session proposes runs as ``ai:<session>``; most wait for the operator's
approval, so the agent ends its turn and waits. When such a run reaches a terminal state the
session is woken with a short synthetic message naming the run(s), so nobody has to type
"done" to continue.

Delivery is exactly-once per run: the ids handed to a session (or already read by the agent
through ``orch_run_status`` / ``orch_run_results``) are recorded in the ``ai_continuation``
store setting, and only runs that finished after the watermark set on first use count, so
runs from before this feature (or from an old process) never wake anything.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

from .states import RUN_TERMINAL

_logger = logging.getLogger(__name__)

SETTING = "ai_continuation"
MAX_DELIVERED = 500
MAX_AGE_S = 24 * 3600
_LOCK = threading.Lock()


def _state(store) -> Dict[str, Any]:
    data = store.get_setting(SETTING)
    if not isinstance(data, dict) or "watermark" not in data:
        data = {"watermark": store.now(), "delivered": {}}
        store.set_setting(SETTING, data)
    data.setdefault("delivered", {})
    return data


def _save(store, data: Dict[str, Any]) -> None:
    delivered = data.get("delivered") or {}
    if len(delivered) > MAX_DELIVERED:
        keep = sorted(delivered.items(), key=lambda kv: float(kv[1] or 0))[-MAX_DELIVERED:]
        data["delivered"] = dict(keep)
    store.set_setting(SETTING, data)


def session_of(run) -> str:
    """The proposing session's name, or "" for runs a person proposed."""
    who = str(getattr(run, "proposed_by", "") or "")
    return who[3:] if who.startswith("ai:") else ""


def mark_seen(store, runs: Iterable[Any]) -> None:
    """The agent has read these runs' outcome itself: never wake it for them."""
    finished = [r for r in runs if r is not None and r.state in RUN_TERMINAL and session_of(r)]
    if not finished:
        return
    with _LOCK:
        data = _state(store)
        for run in finished:
            data["delivered"][run.id] = float(run.finished_at or store.now())
        _save(store, data)


def pending(store) -> Dict[str, List[Any]]:
    """Finished AI-proposed runs not yet handed to their session, grouped by session."""
    with _LOCK:
        data = _state(store)
    watermark = float(data["watermark"] or 0)
    floor = max(watermark, store.now() - MAX_AGE_S)
    delivered = data["delivered"]
    out: Dict[str, List[Any]] = {}
    for run in store.list_runs(states=RUN_TERMINAL, limit=200):
        name = session_of(run)
        if not name or run.id in delivered:
            continue
        if float(run.finished_at or 0) <= floor:
            continue
        out.setdefault(name, []).append(run)
    for runs in out.values():
        runs.sort(key=lambda r: float(r.finished_at or 0))
    return out


def mark_delivered(store, runs: Iterable[Any]) -> None:
    mark_seen(store, runs)


def wake_text(runs: List[Any]) -> str:
    """The synthetic message that resumes the session."""
    lines = []
    for run in runs:
        summary = run.summary or {}
        counts = ", ".join(f"{k} {v}" for k, v in summary.items() if isinstance(v, (int, float)) and v)
        line = f"- {run.id} ({run.title or run.target_id}): {run.state}"
        if counts:
            line += f" [{counts}]"
        if run.reason:
            line += f" — {str(run.reason)[:200]}"
        lines.append(line)
    noun = "run you proposed has" if len(runs) == 1 else "runs you proposed have"
    return (f"[Automatic] The {noun} finished:\n" + "\n".join(lines) + "\n"
            "Read the outcome with orch_run_results (or orch_run_status for runs that did not run), "
            "then continue where you left off. If nothing is left to do, summarise the results briefly.")


class RunContinuation:
    """Poll for finished AI-proposed runs and hand each session its batch.

    ``orch_getter`` returns the live orchestrator or ``None`` (orchestration unused);
    ``handler(session_name, runs)`` resumes the session and returns ``True`` when the runs
    were delivered (or deliberately dropped), ``False`` to retry later (session busy).
    """

    def __init__(self, orch_getter: Callable[[], Any], handler: Callable[[str, List[Any]], bool],
                 *, interval: float = 3.0) -> None:
        self.orch_getter = orch_getter
        self.handler = handler
        self.interval = max(0.05, float(interval))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._inflight: set = set()
        self._inflight_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mucli-orch-continuation", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - never kill the loop
                _logger.debug("orchestration continuation poll failed", exc_info=True)

    def poll(self) -> List[threading.Thread]:
        """One pass: start a delivery thread per session with pending runs (tests call this)."""
        orch = self.orch_getter()
        if orch is None:
            return []
        started = []
        for name, runs in pending(orch.store).items():
            with self._inflight_lock:
                if name in self._inflight:
                    continue
                self._inflight.add(name)
            worker = threading.Thread(target=self._deliver, args=(orch, name, runs),
                                      name=f"mucli-orch-continue-{name}", daemon=True)
            worker.start()
            started.append(worker)
        return started

    def _deliver(self, orch, name: str, runs: List[Any]) -> None:
        try:
            # The agent may have read them itself between the poll and now.
            fresh = {r.id for r in pending(orch.store).get(name, [])}
            runs = [r for r in runs if r.id in fresh]
            if not runs:
                return
            try:
                done = bool(self.handler(name, runs))
            except Exception:  # noqa: BLE001
                _logger.exception("orchestration continuation for %s failed", name)
                done = True  # never loop on a broken session
            if done:
                mark_delivered(orch.store, runs)
        finally:
            with self._inflight_lock:
                self._inflight.discard(name)


__all__ = ["RunContinuation", "mark_delivered", "mark_seen", "pending", "session_of", "wake_text"]
