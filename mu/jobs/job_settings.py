"""Durable-job pipeline settings: ``$MUCLI_HOME/jobs/settings.json``.

Holds the default provider/model per pipeline stage (plan, implement, review)
and whether a generated plan must be approved by a human before
implementation starts. Per-job ``execution.stages`` / ``execution.provider``
still win; these settings are the fallback so jobs imported from Jira (which
carry no model) can run.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from mu.container.network import PACKAGE_REGISTRY_EGRESS

from .models import STAGES

_DEFAULTS: Dict[str, Any] = {"stage_models": {}, "plan_approval": True}


def settings_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "jobs", "settings.json")


def _clean_stage(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    provider = str(value.get("provider") or "").strip()[:64]
    model = str(value.get("model") or "").strip()[:200]
    if not model:
        return {}
    return {"provider": provider, "model": model}


def load_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            data = raw
    except (OSError, ValueError):
        data = {}
    stages = {}
    for stage in STAGES:
        cleaned = _clean_stage((data.get("stage_models") or {}).get(stage))
        if cleaned:
            stages[stage] = cleaned
    return {"stage_models": stages, "plan_approval": bool(data.get("plan_approval", True)),
            "sandbox": dict(data.get("sandbox") or {}), "checks": dict(data.get("checks") or {}),
            "limits": dict(data.get("limits") or {}), "publish": dict(data.get("publish") or {}),
            "jobs": dict(data.get("jobs") or {}), "defaults": dict(data.get("defaults") or {}),
            "retry": dict(data.get("retry") or {}), "environments": dict(data.get("environments") or {}),
            "harness": dict(data.get("harness") or {})}


def save_settings(update: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(update, dict):
        raise ValueError("settings payload must be an object")
    current = load_settings()
    if "stage_models" in update:
        incoming = update.get("stage_models") or {}
        if not isinstance(incoming, dict):
            raise ValueError("stage_models must be an object")
        unknown = sorted(set(incoming) - set(STAGES))
        if unknown:
            raise ValueError(f"unknown stage(s): {', '.join(unknown)}")
        stages = dict(current["stage_models"])
        for stage, value in incoming.items():
            cleaned = _clean_stage(value)
            if cleaned:
                if not cleaned["provider"]:
                    raise ValueError(f"stage '{stage}': provider is required when a model is set")
                stages[stage] = cleaned
            else:
                stages.pop(stage, None)
        current["stage_models"] = stages
    if "plan_approval" in update:
        current["plan_approval"] = bool(update.get("plan_approval"))
    for key in RULE_SECTIONS:
        if key in update:
            if not isinstance(update[key], dict):
                raise ValueError(f"{key} must be an object")
            current[key] = _merge(current.get(key) or {}, update[key])
    validate_rules(current)
    from .sandbox.policy import SandboxPolicy
    for stage in (*STAGES, "checks"):
        SandboxPolicy.for_stage(stage, current)
    path = settings_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return current


def stage_model(stage: str, settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    data = settings if settings is not None else load_settings()
    return dict((data.get("stage_models") or {}).get(stage) or {})


def resolve(execution: Optional[Dict[str, Any]], stage: str,
            settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """stage override -> job default -> settings for this stage."""
    exe = dict(execution or {})
    cfg = dict((exe.get("stages") or {}).get(stage) or {})
    if cfg.get("model"):
        return {"stage": stage, "provider": str(cfg.get("provider") or exe.get("provider") or ""),
                "model": str(cfg["model"]), "source": "stage"}
    if exe.get("model"):
        return {"stage": stage, "provider": str(exe.get("provider") or ""),
                "model": str(exe["model"]), "source": "job"}
    fallback = stage_model(stage, settings)
    if fallback.get("model"):
        return {"stage": stage, "provider": fallback.get("provider", ""),
                "model": fallback["model"], "source": "settings"}
    return {"stage": stage, "provider": "", "model": "", "source": "unset"}


def missing_stages(execution: Optional[Dict[str, Any]],
                   settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """Agent stages with no mucli model (stages run by Claude Code / Codex need none)."""
    data = settings if settings is not None else load_settings()
    from types import SimpleNamespace
    from .harness import harness_stages
    external = set(harness_stages(SimpleNamespace(execution=dict(execution or {})), data))
    out = []
    for stage in STAGES:
        if stage in external:
            continue
        model = resolve(execution, stage, data)
        if not (model["provider"] and model["model"]):
            out.append(stage)
    return out


def plan_approval_required(execution: Optional[Dict[str, Any]],
                           settings: Optional[Dict[str, Any]] = None) -> bool:
    exe = dict(execution or {})
    if "plan_approval" in exe:
        return bool(exe.get("plan_approval"))
    data = settings if settings is not None else load_settings()
    return bool(data.get("plan_approval", True))


def public_settings() -> Dict[str, Any]:
    data = load_settings()
    missing = missing_stages({}, data)
    return {**data, "stages": list(STAGES), "missing": missing, "configured": not missing,
            "rules": {"schema": RULES_SCHEMA, "values": effective_rules(data), "overrides": rule_overrides(data)}}


# --------------------------------------------------------------------------- run rules
#
# Every rule a job run obeys, described once (planos-style field definitions) so the
# web, Android and terminal Settings screens render the same generated form.
# Values live in settings.json under these sections; an absent value means "default".

RULE_SECTIONS = ("sandbox", "checks", "limits", "publish", "jobs", "defaults", "retry", "environments", "harness")

def _f(path, label, kind, default, hint, **extra):
    return {"path": path, "label": label, "kind": kind, "default": default, "hint": hint, **extra}

RULES_SCHEMA: List[Dict[str, Any]] = [
    {"id": "budget", "title": "Model budget", "hint": "Per pipeline stage, across every run of that stage in a job. A stage that reaches a budget pauses and asks you to approve more or cancel.",
     "fields": [
        _f("sandbox.max_tokens", "Token budget per stage", "int", 50_000_000,
           "Tokens one stage may use across its runs in a job; then the job pauses and asks you to approve more or cancel.", min=1, max=10_000_000_000, unit="tokens"),
        _f("sandbox.count_cache_reads", "Count cache reads", "bool", False,
           "Off: only new tokens (uncached input + output) count. On: every re-sent context token counts, which exhausts long stages quickly."),
        _f("sandbox.max_cost_usd", "Cost budget per stage", "float", 50.0,
           "Estimated API spend one stage may reach across its runs in a job; then the job pauses and asks you to approve more or cancel.", min=0, max=100_000, unit="USD"),
        _f("limits.review_patch_chars", "Review patch in prompt", "int", 60000,
           "Largest diff embedded in the review prompt; larger diffs list the remaining files for the reviewer to read.",
           min=1000, max=2_000_000, unit="characters"),
     ]},
    {"id": "defaults", "title": "New job defaults", "hint": "Applied when a job is created without its own value (the New job form prefills them).",
     "fields": [
        _f("defaults.max_retries", "Repair retries", "int", 2, "Automatic re-attempts after a failed stage.", min=0, max=20),
        _f("defaults.max_cost_usd", "Job cost limit", "float", 100.0,
           "Total spend for the whole job (every stage, run and fix attempt). Reaching it pauses the job and asks you to approve more or cancel. 0 = no cap (stage budgets still apply).",
           min=0, max=1_000_000, unit="USD"),
        _f("defaults.max_runtime_seconds", "Job runtime limit", "int", None, "Active (worker) time for the whole job. Reaching it pauses the running stage and asks you to approve more time or cancel. Blank = none.",
           min=1, max=7 * 24 * 3600, unit="seconds", optional=True),
        _f("defaults.run_unattended", "Run unattended in sandbox", "bool", False,
           "Default for the New job toggle. On: the implement stage auto-approves its writes inside the job's disposable "
           "container. Off: each write waits for your approval. Never inherited from a chat session's auto-approve."),
     ]},
    {"id": "iterations", "title": "Agent iterations", "hint": "How many agent loop turns one stage run may take; reaching it pauses the job and asks you to approve more or cancel. A job's own limit wins.",
     "fields": [
        _f("limits.max_iterations", "Default iterations per stage", "int", 1000, "Applies to every stage without a more specific limit.", min=1, max=100_000),
        _f("limits.stage_max_iterations.plan", "Plan stage iterations", "int", None, "Blank = default above.", min=1, max=100_000, optional=True),
        _f("limits.stage_max_iterations.implement", "Implement stage iterations", "int", None, "Blank = default above.", min=1, max=100_000, optional=True),
        _f("limits.stage_max_iterations.review", "Review stage iterations", "int", None, "Blank = default above.", min=1, max=100_000, optional=True),
        _f("jobs.stage_effort.plan", "Plan stage effort", "select", "", "Model effort (thinking) per request. Lower is faster.",
           options=[["", "Provider default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["max", "Max"]]),
        _f("jobs.stage_effort.implement", "Implement stage effort", "select", "", "",
           options=[["", "Provider default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["max", "Max"]]),
        _f("jobs.stage_effort.review", "Review stage effort", "select", "", "",
           options=[["", "Provider default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["max", "Max"]]),
     ]},
    {"id": "timeouts", "title": "Stage time limits", "hint": "Wall-clock limit for each run of a sandboxed stage; reaching it pauses the stage (resumably) and asks you to approve more time or cancel. Checks: per check command.",
     "fields": [
        _f("sandbox.model_request_timeout_s", "Model request timeout", "int", 3600,
           "How long one model call from inside the sandbox may take.", min=10, max=7200, unit="seconds"),
        _f("sandbox.tool_timeout_s", "Tool command timeout", "int", 3600,
           "Default limit for one shell command the agent runs inside a stage (when it doesn't set its own).",
           min=1, max=86_400, unit="seconds"),
        _f("sandbox.stage_timeout_s.plan", "Plan", "int", 3600, "", min=1, max=86_400, unit="seconds"),
        _f("sandbox.stage_timeout_s.implement", "Implement", "int", 3600, "", min=1, max=86_400, unit="seconds"),
        _f("sandbox.stage_timeout_s.review", "Review", "int", 3600, "", min=1, max=86_400, unit="seconds"),
        _f("sandbox.stage_timeout_s.checks", "Checks", "int", 3600, "", min=1, max=86_400, unit="seconds"),
     ]},
    {"id": "checks", "title": "Checks", "hint": "Host-run verification after implement.",
     "fields": [
        _f("checks.max_fix_attempts", "Automatic fix attempts", "int", 2,
           "Times a check failure is sent back to the implementer before a human is asked.", min=0, max=50),
        _f("checks.baseline_triage", "Baseline triage", "bool", True,
           "Re-run failing checks on the base commit; failures that also happen there are reported, not sent back to the implementer."),
        _f("checks.command_timeout_s", "Validation command timeout", "int", 3600,
           "Per-command limit for host-run validation commands (legacy verifier).", min=1, max=3600, unit="seconds"),
        _f("checks.suites", "Required check suites", "suites", [],
           "Added to every job's plan checks. Commands run in the sandboxed checks stage."),
     ]},
    {"id": "toolchains", "title": "Toolchains", "hint": "Per-repository build environments (planos-style). A matching repository's stages run in a per-job snapshot of the source; mucli adds Python + its agent code. Used when neither the job, the repository's .mucli/jobs.toml nor the job's container session names an environment.",
     "fields": [
        _f("sandbox.toolchains", "Repository toolchain images", "rows", [],
           "Base image = an image that already has the project's toolchain (e.g. rust:1.88 or the project's CI image), "
           "template:<name> or environment:<name> (a container-mode environment). Paths limit the row to repository-relative "
           "components (comma-separated; blank = whole repository). Docker = yes gives the job a private Docker engine (needs Sysbox). "
           "npm, Cargo/crates.io, Maven/Gradle and PyPI registries are allowed by default (Extra egress domains); add any other registry the build needs there.",
           columns=[{"key": "repository", "label": "Repository path", "placeholder": "/home/me/src/project", "required": True},
                    {"key": "base_image", "label": "Base image", "placeholder": "rust:1.88-bookworm · environment:rust-build", "required": True,
                     "pattern": r"[\w./:@-]+"},
                    {"key": "paths", "label": "Paths", "placeholder": "resq-backend, stream-processor-rs",
                     "pattern": r"[\w./@+ ,-]*"},
                    {"key": "docker", "label": "Docker", "placeholder": "no", "pattern": r"(?i)(yes|no|true|false|on|off|1|0)?"},
                    {"key": "gpus", "label": "GPUs", "placeholder": "inherit · all · 0,1 · none", "pattern": r"[\w,.:=-]*"}]),
     ]},
    {"id": "build_environments", "title": "Build environments",
     "hint": "Per-job snapshots of container-mode environments, templates or images. Each job builds its own images at start and removes them when it ends.",
     "fields": [
        _f("environments.use_session_environment", "Use the session's environment", "bool", True,
           "A job created from a container session runs in a snapshot of that session's environment."),
        _f("environments.inherit_egress", "Inherit environment egress", "bool", True,
           "Implement and checks may reach the environment's egress allowlist (model-provider hosts are always dropped)."),
        _f("environments.home_exclude_extra", "Extra home excludes", "list", [],
           "Paths under /root (globs allowed) never copied into a job snapshot, in addition to secrets, caches and history.",
           item_pattern=r"[^\s,]+", item_label="paths"),
        _f("environments.probe_env_extra", "Extra probed variables", "list", [],
           "Environment variables kept from the environment's login shell, in addition to PATH and the toolchain homes.",
           item_pattern=r"[A-Za-z_][A-Za-z0-9_]*", item_label="variable names"),
        _f("environments.secret_scan_allow", "Allowed secret-scan paths", "list", [],
           "Paths (globs allowed) the post-build secret scan accepts, e.g. /root/.config/tool/test.pem.",
           item_pattern=r"[^\s,]+", item_label="paths"),
        _f("jobs.environment_retention_h", "Idle environment retention", "float", 72.0,
           "How long a failed or waiting job keeps its build environment images. Kept jobs are exempt; images are re-created on retry.",
           min=1, max=24 * 365, unit="hours"),
        _f("sandbox.gpus_enabled", "GPUs for jobs", "bool", True,
           "Off: no job container gets a GPU, whatever its build environment requests."),
        _f("sandbox.docker_engine_image", "Docker engine image", "text", "docker:dind",
           "Image of the per-job Docker engine, run under the Sysbox runtime.", pattern=r"[\w./:@-]+"),
        _f("sandbox.docker_storage_gb", "Docker engine storage cap", "int", 20,
           "Space a job's Docker engine may use for images, containers and volumes.", min=1, max=10_000, unit="GB"),
     ]},
    {"id": "sandbox", "title": "Sandbox", "hint": "The locked-down container each stage runs in.",
     "fields": [
        _f("sandbox.image", "Runtime image", "text", "mucli-job-runtime:latest", "Local image; it is never pulled.", pattern=r"[\w./:@-]+"),
        _f("sandbox.cpu", "CPUs", "float", 4, "", min=0.1, max=64),
        _f("sandbox.memory", "Memory limit", "text", "8g", "Docker memory value, e.g. 8g or 16384m.", pattern=r"[1-9][0-9]*[kmgKMG]?"),
        _f("sandbox.max_file_bytes", "Largest changed file", "int", 20 * 1024 * 1024, "Changes with a bigger file are rejected.", min=1, max=2 ** 40, unit="bytes"),
        _f("sandbox.pids_limit", "Process limit", "int", 4096,
           "Most processes and threads one stage or check container may run (builds and parallel test runners need many).",
           min=64, max=1_000_000),
        _f("sandbox.cache_max_gb", "Dependency cache cap", "int", 20,
           "Shared package caches reused across jobs; least recently used entries are pruned. 0 = unlimited.",
           min=0, max=10000, unit="GB"),
        _f("jobs.container_cli", "Container CLI", "select", "auto",
           "Auto uses Docker when present, else Podman. Podman cannot run the per-job Docker engine.",
           options=[["auto", "Auto"], ["docker", "Docker"], ["podman", "Podman"]]),
        _f("sandbox.egress_extra", "Extra egress domains", "list", list(PACKAGE_REGISTRY_EGRESS),
           "Domains implement, checks and template job setup may reach in addition to the model relay. "
           "Defaults to the npm, Cargo/crates.io, Maven/Gradle and PyPI registries; clear the list to lock jobs down.",
           item_pattern=r"(?:\*\.)?[a-zA-Z0-9.-]+"),
     ]},
    {"id": "publish", "title": "Push", "hint": "What “Push / open PR” does.",
     "fields": [
        _f("publish.timeout_s", "Push timeout", "int", 3600, "Limit for git push and the PR/MR request.", min=10, max=3600, unit="seconds"),
        _f("publish.mode", "Publish mode", "select", "", "Used when a job does not set its own mode.",
           options=[["", "Per job (default)"], ["auto", "Auto-detect"], ["gh", "GitHub CLI"], ["github", "GitHub API"],
                    ["gitlab", "GitLab API"], ["none", "Don't publish"]]),
     ]},
]

_AGENT_OPTIONS = [["mucli", "mucli"], ["claude", "Claude Code"], ["codex", "Codex"]]

RULES_SCHEMA += [
    {"id": "harness", "title": "Agent harnesses",
     "hint": "Which agent runs each job stage: mucli, Claude Code or Codex (picked per stage at job launch; these are the defaults). "
             "External agents run inside the same sandboxed stage container with the host CLI mounted read-only and one copied credential. "
             "Check status and credentials with /job agents or the Agents panel.",
     "fields": [
        _f("harness.default.plan", "Plan agent", "select", "mucli", "", options=_AGENT_OPTIONS),
        _f("harness.default.implement", "Implement agent", "select", "mucli", "", options=_AGENT_OPTIONS),
        _f("harness.default.review", "Review agent", "select", "mucli", "", options=_AGENT_OPTIONS),
        _f("harness.claude.binary", "Claude Code binary", "text", "", "Blank = ~/.local/bin/claude or claude on PATH.",
           pattern=r"[^\s'\"]*"),
        _f("harness.claude.auth", "Claude credential", "select", "auto",
           "Copied into the stage sandbox. Auto: ANTHROPIC_API_KEY, then a `claude setup-token` token, then your ~/.claude login.",
           options=[["auto", "Auto"], ["api_key", "ANTHROPIC_API_KEY"], ["oauth_token", "Setup token (CLAUDE_CODE_OAUTH_TOKEN)"],
                    ["login", "~/.claude login"]]),
        _f("harness.claude.models.plan", "Claude plan model", "text", "claude-opus-5-5", "", pattern=r"[\w.:/@+-]{0,120}"),
        _f("harness.claude.models.implement", "Claude implement model", "text", "claude-sonnet-5-5", "", pattern=r"[\w.:/@+-]{0,120}"),
        _f("harness.claude.models.review", "Claude review model", "text", "claude-opus-5-5", "", pattern=r"[\w.:/@+-]{0,120}"),
        _f("harness.codex.binary", "Codex binary", "text", "", "Blank = ~/.local/bin/codex or codex on PATH.", pattern=r"[^\s'\"]*"),
        _f("harness.codex.auth", "Codex credential", "select", "auto",
           "Copied into the stage sandbox. Auto: your ~/.codex login (auth.json, as Codex itself prefers), then OPENAI_API_KEY.",
           options=[["auto", "Auto"], ["api_key", "OPENAI_API_KEY"], ["login", "~/.codex login"]]),
        _f("harness.codex.models.plan", "Codex plan model", "text", "", "Blank = Codex's configured model.", pattern=r"[\w.:/@+-]{0,120}"),
        _f("harness.codex.models.implement", "Codex implement model", "text", "", "", pattern=r"[\w.:/@+-]{0,120}"),
        _f("harness.codex.models.review", "Codex review model", "text", "", "", pattern=r"[\w.:/@+-]{0,120}"),
     ]},
    {"id": "retry", "title": "Automatic retries", "hint": "Transient provider failures are retried with exponential backoff.",
     "fields": [
        _f("retry.backoff_base_s", "First retry delay", "float", 30.0, "Doubles on every further attempt.", min=1, max=3600, unit="seconds"),
        _f("retry.backoff_max_s", "Longest retry delay", "float", 600.0, "Cap for the doubling delay.", min=1, max=86_400, unit="seconds"),
     ]},
    {"id": "scheduling", "title": "Scheduling & retention", "hint": "How the job controller runs and cleans up work.",
     "fields": [
        _f("jobs.max_concurrent", "Concurrent jobs", "int", 5, "Jobs that may run a stage at the same time.", min=1, max=50),
        _f("jobs.max_stage_crash_retries", "Stage crash retries", "int", 2,
           "Crash retries per stage before a human is asked.", min=0, max=50),
        _f("jobs.drift_check_interval_s", "Base drift check interval", "int", 300,
           "How often finished jobs are checked against their base branch. 0 = off.", min=0, max=86_400, unit="seconds"),
        _f("jobs.retention_days", "Archive retention", "float", 30.0,
           "Artifacts of archived jobs older than this are purged. 0 = keep forever (env MUCLI_JOBS_RETENTION_DAYS wins when set).",
           min=0, max=36_500, unit="days"),
     ]},
]

# --- W3 (git and publish) settings: one additive block, owned by mu/jobs/publish.py & friends.
RULES_SCHEMA += [
    {"id": "git_publish", "title": "Git & PR hosts", "hint": "Remotes, PR providers and Git limits used by job branches and Push.",
     "fields": [
        _f("publish.push_remote", "Push remote", "text", "origin", "Remote the job branch is pushed to.", pattern=r"[\w.-]+"),
        _f("publish.pr_remote", "PR remote", "text", "origin",
           "Remote whose host receives the PR/MR (also the base branch's remote). Differs from the push remote for forks.",
           pattern=r"[\w.-]+"),
        _f("publish.provider_hosts", "PR provider hosts", "list", [],
           "Self-hosted PR providers as host=kind (kind: github, gitlab, bitbucket, gitea), e.g. git.acme.dev=gitea.",
           item_pattern=r"[a-zA-Z0-9.-]+(?::\d+)?=(?:github|gitlab|bitbucket|gitea)", item_label="host=kind entries"),
        _f("publish.run_hooks", "Run commit-msg hook", "bool", False,
           "Run the repository's commit-msg hook on the squashed publish commit (checkpoints never run hooks)."),
        _f("publish.delete_remote_branch", "Delete remote branch on archive", "bool", False,
           "Archiving a pushed, merged or cancelled job also deletes its remote branch (the local branch is always deleted)."),
        _f("publish.merge_poll_seconds", "PR merge check interval", "int", 300,
           "How often pushed jobs check whether their PR was merged (merged → done; closed → needs you). 0 = off.",
           min=0, max=86_400, unit="seconds"),
        _f("jobs.git_timeout_seconds", "Git command timeout", "int", 120,
           "Limit for one local git command on a job repository (raise for very large repositories).",
           min=5, max=3600, unit="seconds"),
     ]},
]
for _section in RULES_SCHEMA:
    for _field in _section["fields"]:
        if _field["path"] == "publish.mode":
            _field["options"] = [*_field["options"], *[o for o in (["bitbucket", "Bitbucket Cloud API"], ["gitea", "Gitea API"])
                                                      if o not in _field["options"]]]
# --- end W3 block

# --- W8 (repository intake) settings, owned by mu/jobs/clone.py.
for _section in RULES_SCHEMA:
    if _section["id"] == "git_publish":
        _section["fields"] += [
            _f("jobs.clone.submodules", "Clone submodules", "bool", True,
               "Initialise submodules when a job clones a repository by URL and in every job worktree."),
            _f("jobs.clone.lfs", "Git LFS", "select", "auto",
               "auto: run git lfs pull when the repository uses LFS and git-lfs is installed (otherwise warn).",
               options=[["auto", "Auto"], ["off", "Off"]]),
        ]
# --- end W8 block

FIELD_BY_PATH = {f["path"]: f for section in RULES_SCHEMA for f in section["fields"]}


def _merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge; ``None`` (JSON null) removes an override so the default applies again."""
    out = dict(base)
    for key, value in update.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = _merge(out[key], value)
            if merged:
                out[key] = merged
            else:
                out.pop(key, None)
        elif isinstance(value, dict):
            cleaned = _merge({}, value)
            if cleaned:
                out[key] = cleaned
        else:
            out[key] = value
    return out


