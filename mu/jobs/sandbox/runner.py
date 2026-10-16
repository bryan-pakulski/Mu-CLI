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
from ..runner import JobRunOutcome, implement_prompt
from .model_relay import HarnessRelay, ModelRelay
from .worker import classify_error
from .policy import with_build_environment, SandboxPolicy, container_args, validate_changes


class SandboxUnavailable(RuntimeError):
    pass


class StagePaused(RuntimeError):
    pass


class StageLimitReached(StagePaused):
    """A cost, token, time or iteration cap was reached. The stage paused resumably (its
    conversation and harness session are kept) and the job waits for a human to approve more
    (``raiseLimit``) or cancel. ``detail`` is ``mu.jobs.limits.limit_detail``."""

    def __init__(self, detail):
        super().__init__(str(detail.get("message") or "limit reached"))
        self.detail = dict(detail)


class StageRestart(RuntimeError):
    pass


class SandboxGuardError(ValueError):
    """A finished stage left the worktree in a state that must not be committed or pushed."""


# Shown on every surface (terminal, web, mobile): no terminal keys, no module invocations.
SETUP_HINT = "Open Setup → Job runtime image to build or refresh it"


def check_restart(service, job, stage, cursor):
    names = {"plan": "planning", "implement": "implementing", "review": "reviewing", "checks": "checking"}
    events = service.store.list_events_by_type(job.id, ("stage_restart_requested",))
    if any(e.id > cursor and e.payload.get("stage") == names[stage] for e in events):
        raise StageRestart(f"restart {stage}")


def check_pause(service, job, cursor):
    """Payload of a ``stage_pause_requested`` event (the controller's graceful stop for a job past
    its runtime limit) logged after ``cursor``, else None."""
    events = service.store.list_events_by_type(job.id, ("stage_pause_requested",))
    fresh = [e for e in events if e.id > cursor]
    return dict(fresh[-1].payload or {}) if fresh else None


# A stage whose model relay refused a request at a budget normally stops on its own; if it is
# still running this long after the refusal it is stopped the same graceful way as a time limit.
LIMIT_STOP_GRACE_S = 30.0


def _limit_paused(service, job, detail):
    """Record the reached cap on the job and return the exception that pauses the stage."""
    from ..limits import record_pause
    record_pause(service, job.id, detail)
    return StageLimitReached(detail)


def _job_time_detail(service, job, stage):
    from ..limits import limit_detail, remaining_runtime
    fresh = service.get(job.id)
    limit = float(fresh.max_runtime_seconds or job.max_runtime_seconds or 0)
    left = remaining_runtime(service, fresh)
    used = limit - float(left) if left is not None else limit
    return limit_detail("time", "job", stage, used, limit)


def claim_stage_response(service, job, stage, payload):
    """The relay's ``/interaction``. Jobs run unattended (``mu.jobs.unattended``): a human
    decision recorded for this exact request (a job parked before that rule) is consumed once;
    otherwise the automatic response is returned and recorded here as ``auto_permission`` /
    ``auto_answered`` (``auto``: the stage does not record it again). An approval in a read-only
    stage is always denied."""
    from ..ui import JobUI
    from ..unattended import answered_event, event_reason, interaction_response, permission_event, read_only
    kind = str(payload.get("kind") or "question")
    if not (kind == "approval_required" and read_only(stage)):
        ui = JobUI(service, job.id, stage=stage)
        if kind == "approval_required":
            ui._responses = [e for e in ui._responses if
                (e.payload.get("target") or {}).get("tool_name") == payload.get("tool_name") and
                (e.payload.get("target") or {}).get("tool_args") == payload.get("tool_args")]
        human = ui._take_response(kind, tool_name=str(payload.get("tool_name") or ""))
        if human is not None:
            return human
    response = interaction_response(stage, kind, payload)
    tool_name = str(payload.get("tool_name") or "")
    if kind == "approval_required":
        event_type = "auto_permission"
        recorded = permission_event(stage, tool_name, allow=response["decision"] == "approve", source="host")
    else:
        event_type = "auto_answered"
        recorded = answered_event(stage, str(payload.get("shape") or kind), str(payload.get("message") or ""))
    service.store.append_event(job.id, event_type, reason=event_reason(event_type, recorded), payload=recorded)
    return response


