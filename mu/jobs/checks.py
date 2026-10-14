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
from .verification import VerificationCheck, VerificationRun, VerificationStore, changed_files as _changed_files


@dataclass(frozen=True)
class Suite:
    name: str
    command: str
    junit: str = ""
    env: str = ""   # build environment (binding name); blank = owner of cwd / named path / primary
    cwd: str = ""   # repository-relative working directory


def resolve_suites(job, settings=None):
    from .job_settings import load_settings
    settings = settings if settings is not None else load_settings()
    suites = {}
    plan = approved_plan_text(job)
    if plan:
        for check in PlanDoc.parse(plan).checks:
            suites[check.suite] = Suite(check.suite, check.cmd, env=check.env, cwd=check.cwd)
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
            # Required configuration adds to the plan; a reused suite name must
            # not silently erase an approved check with a different command.
            if name in suites and suites[name].command != command:
                name = f"{name}-required-{len(suites) + 1}"
            suites[name] = Suite(name, command, str(raw.get("junit") or ""), env=str(raw.get("env") or "").strip(),
                                 cwd=str(raw.get("cwd") or "").strip())
    # Revalidate at execution, including configured commands and legacy jobs.
    for suite in suites.values():
        ok, reason = check_command(suite.command, allowed_executables(job))
        if not ok:
            raise ValueError(f"invalid check {suite.name}: {reason}")
        if suite.junit and (Path(suite.junit).is_absolute() or ".." in Path(suite.junit).parts):
            raise ValueError(f"JUnit path must be workspace-relative: {suite.junit}")
        if suite.cwd and (Path(suite.cwd).is_absolute() or ".." in Path(suite.cwd).parts):
            raise ValueError(f"check {suite.name}: cwd must be repository-relative: {suite.cwd}")
    return list(suites.values())


def parse_junit(text: str):
    if len(text) > 8 * 1024 * 1024 or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("unsafe or oversized JUnit report")
    root = ET.fromstring(text)
    cases = []
    suites = [root] if root.tag == "testsuite" else []
    suites += [s for s in root.iter("testsuite") if s is not root]
    owner = {}
    for suite_node in suites:
        for child in suite_node:
            if child.tag == "testcase":
                owner[id(child)] = suite_node.get("name", "")
    for node in root.iter("testcase"):
        failure = node.find("failure")
        if failure is None:
            failure = node.find("error")
        status = "failed" if failure is not None else (
            "skipped" if node.find("skipped") is not None else "passed")
        case = {"id": f"{node.get('classname', '')}::{node.get('name', '')}", "status": status,
                "time": node.get("time", "0"),
                "suite": node.get("classname") or owner.get(id(node), ""), "name": node.get("name", "")}
        if failure is not None:
            parts = [str(failure.get("message") or "").strip(), str(failure.text or "").strip()[:4000]]
            case["message"] = "\n".join(p for p in parts if p)
        cases.append(case)
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


# Lockfiles a package manager may rewrite during a suite (install/resolve).
LOCKFILES = frozenset({
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
    "go.sum", "flake.lock", "mix.lock", "pubspec.lock", "Podfile.lock", "packages.lock.json",
})


def status_entries(workspace):
    """``(XY, path)`` for every porcelain entry, untracked files listed individually."""
    raw = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", workspace, "status", "--porcelain=v1", "-z",
                          "--untracked-files=all"], capture_output=True, text=True, timeout=60, check=True).stdout
    parts, out, i = raw.split("\0"), [], 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":
            i += 1  # the rename/copy source follows as its own NUL-terminated field
        out.append((code, path))
    return out


def discard_byproducts(workspace, baseline, suite):
    """Undo what a suite left behind that is a build byproduct, not a source edit:
    new untracked files (removed) and rewritten lockfiles (restored to HEAD).
    Entries already present before checks started (``baseline``) are left alone,
    and any other change stays in place so the dirty-tree gate still fails."""
    found = []
    for code, path in status_entries(workspace):
        if (code, path) in baseline:
            continue
        if code == "??":
            git(workspace, "clean", "-f", "-q", "--", path)
            found.append({"suite": suite, "path": path, "kind": "untracked"})
        elif "M" in code and "D" not in code and "A" not in code and os.path.basename(path) in LOCKFILES:
            git(workspace, "checkout", "-q", "HEAD", "--", path)
            found.append({"suite": suite, "path": path, "kind": "lockfile"})
    return found


