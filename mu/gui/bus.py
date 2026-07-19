"""In-process event bus for the GUI.

Producers (the agent thread, via ``WebUI``) call ``publish_threadsafe``
to push events. Each subscribed browser tab owns one
``asyncio.Queue`` and receives every event.

The loop is bound lazily by ``mu.gui.app.create_app``'s startup hook —
``create_app`` constructs the bus before uvicorn has a running loop,
so the loop must be attached AFTER uvicorn's loop is up. Publishing
before the loop is bound silently drops the event (only the agent
thread fires that early, and it doesn't run until a user message
arrives).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


class EventBus:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: List[asyncio.Queue] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach uvicorn's running loop. Called from the FastAPI
        startup hook so cross-thread publishes target the right loop."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        # Lazy fallback if subscribe lands before bind_loop fires.
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    async def publish(self, event: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    pass

    def publish_threadsafe(self, event: Dict[str, Any]) -> None:
        """Schedule publish on the bound loop from any thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.publish(event), loop)
        except RuntimeError:
            pass
