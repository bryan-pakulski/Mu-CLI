"""UI adapter for autonomous durable job execution.

The adapter never blocks on stdin or a browser. Human interactions become a
control-flow signal which the outer job runner persists as NEEDS_HUMAN.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict

from mu.ui.base import BaseUI
from mu.ui.exceptions import InteractionRequired

from .service import JobService


class JobUI(BaseUI):
    def __init__(self, service: JobService, job_id: str):
        self.service = service
        self.job_id = job_id
        self._variables: Dict[str, Any] = {}

    def _event(self, event_type: str, *, payload: Dict[str, Any] | None = None, reason: str = "") -> None:
        self.service.store.append_event(
            self.job_id,
            event_type,
            reason=reason,
            payload=payload or {},
        )

    def render_message(self, role, content, model_name=None):
        text = str(content or "")
        self._event(
            "agent_message",
            payload={"role": str(role), "text": text[:24000], "model": model_name or ""},
        )

    def get_input(self, session_name, staged_files, agent_mode="default", current_task=None, feature_context=None):
        return ""

    def show_error(self, message):
        self._event("runtime_error", payload={"text": str(message or "")[:12000]})

    def show_info(self, message):
        # Keep operational breadcrumbs, but bound noisy harness messages.
        self._event("runtime_info", payload={"text": str(message or "")[:8000]})

    @contextmanager
    def show_status(self, message):
        self._event("runtime_status", payload={"text": str(message or "")[:1000]})
        yield

    def show_tool_result(self, result_str):
        self._event("tool_result_ui", payload={"preview": str(result_str or "")[:8000]})

    def show_diff(self, filename, original_content, modified_content):
        self._event(
            "approval_diff",
            payload={
                "filename": str(filename or ""),
                "original_chars": len(str(original_content or "")),
                "modified_chars": len(str(modified_content or "")),
            },
        )

    def set_variables(self, variables_dict):
        self._variables = variables_dict or {}

    def stream_tool_call(self, tool_name: str):
        self._event("tool_call_ui", payload={"tool_name": str(tool_name or "")})

    def request_tool_approval(self, **kwargs):
        tool_name = str(kwargs.get("tool_name") or "tool")
        raise InteractionRequired(
            "approval_required",
            f"Approval required for {tool_name}",
            payload={
                "tool_name": tool_name,
                "tool_args": kwargs.get("tool_args") or kwargs.get("display_args") or {},
                "can_approve": bool(kwargs.get("can_approve", True)),
                "preview_error": kwargs.get("preview_error"),
                "error_code": kwargs.get("error_code"),
            },
        )

    def prompt(self, message, default=None):
        raise InteractionRequired(
            "question",
            str(message or "Input required"),
            payload={"shape": "input", "default": default},
        )

    def confirm(self, message, default=True):
        raise InteractionRequired(
            "question",
            str(message or "Confirmation required"),
            payload={"shape": "confirm", "default": bool(default)},
        )

    def prompt_choices(self, message, choices, default=None):
        raise InteractionRequired(
            "question",
            str(message or "Choice required"),
            payload={"shape": "choices", "choices": list(choices or []), "default": default},
        )

    def run_quiz(self, questions):
        raise InteractionRequired(
            "question",
            "Quiz response required",
            payload={"shape": "quiz", "questions": list(questions or [])},
        )

    def ask_user_choice(self, question, options, *, multi_select=False, description="", allow_other=False):
        raise InteractionRequired(
            "question",
            str(question or "Choice required"),
            payload={
                "shape": "choice",
                "options": list(options or []),
                "multi_select": bool(multi_select),
                "description": str(description or ""),
                "allow_other": bool(allow_other),
            },
        )
