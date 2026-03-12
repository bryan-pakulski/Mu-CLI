import asyncio
import copy
import re
from datetime import datetime

from sqlalchemy import select

from server.app.core.config import settings
from server.app.persistence.db import SessionLocal
from server.app.persistence.models import (
    ApprovalModel,
    ApprovalState,
    JobModel,
    JobState,
    SessionModel,
)
from server.app.policies.engine import policy_engine
from server.app.providers.router import provider_router, resolve_ordered_providers
from server.app.runtime.agent_loop import LoopStep, run_agent_loop
from server.app.runtime.orchestrator import emit_event, update_job_state
from server.app.skills.registry import skill_registry
from server.app.tools.registry import tool_registry

STAGE_READY_PREFIX = "STAGE_READY::"
STAGE_NEEDS_MORE_PREFIX = "STAGE_NEEDS_MORE::"
MAX_STAGE_REPROMPTS = 3


def _extract_stage_signal(output: str, expected_stage: str) -> tuple[bool, str, str]:
    text = (output or "").strip()
    ready_pattern = re.compile(r"^STAGE_READY::([^:]+)::\\s*(.*)$", re.IGNORECASE | re.DOTALL)
    needs_more_pattern = re.compile(r"^STAGE_NEEDS_MORE::([^:]+)::\\s*(.*)$", re.IGNORECASE | re.DOTALL)

    ready_match = ready_pattern.match(text)
    if ready_match:
        stage_name = (ready_match.group(1) or "").strip()
        body = (ready_match.group(2) or "").strip()
        stage_matches = stage_name.lower() == expected_stage.lower()
        return stage_matches, "ready", body or text

    needs_more_match = needs_more_pattern.match(text)
    if needs_more_match:
        body = (needs_more_match.group(2) or "").strip()
        return False, "needs_more", body or text

    return False, "missing", text


class JobRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    def _cancelled(self, job_id: str) -> bool:
        event = self._cancel_flags.get(job_id)
        return event.is_set() if event else False

    def _cleanup(self, job_id: str) -> None:
        self._cancel_flags.pop(job_id, None)
        self._tasks.pop(job_id, None)

    async def _handle_tool_policy(
        self,
        db,
        session: SessionModel,
        job: JobModel,
    ) -> bool:
        requested_tool = (job.constraints or {}).get("tool_name")
        if not requested_tool:
            return True

        context_state = session.context_state or {}
        enabled_tools = context_state.get("enabled_tools")
        if isinstance(enabled_tools, list) and requested_tool not in enabled_tools:
            await emit_event(
                db,
                job.session_id,
                "policy",
                {"tool_name": requested_tool, "decision": "deny", "reason": "tool disabled for session"},
                job_id=job.id,
            )
            await update_job_state(db, job.id, JobState.blocked)
            return False

        tool = tool_registry.get(requested_tool)
        if not tool:
            await emit_event(
                db,
                job.session_id,
                "policy",
                {"tool_name": requested_tool, "decision": "deny", "reason": "unknown tool"},
                job_id=job.id,
            )
            await update_job_state(db, job.id, JobState.failed)
            return False

        decision = policy_engine.evaluate(session.mode, tool)
        await emit_event(
            db,
            job.session_id,
            "policy",
            {
                "tool_name": tool.name,
                "decision": decision.decision,
                "reason": decision.reason,
            },
            job_id=job.id,
        )

        if decision.decision == "allow":
            return True

        if decision.decision == "deny":
            await update_job_state(db, job.id, JobState.blocked)
            return False

        if decision.decision not in {"ask", "escalate"}:
            await update_job_state(db, job.id, JobState.blocked)
            return False

        approval = ApprovalModel(
            session_id=job.session_id,
            job_id=job.id,
            tool_name=tool.name,
            reason=decision.reason,
            state=ApprovalState.pending,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        await update_job_state(db, job.id, JobState.awaiting_approval)
        await emit_event(
            db,
            job.session_id,
            "approval_requested",
            {
                "approval_id": approval.id,
                "tool_name": tool.name,
                "reason": approval.reason,
                "decision": decision.decision,
            },
            job_id=job.id,
        )

        for _ in range(300):
            if self._cancelled(job.id):
                return False
            latest_approval = await db.scalar(
                select(ApprovalModel).where(ApprovalModel.id == approval.id)
            )
            if not latest_approval:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            if latest_approval.state == ApprovalState.approved:
                await update_job_state(db, job.id, JobState.queued)
                return True
            if latest_approval.state == ApprovalState.denied:
                await update_job_state(db, job.id, JobState.blocked)
                return False
            await asyncio.sleep(0.1)

        await update_job_state(db, job.id, JobState.blocked)
        return False

    async def _run(self, job_id: str) -> None:
        async with SessionLocal() as db:
            job = await db.scalar(select(JobModel).where(JobModel.id == job_id))
            if not job:
                return

            session = await db.scalar(select(SessionModel).where(SessionModel.id == job.session_id))
            if not session:
                await update_job_state(db, job_id, JobState.failed)
                return

            checkpoints = job.checkpoints or {}
            attempts = int(checkpoints.get("attempts", 0)) + 1
            checkpoints["attempts"] = attempts
            job.checkpoints = checkpoints
            await db.commit()

            if not await self._handle_tool_policy(db, session, job):
                return

            await update_job_state(db, job_id, JobState.running)
            await emit_event(
                db,
                job.session_id,
                "log",
                {"message": "job started", "attempt": attempts},
                job_id=job.id,
            )

            provider_preferences = session.provider_preferences or {}
            ordered_providers = resolve_ordered_providers(provider_preferences)
            selected_model = provider_preferences.get("model")

            def append_context_message(role: str, content: str) -> None:
                context_state = copy.deepcopy(session.context_state or {"name": session.name, "messages": [], "summary": None, "memory_refs": [], "max_context_messages": 40})
                context_state.setdefault("messages", []).append({"role": role, "content": content, "created_at": datetime.now().isoformat(timespec="seconds")})
                max_messages = max(5, int(context_state.get("max_context_messages", 40)))
                if len(context_state["messages"]) > max_messages:
                    context_state["messages"] = context_state["messages"][-max_messages:]
                session.context_state = context_state

            async def emit_step(step: LoopStep) -> None:
                context_state = session.context_state or {}
                enabled_skills = context_state.get("enabled_skills")
                enabled_tools = context_state.get("enabled_tools")
                skills_hint = ",".join(enabled_skills) if isinstance(enabled_skills, list) and enabled_skills else "none"
                tools_hint = ",".join(enabled_tools) if isinstance(enabled_tools, list) and enabled_tools else "all"
                system_prompt_override = context_state.get("system_prompt_override")
                rules_checklist = context_state.get("rules_checklist")

                context_messages = context_state.get("messages") if isinstance(context_state.get("messages"), list) else []
                max_context_messages = max(5, int(context_state.get("max_context_messages", 40)))
                history_window = min(20, max_context_messages)
                recent_context = context_messages[-history_window:] if history_window else []
                context_lines = [
                    f"{(item.get('role') or 'unknown')}: {item.get('content') or ''}"
                    for item in recent_context
                    if isinstance(item, dict)
                ]
                context_block = "\n".join(context_lines).strip()

                all_tools = tool_registry.list_tools()
                all_skills = skill_registry.discover(session.workspace_path)
                tool_reference_lines = []
                for tool in all_tools:
                    approval = "requires approval" if tool.requires_approval else "no approval"
                    tool_reference_lines.append(
                        f"- {tool.name}: {tool.description}. Use when this capability is needed. "
                        f"Call by setting constraints.tool_name={tool.name}. Risk={tool.risk_level}, {approval}."
                    )
                skill_reference_lines = []
                for skill in all_skills:
                    skill_reference_lines.append(
                        f"- {skill.name}: {skill.description}. Use for specialized workflow instructions. "
                        f"Call by explicitly referencing skill '{skill.name}' in your plan/tooling rationale."
                    )

                tools_reference_block = "\n".join(tool_reference_lines)
                skills_reference_block = "\n".join(skill_reference_lines) if skill_reference_lines else "- none"

                stage_output = ""
                stage_feedback = ""
                last_provider = ""
                for stage_attempt in range(1, MAX_STAGE_REPROMPTS + 1):
                    prompt = f"goal={job.goal}\nmode={session.mode}\nstep={step.label}\nenabled_skills={skills_hint}\nenabled_tools={tools_hint}"
                    prompt += "\n\navailable_tools_by_name_and_usage:\n" + tools_reference_block
                    prompt += "\n\navailable_skills_by_name_and_usage:\n" + skills_reference_block
                    if context_block:
                        prompt += f"\n\nconversation_context:\n{context_block}"
                    if isinstance(system_prompt_override, str) and system_prompt_override.strip():
                        prompt += f"\n\nsystem_prompt_override={system_prompt_override.strip()}"
                    if isinstance(rules_checklist, str) and rules_checklist.strip():
                        prompt += f"\n\nrules_checklist={rules_checklist.strip()}"
                    prompt += (
                        "\n\nstage_protocol:\n"
                        f"- When this stage is complete, prefix your response with {STAGE_READY_PREFIX}{step.label}::\n"
                        f"- If not complete, prefix with {STAGE_NEEDS_MORE_PREFIX}{step.label}:: and explain what is missing.\n"
                        "- Do not omit this prefix."
                    )
                    prompt += f"\ncurrent_stage_attempt={stage_attempt}/{MAX_STAGE_REPROMPTS}"
                    if stage_feedback:
                        prompt += f"\n\nstage_feedback:\n{stage_feedback}"

                    stage_meta = {
                        "index": step.index,
                        "label": step.label,
                        "attempt": stage_attempt,
                        "max_attempts": MAX_STAGE_REPROMPTS,
                        "status": "in_progress",
                    }

                    await emit_event(
                        db,
                        job.session_id,
                        "model_request",
                        {
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "provider_order": ordered_providers,
                            "selected_model": selected_model,
                            "prompt": prompt,
                        },
                        job_id=job.id,
                    )

                    await emit_event(
                        db,
                        job.session_id,
                        "system_prompt",
                        {
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "goal": job.goal,
                            "mode": session.mode,
                            "provider_order": ordered_providers,
                            "selected_model": selected_model,
                            "enabled_skills": enabled_skills if isinstance(enabled_skills, list) else [],
                            "enabled_tools": enabled_tools if isinstance(enabled_tools, list) else [],
                            "available_tools": [
                                {"name": t.name, "description": t.description}
                                for t in all_tools
                            ],
                            "available_skills": [
                                {"name": s.name, "description": s.description}
                                for s in all_skills
                            ],
                            "context_messages_count": len(context_messages),
                            "context_messages_window": history_window,
                            "system_prompt_override": system_prompt_override if isinstance(system_prompt_override, str) else "",
                            "rules_checklist": rules_checklist if isinstance(rules_checklist, str) else "",
                            "prompt": prompt,
                        },
                        job_id=job.id,
                    )

                    result = await provider_router.generate_with_fallback(
                        prompt=prompt,
                        ordered_providers=ordered_providers,
                        model=selected_model,
                        max_retries=settings.provider_max_retries,
                    )

                    is_ready, signal, cleaned_output = _extract_stage_signal(result.output, step.label)
                    await emit_event(
                        db,
                        job.session_id,
                        "model_response",
                        {
                            "step": step.index,
                            "label": step.label,
                            "stage": stage_meta,
                            "stage_signal": signal,
                            "stage_ready": is_ready,
                            "provider": result.provider_name,
                            "model": selected_model,
                            "text": result.output,
                            "output_chars": len(result.output or ""),
                        },
                        job_id=job.id,
                    )

                    if is_ready:
                        stage_output = cleaned_output
                        last_provider = result.provider_name
                        break

                    stage_feedback = (
                        f"Previous model output did not confirm completion for stage '{step.label}'. "
                        f"Expected prefix: {STAGE_READY_PREFIX}{step.label}::\n"
                        f"Received signal: {signal}\n"
                        f"Previous output:\n{result.output}"
                    )

                if not stage_output:
                    raise RuntimeError(
                        f"Stage '{step.label}' did not provide required readiness confirmation "
                        f"after {MAX_STAGE_REPROMPTS} attempts"
                    )

                job.checkpoints = {
                    "last_completed_step": step.index,
                    "mode": session.mode,
                    "attempts": attempts,
                    "provider": last_provider,
                    "model": selected_model,
                    "last_output": stage_output,
                }
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "loop_step",
                    {
                        "index": step.index,
                        "label": step.label,
                        "stage": {
                            "index": step.index,
                            "label": step.label,
                            "status": "completed",
                        },
                        "mode": session.mode,
                        "provider": last_provider,
                        "model": selected_model,
                        "output_preview": stage_output[:180],
                    },
                    job_id=job.id,
                )
                await emit_event(
                    db,
                    job.session_id,
                    "assistant_chunk",
                    {
                        "step": step.index,
                        "label": step.label,
                        "stage": {
                            "index": step.index,
                            "label": step.label,
                            "status": "completed",
                        },
                        "text": stage_output,
                        "provider": last_provider,
                        "model": selected_model,
                    },
                    job_id=job.id,
                )

            try:
                timeout_s = max(30, int((session.context_state or {}).get("max_timeout_s", 300)))
                result = await asyncio.wait_for(
                    run_agent_loop(
                        session=session,
                        job=job,
                        emit_step=emit_step,
                        is_cancelled=lambda: self._cancelled(job_id),
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                append_context_message("assistant", "Job timed out before completion")
                await db.commit()
                await update_job_state(db, job.id, JobState.failed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job failed", "error": "timeout exceeded", "attempt": attempts},
                    job_id=job.id,
                )
                return

            except Exception as exc:  # noqa: BLE001
                append_context_message("assistant", f"Job failed: {exc}")
                await db.commit()
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job failed", "error": str(exc), "attempt": attempts},
                    job_id=job.id,
                )
                await update_job_state(db, job_id, JobState.failed)
                return

            if result["status"] == "cancelled":
                job.result_artifacts = {
                    "summary": "Job cancelled by user",
                    "mode": result["mode"],
                }
                append_context_message("assistant", "Job cancelled by user")
                await db.commit()
                await update_job_state(db, job_id, JobState.cancelled)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {"message": "job cancelled"},
                    job_id=job.id,
                )
            else:
                job.result_artifacts = {
                    "summary": "Job completed successfully",
                    "mode": result["mode"],
                    "steps": result["steps_executed"],
                    "attempts": attempts,
                    "provider": (job.checkpoints or {}).get("provider"),
                    "model": (job.checkpoints or {}).get("model"),
                }
                append_context_message(
                    "assistant",
                    (job.checkpoints or {}).get("last_output") or "Job completed successfully",
                )
                await db.commit()
                await update_job_state(db, job_id, JobState.completed)
                await emit_event(
                    db,
                    job.session_id,
                    "log",
                    {
                        "message": "job completed",
                        "attempt": attempts,
                        "provider": (job.checkpoints or {}).get("provider"),
                    },
                    job_id=job.id,
                )

    def start(self, job_id: str) -> None:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._cancel_flags[job_id] = asyncio.Event()
        task = asyncio.create_task(self._run(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._cleanup(job_id))

    async def cancel(self, job_id: str) -> None:
        if job_id not in self._cancel_flags:
            self._cancel_flags[job_id] = asyncio.Event()
        self._cancel_flags[job_id].set()

    async def resume(self, job_id: str) -> None:
        self.start(job_id)


job_runner = JobRunner()
