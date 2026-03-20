from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_REVIEW_PENDING = "pending"

STATUS_MARKERS = {
    STATUS_NOT_STARTED: "[ ]",
    STATUS_IN_PROGRESS: "[~]",
    STATUS_COMPLETED: "[x]",
}
MARKER_TO_STATUS = {value: key for key, value in STATUS_MARKERS.items()}
SECTION_ORDER = ["Objectives", "Action Points", "Exit Criteria"]
FEATURE_DIRECTORY_PREFIX = "feature_req_"
FEATURE_PLAN_FILENAME = "feature_plan.json"


@dataclass
class FeatureTask:
    text: str
    status: str = STATUS_NOT_STARTED


@dataclass
class FeaturePhase:
    number: int
    title: str
    objectives: list[FeatureTask] = field(default_factory=list)
    action_points: list[FeatureTask] = field(default_factory=list)
    exit_criteria: list[FeatureTask] = field(default_factory=list)
    notes: str = ""

    def completion_status(self) -> str:
        tasks = self.all_tasks()
        if tasks and all(task.status == STATUS_COMPLETED for task in tasks):
            return STATUS_COMPLETED
        if any(task.status in {STATUS_IN_PROGRESS, STATUS_COMPLETED} for task in tasks):
            return STATUS_IN_PROGRESS
        return STATUS_NOT_STARTED

    def all_tasks(self) -> list[FeatureTask]:
        return [*self.objectives, *self.action_points, *self.exit_criteria]


@dataclass
class FeaturePlan:
    feature_id: str
    feature_name: str
    feature_request: str
    directory: str
    metadata_path: str = ""
    approved: bool = False
    review_status: str = STATUS_REVIEW_PENDING
    review_notes: str = ""
    phases: list[FeaturePhase] = field(default_factory=list)

    def next_incomplete_phase(self) -> FeaturePhase | None:
        for phase in self.phases:
            if phase.completion_status() != STATUS_COMPLETED:
                return phase
        return None

    def phases_completed(self) -> bool:
        return bool(self.phases) and all(
            phase.completion_status() == STATUS_COMPLETED for phase in self.phases
        )

    def overall_status(self) -> str:
        if self.review_status == STATUS_COMPLETED:
            return STATUS_COMPLETED
        if self.phases_completed():
            return STATUS_IN_PROGRESS
        if any(phase.completion_status() != STATUS_NOT_STARTED for phase in self.phases):
            return STATUS_IN_PROGRESS
        return STATUS_NOT_STARTED


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "feature"


def _workspace_root(folder_context) -> str:
    if folder_context and getattr(folder_context, "folders", None):
        return os.path.abspath(folder_context.folders[0])
    return os.getcwd()


def feature_plan_path(directory: str, metadata_path: str | None = None) -> str:
    return metadata_path or os.path.join(directory, FEATURE_PLAN_FILENAME)


def phase_markdown_path(directory: str, phase_number: int) -> str:
    return os.path.join(directory, f"phase_{phase_number}.md")


