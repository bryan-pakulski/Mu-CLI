"""Durable job v2: planner-generated acceptance criteria + validation commands.

The planner proposes criteria and checks; this module merges them into the
job contract. Rules:

* User-supplied criteria/commands are never dropped or rewritten.
* Planner commands must start with an allow-listed executable and contain
  no shell operators (they run without a shell, but chaining/redirection in
  a model-written string is still refused rather than "interpreted").
* If the merged contract has zero validation commands the job parks on a
  human with ``NO_VALIDATION`` instead of entering implementation.
"""

from __future__ import annotations

import shlex
from typing import Any, Dict, Iterable, List, Tuple

from .models import AttentionReason, Job, JobStatus

DEFAULT_ALLOWED_EXECUTABLES = frozenset({
    "pytest", "python", "python3", "tox", "nox", "ruff", "mypy", "flake8", "black",
    "npm", "npx", "yarn", "pnpm", "node", "tsc", "jest", "eslint", "vitest",
    "make", "go", "cargo", "gradle", "./gradlew", "mvn", "dotnet", "bundle", "rake",
    "rspec", "php", "composer", "mix", "swift", "ctest", "cmake",
})

SHELL_OPERATORS = (";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "&")
MAX_PLANNER_COMMANDS = 8


def _dedupe(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def allowed_executables(job: Job) -> frozenset:
    extra = (job.execution or {}).get("validation_allowlist") or []
    if isinstance(extra, str):
        extra = [extra]
    return DEFAULT_ALLOWED_EXECUTABLES | {str(e).strip() for e in extra if str(e).strip()}


def check_command(command: str, allowed: frozenset) -> Tuple[bool, str]:
    text = str(command or "").strip()
    if not text:
        return False, "empty"
    for op in SHELL_OPERATORS:
        if op in text:
            return False, f"shell operator {op!r} not allowed"
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        return False, f"unparseable: {exc}"
    if not argv:
        return False, "empty"
    exe = argv[0]
    if exe not in allowed and exe.rsplit("/", 1)[-1] not in allowed:
        return False, f"executable {exe!r} not in allow-list"
    return True, ""


def merge_contract(job: Job, plan: Dict[str, Any]) -> Dict[str, Any]:
    allowed = allowed_executables(job)
    user_criteria = list(job.acceptance_criteria or [])
    user_commands = list(job.validation_commands or [])
    accepted: List[str] = []
    rejected: List[Dict[str, str]] = []
    for command in _dedupe(plan.get("validation_commands") or []):
        if command in user_commands:
            continue
        ok, why = check_command(command, allowed)
        if not ok:
            rejected.append({"command": command, "reason": why})
        elif len(accepted) < MAX_PLANNER_COMMANDS:
            accepted.append(command)
        else:
            rejected.append({"command": command, "reason": "planner command limit reached"})
    planner_criteria = [c for c in _dedupe(plan.get("acceptance_criteria") or []) if c not in user_criteria]
    return {
        "acceptance_criteria": _dedupe([*user_criteria, *planner_criteria]),
        "validation_commands": _dedupe([*user_commands, *accepted]),
        "planner_criteria": planner_criteria,
        "planner_commands": accepted,
        "rejected_commands": rejected,
    }


def apply_plan_contract(service, job_id: str, plan: Dict[str, Any]) -> Job:
    from .stages import merge_metadata

    job = service.get(job_id)
    contract = merge_contract(job, plan)
    service.store.update_runtime_fields(
        job_id,
        acceptance_json=contract["acceptance_criteria"],
        validation_json=contract["validation_commands"],
    )
    job = merge_metadata(service, job_id, plan_contract=contract)
    service.store.append_event(job_id, "plan_contract_applied", reason="dynamic checks merged", payload=contract)
    if not contract["validation_commands"] and job.status == JobStatus.PLANNING:
        service.require_human(
            job_id,
            AttentionReason.NO_VALIDATION,
            "The planner produced no allow-listed validation commands and none were supplied; "
            "add a validation command so MuCLI can verify the change.",
            payload=contract,
        )
        job = service.get(job_id)
    return job
