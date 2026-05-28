"""Cross-process session file watcher (multi-session aware).

Polls every loaded session's ``session.json`` every few seconds. When
another process writes a file (different ``__writer_pid__`` than ours),
reloads that session's SessionManager from disk and emits
``session_updated`` on the SSE bus so connected browsers refresh.

Without this, two mucli processes (TUI + GUI, or two GUI tabs) writing
to the same session.json silently clobber each other. The last writer
wins on disk; the loser's in-memory state is stale until the next page
reload.

Each loaded session gets its own watcher entry so cross-process sync
works for every session in the daemon's cache — not just whichever one
the user is currently focused on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class _Track:
    mtime: float = 0.0
    size: int = 0
    initialized: bool = False
    external_active: bool = False
    external_last_at: float = 0.0


class SessionWatcher:
    def __init__(self, app, *, interval: float = 2.0) -> None:
        self._app = app
        self._interval = interval
        self._task: Optional[asyncio.Task] = None
        self._tracks: Dict[str, _Track] = {}
        self._our_pid: int = os.getpid()

    # ---- compat shims -----------------------------------------------------
    # Old single-session UI reads these flags. Surface the focused session's
    # values so the existing /api/sessions/active endpoint keeps working.
    @property
    def external_active(self) -> bool:
        track = self._focused_track()
        return bool(track and track.external_active)

    @property
    def external_last_at(self) -> float:
        track = self._focused_track()
        return float(track.external_last_at) if track else 0.0

    def external_active_for(self, name: str) -> bool:
        track = self._tracks.get(name)
        return bool(track and track.external_active)

    def external_last_at_for(self, name: str) -> float:
        track = self._tracks.get(name)
        return float(track.external_last_at) if track else 0.0

    def _focused_track(self) -> Optional[_Track]:
        cur = self._app.state.current_session_name
        return self._tracks.get(cur) if cur else None

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._tick()
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("session watcher tick failed: %s", exc)
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        state = self._app.state
        loaded = dict(state.sessions)  # snapshot

        # Drop trackers for sessions that were unloaded.
        for stale in list(self._tracks.keys()):
            if stale not in loaded:
                self._tracks.pop(stale, None)

        for name, session in loaded.items():
            try:
                await self._tick_one(name, session)
            except Exception as exc:
                logger.warning("watcher: %s failed: %s", name, exc)

    async def _tick_one(self, name: str, session) -> None:
        sm = session.session_manager
        path = sm._get_filepath(name)
        if not os.path.exists(path):
            return

        track = self._tracks.setdefault(name, _Track())
        st = os.stat(path)
        mtime = st.st_mtime
        size = st.st_size

        if not track.initialized:
            track.mtime = mtime
            track.size = size
            track.initialized = True
            return

        if abs(mtime - track.mtime) < 0.05 and size == track.size:
            return

        writer_pid = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            writer_pid = data.get("__writer_pid__")
        except (OSError, json.JSONDecodeError):
            # Race with a write in progress — skip this tick.
            return

        logger.info(
            "watcher: %r changed (mtime %.3f→%.3f) writer_pid=%s our_pid=%d",
            name, track.mtime, mtime, writer_pid, self._our_pid,
        )
        track.mtime = mtime
        track.size = size

        if writer_pid == self._our_pid:
            return

        logger.info("watcher: external write detected on %r — reloading", name)
        await self._handle_external_write(session, name, track)

    async def _handle_external_write(self, session, name: str, track: _Track) -> None:
        state = self._app.state
        bus = state.bus
        lock = state.session_lock_for(name)
        busy = state.session_busy_for(name)

        def _do_reload():
            with lock:
                try:
                    session.session_manager._load_session(name)
                except Exception as exc:
                    logger.warning("session reload failed: %s", exc)

        if busy.is_set():
            logger.info("watcher: turn in flight on %r — deferring reload", name)
        else:
            await asyncio.to_thread(_do_reload)

        track.external_active = True
        track.external_last_at = time.time()
        await bus.publish(
            {
                "kind": "session_updated",
                "name": name,
                "session_name": name,
                "reason": "external_write",
                "reloaded": not busy.is_set(),
            }
        )
