#!/usr/bin/env python3
"""Capture Work view-model fixtures for the Android tests (documentation/plans/mobile_parity.md §8).

Builds a few real jobs with the Python job helpers in a throwaway MUCLI_HOME, then writes what the
server would answer into mobile/android/__tests__/fixtures/server/work/:

  home.json                    GET /api/jobs/vm/home
  job-plan-review.json         GET /api/jobs/vm/job/{id}   (waiting on plan approval)
  job-checked.json             GET /api/jobs/vm/job/{id}   (implementing, one checks run with artifacts)
  artifacts.json               GET /api/jobs/{id}/checks/{run}/artifacts
  preview-<kind>.json          GET /api/jobs/{id}/checks/{run}/preview?path=…   (log, junit, pcap, csv, json)

Run from the repository root:  python3 scripts/fixtures/capture_work.py
Temporary paths are replaced with /work so the fixtures stay stable.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mobile" / "android" / "__tests__" / "fixtures" / "server" / "work"

JUNIT = ('<?xml version="1.0"?><testsuites><testsuite name="unit" time="0.5">'
         '<testcase classname="pkg.mod" name="test_ok" time="0.1"/>'
         '<testcase classname="pkg.mod" name="test_bad" time="0.2"><failure message="assert 1 == 2">trace here</failure></testcase>'
         '<testcase classname="pkg.mod" name="test_skip"><skipped/></testcase></testsuite></testsuites>')
PLAN = {"summary": "Add the widget", "steps": ["edit widget.py"], "acceptance_criteria": ["widget renders"], "validation_commands": ["pytest -q"]}


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], text=True, capture_output=True, check=True).stdout.strip()


def make_repo(base: Path) -> Path:
    path = base / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.com")
    (path / "code.txt").write_text("base\n", encoding="utf-8")
    (path / ".gitignore").write_text("reports/\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    return path


def pcap() -> bytes:
    def frame(proto: int, src: str, dst: str, payload: bytes) -> bytes:
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, socket.inet_aton(src), socket.inet_aton(dst))
        return b"\xaa" * 6 + b"\xbb" * 6 + b"\x08\x00" + ip + payload
    q = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    dns = frame(17, "10.0.0.2", "10.0.0.1", struct.pack("!HHHH", 53000, 53, 8 + len(q), 0) + q)
    syn = frame(6, "10.0.0.2", "10.0.0.3", struct.pack("!HHIIBBHHH", 40000, 443, 1, 0, 0x50, 0x02, 65535, 0, 0))
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for i, f in enumerate([dns, syn]):
        out += struct.pack("<IIII", 1000 + i, 500, len(f), len(f)) + f
    return out


def suite_writes(job, workspace, suite):
    w = Path(workspace)
    (w / "reports").mkdir(exist_ok=True)
    (w / "reports" / "junit.xml").write_text(JUNIT)
    (w / "test.log").write_text("starting\nWARNING slow fixture\nERROR boom\nTraceback (most recent call last):\ndone\n")
    (w / "capture.pcap").write_bytes(pcap())
    (w / "timings.csv").write_text("test,seconds\ntest_ok,0.1\ntest_bad,0.2\n")
    (w / "coverage.json").write_text('{"total": 81.5, "files": {"widget.py": 90}}')
    return {"passed": False, "return_code": 1, "stdout": "1 failed, 1 passed, 1 skipped", "stderr": ""}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mucli-fixtures-"))
    os.environ["MUCLI_HOME"] = str(tmp / "home")
    sys.path.insert(0, str(ROOT))
    from mu.jobs import JobService, JobStatus, JobStore, JobSpec
    from mu.jobs import check_artifacts as ca
    from mu.jobs.checks import ChecksStage
    from mu.jobs.core import vm
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.core.plan_ops import record_planner_plan
    from mu.jobs.models import AttentionReason
    from mu.jobs.stages import merge_metadata
    from mu.jobs.worktree import JobWorktreeManager

    service = JobService(JobStore(str(tmp / "jobs.sqlite3")))
    repo = make_repo(tmp)
    execution = {"provider": "ollama", "model": "small", "session_type": "workspace"}

    # A job waiting on plan approval.
    waiting = service.create(JobSpec(title="Add the widget", repository=str(repo), execution=execution))
    service.transition(waiting.id, JobStatus.PREPARING)
    service.transition(waiting.id, JobStatus.PLANNING)
    merge_metadata(service, waiting.id, plan=PLAN)
    record_planner_plan(service.get(waiting.id), PLAN)
    service.require_human(waiting.id, AttentionReason.PLAN_REVIEW, "review the plan")

    # A running job with one checks run that produced artifacts.
    checked = service.create(JobSpec(title="Fix the login race", repository=str(repo), execution=execution))
    JobWorktreeManager(service, root=str(tmp / "worktrees")).prepare(checked)
    service.transition(checked.id, JobStatus.PREPARING)
    service.start_attempt(checked.id, worker_id="worker", session_name="job-session")
    service.transition(checked.id, JobStatus.RUNNING)
    service.acquire(checked.id, "worker", ttl_seconds=600)
    job = replace(service.get(checked.id), validation_commands=["pytest -q"])
    run = ChecksStage(service, run_suite=suite_writes, settings={}).verify(job)
    run_no = run.summary["run"]
    art = artifacts_for(job)
    listing = ca.run_listing(art, run_no)

    out = {
        "home.json": vm.home_vm(service),
        "job-plan-review.json": vm.job_vm(service, waiting.id),
        "job-checked.json": vm.job_vm(service, checked.id),
        "artifacts.json": listing,
    }
    wanted = {"log": "test.log", "junit": "reports/junit.xml", "pcap": "capture.pcap", "csv": "timings.csv", "json": "coverage.json"}
    for kind, name in wanted.items():
        entry = next(f for f in listing["files"] if f.get("name") == name)
        out[f"preview-{kind}.json"] = ca.build_preview(ca.resolve(art, run_no, entry["path"]))

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in out.items():
        text = json.dumps(data, indent=2, sort_keys=True, default=str).replace(str(tmp), "/work")
        (OUT / name).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT) / name}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
