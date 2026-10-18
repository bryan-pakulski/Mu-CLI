"""Environment repair (``documentation/plans/job_templates.md`` §7).

When a checks run has suites that could not run (``unrunnable``: a missing top-level executable,
a missing nested tool, a missing module, a failed install) or that fail only for environment
reasons (missing dependency, no network), the job does not park. An unattended **repair** stage
runs the job's implement agent (mucli, Claude Code or Codex) in a repair container built from the
binding's current job image (or the shared runtime image), with a prompt asking it to make the
check commands *runnable*. Afterwards the host:

1. reverts tracked-file changes the repair made (``git status`` before and after; only files the
   repair changed, never the implement stage's work) and records them;
2. relies on the container's entrypoint to chown ``/workspace`` and ``/cache`` back to the host
   uid:gid (a helper container does it when the repair was stopped first);
3. commits the container as ``mucli/job-<job12>-<binding>-repair<n>`` (job-labelled, so job
   cleanup removes it);
4. points the binding's job image (``metadata.build_environments[].job_image``, or
   ``metadata.runtime_repair_image`` for the shared runtime) at it, so every later stage and
   check uses it;
5. re-runs the checks with trigger ``repair``.

Isolation of the repair container (``policy.container_args`` stage ``repair``): root inside with
``--cap-drop ALL`` plus CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, FSETID and
``no-new-privileges``; writable rootfs, tmpfs ``/tmp``; network none with egress only through the
host bridge (:func:`repair_egress`); the worktree read-write at ``/workspace`` plus ``/cache``; no
git or registry credentials, no Docker socket, no GPUs; the model only through the relay (Claude
Code / Codex reach their own provider through the bridge, as in implement).

At most ``jobs.repair.max_attempts`` attempts per job, each with its own budget
(``jobs.repair.max_cost_usd`` / ``max_runtime_s`` / ``max_iterations``); repair spend is recorded
in the job's cost ledger (stage ``repair``). A repair that fails, times out or runs out of budget
is recorded and the job continues unverified (§5). Records: ``metadata.environment_repairs[]`` and
``environment_repair`` events (``phase`` started / command / finished).
"""
from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets

META_KEY = "environment_repairs"
EVENT = "environment_repair"
OS_MIRRORS = ("deb.debian.org", "security.debian.org", "archive.ubuntu.com", "security.ubuntu.com",
              "dl-cdn.alpinelinux.org")
TOOLCHAIN_INSTALLERS = ("static.rust-lang.org", "sh.rustup.rs", "nodejs.org", "go.dev", "dl.google.com",
                        "objects.githubusercontent.com", "github.com")
OUTCOMES = ("running", "repaired", "unresolved", "failed", "no_commands", "timed_out", "budget_exhausted", "error")
# Outcomes that committed an image (the actions offer it).
COMMITTED = ("repaired", "unresolved")
TAIL_CHARS = 3000
LIMIT_STOP_GRACE_S = 30.0
DEFAULTS = {"enabled": True, "max_attempts": 2, "registries": "auto", "max_cost_usd": 3.0, "max_runtime_s": 1200,
            "max_iterations": 40}
# Project-level installs (no root needed): offered for the template's job setup.
_PROJECT_TOOLS = frozenset({"npm", "pnpm", "yarn", "bun", "npx", "uv", "poetry", "pdm", "pipenv", "cargo", "go",
                            "bundle", "composer", "mvn", "./mvnw", "gradle", "./gradlew", "dotnet", "mix", "deno"})
_SYSTEM_MARKERS = re.compile(r"(?:^|\s)(?:-g|--global|--system|--break-system-packages)(?:\s|$)|\bsudo\b|/usr/|/etc/|/opt/")
_COMMANDS_RE = re.compile(r"REPAIR COMMANDS:?\s*\n?```[^\n]*\n(.*?)```", re.S | re.I)


# ------------------------------------------------------------------ settings and records


