"""Feature-mode helpers for the terminal surface (prompt badge, markdown view).

Reads the active plan through ``mu.feature.engine`` (the session keeps only a
pointer record). ``mucli`` re-exports these names.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from mu.feature import engine


def _slugify_feature_id(value):
    return (
        re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        or "feature"
    )


def _default_feature_directory(session, feature_name):
    folders = getattr(session.folder_context, "folders", None) or []
    return os.path.abspath(folders[0]) if folders else os.getcwd()


def refresh_feature_record(session, feature_id=None) -> Optional[Dict[str, Any]]:
    """Re-sync the session's pointer record for a feature from its plan file."""
    manager = session.session_manager
    record = manager.get_feature(feature_id)
    if not isinstance(record, dict):
        return None
    plan = engine.read_feature_plan(str(record.get("metadata_path") or ""))
    if plan is None:
        return record
    engine.sync_registry(session, plan, activate=manager.active_feature_id == plan.feature_id)
    manager.save_history(session.folder_context)
    return manager.get_feature(plan.feature_id)


def get_current_feature_task_label(session) -> Optional[str]:
    plan = engine.active_plan(session, cached=True)
    if plan is None:
        return None
    task = plan.next_task()
    return f"t{task.id} {task.title}" if task is not None else None


def get_feature_prompt_context(session) -> Optional[Dict[str, Any]]:
    """Counts for the prompt badge: plan status, current task, criteria and task progress."""
    plan = engine.active_plan(session, cached=True)
    if plan is None:
        return None
    live = plan.live_tasks()
    done = sum(1 for t in live if t.status == engine.STATUS_COMPLETED)
    task = plan.next_task()
    if task is None:
        return {"status": plan.status, "task": "completed" if live and done == len(live) else "n/a",
                "phase_done": 1 if live else 0, "phase_total": 1, "overall_done": done,
                "overall_total": max(1, len(live))}
    met = sum(1 for c in task.criteria if c.met)
    return {"status": plan.status, "task": task.title, "phase_done": met,
            "phase_total": max(1, len(task.criteria)), "overall_done": done, "overall_total": max(1, len(live))}


def build_feature_markdown(feature, *, include_phases=True) -> str:
    if not isinstance(feature, dict):
        return "## Feature\n\nNo feature is currently selected."
    plan = engine.read_feature_plan(str(feature.get("metadata_path") or ""))
    if plan is None:
        return f"# Feature: {feature.get('feature_name') or feature.get('feature_id')}\n\nNo plan file found."
    approval = plan.approval or {}
    lines = [
        f"# Feature: {plan.feature_name}",
        "",
        f"- **ID:** `{plan.feature_id}` · **Status:** `{plan.status}` · **Revision:** {plan.revision}",
        "- **Approval:** " + (f"{approval.get('actor')} (hash `{approval.get('plan_hash')}`)" if approval
                               else "waiting for the user — `/feature approve`"),
        "",
        "## Goal",
        "",
        plan.feature_request,
        "",
    ]
    live = plan.live_tasks()
    done = sum(1 for t in live if t.status == engine.STATUS_COMPLETED)
    lines.append(f"**Progress:** {done} of {len(live)} tasks complete")
    lines.append("")
    if include_phases:
        icons = {"completed": "✓", "in_progress": "▸", "blocked": "!", "not_started": "○", "archived": "–"}
        for phase in sorted(plan.phases_meta, key=lambda p: p.order or p.id):
            lines.extend([f"### Phase {phase.id}: {phase.title}", ""])
            for task in [t for t in plan.tasks if t.phase_id == phase.id]:
                flag = " · reviewed" if task.reviewed else ""
                lines.append(f"- {icons.get(task.status, '○')} **t{task.id} {task.title}** `{task.status}`{flag}")
                for crit in task.criteria:
                    how = ""
                    if crit.evidence:
                        last = crit.evidence[-1]
                        how = f" — {last.get('kind')} {last.get('ref') or ''}".rstrip()
                    lines.append(f"  - {'✓' if crit.met else '○'} {crit.id} {crit.text}{how}")
            lines.append("")
    return "\n".join(lines).strip()


__all__ = [
    "_default_feature_directory",
    "_slugify_feature_id",
    "build_feature_markdown",
    "get_current_feature_task_label",
    "get_feature_prompt_context",
    "refresh_feature_record",
]
