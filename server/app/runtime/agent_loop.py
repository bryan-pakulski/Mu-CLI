import asyncio
from dataclasses import dataclass

from server.app.persistence.models import JobModel, SessionModel


@dataclass
class LoopStep:
    index: int
    label: str
    objective: str
    success_criteria: list[str]


_MODE_STEP_DEFINITIONS: dict[str, list[dict[str, object]]] = {
    "chat": [
        {
            "label": "chat",
            "objective": "Respond directly to the user request in conversational form.",
            "success_criteria": [
                "Response addresses the user goal clearly.",
                "Tone is concise, helpful, and conversational.",
                "No agentic planning or tool workflow narration is included.",
            ],
        },
    ],
    "interactive": [
        {
            "label": "plan",
            "objective": "Produce a concrete implementation plan for the user goal.",
            "success_criteria": [
                "Plan is explicit and actionable.",
                "Required tools/files are identified.",
                "No implementation claims are made yet.",
            ],
        },
        {
            "label": "act",
            "objective": "Execute the approved plan and make the required changes.",
            "success_criteria": [
                "Requested artifacts are created/updated.",
                "Tooling/actions are relevant to the plan.",
                "Output summarizes completed implementation work.",
            ],
        },
        {
            "label": "verify",
            "objective": "Validate outputs and report readiness.",
            "success_criteria": [
                "Relevant checks/tests are run or limitations are stated.",
                "Results are summarized clearly.",
                "Final response states whether goal is satisfied.",
            ],
        },
    ],
    "research": [
        {
            "label": "plan",
            "objective": "Define the research approach and sources to gather.",
            "success_criteria": [
                "Scope and investigation approach are explicit.",
                "Information sources are identified.",
                "No final conclusions are claimed yet.",
            ],
        },
        {
            "label": "explore",
            "objective": "Gather and analyze relevant information.",
            "success_criteria": [
                "Findings are relevant to the question.",
                "Evidence/source quality is considered.",
                "Open uncertainties are called out.",
            ],
        },
        {
            "label": "summarize",
            "objective": "Produce a concise synthesis of findings.",
            "success_criteria": [
                "Summary answers the user goal directly.",
                "Trade-offs and confidence are stated.",
                "Recommended next actions are provided when applicable.",
            ],
        },
    ],
    "debugging": [
        {
            "label": "reproduce",
            "objective": "Establish a reproducible understanding of the issue.",
            "success_criteria": [
                "Symptoms and likely cause are identified.",
                "Reproduction or diagnostic evidence is described.",
                "Fix is not claimed complete yet.",
            ],
        },
        {
            "label": "fix",
            "objective": "Apply changes that address the identified issue.",
            "success_criteria": [
                "Code/config updates target the root cause.",
                "Changes are minimal and coherent.",
                "Fix rationale is explained.",
            ],
        },
        {
            "label": "test",
            "objective": "Validate that the fix resolves the issue.",
            "success_criteria": [
                "Relevant tests/checks are executed or limitations are stated.",
                "Observed behavior after fix is reported.",
                "Any remaining risks are highlighted.",
            ],
        },
    ],
    "yolo": [
        {
            "label": "plan",
            "objective": "Define an aggressive but coherent execution plan.",
            "success_criteria": [
                "High-level sequence is clear.",
                "Critical risks are acknowledged.",
                "Execution boundaries are explicit.",
            ],
        },
        {
            "label": "bulk_execute",
            "objective": "Execute the main work quickly across required tasks.",
            "success_criteria": [
                "Primary deliverables are produced.",
                "Major blockers are surfaced immediately.",
                "Progress is summarized clearly.",
            ],
        },
        {
            "label": "finalize",
            "objective": "Consolidate outcomes and final readiness state.",
            "success_criteria": [
                "Deliverables and status are summarized.",
                "Known gaps/risks are listed.",
                "Final recommendation is explicit.",
            ],
        },
    ],
}


def _mode_steps(mode: str) -> list[dict[str, object]]:
    return _MODE_STEP_DEFINITIONS.get(mode, _MODE_STEP_DEFINITIONS["interactive"])


async def run_agent_loop(
    session: SessionModel,
    job: JobModel,
    emit_step,
    is_cancelled,
) -> dict:
    mode = (session.mode or "interactive").lower()
    steps = _mode_steps(mode)

    start_at = int((job.checkpoints or {}).get("last_completed_step", -1)) + 1
    for index in range(start_at, len(steps)):
        if is_cancelled():
            return {
                "status": "cancelled",
                "last_completed_step": index - 1,
                "mode": mode,
            }

        step = steps[index]
        await emit_step(
            LoopStep(
                index=index,
                label=str(step["label"]),
                objective=str(step["objective"]),
                success_criteria=[str(item) for item in list(step["success_criteria"])],
            )
        )
        await asyncio.sleep(0.05)

    return {
        "status": "completed",
        "last_completed_step": len(steps) - 1,
        "mode": mode,
        "steps_executed": [str(step["label"]) for step in steps],
    }
