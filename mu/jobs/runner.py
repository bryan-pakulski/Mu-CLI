"""Execute one durable job attempt through the existing MuCLI Session runtime."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from mu.ui.exceptions import InteractionRequired

from .models import AttentionReason, Job, JobAttempt
from .service import JobService
from .ui import JobUI


@dataclass
class JobRunOutcome:
    kind: str
    status: str = ""
    error: str = ""
    cost_usd: float = 0.0
    attention_reason: AttentionReason = AttentionReason.NONE
    attention_detail: str = ""
    attention_payload: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)


class SessionJobRunner:
    def __init__(self, service: JobService, *, build_session_fn: Callable, base_args: Any):
        self.service = service
        self.build_session_fn = build_session_fn
        self.base_args = base_args

    @staticmethod
    def session_name(job: Job) -> str:
        return f"job-{job.id[:20]}"

    def _args_for(self, job: Job):
        execution = dict(job.execution or {})
        provider = str(execution.get("provider") or "").strip()
        model = str(execution.get("model") or "").strip()
        if not provider or not model:
            raise InteractionRequired(
                "question",
                "This job needs a provider and model before it can run.",
                payload={"shape": "execution_profile"},
            )
        args = copy.copy(self.base_args)
        args.session = self.session_name(job)
        args.provider = provider
        args.model = model
        args.provider_prevalidated = True
        args.session_type = str(execution.get("session_type") or "workspace")
        args.workspace = [job.repository] if job.repository and args.session_type == "workspace" else []
        args.yolo = bool(execution.get("auto_approve_writes", False))
        args.gui = False
        args.trace = False
        return args

    def _prompt(self, job: Job) -> str:
        lines = ["DURABLE ENGINEERING JOB", f"Title: {job.title}"]
        if job.description:
            lines.extend(["", "Description:", job.description])
        if job.acceptance_criteria:
            lines.extend(["", "Acceptance criteria:", *[f"- {v}" for v in job.acceptance_criteria]])
        if job.validation_commands:
            lines.extend(["", "Validation expected by the controller:", *[f"- {v}" for v in job.validation_commands]])
        for event in reversed(self.service.events(job.id)):
            if event.event_type == "human_response":
                detail = str(event.payload.get("detail") or "").strip()
                if detail:
                    lines.extend(["", "Latest human response:", detail])
                break
        lines.extend([
            "",
            "Implement the ticket and validate the result where possible.",
            "The controller, not the agent, decides whether the job is ready for review.",
        ])
        return "\n".join(lines)

    def run(self, job: Job, attempt: JobAttempt) -> JobRunOutcome:
        session = None
        initial_cost = 0.0
        try:
            execution = dict(job.execution or {})
            session_type = str(execution.get("session_type") or "workspace")
            if session_type == "workspace":
                if not job.repository:
                    raise InteractionRequired("question", "This job needs a repository/workspace path.", payload={"shape": "repository"})
                if not os.path.isdir(os.path.expanduser(job.repository)):
                    return JobRunOutcome(kind="failed", status="environment_error", error=f"Repository/workspace does not exist: {job.repository}")
            if session_type == "container":
                return JobRunOutcome(
                    kind="needs_human",
                    status="needs_human",
                    attention_reason=AttentionReason.ENVIRONMENT_FAILURE,
                    attention_detail="Container-backed durable jobs need the per-job environment adapter from Milestone 2.",
                    attention_payload={"session_type": "container"},
                )

            ui = JobUI(self.service, job.id)
            session = self.build_session_fn(self._args_for(job), ui, allow_prompt=False)
            session.ui = ui
            session.session_manager.ui = ui
            ui.set_variables(session.variables)
            session.variables["agent_mode"] = str(execution.get("agent_mode") or "default")
            session.variables["session_type"] = session_type
            session.variables["yolo"] = bool(execution.get("auto_approve_writes", False))
            session.variables["durable_job_id"] = job.id
            session.variables["durable_job_attempt"] = attempt.number
            if job.max_iterations is not None:
                session.variables["max_iterations"] = int(job.max_iterations)
            session.session_manager.save_history(session.folder_context)
            self.service.store.update_runtime_fields(job.id, session_name=self.session_name(job))

            initial_cost = float(session.session_manager.token_counts.get("total_cost", 0.0) or 0.0)
            result = session.send_message(self._prompt(job)) or {}
            final_cost = float(session.session_manager.token_counts.get("total_cost", 0.0) or 0.0)
            cost = max(0.0, final_cost - initial_cost)
            status = str(result.get("status") or "completed") if isinstance(result, dict) else "completed"
            error = str(result.get("error") or "") if isinstance(result, dict) else ""
            if status == "completed":
                return JobRunOutcome(kind="completed", status=status, cost_usd=cost, result=dict(result))
            return JobRunOutcome(kind="failed", status=status, error=error or f"Agent stopped with status {status}", cost_usd=cost, result=dict(result) if isinstance(result, dict) else {})

        except InteractionRequired as gate:
            current = initial_cost
            if session is not None:
                current = float(session.session_manager.token_counts.get("total_cost", initial_cost) or initial_cost)
            reason = AttentionReason.APPROVAL_REQUIRED if gate.kind == "approval_required" else AttentionReason.QUESTION
            return JobRunOutcome(
                kind="needs_human",
                status="needs_human",
                cost_usd=max(0.0, current - initial_cost),
                attention_reason=reason,
                attention_detail=gate.detail,
                attention_payload=gate.payload,
            )
        except Exception as exc:
            current = initial_cost
            if session is not None:
                current = float(session.session_manager.token_counts.get("total_cost", initial_cost) or initial_cost)
            return JobRunOutcome(kind="failed", status="error", error=str(exc), cost_usd=max(0.0, current - initial_cost))
        finally:
            if session is not None:
                try:
                    session.session_manager.save_history(session.folder_context)
                except Exception:
                    pass
                try:
                    session.shutdown()
                except Exception:
                    pass
