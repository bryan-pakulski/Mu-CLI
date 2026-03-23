from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_REVIEW_PENDING = "pending"

FEATURE_PLAN_FILENAME = "feature_plan.json"

@dataclass
class FeatureTask:
    id: int
    title: str
    objectives: list[str] = field(default_factory=list)
    action_points: list[str] = field(default_factory=list)
    exit_criteria: list[str] = field(default_factory=list)
    status: str = STATUS_NOT_STARTED
    notes: str = ""

@dataclass
class FeaturePlan:
    feature_id: str
    feature_name: str
    feature_request: str
    directory: str  # Workspace root
    metadata_path: str = ""
    approved: bool = False
    review_status: str = STATUS_REVIEW_PENDING
    review_notes: str = ""
    tasks: list[FeatureTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def next_incomplete_task(self) -> FeatureTask | None:
        for task in self.tasks:
            if task.status != STATUS_COMPLETED:
                return task
        return None

    def next_incomplete_phase(self) -> FeatureTask | None:
        # Alias for backward compatibility
        return self.next_incomplete_task()

    def tasks_completed(self) -> bool:
        return bool(self.tasks) and all(
            task.status == STATUS_COMPLETED for task in self.tasks
        )

    def phases_completed(self) -> bool:
        # Alias for backward compatibility
        return self.tasks_completed()

    def overall_status(self) -> str:
        if self.review_status == STATUS_COMPLETED:
            return STATUS_COMPLETED
        if self.tasks_completed():
            return STATUS_IN_PROGRESS
        if any(task.status != STATUS_NOT_STARTED for task in self.tasks):
            return STATUS_IN_PROGRESS
        return STATUS_NOT_STARTED

def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "feature"

def _workspace_root(folder_context) -> str:
    if folder_context and getattr(folder_context, "folders", None) and folder_context.folders:
        return os.path.abspath(folder_context.folders[0])
    return os.getcwd()

def save_feature_plan(plan: FeaturePlan) -> FeaturePlan:
    plan.updated_at = time.time()
    if plan.metadata_path:
        os.makedirs(os.path.dirname(plan.metadata_path), exist_ok=True)
        with open(plan.metadata_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(plan), handle, indent=2)
    return plan

def create_feature_plan(
    feature_name: str,
    feature_request: str,
    tasks_data: list[dict[str, Any]],
    folder_context=None,
    feature_id: str | None = None,
    metadata_path: str | None = None,
) -> FeaturePlan:
    workspace_root = _workspace_root(folder_context)
    slug = _slugify(feature_id or feature_name)
    
    tasks = []
    for idx, t in enumerate(tasks_data, start=1):
        tasks.append(
            FeatureTask(
                id=idx,
                title=str(t.get("title") or f"Task {idx}").strip(),
                objectives=[str(o).strip() for o in t.get("objectives", [])],
                action_points=[str(a).strip() for a in t.get("action_points", [])],
                exit_criteria=[str(e).strip() for e in t.get("exit_criteria", [])],
                notes=str(t.get("notes", "") or ""),
            )
        )

    plan = FeaturePlan(
        feature_id=slug,
        feature_name=feature_name.strip() or slug,
        feature_request=feature_request.strip() or feature_name.strip() or slug,
        directory=workspace_root,
        metadata_path=metadata_path or "",
        tasks=tasks,
    )
    return save_feature_plan(plan)

def load_feature_plan(metadata_path: str) -> FeaturePlan:
    with open(metadata_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    
    tasks_data = data.pop("tasks", [])
    tasks = [FeatureTask(**t) for t in tasks_data]
    return FeaturePlan(tasks=tasks, **data)

def refresh_and_persist_feature_plan(
    directory: str,
    metadata_path: str | None = None,
) -> FeaturePlan:
    if not metadata_path:
        raise ValueError("metadata_path is required for internal feature system")
    return load_feature_plan(metadata_path)

def summarize_feature_plan(plan: FeaturePlan) -> dict[str, Any]:
    summary = asdict(plan)
    summary["overall_status"] = plan.overall_status()
    summary["tasks_completed"] = plan.tasks_completed()
    summary["task_count"] = len(plan.tasks)
    
    next_task = plan.next_incomplete_task()
    summary["next_task"] = asdict(next_task) if next_task else None
    
    # For backward compatibility with things expecting 'phases'
    summary["phases"] = summary["tasks"]
    summary["next_phase"] = summary["next_task"]
    summary["phase_count"] = summary["task_count"]
    summary["phases_completed"] = summary["tasks_completed"]
    
    return summary

def update_feature_plan_metadata(
    directory: str, # Kept for compatibility
    *,
    approved: bool | None = None,
    review_status: str | None = None,
    review_notes: str | None = None,
    metadata_path: str | None = None,
) -> FeaturePlan:
    if not metadata_path:
        raise ValueError("metadata_path is required")
    plan = load_feature_plan(metadata_path)
    if approved is not None:
        plan.approved = approved
    if review_status is not None:
        plan.review_status = review_status
    if review_notes is not None:
        plan.review_notes = review_notes
    return save_feature_plan(plan)

def update_task_status(
    metadata_path: str,
    task_id: int,
    status: str,
    notes: str | None = None,
) -> FeaturePlan:
    plan = load_feature_plan(metadata_path)
    for task in plan.tasks:
        if task.id == task_id:
            task.status = status
            if notes is not None:
                task.notes = notes
            break
    return save_feature_plan(plan)

def update_task_content(
    metadata_path: str,
    task_id: int,
    title: str | None = None,
    objectives: list[str] | None = None,
    action_points: list[str] | None = None,
    exit_criteria: list[str] | None = None,
    notes: str | None = None,
) -> FeaturePlan:
    plan = load_feature_plan(metadata_path)
    for task in plan.tasks:
        if task.id == task_id:
            if title is not None: task.title = title
            if objectives is not None: task.objectives = objectives
            if action_points is not None: task.action_points = action_points
            if exit_criteria is not None: task.exit_criteria = exit_criteria
            if notes is not None: task.notes = notes
            break
    return save_feature_plan(plan)

def build_phase_execution_prompt(plan: FeaturePlan, task: FeatureTask) -> str:
    return (
        f"Continue implementing feature '{plan.feature_name}'. "
        f"Work on task {task.id}: '{task.title}' only. "
        f"Objectives: {', '.join(task.objectives)}. "
        f"Exit Criteria: {', '.join(task.exit_criteria)}. "
        "Update the task status to 'completed' only when all exit criteria are met. "
        "If you are blocked, explain why in your response."
    )

def build_review_prompt(plan: FeaturePlan) -> str:
    return (
        f"Review the completed feature '{plan.feature_name}'. "
        "Verify all tasks and their exit criteria were genuinely met. "
        "If everything passes review, set review_status to 'completed'. "
        "Otherwise, update the failing task status and explain the failure."
    )