def phase_to_markdown(phase: FeaturePhase) -> str:
    lines = [f"# Phase {phase.number}: {phase.title}", ""]
    sections = {
        "Objectives": phase.objectives,
        "Action Points": phase.action_points,
        "Exit Criteria": phase.exit_criteria,
    }
    for section_name in SECTION_ORDER:
        lines.append(f"## {section_name}")
        tasks = sections[section_name]
        if tasks:
            for task in tasks:
                marker = STATUS_MARKERS.get(task.status, STATUS_MARKERS[STATUS_NOT_STARTED])
                lines.append(f"- {marker} {task.text}")
        else:
            lines.append("- [ ] TODO")
        lines.append("")

    if phase.notes.strip():
        lines.append("## Notes")
        lines.append(phase.notes.rstrip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _normalize_task_list(items: list[str] | list[dict[str, Any]] | None) -> list[FeatureTask]:
    tasks = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            status = str(item.get("status", STATUS_NOT_STARTED)).strip()
        else:
            text = str(item).strip()
            status = STATUS_NOT_STARTED
        if not text:
            continue
        if status not in STATUS_MARKERS:
            status = STATUS_NOT_STARTED
        tasks.append(FeatureTask(text=text, status=status))
    return tasks


def _plan_to_dict(plan: FeaturePlan) -> dict[str, Any]:
    return asdict(plan)


def _phase_from_dict(data: dict[str, Any]) -> FeaturePhase:
    return FeaturePhase(
        number=int(data.get("number", 0) or 0),
        title=str(data.get("title", "")).strip(),
        objectives=_normalize_task_list(data.get("objectives")),
        action_points=_normalize_task_list(data.get("action_points")),
        exit_criteria=_normalize_task_list(data.get("exit_criteria")),
        notes=str(data.get("notes", "") or ""),
    )


def _plan_from_dict(data: dict[str, Any]) -> FeaturePlan:
    return FeaturePlan(
        feature_id=str(data.get("feature_id", "")).strip(),
        feature_name=str(data.get("feature_name", "")).strip(),
        feature_request=str(data.get("feature_request", "")).strip(),
        directory=str(data.get("directory", "")).strip(),
        metadata_path=str(data.get("metadata_path", "") or ""),
        approved=bool(data.get("approved", False)),
        review_status=str(data.get("review_status", STATUS_REVIEW_PENDING) or STATUS_REVIEW_PENDING),
        review_notes=str(data.get("review_notes", "") or ""),
        phases=[_phase_from_dict(phase) for phase in data.get("phases", [])],
    )


def save_feature_plan(plan: FeaturePlan) -> FeaturePlan:
    os.makedirs(plan.directory, exist_ok=True)
    for phase in plan.phases:
        with open(phase_markdown_path(plan.directory, phase.number), "w", encoding="utf-8") as handle:
            handle.write(phase_to_markdown(phase))
    metadata_path = feature_plan_path(plan.directory, plan.metadata_path)
    plan.metadata_path = metadata_path
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(_plan_to_dict(plan), handle, indent=2)
    return plan


def create_feature_plan(
    feature_name: str,
    feature_request: str,
    phases: list[dict[str, Any]],
    folder_context=None,
    feature_id: str | None = None,
    metadata_path: str | None = None,
) -> FeaturePlan:
    workspace_root = _workspace_root(folder_context)
    slug = _slugify(feature_id or feature_name)
    directory = os.path.join(workspace_root, "documentation", f"{FEATURE_DIRECTORY_PREFIX}{slug}")
    normalized_phases = []
    for idx, phase in enumerate(phases, start=1):
        normalized_phases.append(
            FeaturePhase(
                number=idx,
                title=str(phase.get("title") or f"Phase {idx}").strip(),
                objectives=_normalize_task_list(phase.get("objectives")),
                action_points=_normalize_task_list(phase.get("action_points")),
                exit_criteria=_normalize_task_list(phase.get("exit_criteria")),
                notes=str(phase.get("notes", "") or ""),
            )
        )

    plan = FeaturePlan(
        feature_id=slug,
        feature_name=feature_name.strip() or slug,
        feature_request=feature_request.strip() or feature_name.strip() or slug,
        directory=directory,
        metadata_path=feature_plan_path(directory, metadata_path),
        phases=normalized_phases,
    )
    return save_feature_plan(plan)


def parse_phase_markdown(path: str, fallback_phase: FeaturePhase) -> FeaturePhase:
    phase = deepcopy(fallback_phase)
    current_section = None
    notes_lines: list[str] = []
    parsed = {"Objectives": [], "Action Points": [], "Exit Criteria": []}
    in_notes = False

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("## "):
                section_title = stripped[3:].strip()
                if section_title in parsed:
                    current_section = section_title
                    in_notes = False
                    continue
                if section_title == "Notes":
                    current_section = None
                    in_notes = True
                    continue
                current_section = None
                in_notes = False
                continue

            if in_notes:
                notes_lines.append(line)
                continue

            match = re.match(r"^-\s*(\[[ ~xX]\])\s+(.*)$", stripped)
            if match and current_section in parsed:
                marker, text = match.groups()
                status = MARKER_TO_STATUS.get(marker.lower().replace("x", "x"), STATUS_NOT_STARTED)
                normalized_marker = marker.lower().replace("x", "x")
                parsed[current_section].append(
                    FeatureTask(text=text.strip(), status=MARKER_TO_STATUS.get(normalized_marker, STATUS_NOT_STARTED))
                )

    if parsed["Objectives"]:
        phase.objectives = parsed["Objectives"]
    if parsed["Action Points"]:
        phase.action_points = parsed["Action Points"]
    if parsed["Exit Criteria"]:
        phase.exit_criteria = parsed["Exit Criteria"]
    phase.notes = "\n".join(notes_lines).strip()
    return phase


def load_feature_plan(
    directory: str,
    refresh_from_markdown: bool = True,
    metadata_path: str | None = None,
) -> FeaturePlan:
    resolved_metadata_path = feature_plan_path(directory, metadata_path)
    with open(resolved_metadata_path, "r", encoding="utf-8") as handle:
        plan = _plan_from_dict(json.load(handle))
    if not plan.metadata_path:
        plan.metadata_path = resolved_metadata_path

    if refresh_from_markdown:
        refreshed = []
        for phase in plan.phases:
            phase_path = phase_markdown_path(directory, phase.number)
            if os.path.exists(phase_path):
                refreshed.append(parse_phase_markdown(phase_path, phase))
            else:
                refreshed.append(phase)
        plan.phases = refreshed
    return plan


def refresh_and_persist_feature_plan(
    directory: str,
    metadata_path: str | None = None,
) -> FeaturePlan:
    plan = load_feature_plan(
        directory,
        refresh_from_markdown=True,
        metadata_path=metadata_path,
    )
    return save_feature_plan(plan)


def update_feature_plan_metadata(
    directory: str,
    *,
    approved: bool | None = None,
    review_status: str | None = None,
    review_notes: str | None = None,
    metadata_path: str | None = None,
) -> FeaturePlan:
    plan = load_feature_plan(
        directory,
        refresh_from_markdown=True,
        metadata_path=metadata_path,
    )
    if approved is not None:
        plan.approved = approved
    if review_status is not None:
        plan.review_status = review_status
    if review_notes is not None:
        plan.review_notes = review_notes
    return save_feature_plan(plan)


def summarize_feature_plan(plan: FeaturePlan) -> dict[str, Any]:
    phase_summaries = []
    for phase in plan.phases:
        phase_summaries.append(
            {
                "number": phase.number,
                "title": phase.title,
                "status": phase.completion_status(),
                "path": phase_markdown_path(plan.directory, phase.number),
                "task_counts": {
                    STATUS_COMPLETED: sum(task.status == STATUS_COMPLETED for task in phase.all_tasks()),
                    STATUS_IN_PROGRESS: sum(task.status == STATUS_IN_PROGRESS for task in phase.all_tasks()),
                    STATUS_NOT_STARTED: sum(task.status == STATUS_NOT_STARTED for task in phase.all_tasks()),
                },
            }
        )

    next_phase = plan.next_incomplete_phase()
    return {
        "feature_id": plan.feature_id,
        "feature_name": plan.feature_name,
        "feature_request": plan.feature_request,
        "directory": plan.directory,
        "metadata_path": plan.metadata_path,
        "approved": plan.approved,
        "review_status": plan.review_status,
        "review_notes": plan.review_notes,
        "overall_status": plan.overall_status(),
        "phases_completed": plan.phases_completed(),
        "phase_count": len(plan.phases),
        "phases": phase_summaries,
        "next_phase": (
            None
            if next_phase is None
            else {
                "number": next_phase.number,
                "title": next_phase.title,
                "path": phase_markdown_path(plan.directory, next_phase.number),
            }
        ),
    }


def build_phase_execution_prompt(plan: FeaturePlan, phase: FeaturePhase) -> str:
    phase_path = phase_markdown_path(plan.directory, phase.number)
    return (
        f"Continue implementing feature '{plan.feature_name}' using the approved phased plan in {plan.directory}. "
        f"Work on phase {phase.number} only, using {phase_path} as the source of truth. "
        "Implement the necessary code changes, update the phase markdown statuses as you go, and do not start the next phase until every checklist item in Objectives, Action Points, and Exit Criteria is marked [x]. "
        "If you are blocked or partially complete, mark the relevant checklist items as [~] and explain the blocker in your assistant response."
    )


def build_review_prompt(plan: FeaturePlan) -> str:
    return (
        f"Review the completed feature plan in {plan.directory}. Inspect every phase_*.md file and the related code changes to verify the exit criteria were genuinely met. "
        "If any phase is incomplete or implementation quality is unacceptable, update the relevant phase file by changing at least one item back to [~], explain the failure, and continue implementation. "
        "If everything passes review, update feature_plan.json so review_status is 'completed' and summarize the successful review."
    )