def repair_settings(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Effective ``jobs.repair.*`` rules (schema defaults when unset or unreadable)."""
    from ..job_settings import rule
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        try:
            value = rule(f"jobs.repair.{key}", settings)
        except (KeyError, OSError, ValueError, TypeError):
            value = None
        if value is not None:
            out[key] = value
    out["registries"] = "template" if out.get("registries") == "template" else "auto"
    return out


def repair_records(job: Any) -> List[Dict[str, Any]]:
    raw = dict(getattr(job, "metadata", None) or {}).get(META_KEY) or []
    return [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def repair_files(job: Any) -> List[str]:
    """Untracked files the repairs left in the worktree (the checks' dirty rule ignores them)."""
    out: List[str] = []
    for record in repair_records(job):
        for path in record.get("files") or []:
            if path not in out:
                out.append(str(path))
    return out


def latest_repair(job: Any, *, committed: bool = False) -> Optional[Dict[str, Any]]:
    records = [r for r in repair_records(job) if not committed or (r.get("outcome") in COMMITTED and r.get("image"))]
    return records[-1] if records else None


def _record_for(job: Any, attempt: Any) -> Optional[Dict[str, Any]]:
    if attempt in (None, ""):
        return latest_repair(job, committed=True)
    try:
        wanted = int(attempt)
    except (TypeError, ValueError):
        return None
    return next((r for r in repair_records(job) if int(r.get("attempt") or 0) == wanted), None)


def _save_record(service, job_id: str, record: Dict[str, Any]) -> None:
    def mutate(meta: Dict[str, Any]) -> None:
        records = [r for r in (meta.get(META_KEY) or []) if isinstance(r, dict)]
        for index, existing in enumerate(records):
            if existing.get("attempt") == record["attempt"]:
                records[index] = dict(record)
                break
        else:
            records.append(dict(record))
        meta[META_KEY] = records
    service.store.patch_metadata(job_id, mutate=mutate)


def _event(service, job_id: str, payload: Dict[str, Any], reason: str = "") -> None:
    try:
        service.store.append_event(job_id, EVENT, reason=reason[:2000], payload=payload)
    except Exception:  # noqa: BLE001 - events are informational
        pass


# ------------------------------------------------------------------ trigger and target


def trigger_suites(job: Any, run: Any) -> List[Dict[str, Any]]:
    """Suites of ``run`` an environment repair could fix: every unrunnable suite (missing tools
    included), plus failing suites when *all* new failures are environment symptoms (the case
    that used to park the job). ``[]`` = nothing to repair."""
    summary = dict(getattr(run, "summary", None) or {})
    results = {str(s.get("suite")): s for s in summary.get("suites") or [] if isinstance(s, dict)}
    out: List[Dict[str, Any]] = []
    for item in summary.get("unrunnable_suites") or []:
        if isinstance(item, dict) and item.get("suite") in results:
            out.append({**item, "environment": results[item["suite"]].get("environment") or ""})
    if out:
        return out
    from ..verify_worker import environment_failure, futile_fix_reason
    try:
        futile = futile_fix_reason(job, run) if summary.get("new_failures", 0) > 0 else ""
    except Exception:  # noqa: BLE001 - no trigger then
        futile = ""
    if not futile.startswith("environment"):
        return []
    for result in results.values():
        if result.get("new_failures"):
            out.append({"suite": result.get("suite"), "command": result.get("command"), "kind": "environment",
                        "tool": "", "cause": environment_failure(result), "environment": result.get("environment") or ""})
    return out


def plan_repair(service, job: Any, run: Any, settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The next repair to run for ``run`` or None (disabled, attempts used up, nothing to repair,
    or the suites' environment is not prepared). ``{binding, state, suites, image, attempt}``."""
    config = repair_settings(settings)
    if not config["enabled"]:
        return None
    job = service.get(job.id)
    attempts = len(repair_records(job))
    if attempts >= int(config["max_attempts"]):
        return None
    suites = trigger_suites(job, run)
    if not suites:
        return None
    from .environments import job_states
    states = {str(s.get("name")): s for s in job_states(job)}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for suite in suites:
        groups.setdefault(str(suite.get("environment") or ""), []).append(suite)
    for name, members in sorted(groups.items(), key=lambda item: -len(item[1])):
        if name:
            state = states.get(name)
            if not state or state.get("status") != "ready" or not state.get("job_image"):
                continue
            return {"binding": name, "state": state, "suites": members, "image": str(state["job_image"]),
                    "attempt": attempts + 1}
        if states:
            continue  # a suite outside every binding of a job that has bindings: nothing to repair
        from .policy import SandboxPolicy
        image = str((job.metadata or {}).get("runtime_repair_image") or "")
        if not image:
            image = (SandboxPolicy.for_stage("checks", settings) if settings is not None
                     else SandboxPolicy.for_stage("checks")).image
        return {"binding": "runtime", "state": None, "suites": members, "image": image, "attempt": attempts + 1}
    return None


# ------------------------------------------------------------------ egress


def detected_registries(job: Any) -> List[str]:
    """Package registries ``detect.py`` finds for the repository at the base commit."""
    try:
        from .detect import detect_for_job, summary
        return list(summary(detect_for_job(job)).get("egress") or [])
    except Exception:  # noqa: BLE001 - no repository view: no registries
        return []


def repair_egress(binding_allow: Iterable[str], *, registries: str = "auto",
                  detected: Iterable[str] = ()) -> List[str]:
    """Repair-container egress: the binding's allowlist ∪ (``auto``) the detected registries ∪ OS
    mirrors ∪ toolchain installers; model-provider hosts are always dropped."""
    from .environments import filter_model_hosts
    domains = [*binding_allow, *(detected if registries == "auto" else ()), *OS_MIRRORS, *TOOLCHAIN_INSTALLERS]
    return filter_model_hosts(dict.fromkeys(str(d).strip().lower() for d in domains if str(d or "").strip()))


def repair_policy(job: Any, target: Dict[str, Any], settings: Optional[Dict[str, Any]] = None, *,
                  detected: Optional[Sequence[str]] = None):
    """``(policy, registries added)`` of the repair container."""
    from .policy import SandboxPolicy, with_build_environment
    config = repair_settings(settings)
    base = SandboxPolicy.for_stage("repair", settings) if settings is not None else SandboxPolicy.for_stage("repair")
    policy = with_build_environment(base, job, target.get("state"), stage="repair", settings=settings)
    found = list(detected if detected is not None else (detected_registries(job) if config["registries"] == "auto" else []))
    egress = repair_egress(policy.egress_extra, registries=config["registries"], detected=found)
    added = [d for d in found if d in egress and d not in policy.egress_extra] if config["registries"] == "auto" else []
    return replace(policy, image=str(target["image"]), egress_extra=tuple(egress), gpus="", docker_socket="",
                   timeout=int(config["max_runtime_s"]), max_cost_usd=float(config["max_cost_usd"])), added


def repair_container_args(job: Any, dirs: Sequence[Path], name: str, policy, token: str, *,
                          harness_mounts: tuple = (), module: str = "mu.jobs.sandbox.worker") -> List[str]:
    """``docker create`` arguments of the repair container (isolation: see the module doc)."""
    from .policy import container_args
    return container_args(name, "repair", job.worktree, *map(str, dirs), policy, token, role="job-repair",
                          harness_mounts=tuple(harness_mounts), module=module)


# ------------------------------------------------------------------ prompt and output


def _ci_hints(worktree: str) -> List[str]:
    root = Path(worktree)
    found: List[Path] = []
    for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml", ".circleci/config.yml",
                    "Dockerfile", ".devcontainer/devcontainer.json", ".tool-versions", ".nvmrc", "rust-toolchain.toml"):
        found += sorted(p for p in root.glob(pattern) if p.is_file() and not p.is_symlink())
    out = []
    for path in found[:6]:
        try:
            text = path.read_text(errors="replace")[:2500]
        except OSError:
            continue
        out.append(f"--- {path.relative_to(root)} ---\n{text}")
    return out


def build_prompt(job: Any, run: Any, target: Dict[str, Any], *, detected: Optional[List[Dict[str, Any]]] = None) -> str:
    summary = dict(getattr(run, "summary", None) or {})
    results = {str(s.get("suite")): s for s in summary.get("suites") or [] if isinstance(s, dict)}
    lines = ["You are the environment repair stage of a durable engineering job. The job's check commands could not "
             "run in this build environment. Your goal is to make these check commands RUNNABLE in this container "
             "(they do not have to pass; test failures are not your concern).", "", "Checks that could not run:"]
    for suite in target["suites"]:
        result = results.get(str(suite.get("suite"))) or {}
        tail = redact_secrets(f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip())[0][-1500:]
        cwd = f" (cwd: {result.get('cwd')})" if result.get("cwd") else ""
        lines += [f"- `{suite.get('command')}`{cwd}: {suite.get('cause') or 'could not run'}",
                  "  Output tail:", "  ```", *("  " + line for line in tail.splitlines()[-30:]), "  ```"]
    commands = list(dict.fromkeys(str(s.get("command")) for s in target["suites"] if s.get("command")))
    lines += ["", "Check commands (run from /workspace unless a cwd is shown):", *(f"- {c}" for c in commands)]
    if detected:
        lines += ["", "Detected toolchains (from manifests at the base commit):"]
        for tc in detected[:8]:
            lines.append(f"- {tc.get('name')} ({tc.get('manifest')}); lockfile {tc.get('lockfile') or 'none'}")
    hints = _ci_hints(job.worktree)
    if hints:
        lines += ["", "CI and toolchain files in the repository (hints for the expected setup):", *hints]
    lines += ["", "Rules:",
              "- Do not modify tracked files (source, manifests, lockfiles); the host reverts any such change.",
              "- Prefer project-local installs that use the lockfile (`npm ci`, `uv sync`, `cargo fetch`, "
              "`go mod download`).",
              "- System packages are allowed (you are root in this container: apt-get / apk work; use "
              "`apt-get update` first). Network access is limited to package registries, OS mirrors and toolchain "
              "installers.",
              "- Do not commit, push, or edit .git.",
              "- Finish by running each check command once to confirm it now starts (a test failure is fine; "
              "\"command not found\" or a missing module is not).",
              "", "When you are done, end your final message with the exact commands that fixed the environment, "
              "in order, one per line, in this format:", "REPAIR COMMANDS:", "```", "npm ci", "```"]
    return "\n".join(lines)


def _transcript_commands(transcript: Any) -> List[str]:
    out: List[str] = []
    for message in transcript if isinstance(transcript, list) else []:
        for part in (message.get("parts") or []) if isinstance(message, dict) else []:
            if isinstance(part, dict) and part.get("type") == "tool_call" and part.get("tool_name") in (
                    "bash", "bash_background"):
                command = str((part.get("tool_args") or {}).get("command") or "").strip()
                if command:
                    out.append(command)
    return out


def parse_commands(text: str, transcript: Any = None) -> List[str]:
    """The commands that fixed the environment: the agent's ``REPAIR COMMANDS`` block, else every
    shell command from the transcript.

    With a transcript, only commands the agent actually ran count: a block line must appear in
    one of its shell calls. An agent that could not run anything (a stage without a shell) lists
    what it *would* run; those are suggestions, not repairs."""
    executed = list(dict.fromkeys(_transcript_commands(transcript)))
    match = None
    for match in _COMMANDS_RE.finditer(str(text or "")):
        pass
    if match:
        lines = [line.strip() for line in match.group(1).splitlines()]
        found = [line for line in lines if line and not line.startswith("#")]
        if transcript is not None:
            squash = lambda c: " ".join(c.split())  # noqa: E731
            ran = [squash(c) for c in executed]
            found = [line for line in found if any(squash(line) in c for c in ran)]
        if found:
            return found[:50]
    return executed[:50]


def project_commands(commands: Iterable[str]) -> List[str]:
    """Commands that ran without needing root (project-level installs such as ``npm ci`` or
    ``uv sync``): candidates for the template's job setup."""
    out = []
    for raw in commands:
        command = str(raw or "").strip()
        if not command or _SYSTEM_MARKERS.search(command):
            continue
        head = command.split()[0]
        if head in _PROJECT_TOOLS and command not in out:
            out.append(command)
    return out


# ------------------------------------------------------------------ worktree hygiene


def _status(worktree: str) -> set:
    from ..checks import status_entries
    try:
        return set(status_entries(worktree))
    except (OSError, subprocess.SubprocessError):
        return set()


def _snapshot(worktree: str, entries: set, store: Path) -> Dict[str, Optional[Path]]:
    """A copy, under ``store``, of every file already changed before the repair (the implement
    stage's work), whatever its size; ``None`` for deletions. Copies on disk, not bytes in memory,
    so no file is too big to protect."""
    saved: Dict[str, Optional[Path]] = {}
    for index, (code, path) in enumerate(sorted(entries, key=lambda e: e[1])):
        target = Path(worktree) / path
        if "D" in code or not target.is_file() or target.is_symlink():
            saved[path] = None
            continue
        copy = store / str(index)
        try:
            store.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, copy)
            saved[path] = copy
        except OSError:
            continue
    return saved


def _same(target: Path, copy: Path) -> bool:
    try:
        return target.is_file() and not target.is_symlink() and filecmp.cmp(target, copy, shallow=False)
    except OSError:
        return False


def revert_tracked_changes(worktree: str, before: set, saved: Dict[str, Optional[Path]]) -> Dict[str, List[str]]:
    """Undo tracked-file changes made since ``before``; never the implement stage's work.

    Returns ``{reverted, files}``: paths restored, and new untracked files the repair left (e.g.
    an install's output that git does not ignore)."""
    from ..checks import git
    after = _status(worktree)
    reverted: List[str] = []
    before_paths = {path for _code, path in before}
    for code, path in sorted(after - before, key=lambda e: e[1]):
        if code == "??" or path in before_paths:
            continue
        try:
            git(worktree, "checkout", "-q", "HEAD", "--", path)
            reverted.append(path)
        except (OSError, subprocess.SubprocessError):
            continue
    for path, copy in saved.items():
        target = Path(worktree) / path
        if copy is None:
            continue
        if not _same(target, copy):
            try:
                if target.is_symlink() or target.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(copy, target)
                reverted.append(path)
            except OSError:
                continue
    for code, path in sorted(before - after, key=lambda e: e[1]):
        # A file the implement stage had changed that is now back at HEAD or gone.
        if path in saved and saved[path] is not None and path not in reverted:
            try:
                shutil.copyfile(saved[path], Path(worktree) / path)  # type: ignore[arg-type]
                reverted.append(path)
            except OSError:
                pass
    from ..checks import collapse_untracked
    files = collapse_untracked(worktree, {path for code, path in _status(worktree) - before if code == "??"})
    return {"reverted": sorted(dict.fromkeys(reverted)), "files": files}


# ------------------------------------------------------------------ the repair stage


def _added_env_keys(args: Sequence[str]) -> List[str]:
    keys = []
    for index, arg in enumerate(args):
        if arg == "-e" and index + 1 < len(args):
            keys.append(args[index + 1].split("=", 1)[0])
    return list(dict.fromkeys(keys))


def commit_changes(docker, base_image: str, env_keys: Sequence[str], labels: Dict[str, str]) -> List[str]:
    """``docker commit --change`` list: restore the base image's environment for every variable the
    repair container set (no relay token or stage settings baked into the image), its user,
    entrypoint and command, and job-label the result."""
    from .toolchain import env_value
    try:
        env = json.loads(docker.inspect("image", base_image, "{{json .Config.Env}}") or "null") or []
    except ValueError:
        env = []
    parent = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
    changes: List[str] = []
    for key in env_keys:
        changes += ["--change", f'ENV {key}="{env_value(parent.get(key, ""))}"']
    user = docker.inspect("image", base_image, "{{.Config.User}}") or "root"
    changes += ["--change", f"USER {user}"]
    for field, directive in (("Entrypoint", "ENTRYPOINT"), ("Cmd", "CMD")):
        raw = docker.inspect("image", base_image, "{{json .Config.%s}}" % field) or "null"
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        changes += ["--change", f"{directive} {json.dumps(value if isinstance(value, list) else [])}"]
    changes += ["--change", "LABEL " + " ".join(f'{k}="{env_value(v)}"' for k, v in sorted(labels.items()))]
    return changes


def pin_tag(job_id: str, attempt: int) -> str:
    """Temporary tag that holds a repair's base image until the repair finishes."""
    from .environments import job12
    return f"mucli/job-{job12(job_id)}-repair-base:{int(attempt)}"


def repaired_tag(job_id: str, binding: str, attempt: int) -> str:
    from .environments import _slug, job12
    return f"mucli/job-{job12(job_id)}-{_slug(binding)}-repair{int(attempt)}"


def _fix_ownership(runner, docker_bin: str, job: Any, policy, image: str) -> None:
    """The repair was stopped before its entrypoint could chown the mounts: do it in a throwaway
    container (same capabilities, no network)."""
    from .cli import bind_mount, is_podman, selinux_enforcing
    from .policy import repair_owner
    podman, selinux = is_podman(), selinux_enforcing()
    args = ["run", "--rm", "--network", "none", "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "CHOWN",
            "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER", "--security-opt", "no-new-privileges:true",
            "--label", "io.mucli.role=job-repair-chown"]
    if policy.job_id:
        from .environments import job_labels
        for key, value in job_labels(policy.job_id).items():
            args += ["--label", f"{key}={value}"]
    targets = ["/workspace"]
    args += bind_mount(str(Path(job.worktree).resolve()), "/workspace", False, podman=podman, selinux=selinux)
    if policy.cache_dir and os.path.isdir(policy.cache_dir):
        args += bind_mount(str(Path(policy.cache_dir).resolve()), "/cache", False, podman=podman, selinux=selinux)
        targets.append("/cache")
    args += ["--entrypoint", "chown", image, "-R", repair_owner(podman=podman), *targets]
    try:
        runner.run([docker_bin, *args], check=False, timeout=600)
    except (ContainerRuntimeError, OSError, subprocess.SubprocessError):
        pass


def run_repair(service, job: Any, run: Any, target: Dict[str, Any], *, runner: Optional[CommandRunner] = None,
               relay_factory=None, settings: Optional[Dict[str, Any]] = None,
               alive: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """One repair attempt. Never raises: the returned record's ``outcome`` is ``committed`` (an image
    was committed and the binding points at it; the caller re-runs checks) or a failure outcome."""
    from ..core.artifacts import artifacts_for
    from .model_relay import HarnessRelay, ModelRelay
    from .. import harness as harnesses
    from ..models import resolve_stage_model
    relay_factory = relay_factory or ModelRelay
    runner = runner or CommandRunner()
    job = service.get(job.id)
    config = repair_settings(settings)
    attempt = int(target["attempt"])
    binding = str(target["binding"])
    state = target.get("state") or {}
    record: Dict[str, Any] = {
        "attempt": attempt, "binding": binding, "source": str(state.get("source") or "runtime"),
        "template": str(state.get("ref") or "") if state.get("source") == "template" else "",
        "trigger_suites": [{k: s.get(k) for k in ("suite", "command", "kind", "tool", "cause")} for s in target["suites"]],
        "commands": [], "project_commands": [], "image": "", "base_image": str(target["image"]), "cost_usd": 0.0,
        "outcome": "running", "error": "", "reverted_files": [], "files": [], "registries": [], "egress": [],
        "agent": "", "transcript": f"logs/repair-{attempt}-transcript.json", "started_at": time.time(),
        "finished_at": None, "checks_after": None}
    _save_record(service, job.id, record)
    suites_text = ", ".join(f"`{s.get('suite')}`" for s in target["suites"][:6])
    _event(service, job.id, {"phase": "started", "attempt": attempt, "binding": binding, "image": record["base_image"],
                             "trigger_suites": record["trigger_suites"]},
           reason=f"Checks could not run ({suites_text}); repairing build environment `{binding}` "
                  f"(attempt {attempt} of {config['max_attempts']})…")
    art = artifacts_for(job).ensure()
    name = f"mucli-job-{str(job.id)[:12]}-repair-{uuid.uuid4().hex[:8]}"
    relay = bridge = None
    created = False
    alive = alive or (lambda: not job.worker_id or service.store.assert_lease(job.id, job.worker_id))
    try:
        if not job.worktree or not Path(job.worktree).is_dir():
            raise RuntimeError("the job's managed worktree is unavailable")
        try:
            from .detect import detect_for_job
            detected = detect_for_job(job)
        except Exception:  # noqa: BLE001 - hints only
            detected = []
        from .detect import summary as detect_summary
        policy, added = repair_policy(job, target, settings,
                                      detected=list(detect_summary(detected).get("egress") or []) if detected else [])
        from .model_relay import prior_spend
        prior_repair, _job_spent = prior_spend(art, "repair")
        # Each attempt has its own budget; the ledger keeps every attempt's spend.
        policy = replace(policy, max_cost_usd=float(config["max_cost_usd"]) + prior_repair)
        from .. import limits
        left = limits.remaining_runtime(service, job)
        if left is not None and left <= 0:
            record.update(outcome="budget_exhausted", error="the job's runtime limit is used up")
            return record
        if left is not None and left < policy.timeout:
            policy = replace(policy, timeout=max(1, int(left)))
        record["registries"], record["egress"] = list(added), list(policy.egress_extra)
        agent_cfg = harnesses.stage_agent(job, "implement")
        agent = agent_cfg["agent"]
        record["agent"] = agent
        harness_mounts: List[tuple] = []
        harness_request: Dict[str, Any] = {}
        from .cli import container_cli
        docker_bin = runner.require(container_cli(settings) if settings is not None else container_cli())
        runner.run([docker_bin, "image", "inspect", policy.image], timeout=15)
        # Pin the base image for the whole repair: a rebuild that moves its tag mid-repair
        # (Setup → build the runtime image) otherwise leaves it untagged, the containerd image
        # store collects its content, and the commit fails ("content digest … not found").
        pinned = pin_tag(job.id, attempt)
        runner.run([docker_bin, "tag", policy.image, pinned], timeout=30)
        policy = replace(policy, image=pinned)
        target = {**target, "base_ref": pinned}
        prompt = build_prompt(job, run, target, detected=detected)
        from .runner import claim_stage_response
        interaction = lambda payload: claim_stage_response(service, job, "repair", payload)  # noqa: E731
        with tempfile.TemporaryDirectory(prefix="mucli-repair-") as tmp:
            root = Path(tmp)
            inputs, outputs, relay_dir = (root / p for p in ("input", "output", "relay"))
            for path in (inputs, outputs, relay_dir):
                path.mkdir()
            summary_text = art.read_text("checks/summary.json")
            if summary_text is not None:
                (inputs / "checks").mkdir()
                (inputs / "checks" / "summary.json").write_text(summary_text)
            if agent != "mucli":
                harness_mounts = list(harnesses.binary_mounts(agent))
                if not harnesses.credentials(agent):
                    raise PermissionError(f"no {harnesses.AGENT_LABELS[agent]} credentials are configured")
                # The harness reaches its own provider through the bridge, as in implement.
                policy = replace(policy, egress_extra=tuple(dict.fromkeys((*policy.egress_extra,
                                                                           *harnesses.provider_hosts(agent)))))
                model = {"provider": agent, "model": agent_cfg["model"], "stage": "repair"}
                state_dir, creds_dir = root / "state", root / "creds"
                state_dir.mkdir()
                harnesses.stage_credentials(agent, str(creds_dir))
                harness_mounts += [(str(creds_dir), "/harness/creds", True), (str(state_dir), "/harness/state", False)]
                harness_request = {"agent": agent, "model": model["model"], "effort": agent_cfg["effort"],
                                   "resume_session": ""}
                relay = HarnessRelay(job, "repair", model, policy, art, agent=agent, alive=alive,
                                     interaction=interaction, message_events=lambda: [])
            else:
                model = {**resolve_stage_model(job.execution, "implement"), "stage": "repair"}
                relay = relay_factory(job, "repair", model, policy, art, alive=alive, messages=lambda: [],
                                      interaction=interaction)
            from .runner import _context_window, _model_request_timeout, _stage_effort, _tool_timeout
            (inputs / "request.json").write_text(json.dumps({
                "model": model, "prompt": prompt, "resume": False, "auto_approve_writes": True,
                "max_iterations": int(config["max_iterations"]), "model_request_timeout_s": _model_request_timeout(),
                "tool_timeout_s": min(_tool_timeout(), int(policy.timeout)), "context_window": _context_window(relay, model),
                "reasoning_effort": _stage_effort("implement"), "environments": [],
                **({"harness": harness_request} if harness_request else {})}))
            relay.start(relay_dir)
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(relay_dir, policy.egress_extra).start()
            before = _status(job.worktree)
            saved = _snapshot(job.worktree, before, root / "snapshot")
            args = repair_container_args(job, (inputs, outputs, relay_dir), name, policy, relay.token,
                                         harness_mounts=tuple(harness_mounts),
                                         module="mu.jobs.sandbox.harness_worker" if agent != "mucli" else "mu.jobs.sandbox.worker")
            runner.run([docker_bin, *args], timeout=60)
            created = True
            status = _supervise(service, job, runner, docker_bin, name, policy, relay, alive)
            result: Dict[str, Any] = {}
            try:
                from .runner import _read_output
                result = _read_output(outputs, "result.json")
            except (RuntimeError, ValueError, OSError):
                result = {}
            transcript: Any = []
            try:
                if (outputs / "transcript.json").is_file():
                    from .runner import _read_output
                    transcript = _read_output(outputs, "transcript.json")
                    art.write_json(record["transcript"], transcript)
                    art.write_json("logs/repair-transcript.json", transcript)
            except (RuntimeError, ValueError, OSError):
                transcript = []
            if not (outputs / ".chowned").exists():
                _fix_ownership(runner, docker_bin, job, policy, policy.image)
            hygiene = revert_tracked_changes(job.worktree, before, saved)
            record.update(reverted_files=hygiene["reverted"], files=hygiene["files"])
            commands = parse_commands(str((result or {}).get("text") or ""), transcript)
            record.update(commands=commands, project_commands=project_commands(commands))
            for index, command in enumerate(commands):
                _event(service, job.id, {"phase": "command", "attempt": attempt, "binding": binding, "index": index,
                                         "command": command[:2000]})
            if hygiene["reverted"]:
                _event(service, job.id, {"phase": "reverted", "attempt": attempt, "binding": binding,
                                         "files": hygiene["reverted"]},
                       reason="The repair changed tracked files; they were reverted: " + ", ".join(hygiene["reverted"][:12]))
            hit = getattr(relay, "limit_hit", None)
            if status == "lease_lost":
                record.update(outcome="error", error="the repair stopped after the job lost its lease")
            elif status == "timed_out":
                record.update(outcome="timed_out", error=f"the repair reached its time limit ({int(policy.timeout)}s)")
            elif hit or str(result.get("status") or "") == "max_iterations_reached":
                what = hit.get("message") if isinstance(hit, dict) else f"{config['max_iterations']} iterations"
                record.update(outcome="budget_exhausted", error=f"the repair ran out of budget: {what}"[:1000])
            elif status == "failed" or result.get("kind") != "completed":
                error = str(result.get("error") or "") or "the repair container failed"
                record.update(outcome="failed", error=redact_secrets(error)[0][:1000])
            elif not _transcript_commands(transcript):
                # Nothing ran, so nothing changed: committing would only relabel the same image.
                suggested = parse_commands(str((result or {}).get("text") or ""))
                record.update(outcome="no_commands", error=(
                    "the repair agent ran no shell commands, so the environment is unchanged"
                    + (f"; it suggested: {'; '.join(suggested[:6])}" if suggested else "")))
            else:
                record["image"] = _commit(service, job, runner, name, target, attempt, args, record, settings)
                record["outcome"] = "committed"
    except Exception as exc:  # noqa: BLE001 - a repair never fails the job
        record.update(outcome="failed" if record["outcome"] == "running" else record["outcome"],
                      error=redact_secrets(str(exc) or type(exc).__name__)[0][:1000])
    finally:
        if relay is not None:
            try:
                record["cost_usd"] = round(float(relay.cost or 0), 6)
            except (TypeError, ValueError):
                pass
            relay.close()
        if bridge is not None:
            bridge.close()
        if created:
            try:
                runner.run([runner.require(_cli(settings)), "rm", "-f", name], check=False, timeout=60)
            except (ContainerRuntimeError, OSError, subprocess.SubprocessError):
                pass
        if target.get("base_ref"):
            try:  # untag only: the base keeps its own tag, the committed image its own layers
                runner.run([runner.require(_cli(settings)), "rmi", target["base_ref"]], check=False, timeout=60)
            except (ContainerRuntimeError, OSError, subprocess.SubprocessError):
                pass
        try:
            from ..stages import sync_job_cost
            sync_job_cost(service, job.id)
        except Exception:  # noqa: BLE001 - cost bookkeeping never masks the outcome
            pass
        if record["outcome"] != "committed":
            record["finished_at"] = time.time()
        _save_record(service, job.id, record)
        if record["outcome"] != "committed":
            finish(service, job.id, record)
    return record


def _cli(settings):
    from .cli import container_cli
    return container_cli(settings) if settings is not None else container_cli()


def _supervise(service, job, runner, docker_bin, name, policy, relay, alive) -> str:
    """Run the repair container to completion: ``ok``, ``failed``, ``timed_out`` or ``lease_lost``."""
    errors: List[BaseException] = []
    completed = threading.Event()

    def output(stream, line):
        if stream != "stdout":
            return
        try:
            data = json.loads(line)
        except ValueError:
            return
        if isinstance(data, dict) and data.get("event"):
            try:
                safe = json.loads(redact_secrets(json.dumps(data))[0])
                service.store.append_event(job.id, "sandbox_event", payload={"stage": "repair", **safe})
            except Exception:  # noqa: BLE001 - live view only
                pass

    def execute():
        try:
            run_with_output(runner, [docker_bin, "start", "-a", name], timeout=policy.timeout + 120, output_callback=output)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            completed.set()
    threading.Thread(target=execute, daemon=True).start()
    deadline = time.monotonic() + policy.timeout
    limit_seen = None
    status = ""
    while not completed.wait(.25):
        now = time.monotonic()
        if not alive():
            status = "lease_lost"
        elif now >= deadline:
            status = "timed_out"
        elif getattr(relay, "limit_hit", None):
            limit_seen = limit_seen or now
            if now - limit_seen >= LIMIT_STOP_GRACE_S:
                status = "budget"
        if status:
            # SIGTERM: the entrypoint stops the worker and still chowns the mounts.
            runner.run([docker_bin, "stop", "-t", "60", name], check=False, timeout=90)
            completed.wait(30)
            return status if status != "budget" else "ok"
    return "failed" if errors else "ok"


def _commit(service, job, runner, name: str, target: Dict[str, Any], attempt: int, args: Sequence[str],
            record: Dict[str, Any], settings) -> str:
    """Commit the repair container, scan it, and point the binding at it. Returns the tag."""
    from .environments import (Docker, LABEL_BINDING, LABEL_HOME, LABEL_ROLE, LABEL_SOURCE, home_id, job_labels,
                               remove_images, save_meta, secret_scan, job_states)
    from .job_setup import META_KEY as SETUP_KEY, setup_records
    from ..job_settings import rule
    docker = Docker(runner)
    binding = str(target["binding"])
    tag = repaired_tag(job.id, binding, attempt)
    labels = {**job_labels(job.id), LABEL_BINDING: binding, LABEL_ROLE: "job-image", LABEL_HOME: home_id(),
              LABEL_SOURCE: f"repair:{binding}:{attempt}", "io.mucli.repair": str(attempt)}
    # The base's config comes from the pinned base, not a tag a rebuild may have moved.
    changes = commit_changes(docker, str(target.get("base_ref") or target["image"]), _added_env_keys(args), labels)
    docker.run("commit", *changes, name, tag, timeout=1800)
    try:
        allow = list(rule("environments.secret_scan_allow", settings) or [])
    except (KeyError, OSError, ValueError, TypeError):
        allow = []
    hits = secret_scan(docker, tag, allow)
    if hits:
        remove_images(docker, [tag])
        raise RuntimeError("the repaired image contains secret files and was discarded: " + "; ".join(hits[:6]))
    fresh = service.get(job.id)
    if target.get("state"):
        states = job_states(fresh)
        for state in states:
            if state.get("name") == binding:
                previous = state.get("job_image")
                state.update(job_image=tag, repaired_from=previous, repair_attempt=attempt,
                             digest=docker.inspect("image", tag, "{{.Id}}") or state.get("digest", ""))
        records = setup_records(fresh)
        setup = records.get(binding)
        updates: Dict[str, Any] = {"build_environments": states}
        if setup and setup.get("image") == target["image"]:
            # Job setup already ran in this worktree; the repaired image keeps its result.
            setup["image"] = tag
            updates[SETUP_KEY] = records
        save_meta(service, job.id, **updates)
    else:
        save_meta(service, job.id, runtime_repair_image=tag)
    return tag


def finish(service, job_id: str, record: Dict[str, Any]) -> None:
    """The ``finished`` event of one attempt (after the re-run checks, for a committed image)."""
    outcome = str(record.get("outcome") or "")
    binding = record.get("binding")
    if outcome == "repaired":
        reason = (f"Environment repaired: build environment `{binding}` now runs the checks "
                  f"(image {record.get('image')}).")
    elif outcome == "no_commands":
        reason = (f"Environment repair of `{binding}` changed nothing: the repair agent ran no shell commands. "
                  "The job continues unverified.")
    elif outcome == "unresolved":
        reason = (f"Environment repair committed image {record.get('image')} for `{binding}`, but some checks "
                  "still could not run.")
    else:
        reason = (f"Environment repair of `{binding}` did not finish ({outcome.replace('_', ' ')}): "
                  f"{record.get('error') or 'no detail'}. The job continues unverified.")
    _event(service, job_id, {"phase": "finished", "attempt": record.get("attempt"), "binding": binding,
                             "outcome": outcome, "image": record.get("image") or "",
                             "cost_usd": record.get("cost_usd") or 0, "commands": list(record.get("commands") or []),
                             "reverted_files": list(record.get("reverted_files") or []),
                             "error": str(record.get("error") or "")[:1000]}, reason=reason)


def repair_and_recheck(service, job_id: str, run: Any, *, verify: Callable[[Any], Any],
                       owned: Callable[[], bool] = lambda: True, runner: Optional[CommandRunner] = None,
                       relay_factory=None, settings: Optional[Dict[str, Any]] = None,
                       repair: Optional[Callable[..., Dict[str, Any]]] = None) -> Any:
    """Repair the environment while ``run`` has checks that could not run, then re-run the checks
    (trigger ``repair``). Returns the last run. Never parks: a failed repair leaves ``run`` as is
    (the caller records it unverified)."""
    repair = repair or run_repair
    while owned():
        job = service.get(job_id)
        target = plan_repair(service, job, run, settings)
        if target is None:
            return run
        record = repair(service, job, run, target, runner=runner, relay_factory=relay_factory, settings=settings)
        if record.get("outcome") != "committed":
            return run
        try:
            service.store.patch_metadata(job_id, {"checks_trigger": "repair"})
            run = verify(service.get(job_id))
            summary = dict(getattr(run, "summary", None) or {})
            still = {s.get("suite") for s in trigger_suites(service.get(job_id), run)}
            fixed = not (still & {s.get("suite") for s in target["suites"]})
            record.update(outcome="repaired" if fixed else "unresolved",
                          checks_after={"run": summary.get("run"), "verified": summary.get("verified"),
                                        "passed": summary.get("passed"), "unrunnable": summary.get("unrunnable", 0)})
        except BaseException as exc:
            record.update(outcome="unresolved", error=redact_secrets(str(exc))[0][:1000])
            raise
        finally:
            record["finished_at"] = time.time()
            _save_record(service, job_id, record)
            finish(service, job_id, record)
    return run


# ------------------------------------------------------------------ read model + actions


def describe_repairs(job: Any) -> List[Dict[str, Any]]:
    """Job VM: every repair attempt (newest last) with what the actions need."""
    out = []
    for record in repair_records(job):
        out.append({k: record.get(k) for k in ("attempt", "binding", "source", "template", "trigger_suites", "commands",
                                               "project_commands", "image", "base_image", "cost_usd", "outcome",
                                               "error", "reverted_files", "files", "registries", "agent",
                                               "transcript", "started_at", "finished_at", "checks_after")})
    return out


def add_to_template_reason(job: Any, attempt: Any = None) -> Optional[str]:
    """``None`` when ``addRepairToTemplate`` may run, else why not."""
    record = _record_for(job, attempt)
    if record is None:
        return "no committed environment repair"
    if record.get("source") != "template" or not record.get("template"):
        return "the repaired build environment is not a template"
    if not record.get("project_commands"):
        return "the repair used no project-level commands"
    return None


def save_template_reason(job: Any, attempt: Any = None) -> Optional[str]:
    record = _record_for(job, attempt)
    if record is None or record.get("outcome") not in COMMITTED or not record.get("image"):
        return "no committed environment repair"
    return None


def add_repair_to_template(job: Any, attempt: Any = None) -> Dict[str, Any]:
    """Append the repair's project-level commands to the binding's template ``job_setup`` (only
    the commands not already there). Raises ``ValueError`` when not applicable."""
    from mu.container.templates import TemplateRegistry
    reason = add_to_template_reason(job, attempt)
    if reason:
        raise ValueError(reason)
    record = _record_for(job, attempt) or {}
    registry = TemplateRegistry()
    template = registry.get(str(record["template"]))
    if template is None:
        raise ValueError(f"template {record['template']!r} no longer exists")
    added = [c for c in record.get("project_commands") or [] if c not in template.job_setup]
    updated = registry.update(template.name, job_setup=[*template.job_setup, *added])
    return {"template": template.name, "added": added, "job_setup": list(updated.job_setup if updated else [])}


def save_repaired_template(service, job: Any, name: str = "", attempt: Any = None, *, docker=None) -> Dict[str, Any]:
    """Create a container-mode template from the repaired image: the binding's egress plus the
    registries the repair used, and its job setup. The template gets its own image (no job
    label), so job cleanup does not remove it. Raises ``ValueError``."""
    from mu.container.builder import container_slug
    from mu.container.templates import ContainerTemplate, TemplateRegistry, clean_egress_allow, clean_job_setup
    from .environments import Docker, filter_model_hosts, job_states
    from .toolchain import env_value
    reason = save_template_reason(job, attempt)
    if reason:
        raise ValueError(reason)
    record = _record_for(job, attempt) or {}
    default = f"{record.get('template') or record.get('binding') or 'job'}-repaired"
    try:
        slug = container_slug(str(name or "").strip() or default)
    except ValueError as exc:
        raise ValueError("a template name is required") from exc
    registry = TemplateRegistry()
    if registry.get(slug) is not None:
        raise ValueError(f"template {slug!r} already exists; choose another name")
    state = next((s for s in job_states(job) if s.get("name") == record.get("binding")), {}) or {}
    egress = clean_egress_allow(filter_model_hosts([*(state.get("egress_allow") or []), *(record.get("registries") or [])]))
    setup = clean_job_setup(list(state.get("job_setup") or []))
    docker = docker or Docker()
    image = f"mucli/template-{slug}:{int(time.time())}"
    labels = {"io.mucli.template": slug, "io.mucli.source": f"repair:{str(job.id)[:12]}:{record.get('attempt')}",
              # Cleared so job cleanup (label io.mucli.job=<id>) never matches the template's image.
              "io.mucli.job": "", "io.mucli.binding": "", "io.mucli.role": "template", "io.mucli.repair": ""}
    with tempfile.TemporaryDirectory(prefix="mucli-repaired-template-") as tmp:
        Path(tmp, "Dockerfile").write_text(f"FROM {record['image']}\nLABEL " + " ".join(
            f'{k}="{env_value(v)}"' for k, v in sorted(labels.items())) + "\n")
        try:
            docker.run("build", "--pull=false", "-t", image, tmp, timeout=1800)
        except (ContainerRuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"could not create the template image: {redact_secrets(str(exc))[0][:500]}") from exc
    template = registry.upsert(ContainerTemplate(
        name=slug, image=image, source_container="", job_setup=setup, egress_allow=egress,
        description=f"Repaired environment from job {str(job.id)[:12]} (binding {record.get('binding')}, "
                    f"repair {record.get('attempt')})."))
    return {"template": template.name, "image": image, "egress_allow": list(template.egress_allow),
            "job_setup": list(template.job_setup)}