def overflow_retry_prompt(prompt, result, stage):
    """The stage brief for a context_overflow retry: the original brief plus what the first
    conversation achieved (its L2 summary, else its last reply). The worktree keeps its edits."""
    summary = str(result.get("summary") or "").strip() or str(result.get("text") or "").strip()
    note = (f"\n\nCONTEXT RECOVERY: an earlier run of this {stage} stage ran out of model context and was "
            "restarted with a fresh conversation. Any changes it made are still in /workspace; check "
            "them (for example git status and git diff) before continuing, and do not redo finished work.")
    if summary:
        note += "\n\nSummary of the earlier run:\n" + summary
    return str(prompt or "") + note


def run_stage_in_container(service, job, stage, prompt, **kwargs):
    spent = 0.0  # runs discarded by a restart were still paid for
    overflow_retried = False
    while True:
        try:
            result = _run_stage_once(service, job, stage, prompt, **kwargs)
            if result.get("error_class") == "context_overflow" and not overflow_retried:
                # Every in-stage recovery rung failed: retry ONCE with a fresh conversation seeded
                # from the summary and the stage brief, instead of stopping the job.
                overflow_retried = True
                spent += float(result.get("cost_usd") or 0)
                service.store.append_event(job.id, "stage_context_overflow", reason=str(result.get("error") or "")[:500],
                                           payload={"stage": stage, "retry": "fresh_conversation"})
                prompt = overflow_retry_prompt(prompt, result, stage)
                kwargs = {**kwargs, "fresh": True}
                job = service.get(job.id)
                continue
            result["cost_usd"] = float(result.get("cost_usd") or 0) + spent
            return result
        except StageRestart as exc:
            spent += _spent(exc)
            service.store.append_event(job.id, "stage_restarted", payload={"stage": stage})
            _mark_resumable(service, job, stage, False)  # a restart discards the stage's work
            job = service.get(job.id)
        except BaseException as exc:
            if spent:
                try:
                    exc.cost_usd = _spent(exc) + spent
                except (AttributeError, TypeError):
                    pass
            raise


def _read_output(directory: Path, name: str, limit=8 * 1024 * 1024):
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError(f"invalid sandbox output: {name}")
    return json.loads(path.read_text())


def _git(job, *args, timeout=60):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", job.worktree, *args],
                          capture_output=True, check=True, timeout=timeout)


def worktree_patch(job, *, max_untracked_bytes=20 * 1024 * 1024) -> str:
    """Everything the job would commit relative to its base: tracked changes (committed or
    not) plus untracked, non-ignored files as additions. Read-only (no index changes).

    ``--text``: binary files and paths a (job-written) ``.gitattributes`` marks ``-diff`` are
    diffed as text, never hidden behind "Binary files differ". An untracked file larger than
    ``max_untracked_bytes`` is refused (ValueError), never silently left unscanned."""
    base = str(getattr(job, "base_sha", "") or "") or "HEAD"
    patch = _git(job, "diff", "--no-color", "--text", "--no-ext-diff", "--no-textconv", base, "--").stdout.decode(
        "utf-8", errors="replace")
    parts = [patch]
    untracked = _git(job, "ls-files", "-z", "--others", "--exclude-standard").stdout.decode("utf-8", "surrogateescape")
    root = Path(job.worktree)
    for name in (n for n in untracked.split("\0") if n):
        path = root / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > max_untracked_bytes:
                raise ValueError(f"untracked file exceeds the sandbox size limit and cannot be scanned: {name}")
            lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        parts.append(f"diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n"
                     f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines))
    return "".join(parts)


def injected_literals(agent) -> list:
    """Credential values the host copied into the stage (Claude Code / Codex); mucli stages get none."""
    if not agent or agent == "mucli":
        return []
    from .. import harness as harnesses
    try:
        creds = harnesses.credentials(agent) or {}
    except Exception:  # noqa: BLE001 - the pattern scan still runs
        return []
    values = [str(v) for v in (creds.get("env") or {}).values() if v]

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from leaves(item)
        elif isinstance(value, str):
            yield value
    for content in (creds.get("files") or {}).values():
        try:
            values += list(leaves(json.loads(content)))
        except (TypeError, ValueError):
            values += [line.strip() for line in str(content).splitlines() if line.strip()]
    return values


