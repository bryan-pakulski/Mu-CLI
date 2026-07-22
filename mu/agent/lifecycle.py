"""Adaptive sub-agent lifecycle management.

``SubagentLifecycleManager`` replaces the old hardcoded
``max_iterations - 3`` wrap-up cutoff with signal-driven heuristics.
One instance is attached to each spawned child Session
(``child._subagent_lifecycle``) and fed tool calls from the child's
agentic loop (see ``loop_body._run_turn`` Phase 3). It tracks:

  * **tool count** — total tool calls issued.
  * **tool diversity** — distinct tool names used (a child calling one
    tool repeatedly is a stuck signal).
  * **stuck** — the same tool + same arguments hash was called
    ``subagent_stuck_threshold`` times in a row.
  * **stall** — the last ``subagent_stall_threshold`` calls produced no
    novel output (empty / error / an already-seen result fingerprint).
  * **runtime** — a watchdog daemon that auto-kills a runaway child
    after ``subagent_max_runtime_seconds``. This is the ONLY auto-kill
    path; stuck/stall are advisory and surfaced to the parent via
    ``poll_subagent`` so the orchestrator decides.

Thread-safety: every state mutation takes ``self._lock``. The
``on_signal`` callback (wired by ``SubagentRegistry`` to push stuck/stall
state into the live progress tracker) is invoked under the lock so the
tracker never observes a half-updated snapshot.

The cooperative kill mechanism reuses the existing ``_hook_abort_requested``
pattern: setting ``session._subagent_cancelled`` (a plain bool read once
per loop iteration — GIL-safe) makes the child's ``run_turn`` exit cleanly
with ``status="killed"`` at the next iteration boundary, after which the
registry's thread wrapper captures partial results via
``_extract_partial_summary``.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Dict, Optional


class SubagentLifecycleManager:
    """Per-child progress signal tracker + stuck/stall/runtime detector."""

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None) -> None:
        t = thresholds or {}
        self.stuck_threshold = max(1, int(t.get("stuck_threshold", 3) or 3))
        self.stall_threshold = max(1, int(t.get("stall_threshold", 5) or 5))
        self.max_runtime_seconds = int(t.get("max_runtime_seconds", 300) or 300)
        self.enabled = bool(t.get("enabled", True))

        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._finished_at: Optional[float] = None
        self._tool_count = 0
        self._tool_names: list[str] = []
        self._distinct_tools: set[str] = set()
        self._last_tool: Optional[str] = None
        self._last_args_fingerprint: Optional[str] = None
        self._consecutive_repeats = 0  # same tool + same args hash in a row
        self._consecutive_stalls = 0  # non-novel results in a row
        self._seen_results: set[str] = set()  # result fingerprints (bounded)
        self._stuck = False
        self._stall = False
        self._kill_reason: Optional[str] = None
        self._done = threading.Event()  # set when child finished / cancelled

        # Wired by SubagentRegistry: called when stuck/stall state changes so
        # the live progress tracker can render it. Signature: cb(self).
        self.on_signal: Optional[Callable[["SubagentLifecycleManager"], None]] = None

    # ----------------------------------------------------------- recording

    @staticmethod
    def _args_fingerprint(tool_name: str, tool_args: Any) -> str:
        try:
            import json as _json

            blob = _json.dumps(tool_args, sort_keys=True, default=str)
        except Exception:
            blob = repr(tool_args)
        return hashlib.md5(f"{tool_name}|{blob}".encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _result_fingerprint(raw_result: Any) -> str:
        text = str(raw_result or "")
        if not text:
            return ""
        # Bound the hash input so a giant result doesn't dominate.
        return hashlib.md5(text[:1024].encode("utf-8", "replace")).hexdigest()

    def _is_novel(self, raw_result: Any) -> bool:
        text = str(raw_result or "")
        if not text or text.startswith("Error"):
            return False
        fp = self._result_fingerprint(raw_result)
        if not fp:
            return False
        if fp in self._seen_results:
            return False
        # Bound the seen-set so a chatty child can't grow it forever.
        if len(self._seen_results) < 4096:
            self._seen_results.add(fp)
        return True

    def record_tool_call(
        self,
        tool_name: str,
        tool_args: Any,
        raw_result: Any,
        *,
        cache_hit: bool = False,
    ) -> None:
        """Record one tool call + its result and recompute stuck/stall.

        ``cache_hit`` marks a result served from the tool-result cache
        (auto-recall or the read-dedup marker) — such a call produced no
        novel output by definition, so it counts as a stall regardless of
        the marker text (which differs from the original content)."""
        if not self.enabled or self._done.is_set():
            return
        with self._lock:
            self._tool_count += 1
            self._tool_names.append(tool_name)
            self._distinct_tools.add(tool_name)
            self._last_tool = tool_name

            fp = self._args_fingerprint(tool_name, tool_args)
            if fp == self._last_args_fingerprint:
                self._consecutive_repeats += 1
            else:
                self._consecutive_repeats = 1
                self._last_args_fingerprint = fp

            novel = False if cache_hit else self._is_novel(raw_result)
            if novel:
                self._consecutive_stalls = 0
            else:
                self._consecutive_stalls += 1

            prev_stuck = self._stuck
            prev_stall = self._stall
            self._stuck = self._consecutive_repeats >= self.stuck_threshold
            self._stall = self._consecutive_stalls >= self.stall_threshold

            changed = (self._stuck != prev_stuck) or (self._stall != prev_stall)
            cb = self.on_signal
        if changed and cb is not None:
            try:
                cb(self)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------- introspection

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            end = self._finished_at if self._finished_at is not None else time.monotonic()
            return {
                "tool_count": self._tool_count,
                "tool_diversity": len(self._distinct_tools),
                "last_tool": self._last_tool,
                "consecutive_repeats": self._consecutive_repeats,
                "consecutive_stalls": self._consecutive_stalls,
                "stuck": self._stuck,
                "stall": self._stall,
                "elapsed": round(max(0.0, end - self._started_at), 2),
                "kill_reason": self._kill_reason,
            }

    # ----------------------------------------------------------- lifecycle control

    def cancel(self, reason: str) -> None:
        """Mark the child as cancelled (cooperative). Idempotent."""
        with self._lock:
            if self._kill_reason is None:
                self._kill_reason = str(reason)
            self._done.set()

    def close(self) -> None:
        """Mark the child as finished (normally or via error). Idempotent."""
        with self._lock:
            self._finished_at = time.monotonic()
        self._done.set()

    def start_watchdog(self, session: Any, seconds: Optional[int] = None) -> None:
        """Start a daemon that auto-kills the child after ``seconds`` of
        wall-clock runtime. Uses ``Event.wait`` so a child that finishes in
        time cancels the watchdog without any race.
        """
        timeout = int(seconds if seconds is not None else self.max_runtime_seconds)
        if timeout <= 0:
            return

        def _watch() -> None:
            # Returns True (child done) before the deadline -> no-op.
            # Returns False (timed out) -> runtime exceeded -> auto-kill.
            if self._done.wait(timeout=timeout):
                return
            try:
                session._subagent_cancelled = True
                session._subagent_kill_reason = "runtime_exceeded"
            except Exception:  # noqa: BLE001
                pass
            self.cancel("runtime_exceeded")

        threading.Thread(target=_watch, daemon=True, name="subagent-watchdog").start()


__all__ = ["SubagentLifecycleManager"]