import asyncio
from dataclasses import dataclass

from server.app.persistence.models import JobModel, SessionModel


@dataclass
class LoopStep:
    index: int
    label: str


def _mode_steps(mode: str) -> list[str]:
    presets = {
        "interactive": ["plan", "act", "verify"],
        "research": ["plan", "explore", "summarize"],
        "debugging": ["reproduce", "fix", "test"],
        "yolo": ["plan", "bulk_execute", "finalize"],
    }
    return presets.get(mode, presets["interactive"])


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

        await emit_step(LoopStep(index=index, label=steps[index]))
        await asyncio.sleep(0.05)

    return {
        "status": "completed",
        "last_completed_step": len(steps) - 1,
        "mode": mode,
        "steps_executed": steps,
    }