def job_injected_literals(job) -> list:
    """Credential values copied into any of ``job``'s stages (every Claude Code / Codex agent
    the job runs); the publish scan uses the same source as the post-stage guard."""
    from .. import harness as harnesses
    try:
        agents = {cfg["agent"] for cfg in harnesses.stage_agents(job).values()}
    except Exception:  # noqa: BLE001 - the pattern scan still runs
        return []
    return list(dict.fromkeys(v for agent in sorted(agents) for v in injected_literals(agent)))


def _offer_repository_egress(service, job, stage) -> None:
    """Repository ``[sandbox] egress`` is applied only once approved; until then the stage runs
    without it and the approval is offered (one event per job and domain list)."""
    if stage not in ("implement", "checks"):
        return
    try:
        from .repo_config import egress_approval
        request = egress_approval(job)
        if not request or request["approved"]:
            return
        seen = service.store.list_events_by_type(job.id, ("repository_egress_approval_required",))
        if any((e.payload or {}).get("fingerprint") == request["fingerprint"] for e in seen):
            return
        service.store.append_event(
            job.id, "repository_egress_approval_required",
            reason=(f"The repository's .mucli/jobs.toml asks for {request['label']}; it is not applied until you "
                    "approve it for this repository (New job → Environments). Stages run without it.")[:500],
            payload={k: v for k, v in request.items() if k not in ("approved",)})
    except Exception:  # noqa: BLE001 - advisory only; egress stays unapplied
        pass


def _guard(job, policy, *, literals=()):
    """Host check after a writing stage (implement, checks): the worktree may be committed and
    pushed only if its changed paths are safe, it holds no secret files, and the diff carries
    no credentials. Failures surface at the stage that caused them."""
    result = _git(job, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=30)
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
    from .policy import workspace_precheck
    try:
        validate_changes(job.worktree, paths, allowed_workflows=allowed, max_file_bytes=policy.max_file_bytes)
        workspace_precheck(job.worktree, allow_paths=getattr(policy, "allow_paths", ()))
    except ValueError as exc:
        raise SandboxGuardError(str(exc)) from exc
    from mu.security.diff_scan import scan_patch, summarize
    try:
        patch = worktree_patch(job, max_untracked_bytes=policy.max_file_bytes)
    except ValueError as exc:
        raise SandboxGuardError(str(exc)) from exc
    findings = scan_patch(patch, literals=literals)
    if findings:
        raise SandboxGuardError("the job's changes contain secrets and cannot be committed or pushed: "
                                + summarize(findings))


def _keep_transcript(outputs, art, stage):
    """Persist the stage conversation from the container output (if it wrote one)."""
    try:
        if (outputs / "transcript.json").exists():
            art.write_json(f"logs/{stage}-transcript.json", _read_output(outputs, "transcript.json", limit=64 * 1024 * 1024))
    except (RuntimeError, ValueError, OSError):
        pass


def _mark_resumable(service, job, stage, resumable):
    """A paused stage (time limit, approval, question) resumes its own conversation;
    a finished one starts fresh next time (fix attempts, re-plan, review rounds)."""
    try:
        meta = dict(service.get(job.id).metadata or {})
        resume = dict(meta.get("stage_resume") or {})
        if resumable:
            resume[stage] = time.time()
        elif stage in resume:
            resume.pop(stage)
        else:
            return
        meta["stage_resume"] = resume
        service.store.update_runtime_fields(job.id, metadata_json=meta)
    except Exception:  # noqa: BLE001 - resumption is an optimisation
        pass


