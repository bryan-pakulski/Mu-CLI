"""Pending-prompt store — bridges synchronous ``WebUI`` prompts to async HTTP.

The agent thread calls ``ask_user_choice`` / ``prompt`` inside
``session.send_message``. We can't pop a modal from a background
thread, so we:

1. ``open(payload)`` returns a fresh ``prompt_id`` and a
   ``threading.Event``.
2. Caller broadcasts the payload to the SSE bus and ``event.wait()``
   blocks the agent thread.
3. The browser POSTs ``/api/prompts/{id}/answer``; the router calls
   ``answer(id, value)`` which stores the value and sets the event.
4. The caller wakes, reads the value via ``take(id)``, and returns it
   into the agent loop.

Questions never time out: the agent waits for as long as the person takes
(user decision, 2026-10-20). A question ends only when it is answered, or
cancelled by the person (the card's cancel, Stop → ``cancel_session``) or by
the GUI shutting down (``cancel_all``). Waiters poll in short slices
(``wait()``) so an interrupt delivered to the agent thread still lands.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional


class PromptStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, threading.Event] = {}
        self._answers: Dict[str, Any] = {}
        self._payloads: Dict[str, Dict[str, Any]] = {}

    def open(self, payload: Dict[str, Any]) -> tuple[str, threading.Event]:
        prompt_id = str(uuid.uuid4())
        event = threading.Event()
        with self._lock:
            self._pending[prompt_id] = event
            self._payloads[prompt_id] = dict(payload)
        return prompt_id, event

    def answer(self, prompt_id: str, value: Any) -> bool:
        with self._lock:
            event = self._pending.pop(prompt_id, None)
            if event is None:
                return False
            self._answers[prompt_id] = value
            self._payloads.pop(prompt_id, None)
        event.set()
        return True

    def cancel(self, prompt_id: str) -> None:
        self.answer(prompt_id, {"cancelled": True})

    def take(self, prompt_id: str) -> Optional[Any]:
        with self._lock:
            return self._answers.pop(prompt_id, None)

    def pending(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [
                {"id": pid, **payload}
                for pid, payload in self._payloads.items()
            ]

    def cancel_session(self, session_name: Optional[str]) -> int:
        """Cancel every pending prompt of one session (Stop); returns how many."""
        with self._lock:
            ids = [pid for pid, payload in self._payloads.items()
                   if session_name is None or payload.get("session_name") == session_name]
        for pid in ids:
            self.cancel(pid)
        return len(ids)

    @staticmethod
    def wait(event: threading.Event, slice_s: float = 0.25) -> None:
        """Block until the prompt resolves — indefinitely, in short slices so the
        agent thread runs Python between them (an async interrupt can land)."""
        while not event.wait(slice_s):
            pass

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._pending.keys())
        for pid in ids:
            self.cancel(pid)
