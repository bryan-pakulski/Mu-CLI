"""``WebUI`` — :class:`BaseUI` adapter that streams agent events to the bus.

Renders nothing locally; every UI side-effect becomes an event the
browser receives via SSE. Blocking prompts pause the agent thread on
a :class:`threading.Event` held by :class:`PromptStore`.

Two non-obvious bits:

- :meth:`stream_assistant_delta` lazily emits ``assistant_start`` on
  the first token because ``mu.ui.stream.build_default_renderer``
  doesn't probe for a start callback. Without this the frontend never
  sees a "new bubble" signal.
- :meth:`stream_assistant_end` is a no-op when no delta arrived (e.g.
  provider erred before any text), so there's no orphan bubble close.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from mu.ui.base import BaseUI

from .bus import EventBus
from .prompts import PromptStore


class WebUI(BaseUI):
    def __init__(
        self,
        bus: EventBus,
        prompts: PromptStore,
        *,
        session_name: Optional[str] = None,
    ):
        self._bus = bus
        self._prompts = prompts
        self._variables: Dict[str, Any] = {}
        self._current_turn_id: Optional[str] = None
        # The session this WebUI belongs to. Stamped onto every event
        # so the frontend can route streaming/prompt traffic to the
        # right per-session chat slot when multiple sessions are loaded.
        self.session_name: Optional[str] = session_name

    def _publish(self, event: Dict[str, Any]) -> None:
        if self.session_name is not None and "session_name" not in event:
            event = {**event, "session_name": self.session_name}
        self._bus.publish_threadsafe(event)

    def _new_turn(self) -> str:
        self._current_turn_id = uuid.uuid4().hex[:12]
        return self._current_turn_id

    # --- BaseUI surface ---------------------------------------------------

    def render_message(self, role, content, model_name=None):
        self._publish(
            {
                "kind": "message",
                "role": role,
                "content": str(content),
                "model": model_name,
            }
        )

    def get_input(
        self,
        session_name,
        staged_files,
        agent_mode="default",
        current_task=None,
        feature_context=None,
    ):
        # Browser drives input via POST /api/chat/send. Stub for interface.
        return ""

    def show_error(self, message):
        self._publish({"kind": "error", "text": str(message)})

    def show_info(self, message):
        self._publish({"kind": "info", "text": str(message)})

    def show_status(self, message):
        return _NullStatus(self, str(message))

    def show_tool_result(self, result_str):
        self._publish({"kind": "tool_result", "text": str(result_str)})

    # --- streaming hooks (duck-typed; see mu/ui/stream.py) ----------------

    def stream_assistant_delta(self, text: str):
        if not text:
            return
        if self._current_turn_id is None:
            self._new_turn()
            self._publish(
                {"kind": "assistant_start", "turn_id": self._current_turn_id}
            )
        self._publish(
            {
                "kind": "assistant_delta",
                "turn_id": self._current_turn_id,
                "text": text,
            }
        )

    def stream_thinking_delta(self, text: str):
        if not text:
            return
        self._publish(
            {
                "kind": "thinking_delta",
                "turn_id": self._current_turn_id,
                "text": text,
            }
        )

    def stream_tool_call(self, tool_name: str):
        self._publish(
            {
                "kind": "tool_call",
                "turn_id": self._current_turn_id,
                "tool_name": tool_name,
            }
        )

    def stream_assistant_end(self):
        if self._current_turn_id is None:
            return  # No deltas this turn — nothing to close.
        self._publish({"kind": "assistant_end", "turn_id": self._current_turn_id})
        self._current_turn_id = None

    def set_variables(self, variables_dict):
        self._variables = dict(variables_dict or {})

    # --- blocking prompts -------------------------------------------------

    def prompt(self, message, default=None):
        result = self._ask_prompt(
            {
                "shape": "input",
                "message": str(message),
                "default": "" if default is None else str(default),
            }
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return default
        if isinstance(result, dict):
            return result.get("value", default)
        return default

    def confirm(self, message, default=True):
        result = self._ask_prompt(
            {
                "shape": "confirm",
                "message": str(message),
                "default": bool(default),
            }
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return bool(default)
        if isinstance(result, dict) and "value" in result:
            return bool(result["value"])
        return bool(default)

    def prompt_choices(self, message, choices, default=None):
        result = self._ask_prompt(
            {
                "shape": "choices",
                "message": str(message),
                "choices": list(choices),
                "default": default,
            }
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return default
        if isinstance(result, dict):
            return result.get("value", default)
        return default

    def request_tool_approval(
        self,
        tool_name=None,
        tool_args=None,
        *,
        description=None,
        risk=None,
        display_args=None,
        **_kwargs,
    ):
        result = self._ask_prompt(
            {
                "shape": "tool_approval",
                "tool_name": tool_name,
                "tool_args": tool_args or display_args,
                "description": description,
                "risk": risk,
            }
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return {"approved": False, "remember": False}
        if isinstance(result, dict):
            return {
                "approved": bool(result.get("approved", False)),
                "remember": bool(result.get("remember", False)),
            }
        return {"approved": False, "remember": False}

    def run_quiz(self, questions):
        result = self._ask_prompt(
            {"shape": "quiz", "questions": list(questions or [])}
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return {}
        if isinstance(result, dict):
            return dict(result.get("answers") or {})
        return {}

    def ask_user_choice(
        self,
        question,
        options,
        *,
        multi_select=False,
        description="",
        allow_other=False,
    ):
        result = self._ask_prompt(
            {
                "shape": "choice",
                "question": str(question),
                "options": list(options),
                "multi_select": bool(multi_select),
                "description": str(description or ""),
                "allow_other": bool(allow_other),
            }
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return {"selected": [], "other_text": "", "cancelled": True}
        if isinstance(result, dict):
            return {
                "selected": list(result.get("selected") or []),
                "other_text": str(result.get("other_text") or ""),
                "cancelled": False,
            }
        return {"selected": [], "other_text": "", "cancelled": True}

    def show_diff(self, filename, original_content, new_content):
        self._publish(
            {
                "kind": "diff",
                "filename": str(filename),
                "original": str(original_content or ""),
                "new": str(new_content or ""),
            }
        )

    # --- prompt plumbing --------------------------------------------------

    def _ask_prompt(self, payload: Dict[str, Any], timeout: float = 600.0) -> Any:
        # Tag the prompt payload itself so it's discoverable later
        # via /api/prompts (for reconnection / debugging).
        tagged_payload = dict(payload)
        if self.session_name is not None:
            tagged_payload.setdefault("session_name", self.session_name)
        prompt_id, event = self._prompts.open(tagged_payload)
        self._publish({"kind": "prompt", "id": prompt_id, "prompt": tagged_payload})
        if not event.wait(timeout=timeout):
            self._prompts.cancel(prompt_id)
            self._publish({"kind": "prompt_cancelled", "id": prompt_id})
        result = self._prompts.take(prompt_id)
        self._publish({"kind": "prompt_resolved", "id": prompt_id})
        return result


class _NullStatus:
    """Context manager surrogate for `show_status` — emits start/end events."""

    def __init__(self, ui: WebUI, message: str):
        self._ui = ui
        self._message = message

    def __enter__(self):
        self._ui._publish({"kind": "status_start", "text": self._message})
        return self

    def __exit__(self, exc_type, exc, tb):
        self._ui._publish({"kind": "status_end", "text": self._message})
        return False

    def update(self, message: str) -> None:
        self._message = str(message)
        self._ui._publish({"kind": "status_update", "text": self._message})
