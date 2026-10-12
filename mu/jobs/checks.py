"""Deterministic suites, JUnit evidence and baseline triage for the Checks step."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from .core.artifacts import artifacts_for
from .core.plan_ops import approved_plan_text
from .core.plan_doc import PlanDoc
from .dynamic_checks import allowed_executables, check_command
from .verification import VerificationCheck, VerificationRun, VerificationStore, DeterministicVerifier


@dataclass(frozen=True)
class Suite:
    name: str
    command: str
    junit: str = ""


def resolve_suites(job, settings=None):
    from .job_settings import load_settings
    settings = settings if settings is not None else load_settings()
    suites = {}
    plan = approved_plan_text(job)
    if plan:
        for check in PlanDoc.parse(plan).checks:
            suites[check.suite] = Suite(check.suite, check.cmd)
    else:
        suites = {f"check{i}": Suite(f"check{i}", cmd) for i, cmd in enumerate(job.validation_commands, 1)}
    configs = [settings.get("checks") or {}, (job.execution or {}).get("checks") or {}]
    repo = Path((job.metadata or {}).get("repository_root") or job.repository)
    config_path = repo / ".mucli/jobs.toml"
    if config_path.is_file():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        configs.append(tomllib.loads(config_path.read_text()).get("checks") or {})
    for config in configs:
        for raw in config.get("suites") or []:
            name = str(raw.get("name") or raw.get("suite") or "").strip()
            command = str(raw.get("command") or raw.get("cmd") or "").strip()
            if not name:
                raise ValueError("configured checks require a suite name")
            suites[name] = Suite(name, command, str(raw.get("junit") or ""))
    # Revalidate at execution, including configured commands and legacy jobs.
    for suite in suites.values():
        ok, reason = check_command(suite.command, allowed_executables(job))
        if not ok:
            raise ValueError(f"invalid check {suite.name}: {reason}")
        if suite.junit and (Path(suite.junit).is_absolute() or ".." in Path(suite.junit).parts):
            raise ValueError(f"JUnit path must be workspace-relative: {suite.junit}")
    return list(suites.values())


def parse_junit(text: str):
    if len(text) > 8 * 1024 * 1024 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("unsafe or oversized JUnit report")
    root = ET.fromstring(text)
    cases = []
    for node in root.iter("testcase"):
        status = "failed" if node.find("failure") is not None or node.find("error") is not None else (
            "skipped" if node.find("skipped") is not None else "passed")
        cases.append({"id": f"{node.get('classname', '')}::{node.get('name', '')}", "status": status,
                      "time": node.get("time", "0")})
    return {"total": len(cases), "passed": sum(c["status"] == "passed" for c in cases),
            "failed": sum(c["status"] == "failed" for c in cases),
            "skipped": sum(c["status"] == "skipped" for c in cases), "cases": cases}


def triage(current, baseline):
    """Only identical, observed failures can be attributed to the base commit."""
    current_ids = {c["id"] for c in current.get("junit", {}).get("cases", []) if c["status"] == "failed"}
    baseline_ids = {c["id"] for c in baseline.get("junit", {}).get("cases", []) if c["status"] == "failed"}
    if current_ids:
        return sorted(current_ids - baseline_ids), sorted(current_ids & baseline_ids)
    if current.get("passed"):
        return [], []
    if not current.get("timed_out") and not baseline.get("timed_out") and not baseline.get("passed"):
        log = str(current.get("stderr") or current.get("stdout") or "").strip()
        base_log = str(baseline.get("stderr") or baseline.get("stdout") or "").strip()
        if log and log == base_log and current.get("return_code") == baseline.get("return_code"):
            return [], ["suite failure (identical baseline output)"]
    return ["suite failed without matching baseline evidence"], []


def git(workspace, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", workspace, *args],
                          capture_output=True, text=True, timeout=60, check=True).stdout.strip()


class ChecksStage:
    def __init__(self, service, *, run_suite=None, settings=None):
        self.service = service
        if run_suite is None:
            from .sandbox.check_runner import run_suite_in_container
            run_suite = run_suite_in_container
        self.run_suite = run_suite
        self.settings = settings

    def verify(self, job):
        started = time.time()
        suites = resolve_suites(job, self.settings)
        art = artifacts_for(job).ensure()
        run_no = art.next_check_run()
        head = git(job.worktree, "rev-parse", "HEAD")
        initial_dirty = git(job.worktree, "status", "--porcelain")
        results = []
        for suite in suites:
            result = self.run_suite(job, job.worktree, suite)
            result.update(suite=suite.name, command=suite.command)
            results.append(result)
        failing = [r for r in results if not r.get("passed")]
        if failing and job.base_sha:
            with tempfile.TemporaryDirectory(prefix="mucli-check-base-") as tmp:
                baseline = str(Path(tmp) / "worktree")
                git(job.worktree, "worktree", "add", "--detach", baseline, job.base_sha)
                try:
                    for result in failing:
                        suite = next(s for s in suites if s.name == result["suite"])
                        base_result = self.run_suite(job, baseline, suite)
                        new, preexisting = triage(result, base_result)
                        result.update(new_failures=new, preexisting_failures=preexisting, baseline=base_result)
                finally:
                    git(job.worktree, "worktree", "remove", "--force", baseline)
        for result in failing:
            result.setdefault("new_failures", ["baseline unavailable"])
            result.setdefault("preexisting_failures", [])
        current_head = git(job.worktree, "rev-parse", "HEAD")
        dirty_status = git(job.worktree, "status", "--porcelain")
        dirty = bool(initial_dirty or dirty_status or current_head != head)
        new_count = sum(len(r.get("new_failures", [])) for r in results) + int(dirty)
        old_count = sum(len(r.get("preexisting_failures", [])) for r in results)
        passed = bool(suites) and not failing and not dirty
        summary = {"run": run_no, "head_sha": head, "passed": passed, "new_failures": new_count,
            "preexisting_failures": old_count, "suites": results, "dirty_worktree": dirty,
            "checks_total": len(suites), "checks_passed": sum(bool(r.get("passed")) for r in results),
            "checks_failed": len(failing), "finished_at": time.time()}
        checks = [VerificationCheck(command=r["command"], return_code=r.get("return_code"),
            passed=bool(r.get("passed")), timed_out=bool(r.get("timed_out")), duration_ms=int(r.get("duration_ms", 0)),
            stdout=str(r.get("stdout") or "")[-24000:], stderr=str(r.get("stderr") or "")[-24000:]) for r in results]
        status = "missing_contract" if not suites else "dirty_worktree" if dirty else "passed" if passed else "failed"
        changed, additions, deletions = DeterministicVerifier._changed_files(job.worktree, job.base_sha, head)
        run = VerificationRun(id=uuid.uuid4().hex, job_id=job.id, status=status, passed=passed,
            started_at=started, finished_at=time.time(), duration_ms=int((time.time() - started) * 1000),
            base_sha=job.base_sha, head_sha=head, branch=job.branch, worktree=job.worktree,
            checks=checks, summary=summary, changed_files=changed, additions=additions, deletions=deletions,
            dirty=dirty, dirty_status=dirty_status)
        VerificationStore(self.service.store).save(run)
        art.write_json(f"checks/run-{run_no}/summary.json", summary)
        art.write_json("checks/summary.json", summary)
        for index, result in enumerate(results, 1):
            art.write_text(f"checks/run-{run_no}/suite-{index}.log", str(result.get("stdout") or "") + "\n" + str(result.get("stderr") or ""))
            if result.get("junit_xml"):
                art.write_text(f"checks/run-{run_no}/suite-{index}.xml", result["junit_xml"])
        self.service.store.append_event(job.id, "checks_completed", payload={k: v for k, v in summary.items() if k != "suites"})
        return run


def push_check_reason(service, job, *, run=None):
    """A human review override never overrides the deterministic check gate."""
    run = run or VerificationStore(service.store).latest(job.id)
    if run is None or not run.passed:
        return "push requires a successful check run"
    workspace = job.worktree or job.repository
    try:
        reference = "HEAD" if job.worktree else job.branch
        head = git(workspace, "rev-parse", "--verify", f"{reference}^{{commit}}")
        if job.worktree and git(workspace, "status", "--porcelain"):
            return "worktree changed since checks; run checks again"
    except (OSError, subprocess.SubprocessError):
        return "cannot verify the job branch; run checks again"
    if head != run.head_sha:
        return "branch changed since checks; run checks again"
    return ""