class ChecksStage:
    def __init__(self, service, *, run_suite=None, settings=None):
        self.service = service
        self._sandboxed = run_suite is None
        self._states: list = []
        self._owners: dict = {}
        if run_suite is None:
            from .sandbox.check_runner import run_suite_in_container
            run_suite = lambda job, workspace, suite: run_suite_in_container(
                job, workspace, suite, service=service, restart_cursor=self._restart_cursor,
                environment=self._owners.get(suite.name), states=self._states)
        self.run_suite = run_suite
        self.settings = settings

    def _run_batched(self, job, suites):
        """One container per build environment for all of its suites; environments run
        concurrently. Returns ``{suite name: result}``."""
        from concurrent.futures import ThreadPoolExecutor
        from .sandbox.check_runner import run_suites_in_container
        groups = {}
        for suite in suites:
            owner = self._owners.get(suite.name)
            groups.setdefault(owner["name"] if owner else "", (owner, []))[1].append(suite)

        def run(group):
            owner, members = group
            started = time.monotonic()
            results = run_suites_in_container(job, job.worktree, members, service=self.service,
                                              restart_cursor=self._restart_cursor, environment=owner, states=self._states)
            share = int((time.monotonic() - started) * 1000 / max(1, len(members)))
            for result in results:
                result.setdefault("duration_ms", share)
            return dict(zip((m.name for m in members), results))
        out = {}
        with ThreadPoolExecutor(max_workers=max(1, min(3, len(groups)))) as pool:
            for part in pool.map(run, list(groups.values())):
                out.update(part)
        return out

    def _assign_environments(self, job, suites):
        """Each suite runs in the build environment that owns it (§4)."""
        from .sandbox.environments import EnvironmentBlocked, binding_for_suite, ensure_environments, job_states
        from .sandbox.runner import planned_files
        self._states = ensure_environments(self.service, job) if self._sandboxed else job_states(job)
        files = planned_files(job)
        try:
            self._owners = {suite.name: binding_for_suite(self._states, suite, files) for suite in suites}
        except ValueError as exc:
            raise EnvironmentBlocked(str(exc)) from exc

    def verify(self, job):
        from .sandbox.runner import StageRestart
        while True:
            self._restart_cursor = self.service.store.event_cursor()
            try:
                return self._verify_once(job)
            except StageRestart:
                self.service.store.append_event(job.id, "stage_restarted", payload={"stage": "checks"})
                job = self.service.get(job.id)

    def _verify_once(self, job):
        started = time.time()
        suites = resolve_suites(job, self.settings)
        self._assign_environments(job, suites)
        art = artifacts_for(job).ensure()
        run_no = art.next_check_run()
        head = git(job.worktree, "rev-parse", "HEAD")
        initial_dirty = git(job.worktree, "status", "--porcelain")
        baseline_entries = set(status_entries(job.worktree))
        byproducts = []
        trigger = str((self.service.get(job.id).metadata or {}).get("checks_trigger") or "auto")
        if trigger not in ("auto", "recheck", "fix"):
            trigger = "auto"
        results = []
        batched = self._run_batched(job, suites) if self._sandboxed else {}
        if batched:
            byproducts += discard_byproducts(job.worktree, baseline_entries, ",".join(batched))
        for suite in suites:
            suite_started = time.monotonic()
            result = batched[suite.name] if suite.name in batched else self.run_suite(job, job.worktree, suite)
            elapsed = int((time.monotonic() - suite_started) * 1000)
            if suite.name not in batched:
                # Between suites: a suite's byproducts must not leak into the next one.
                found = discard_byproducts(job.worktree, baseline_entries, suite.name)
                byproducts += found
                if found:
                    result["byproducts"] = [b["path"] for b in found]
            owner = self._owners.get(suite.name)
            result.update(suite=suite.name, command=suite.command, environment=owner["name"] if owner else "",
                          environment_docker=bool(owner and owner.get("docker")))
            try:
                result["duration_ms"] = int(result.get("duration_ms") if result.get("duration_ms") is not None else elapsed)
            except (TypeError, ValueError):
                result["duration_ms"] = elapsed
            results.append(result)
        failing = [r for r in results if not r.get("passed")]
        missing_tools = sorted({r["missing_executable"] for r in results if r.get("missing_executable")})
        missing_environments = {r["missing_executable"]: r.get("environment") or "" for r in results if r.get("missing_executable")}
        for result in results:
            if result.get("missing_executable"):
                # The base commit would fail the same way; this is an environment gap, not a regression.
                result.update(new_failures=[], preexisting_failures=[], environment_error=result["stderr"])
        failing_for_triage = [r for r in failing if not r.get("missing_executable")]
        from .job_settings import load_settings
        settings = self.settings if self.settings is not None else load_settings()
        baseline_enabled = (job.execution.get("checks") or {}).get("baseline_triage", (settings.get("checks") or {}).get("baseline_triage", True))
        if failing_for_triage and job.base_sha and baseline_enabled:
            with tempfile.TemporaryDirectory(prefix="mucli-check-base-") as tmp:
                baseline = str(Path(tmp) / "worktree")
                git(job.worktree, "worktree", "add", "--detach", baseline, job.base_sha)
                try:
                    for result in failing_for_triage:
                        suite = next(s for s in suites if s.name == result["suite"])
                        base_result = self.run_suite(job, baseline, suite)
                        new, preexisting = triage(result, base_result)
                        result.update(new_failures=new, preexisting_failures=preexisting, baseline=base_result)
                finally:
                    git(job.worktree, "worktree", "remove", "--force", baseline)
        for result in failing_for_triage:
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
            "checks_failed": len(failing), "finished_at": time.time(),
            "started_at": started, "duration_ms": int((time.time() - started) * 1000), "trigger": trigger,
            "byproducts": byproducts,
            "missing_tools": missing_tools, "missing_tool_environments": missing_environments,
            "missing_docker_suites": [{"suite": r["suite"], "environment": r.get("environment") or ""} for r in results
                                      if r.get("missing_executable") == "docker" and not r.get("environment_docker")]}
        checks = [VerificationCheck(command=r["command"], return_code=r.get("return_code"),
            passed=bool(r.get("passed")), timed_out=bool(r.get("timed_out")), duration_ms=int(r.get("duration_ms", 0)),
            stdout=str(r.get("stdout") or "")[-24000:], stderr=str(r.get("stderr") or "")[-24000:]) for r in results]
        status = "missing_contract" if not suites else "dirty_worktree" if dirty else "passed" if passed else "failed"
        changed, additions, deletions = _changed_files(job.worktree, job.base_sha, head)
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
        # The trigger describes one run; later automatic runs are "auto" again.
        fresh = self.service.get(job.id)
        if (fresh.metadata or {}).get("checks_trigger"):
            meta = dict(fresh.metadata)
            meta.pop("checks_trigger", None)
            self.service.store.update_runtime_fields(job.id, metadata_json=meta)
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