def _get(data: Dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def rule_overrides(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data if data is not None else load_settings()
    return {path: _get(data, path) for path in FIELD_BY_PATH if _get(data, path) is not None}


def effective_rules(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = data if data is not None else load_settings()
    out = {}
    for path, field in FIELD_BY_PATH.items():
        value = _get(data, path)
        out[path] = field["default"] if value is None else value
    return out


def validate_rules(data: Dict[str, Any]) -> None:
    import re
    for path, field in FIELD_BY_PATH.items():
        value = _get(data, path)
        if value is None:
            continue
        label, kind = field["label"], field["kind"]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{label} must be true or false")
        elif kind in ("int", "float"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "int" and int(value) != value):
                raise ValueError(f"{label} must be {'a whole number' if kind == 'int' else 'a number'}")
            if value < field.get("min", value) or value > field.get("max", value):
                raise ValueError(f"{label} must be between {field.get('min')} and {field.get('max')}")
        elif kind == "text":
            if not isinstance(value, str) or (field.get("pattern") and not re.fullmatch(field["pattern"], value)):
                raise ValueError(f"{label} is not valid")
        elif kind == "select":
            if value not in [o[0] for o in field["options"]]:
                raise ValueError(f"{label} must be one of {', '.join(o[0] or '(per job)' for o in field['options'])}")
        elif kind == "list":
            if not isinstance(value, list) or any(not isinstance(v, str) or not re.fullmatch(field["item_pattern"], v) for v in value):
                raise ValueError(f"{label} must be a list of {field.get('item_label', 'domain names')}")
        elif kind == "rows":
            if not isinstance(value, list):
                raise ValueError(f"{label} must be a list")
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError(f"{label}: every row must be an object")
                for column in field.get("columns", []):
                    cell = str(item.get(column["key"]) or "").strip()
                    if column.get("required") and not cell:
                        raise ValueError(f"{label}: {column['label']} is required")
                    if cell and column.get("pattern") and not re.fullmatch(column["pattern"], cell):
                        raise ValueError(f"{label}: {column['label']} {cell!r} is not valid")
        elif kind == "suites":
            if not isinstance(value, list):
                raise ValueError(f"{label} must be a list")
            for item in value:
                if not isinstance(item, dict) or not str(item.get("name") or "").strip() or not str(item.get("command") or "").strip():
                    raise ValueError(f"{label}: every suite needs a name and a command")


def rule_or(path: str, fallback: Any = None) -> Any:
    """Effective rule value that never raises (unreadable settings → schema default → fallback)."""
    try:
        value = rule(path)
    except (KeyError, OSError, ValueError, TypeError):
        value = None
    return fallback if value is None else value


def rule(path: str, settings: Optional[Dict[str, Any]] = None) -> Any:
    """Effective value of one rule (override or default)."""
    data = settings if settings is not None else load_settings()
    value = _get(data, path)
    return FIELD_BY_PATH[path]["default"] if value is None else value


# --------------------------------------------------------------------------- text surfaces
#
# Shared by `/job settings` and the terminal jobs dashboard: format values for display,
# parse typed text back into the JSON value for a rule's kind, and save one rule by path.

def rule_patch(path: str, value: Any) -> Dict[str, Any]:
    """``"a.b.c", v`` -> ``{"a": {"b": {"c": v}}}`` (``None`` resets the rule)."""
    out: Dict[str, Any] = {}
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return out


def _group(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.6g}" if abs(value) >= 1000 else f"{value:g}"


def format_rule_value(field: Dict[str, Any], value: Any) -> str:
    """Human-readable value: ``50,000,000 tokens``, ``on``, ``pypi.org, *.npmjs.org``."""
    if value is None:
        return "not set" if field.get("optional") else "—"
    kind = field["kind"]
    if kind in ("int", "float"):
        return _group(value) + (f" {field['unit']}" if field.get("unit") else "")
    if kind == "bool":
        return "on" if value else "off"
    if kind == "select":
        return next((label for opt, label in field.get("options") or [] if opt == value), str(value))
    if kind == "list":
        return ", ".join(value) if value else "none"
    if kind == "suites":
        if not value:
            return "none"
        return "; ".join(f"{s.get('name')}: {s.get('command')}" + (f" (junit {s['junit']})" if s.get("junit") else "")
                         for s in value)
    return str(value) if value != "" else '""'


def rule_edit_text(field: Dict[str, Any], value: Any) -> str:
    """The text a user edits for a value (inverse of :func:`parse_rule_text`)."""
    if value is None:
        return ""
    kind = field["kind"]
    if kind == "bool":
        return "on" if value else "off"
    if kind == "list":
        return ", ".join(value)
    if kind == "suites":
        return json.dumps(value)
    return str(value)


_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def parse_rule_text(path: str, text: str) -> Any:
    """Typed text -> JSON value for the rule at ``path``. Raises ``ValueError`` with a readable message.

    Numbers accept ``_``/``,`` separators; bools on/off/true/false/yes/no; lists are
    comma-separated; suites are a JSON list of ``{"name", "command", "junit"?}``. An empty
    value for an optional rule means "unset" (``None``)."""
    field = FIELD_BY_PATH.get(path)
    if field is None:
        raise ValueError(f"unknown setting: {path}")
    label, kind = field["label"], field["kind"]
    raw = str(text if text is not None else "").strip()
    if kind in ("int", "float"):
        cleaned = raw.replace(",", "").replace("_", "")
        if not cleaned:
            if field.get("optional"):
                return None
            raise ValueError(f"{label} needs a value")
        try:
            number = float(cleaned)
        except ValueError:
            raise ValueError(f"{label} must be {'a whole number' if kind == 'int' else 'a number'}") from None
        if kind == "int":
            if not number.is_integer():
                raise ValueError(f"{label} must be a whole number")
            return int(number)
        return int(number) if number.is_integer() and "." not in cleaned else number
    if kind == "bool":
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        raise ValueError(f"{label} must be on or off")
    if kind == "list":
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    if kind == "suites":
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f'{label} must be JSON, e.g. [{{"name": "unit", "command": "pytest -q"}}] ({exc})') from None
        if isinstance(value, dict):
            value = [value]
        return value
    if kind == "select" and raw.lower() in ("default", "per-job", "per_job", "-"):
        return ""
    return raw


def set_rule(path: str, value: Any) -> Dict[str, Any]:
    """Save one rule (``None`` resets it). Raises ``ValueError`` for unknown paths/invalid values."""
    if path not in FIELD_BY_PATH:
        raise ValueError(f"unknown setting: {path}")
    return save_settings(rule_patch(path, value))


def rules_view(data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Sections with every field's effective value, display text and override flag."""
    data = data if data is not None else load_settings()
    values, overrides = effective_rules(data), rule_overrides(data)
    out = []
    for section in RULES_SCHEMA:
        fields = []
        for field in section["fields"]:
            value = values[field["path"]]
            fields.append({**field, "value": value, "set": field["path"] in overrides,
                           "display": format_rule_value(field, value),
                           "default_display": format_rule_value(field, field["default"]),
                           "edit_text": rule_edit_text(field, value)})
        out.append({"id": section["id"], "title": section["title"], "hint": section["hint"], "fields": fields})
    return out


def rules_text(data: Optional[Dict[str, Any]] = None) -> str:
    """Plain-text listing of every run rule grouped by section (``/job settings``)."""
    lines = [f"Job run rules  ({settings_path()})", ""]
    for section in rules_view(data):
        lines.append(section["title"])
        if section["hint"]:
            lines.append(f"  {section['hint']}")
        for field in section["fields"]:
            marker = "(set)" if field["set"] else "(default)"
            lines.append(f"  {field['label']}: {field['display']}  {marker}")
            detail = [field["path"]]
            if field["set"]:
                detail.append(f"default {field['default_display']}")
            if field["kind"] in ("int", "float") and ("min" in field or "max" in field):
                detail.append(f"{_group(field.get('min'))}–{_group(field.get('max'))}")
            if field["kind"] == "select":
                detail.append("options: " + ", ".join(opt or "default" for opt, _ in field.get("options") or []))
            lines.append(f"      {' · '.join(detail)}")
            if field["hint"]:
                lines.append(f"      {field['hint']}")
        lines.append("")
    lines.append("Change: /job settings set <path> <value>   Reset: /job settings reset <path>")
    lines.append("Lists are comma-separated; suites are JSON, e.g. "
                 '[{"name": "unit", "command": "pytest -q"}]')
    return "\n".join(lines)