def _keep_harness_state(art, agent, stage, state_dir, result):
    """Copy the CLI session store back into the job and remember the stage's session id."""
    from .. import harness as harnesses
    try:
        harnesses.copy_state(str(state_dir), art.path("harness", agent, "state"))
        info = (result or {}).get("harness") or {}
        if info.get("session_id"):
            art.write_json(f"harness/{agent}/{stage}.json", {"session_id": info["session_id"], "model": info.get("model", ""),
                                                             "at": time.time(), "usage": info.get("usage") or {},
                                                             "cost_usd": info.get("cost_usd")})
    except (OSError, ValueError):
        pass


def planned_files(job):
    """The approved plan's "Files likely touched" (picks the primary build environment)."""
    from ..core.plan_ops import approved_plan_text
    from ..core.plan_doc import PlanDoc
    try:
        plan = approved_plan_text(job)
    except (OSError, ValueError):
        plan = ""
    return PlanDoc.parse(plan).files if plan else []


def stage_environment(service, job, stage):
    """Prepare the job's build environments; returns (all states, the binding this stage runs in)."""
    from .environments import ensure_environments, primary_binding
    states = ensure_environments(service, job)
    return states, primary_binding(states, planned_files(job))


def _model_request_timeout() -> int:
    from ..job_settings import rule_or
    return int(rule_or("sandbox.model_request_timeout_s", 3600))


def _context_window(relay, model):
    """The stage model's real input window from the host provider (None when unknown)."""
    try:
        window = relay.provider.effective_context_window(model.get("model"))
        return int(window) if window else None
    except Exception:  # noqa: BLE001 - stub relays and providers without the API
        return None


def harness_context_window(agent, model_name):
    """A harness model's window from the pricing table (None when unknown: the CLI's default)."""
    if agent != "codex" or not model_name:
        return None
    try:
        from utils.model_pricing import resolve_token_pricing
        item = resolve_token_pricing("openai", str(model_name))
        window = int(getattr(item, "context_window", 0) or 0) if item else 0
    except Exception:  # noqa: BLE001 - a missing price row just leaves Codex's default
        return None
    return window or None


def _stage_effort(stage) -> str:
    from ..job_settings import rule_or
    return str(rule_or(f"jobs.stage_effort.{stage}", "") or "")


def _tool_timeout() -> int:
    from ..job_settings import rule_or
    return int(rule_or("sandbox.tool_timeout_s", 3600))

