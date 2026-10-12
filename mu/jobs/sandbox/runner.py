"""Host supervisor for disposable job stage containers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets
from ..core.artifacts import artifacts_for, backfill
from ..models import AttentionReason, resolve_stage_model
from ..runner import JobRunOutcome, SessionJobRunner
from .model_relay import ModelRelay
from .policy import SandboxPolicy, container_args, validate_changes


class SandboxUnavailable(RuntimeError):
    pass


class StagePaused(RuntimeError):
    pass


def _read_output(directory: Path, name: str, limit=8 * 1024 * 1024):
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError(f"invalid sandbox output: {name}")
    return json.loads(path.read_text())


def _guard(job, policy):
    result = subprocess.run(["git", "-C", job.worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                            capture_output=True, check=True, timeout=30)
    entries = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    paths, skip = [], False
    for entry in entries:
        if skip:
            skip = False
            continue
        if entry:
            paths.append(entry[3:])
            skip = "R" in entry[:2] or "C" in entry[:2]
    from ..core.plan_ops import approved_plan_text
    from ..core.plan_doc import PlanDoc
    plan = approved_plan_text(job)
    allowed = PlanDoc.parse(plan).files if plan else []
    validate_changes(job.worktree, paths, allowed_workflows=allowed, max_file_bytes=policy.max_file_bytes)


def run_stage_in_container(service, job, stage, prompt, *, runner=None, relay_factory=ModelRelay):
    runner = runner or CommandRunner()
    policy = SandboxPolicy.for_stage(stage)
    try:
        docker = runner.require("docker")
        runner.run([docker, "image", "inspect", policy.image], timeout=15)
    except (ContainerRuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable("Sandbox required: Docker and the shared " + policy.image +
            " image must be available. Build it with python3 -m mu.jobs.sandbox.build.") from exc
    if not job.worktree or not Path(job.worktree).is_dir():
        raise SandboxUnavailable("Sandbox required: the job's managed worktree is unavailable")
    model = resolve_stage_model(job.execution, stage)
    art = artifacts_for(job).ensure()
    backfill(job)
    name = f"mucli-job-{job.id[:12]}-{stage}-{uuid.uuid4().hex[:8]}"
    def alive():
        return not job.worker_id or service.store.assert_lease(job.id, job.worker_id)
    relay = relay_factory(job, stage, model, policy, art, alive=alive)
    service.store.append_event(job.id, "sandbox_starting", payload={"stage": stage, "container": name, "image": policy.image})
    # Deliberately outside the job artifact folder: mounting that folder would
    # expose another stage's writable outputs and the durable worktree itself.
    with tempfile.TemporaryDirectory(prefix="mucli-job-") as tmp:
        root = Path(tmp)
        inputs, outputs, relay_dir = (root / p for p in ("input", "output", "relay"))
        for path in (inputs, outputs, relay_dir):
            path.mkdir()
        for filename in ("ticket.json", "plan.md", "diff.patch", "review.json", "checks/summary.json"):
            value = art.read_text(filename)
            if value is not None:
                target = inputs / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value)
        guidance = str((job.metadata or {}).get("resume_guidance") or "")
        previous = art.read_json(f"logs/{stage}-transcript.json", []) or []
        if guidance or previous:
            prompt += "\n\nPrevious stage transcript (context only):\n" + json.dumps(previous)[-24000:] + "\nHuman guidance:\n" + guidance
        (inputs / "request.json").write_text(json.dumps({"model": model, "prompt": prompt,
            "auto_approve_writes": bool(job.execution.get("auto_approve_writes", False)),
            "max_iterations": job.max_iterations if stage == "implement" else job.execution.get("stage_max_iterations", 16)}))
        created = False
        try:
            relay.start(relay_dir)
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(relay_dir, policy.egress_extra).start()
            else:
                bridge = None
            args = container_args(name, stage, job.worktree, str(inputs), str(outputs), str(relay_dir), policy, relay.token)
            runner.run([docker, *args], timeout=30)
            created = True
            errors, completed = [], threading.Event()
            def output(label, line):
                if label != "stdout":
                    return
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and data.get("event"):
                        safe = json.loads(redact_secrets(json.dumps(data))[0])
                        service.store.append_event(job.id, "sandbox_event", payload={"stage": stage, **safe})
                except (ValueError, TypeError):
                    pass
            def execute():
                try:
                    run_with_output(runner, [docker, "start", "-a", name], timeout=policy.timeout + 10, output_callback=output)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    completed.set()
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            deadline = time.monotonic() + policy.timeout
            while not completed.wait(.25):
                if not alive():
                    raise RuntimeError("sandbox stopped after losing the job lease")
                if time.monotonic() >= deadline:
                    raise StagePaused(f"{stage} sandbox reached its {policy.timeout}s time limit; resume with guidance")
            if errors:
                raise RuntimeError(f"{stage} container failed: {redact_secrets(str(errors[0]))[0][:1000]}")
            if not alive():
                raise RuntimeError("sandbox outcome discarded after lease loss")
            result = _read_output(outputs, "result.json")
            if not isinstance(result, dict) or result.get("kind") not in ("completed", "failed", "needs_human"):
                raise RuntimeError("invalid sandbox result")
            transcript = outputs / "transcript.json"
            if transcript.exists():
                art.write_json(f"logs/{stage}-transcript.json", _read_output(outputs, "transcript.json"))
            if stage == "implement":
                _guard(job, policy)
            result["cost_usd"] = relay.cost
            return result
        finally:
            relay.close()
            if created:
                runner.run([docker, "rm", "-f", name], check=False, timeout=20)
            if "bridge" in locals() and bridge is not None:
                bridge.close()
            service.store.append_event(job.id, "sandbox_stopped", payload={"stage": stage, "container": name})


class SandboxJobRunner:
    def __init__(self, service):
        self.service = service

    def run(self, job, attempt):
        prompt = SessionJobRunner(self.service, build_session_fn=None, base_args=None)._prompt(job)
        try:
            result = run_stage_in_container(self.service, job, "implement", prompt)
        except StagePaused as exc:
            return JobRunOutcome(kind="needs_human", attention_reason=AttentionReason.QUESTION, attention_detail=str(exc))
        except SandboxUnavailable:
            raise
        except Exception as exc:
            return JobRunOutcome(kind="failed", status="error", error=str(exc))
        return JobRunOutcome(kind=result["kind"], status=result.get("status", ""), error=result.get("error", ""),
            cost_usd=result["cost_usd"], result={"text": result.get("text", "")},
            attention_reason=AttentionReason(result.get("attention_reason") or "question"),
            attention_detail=result.get("error", ""), attention_payload=result.get("attention_payload") or {})


def generate(service):
    def run(job, prompt, workspace, model):
        result = run_stage_in_container(service, job, model["stage"], prompt)
        if result["kind"] == "needs_human":
            raise StagePaused(result.get("error") or "stage needs human input")
        if result["kind"] != "completed":
            raise RuntimeError(result.get("error") or "sandbox agent stage failed")
        return result.get("text", ""), {"cost_usd": result["cost_usd"]}
    return run
