"""UI adapter for autonomous durable job execution.

Jobs run unattended (``mu.jobs.unattended``): the adapter never blocks on stdin or a
browser and never parks the job. Tool approvals are decided by stage (implement/checks
approve, plan/review deny writes) and agent questions are answered automatically; each
decision is recorded as an ``auto_permission`` / ``auto_answered`` event. A human response
recorded for a job that parked before this rule is still consumed exactly once.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict

from mu.ui.base import BaseUI

from .service import JobService


class JobUI(BaseUI):
    def __init__(self, service: JobService, job_id: str, *, stage: str | None = None):
        self.service = service
        self.job_id = job_id
        self.stage = stage
        self._variables: Dict[str, Any] = {}
        self._responses = self._load_pending_responses()

    def _event(self, event_type: str, *, payload: Dict[str, Any] | None = None, reason: str = "") -> None:
        self.service.store.append_event(
            self.job_id,
            event_type,
            reason=reason,
            payload=payload or {},
        )

    def _load_pending_responses(self):
        # Filter by type in SQL: the generic events() call is capped at 500
        # rows oldest-first, so on a chatty job a late interaction_response
        # was silently invisible and the job stayed wedged in NEEDS_HUMAN.
        events = self.service.store.list_events_by_type(
            self.job_id, ("interaction_response", "interaction_response_consumed")
        )
        consumed = {
            int(event.payload.get("response_event_id"))
            for event in events
            if event.event_type == "interaction_response_consumed"
            and str(event.payload.get("response_event_id") or "").isdigit()
        }
        return [
            event for event in events
            if event.event_type == "interaction_response" and event.id not in consumed
        ]

    def _take_response(self, kind: str, *, tool_name: str = "") -> Dict[str, Any] | None:
        for index, event in enumerate(list(self._responses)):
            payload = dict(event.payload or {})
            response_kind = str(payload.get("kind") or "question")
            if kind == "approval_required":
                if response_kind != "approval_required":
                    continue
                target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
                target_tool = str(target.get("tool_name") or "")
                if tool_name and target_tool and target_tool != tool_name:
                    continue
            elif response_kind not in {kind, "question"}:
                continue
            self._responses.pop(index)
            # Round-41 F7: ATOMIC claim — two JobUI instances (GUI + worker
            # session) previously both popped the same in-memory response
            # and both consumed it, double-approving write approvals. The
            # store-level claim lets exactly one caller win; the loser
            # re-loads pending responses (the winner's consumption is now
            # visible) and keeps looking.
            if not self.service.store.claim_interaction_response(
                self.job_id, event.id, kind=kind, tool_name=tool_name
            ):
                self._responses = self._load_pending_responses()
                continue
            # The claim itself wrote the interaction_response_consumed
            # event — do NOT append a second one (double-count).
            return payload
        return None

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

    # ------------------------------------------------------------ unattended decisions

    def _stage(self) -> str:
        """The agent stage this UI serves (``implement`` when it cannot be determined)."""
        stage = getattr(self, "stage", None)
        if stage:
            return str(stage)
        try:
            from .core.states import JobState
            from .unattended import stage_of_state
            core = self.service.store.core_state(self.job_id)
            state = core.paused_from if core.state == JobState.NEEDS_INPUT and core.paused_from else core.state
            stage = stage_of_state(state)
        except Exception:  # noqa: BLE001 - an unknown stage is treated as the writing stage
            stage = "implement"
        self.stage = stage
        return stage

    def request_tool_approval(self, **kwargs):
        """Jobs run unattended: never prompts. Read-only stages (plan, review) deny writes with a
        message for the agent; implement/checks approve inside the sandbox. A human decision
        already recorded for this call (a job parked before jobs ran unattended) is honoured in a
        writing stage."""
        from .unattended import decide, permission_event
        tool_name = str(kwargs.get("tool_name") or "tool")
        stage = self._stage()
        allow, message = decide(stage)
        if not allow:
            self._decided(permission_event(stage, tool_name, allow=False))
            return "e", message
        if kwargs.get("can_approve") is False:
            # Nothing to approve (the modification preview failed): tell the agent why.
            detail = str(kwargs.get("preview_error") or "the change could not be previewed")
            self._decided({**permission_event(stage, tool_name, allow=False), "message": detail})
            return "e", f"This tool call cannot run: {detail}"
        response = self._take_response("approval_required", tool_name=tool_name)
        if response is not None and response.get("auto"):
            # Decided (and recorded) by the host's unattended fallback.
            return ("y", None) if response.get("decision") == "approve" else ("e", str(response.get("detail") or ""))
        if response is not None:
            decision = str(response.get("decision") or "").lower()
            detail = str(response.get("detail") or "").strip() or None
            if decision == "approve":
                return "y", None
            if decision == "explain":
                return "e", detail
            if decision == "deny" and detail:
                # A deny with a reason reaches the agent as
                # "User denied this tool call. Reason: <detail>".
                return "e", detail
            return "n", None
        self._decided(permission_event(stage, tool_name, allow=True))
        return "y", None

    def _decided(self, payload):
        from .unattended import event_reason
        self._event("auto_permission", payload=payload, reason=event_reason("auto_permission", payload))

    def _auto_answer(self, shape: str, question: str = ""):
        from .unattended import AUTO_ANSWER, answered_event
        if not getattr(self, "_host_answered", False):
            from .unattended import event_reason
            payload = answered_event(self._stage(), shape, question)
            self._event("auto_answered", payload=payload, reason=event_reason("auto_answered", payload))
        return AUTO_ANSWER

    def _human_answer(self):
        """A human answer recorded for a job that parked on a question before jobs ran
        unattended (consumed exactly once); None otherwise (the answer is automatic)."""
        response = self._take_response("question")
        self._host_answered = bool(response is not None and response.get("auto"))
        return None if response is None or response.get("auto") else response

    def prompt(self, message, default=None):
        response = self._human_answer()
        if response is not None:
            if response.get("value") is not None:
                return response.get("value")
            detail = str(response.get("detail") or "").strip()
            return detail if detail else default
        return self._auto_answer("input", str(message or ""))

    def confirm(self, message, default=True):
        response = self._human_answer()
        if response is not None:
            value = response.get("value")
            if isinstance(value, bool):
                return value
            decision = str(response.get("decision") or "").lower()
            if decision in {"approve", "yes", "confirm", "true"}:
                return True
            if decision in {"deny", "no", "false"}:
                return False
            return bool(default)
        # A yes/no cannot carry the note; the stage proceeds with the default.
        self._auto_answer("confirm", str(message or ""))
        return bool(default)

    def prompt_choices(self, message, choices, default=None):
        response = self._human_answer()
        if response is not None:
            if response.get("value") is not None:
                return response.get("value")
            selected = list(response.get("selected") or [])
            if selected:
                return selected[0]
            detail = str(response.get("detail") or "").strip()
            return detail or default
        self._auto_answer("choices", str(message or ""))
        if default is not None:
            return default
        options = list(choices or [])
        return options[0] if options else ""

    def run_quiz(self, questions):
        response = self._human_answer()
        if response is not None and isinstance(response.get("value"), dict):
            return dict(response["value"])
        self._auto_answer("quiz", "; ".join(str((q or {}).get("question") if isinstance(q, dict) else q)
                                            for q in list(questions or [])[:5]))
        return {}

    def ask_user_choice(self, question, options, *, multi_select=False, description="", allow_other=False):
        response = self._human_answer()
        if response is not None:
            selected = list(response.get("selected") or [])
            if not selected and response.get("value") is not None:
                selected = [response.get("value")]
            return {
                "selected": selected,
                "other_text": str(response.get("detail") or ""),
                "cancelled": False,
            }
        answer = self._auto_answer("choice", str(question or ""))
        return {"selected": [], "other_text": answer, "cancelled": False}