def require_image(runner, policy, environment):
    """Docker and the stage image must exist; returns the docker binary."""
    try:
        from .cli import container_cli
        docker = runner.require(container_cli())
        runner.run([docker, "image", "inspect", policy.image], timeout=15)
        return docker
    except (ContainerRuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        if environment:
            raise SandboxUnavailable(f"Build environment `{environment['name']}`: its job image {policy.image} is "
                                     "missing; retry the job to rebuild its build environments.") from exc
        raise SandboxUnavailable(f"Sandbox required: Docker and the shared {policy.image} image must be "
                                 f"available. {SETUP_HINT}.") from exc


def runtime_image_warning(label, current, image) -> str:
    """Text when an image's code fingerprint (``io.mucli.source``) is not this host's code."""
    if label is None or label == current:
        return ""
    if label == "":
        return (f"{image} carries no code fingerprint (built by an older mucli), so stages may run old "
                f"job code. {SETUP_HINT}.")
    return (f"{image} was built from older mucli code ({label} ≠ {current}); stages run that old job code. "
            f"{SETUP_HINT}.")


def check_runtime_image(service, job, runner, docker, policy):
    """Warn on the job (once per fingerprint) when the stage image runs stale mucli code; never blocks."""
    from .build import IMAGE, SOURCE_LABEL, source_digest
    try:
        out = runner.run([docker, "image", "inspect", "--format", "{{index .Config.Labels \"%s\"}}" % SOURCE_LABEL,
                          policy.image], check=False, timeout=15)
        if getattr(out, "returncode", 0):
            return ""
        label = str(getattr(out, "stdout", "") or "").strip()
        label = "" if label in ("", "<no value>") else label
        if label == "" and policy.image != IMAGE:
            return ""  # a derived build-environment image without the inherited label: unknown
        warning = runtime_image_warning(label, source_digest(), policy.image)
    except Exception:  # noqa: BLE001 - staleness is advisory
        return ""
    if not warning:
        return ""
    try:
        meta = dict(service.get(job.id).metadata or {})
        if meta.get("runtime_image_warning") != warning:
            meta["runtime_image_warning"] = warning
            service.store.update_runtime_fields(job.id, metadata_json=meta)
            service.store.append_event(job.id, "runtime_image_stale", reason=warning[:500],
                                       payload={"image": policy.image, "warning": warning})
    except Exception:  # noqa: BLE001
        pass
    return warning


def _run_stage_once(service, job, stage, prompt, *, runner=None, relay_factory=ModelRelay, fresh=False):
    from dataclasses import replace as _replace
    from .. import limits
    restart_cursor = service.store.event_cursor()
    runner = runner or CommandRunner()
    # The job runtime limit pauses the job before a stage starts once it is used up, and caps the
    # stage's time limit at what is left, so the stage pauses (resumably) right at the limit.
    left = limits.remaining_runtime(service, job)
    if left is not None and left <= 0:
        raise _limit_paused(service, job, _job_time_detail(service, job, stage))
    states, environment = stage_environment(service, job, stage)
    policy = with_build_environment(SandboxPolicy.for_stage(stage), job, environment, stage=stage)
    # Approved extra stage cost, tokens and time for this job ("Approve more", metadata.limit_grants).
    policy = limits.with_grants(policy, job, stage)
    stage_timeout = int(policy.timeout)
    left = limits.remaining_runtime(service, job)
    time_scope = "stage"
    if left is not None and left <= 0:
        raise _limit_paused(service, job, _job_time_detail(service, job, stage))
    if left is not None and left < stage_timeout:
        policy, time_scope = _replace(policy, timeout=max(1, int(left + 0.999))), "job"
    _offer_repository_egress(service, job, stage)
    docker = require_image(runner, policy, environment)
    check_runtime_image(service, job, runner, docker, policy)
    if not job.worktree or not Path(job.worktree).is_dir():
        raise SandboxUnavailable("Sandbox required: the job's managed worktree is unavailable")
    from .. import harness as harnesses
    agent_cfg = harnesses.stage_agent(job, stage)
    agent = agent_cfg["agent"]
    harness_mounts = []
    if agent != "mucli":
        # Claude Code / Codex run inside the same stage container; they reach their own provider
        # through the egress bridge, so the stage needs no mucli model.
        from dataclasses import replace
        try:
            harness_mounts = harnesses.binary_mounts(agent)
            if not harnesses.credentials(agent):
                raise PermissionError(f"No {harnesses.AGENT_LABELS[agent]} credentials are configured")
        except (OSError, PermissionError) as exc:
            raise StagePaused(f"{stage} runs on {harnesses.AGENT_LABELS[agent]}: {exc}. Fix it in Settings → Agent "
                              "harnesses (or pick another agent for this stage) and resume.") from exc
        policy = replace(policy, egress_extra=tuple(dict.fromkeys((*policy.egress_extra, *harnesses.provider_hosts(agent)))))
        model = {"provider": agent, "model": agent_cfg["model"], "stage": stage}
    else:
        model = resolve_stage_model(job.execution, stage)
    art = artifacts_for(job).ensure()
    backfill(job)
    name = f"mucli-job-{job.id[:12]}-{stage}-{uuid.uuid4().hex[:8]}"
    def alive():
        return not job.worker_id or service.store.assert_lease(job.id, job.worker_id)
    stage_state = {"plan": "planning", "implement": "implementing", "review": "reviewing"}[stage]
    def message_events():
        events = service.store.list_events_by_type(job.id, ("stage_message",))
        return [(e.id, str(e.payload.get("text") or "")) for e in events if e.payload.get("stage") == stage_state][-20:]

    def messages():
        return [text for _, text in message_events()]
    interaction = lambda payload: claim_stage_response(service, job, stage, payload)  # noqa: E731
    if agent != "mucli":
        relay = HarnessRelay(job, stage, model, policy, art, agent=agent, alive=alive, interaction=interaction,
                             message_events=message_events)
    else:
        relay = relay_factory(job, stage, model, policy, art, alive=alive, messages=messages, interaction=interaction)
    if hasattr(relay, "on_error"):
        relay.on_error = lambda info: service.store.append_event(job.id, "model_relay_error", reason=info.get("message", "")[:500], payload=info)
    if hasattr(relay, "on_warning"):
        relay.on_warning = lambda info: service.store.append_event(job.id, "model_relay_warning", reason=info.get("message", "")[:500], payload=info)
    from .broker import EnvironmentBroker, describe
    ready = [s for s in states if s.get("status") == "ready"]
    if stage == "implement" and len(ready) > 1:
        relay.broker = EnvironmentBroker(service, job, ready, stage=stage)
    service.store.append_event(job.id, "sandbox_starting", payload={
        "stage": stage, "container": name, "image": policy.image, "environment": policy.binding, "agent": agent,
        "agent_model": model.get("model") if agent != "mucli" else "",
        "gpus": policy.gpus, "docker": bool(environment and environment.get("docker"))})
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
        # ``fresh``: a context_overflow retry starts a new conversation even if the stage was paused.
        paused = not fresh and stage in ((job.metadata or {}).get("stage_resume") or {})
        harness_request = {}
        if agent != "mucli":
            # Resume the CLI's own session (its transcript lives in the job's harness state).
            session = art.read_json(f"harness/{agent}/{stage}.json", {}) or {}
            resume_session = str(session.get("session_id") or "") if paused else ""
            harness_request = {"agent": agent, "model": model["model"], "effort": agent_cfg["effort"],
                               "resume_session": resume_session,
                               # Codex compacts at 85% of this window (model_auto_compact_token_limit).
                               "context_window": harness_context_window(agent, model["model"])}
            state_dir = root / "state"
            state_dir.mkdir()
            harnesses.copy_state(art.path("harness", agent, "state"), str(state_dir))
            creds_dir = root / "creds"
            harnesses.stage_credentials(agent, str(creds_dir))
            harness_mounts = [*harness_mounts, (str(creds_dir), "/harness/creds", True), (str(state_dir), "/harness/state", False)]
            resume = bool(resume_session)
            if resume:
                prompt = ("Continue this stage from where you left off." + (f"\n\nHuman guidance:\n{guidance}" if guidance else ""))
            elif guidance:
                prompt += "\n\nHuman guidance:\n" + guidance
        elif bool(previous) and paused:
            resume = True
            # Continue the paused stage's own conversation (prior work, warm cache prefix)
            # instead of restarting with a transcript excerpt.
            from utils.token_estimator import strip_replay_fields
            strip_replay_fields(previous, tool_results_only=True)  # older images duplicated signatures
            (inputs / "history.json").write_text(json.dumps(previous))
            prompt = ("Continue this stage from where you left off." + (f"\n\nHuman guidance:\n{guidance}" if guidance else ""))
        else:
            resume = False
            if guidance:
                prompt += "\n\nHuman guidance:\n" + guidance
        (inputs / "request.json").write_text(json.dumps({"model": model, "prompt": prompt, "resume": resume,
            # Jobs run unattended (mu.jobs.unattended); kept for stage images built before that.
            "auto_approve_writes": True,
            "max_iterations": limits.iteration_limit(job, stage),
            "model_request_timeout_s": _model_request_timeout(),
            "tool_timeout_s": _tool_timeout(),
            "context_window": _context_window(relay, model),
            "reasoning_effort": _stage_effort(stage),
            "environments": describe(ready) if stage == "implement" and len(ready) > 1 else [],
            **({"harness": harness_request} if harness_request else {})}))
        created = False
        docker_proxy = None
        try:
            relay.start(relay_dir)
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(relay_dir, policy.egress_extra).start()
            else:
                bridge = None
            from .engine import attach_docker
            policy, docker_proxy = attach_docker(policy, job, environment, states, relay_dir, service=service, stage=stage)
            args = container_args(name, stage, job.worktree, str(inputs), str(outputs), str(relay_dir), policy, relay.token,
                                  harness_mounts=tuple(harness_mounts),
                                  module="mu.jobs.sandbox.harness_worker" if agent != "mucli" else "mu.jobs.sandbox.worker",
                                  on_warning=lambda message: service.store.append_event(
                                      job.id, "sandbox_git_metadata_unavailable", payload={"stage": stage, "message": message}))
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
                        if safe.get("event") in ("auto_permission", "auto_answered"):
                            # Unattended decisions are first-class job events (activity, audit).
                            from ..unattended import event_reason
                            decided = {"stage": stage, **(safe.get("payload") if isinstance(safe.get("payload"), dict) else {})}
                            service.store.append_event(job.id, safe["event"], payload=decided,
                                                       reason=str(safe.get("reason") or event_reason(safe["event"], decided))[:500])
                except (ValueError, TypeError):
                    pass
            def execute():
                try:
                    run_with_output(runner, [docker, "start", "-a", name], timeout=stage_timeout + 10, output_callback=output)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    completed.set()
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            started = time.monotonic()
            stage_deadline = started + stage_timeout
            deadline = started + policy.timeout
            limit_seen = None
            while not completed.wait(.25):
                check_restart(service, job, stage, restart_cursor)
                if not alive():
                    raise RuntimeError("sandbox stopped after losing the job lease")
                now = time.monotonic()
                hit = getattr(relay, "limit_hit", None)
                if hit and limit_seen is None:
                    limit_seen = now
                pause = check_pause(service, job, restart_cursor)
                if now >= deadline and time_scope == "job" and not pause:
                    # The job runtime limit may have been raised while the stage ran.
                    fresh_left = limits.remaining_runtime(service, service.get(job.id))
                    if fresh_left is not None and fresh_left > 1:
                        deadline = min(stage_deadline, now + fresh_left)
                        time_scope = "job" if deadline < stage_deadline else "stage"
                        continue
                if now >= deadline or pause or (limit_seen is not None and now - limit_seen >= LIMIT_STOP_GRACE_S):
                    if hit:
                        detail = hit
                    elif pause or time_scope == "job":
                        detail = _job_time_detail(service, job, stage)
                    else:
                        detail = limits.limit_detail("time", "stage", stage, stage_timeout, stage_timeout)
                    # Let the worker save its conversation (SIGTERM → transcript), so a resume
                    # continues the stage instead of starting it over.
                    runner.run([docker, "stop", "-t", "20", name], check=False, timeout=45)
                    completed.wait(30)
                    _keep_transcript(outputs, art, stage)
                    _mark_resumable(service, job, stage, True)
                    if agent != "mucli":
                        try:
                            paused_result = _read_output(outputs, "result.json")
                        except (RuntimeError, ValueError, OSError):
                            paused_result = {}
                        _keep_harness_state(art, agent, stage, root / "state", paused_result)
                    raise _limit_paused(service, job, detail)
            check_restart(service, job, stage, restart_cursor)
            hit = getattr(relay, "limit_hit", None)
            if errors and not hit:
                raise RuntimeError(f"{stage} container failed: {redact_secrets(str(errors[0]))[0][:1000]}")
            if not alive():
                raise RuntimeError("sandbox outcome discarded after lease loss")
            if errors:
                # The relay refused at a budget and the container then failed: still a pause.
                _keep_transcript(outputs, art, stage)
                _mark_resumable(service, job, stage, True)
                raise _limit_paused(service, job, hit)
            result = _read_output(outputs, "result.json")
            if not isinstance(result, dict) or result.get("kind") not in ("completed", "failed", "needs_human"):
                raise RuntimeError("invalid sandbox result")
            _keep_transcript(outputs, art, stage)
            _mark_resumable(service, job, stage, result["kind"] == "needs_human")
            if agent != "mucli":
                _keep_harness_state(art, agent, stage, root / "state", result)
                if result.get("status") == "usage_limit":
                    service.store.append_event(job.id, "harness_usage_limit", reason=str(result.get("error") or "")[:500],
                                               payload={"stage": stage, "agent": agent, "resets_at": result.get("resets_at")})
            if stage == "implement":
                _guard(job, policy, literals=injected_literals(agent))
            result["cost_usd"] = relay.cost
            if result["kind"] != "completed":
                # A reached cap pauses the stage resumably and asks a human; it never fails the job.
                hit = getattr(relay, "limit_hit", None)
                if hit is None and result.get("status") == "max_iterations_reached":
                    limit = limits.iteration_limit(job, stage)
                    hit = limits.limit_detail("iterations", "stage", stage, limit, limit)
                if hit is not None:
                    _mark_resumable(service, job, stage, True)
                    raise _limit_paused(service, job, hit)
                result["error_class"] = str(result.get("error_class") or "") or classify_error(
                    str(result.get("status") or ""), str(result.get("error") or ""))
            return result
        except BaseException as exc:
            # Paused, failed and timed-out runs still spent money: hand the relay's cost on.
            try:
                exc.cost_usd = float(relay.cost or 0)
            except (AttributeError, TypeError, ValueError):
                pass
            raise
        finally:
            relay.close()
            if created:
                runner.run([docker, "rm", "-f", name], check=False, timeout=20)
            if "bridge" in locals() and bridge is not None:
                bridge.close()
            if docker_proxy is not None:
                docker_proxy.close()
            service.store.append_event(job.id, "sandbox_stopped", payload={"stage": stage, "container": name})


TRANSIENT_ERROR_CLASSES = ("provider_transient", "rate_limited")


def _spent(exc) -> float:
    try:
        return max(0.0, float(getattr(exc, "cost_usd", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


class SandboxJobRunner:
    def __init__(self, service):
        self.service = service

    def run(self, job, attempt):
        prompt = implement_prompt(self.service, job)
        from .environments import EnvironmentBlocked
        try:
            result = run_stage_in_container(self.service, job, "implement", prompt)
        except EnvironmentBlocked as exc:
            return JobRunOutcome(kind="needs_human", attention_reason=AttentionReason.ENVIRONMENT_FAILURE,
                                 attention_detail=str(exc), cost_usd=_spent(exc))
        except StageLimitReached as exc:
            return JobRunOutcome(kind="needs_human", attention_reason=AttentionReason.LIMIT_REACHED,
                                 attention_detail=str(exc), attention_payload={"stage": "implement", "limit": exc.detail},
                                 cost_usd=_spent(exc))
        except StagePaused as exc:
            return JobRunOutcome(kind="needs_human", attention_reason=AttentionReason.QUESTION, attention_detail=str(exc),
                                 cost_usd=_spent(exc))
        except SandboxUnavailable:
            raise
        except SandboxGuardError as exc:
            return JobRunOutcome(kind="failed", status="error", error=str(exc), cost_usd=_spent(exc), error_class="fatal")
        except Exception as exc:
            error_class = classify_error("error", str(exc))
            return JobRunOutcome(kind="failed", status="error", error=str(exc), cost_usd=_spent(exc),
                                 error_class=error_class, transient=error_class in TRANSIENT_ERROR_CLASSES)
        error_class = str(result.get("error_class") or "")
        if error_class in TRANSIENT_ERROR_CLASSES:
            # A provider outage or rate limit: the worker requeues with backoff (retry budget).
            return JobRunOutcome(kind="failed", status=result.get("status") or "error", error=result.get("error", ""),
                                 cost_usd=result["cost_usd"], result={"text": result.get("text", "")},
                                 transient=True, error_class=error_class)
        return JobRunOutcome(kind=result["kind"], status=result.get("status", ""), error=result.get("error", ""),
            cost_usd=result["cost_usd"], result={"text": result.get("text", "")},
            attention_reason=AttentionReason(result.get("attention_reason") or "question"),
            attention_detail=result.get("error", ""), attention_payload=result.get("attention_payload") or {},
            error_class=error_class)


def generate(service):
    def run(job, prompt, workspace, model):
        result = run_stage_in_container(service, job, model["stage"], prompt)
        if result["kind"] == "needs_human":
            raise StagePaused(result.get("error") or "stage needs human input")
        if result["kind"] != "completed":
            raise RuntimeError(result.get("error") or "sandbox agent stage failed")
        return result.get("text", ""), {"cost_usd": result["cost_usd"]}
    return run
