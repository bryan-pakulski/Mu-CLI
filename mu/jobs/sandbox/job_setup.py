"""Template job setup (``documentation/plans/job_templates.md`` §4).

A container template can carry ``job_setup``: an ordered list of shell commands (``npm ci``,
``cargo fetch`` …) that install the repository's dependencies into the job's worktree. They run
once per job image, after the job image is built and before planning, and again whenever the
image is re-created (``refreshEnvironments``, re-creation after idle cleanup).

Isolation is the checks stage's: a fresh container from the binding's job image, ``--network
none`` with egress only through the host bridge (the template's allowlist plus Settings → Extra
egress domains, model hosts dropped), read-only rootfs, host uid:gid, ``--cap-drop ALL``,
``no-new-privileges``, the worktree read-write at ``/workspace``, the shared ``/cache``, no
credentials and no model relay. The commands are authored by the template owner in mucli (like
Toolchains rows); they are never read from the repository.

Progress is recorded as ``environment_setup`` events ``{binding, command, index, status:
running|ok|failed, exit_code?, output_tail?}`` (output chunks as ``environment_setup_log``). A
failing command parks the job (``ENVIRONMENT_FAILURE``) with the command and its log tail.
Files that setup leaves in ``git status`` are listed (``environment_setup_dirty``) and ignored
by the checks' dirty-worktree rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets

META_KEY = "environment_setup"
TAIL_CHARS = 4000
MAX_LOG_EVENTS = 400


def commands_digest(commands: Sequence[str]) -> str:
    return hashlib.sha1(json.dumps(list(commands or [])).encode()).hexdigest()[:16]


def setup_records(job: Any) -> Dict[str, Dict[str, Any]]:
    raw = dict(getattr(job, "metadata", None) or {}).get(META_KEY) or {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def needs_setup(job: Any, state: Dict[str, Any]) -> bool:
    """True when the binding has setup commands not yet run (ok or skipped) for its job image."""
    commands = list(state.get("job_setup") or [])
    if not commands or state.get("status") != "ready" or not state.get("job_image"):
        return False
    record = setup_records(job).get(str(state.get("name"))) or {}
    return not (record.get("image") == state.get("job_image") and record.get("digest") == commands_digest(commands)
                and record.get("status") in ("ok", "skipped"))


def setup_files(job: Any) -> List[str]:
    """Paths setup left in ``git status`` (all bindings); the checks' dirty rule ignores them."""
    out: List[str] = []
    for record in setup_records(job).values():
        for path in record.get("files") or []:
            if path not in out:
                out.append(str(path))
    return out


def setup_policy(job: Any, state: Dict[str, Any], settings: Optional[Dict[str, Any]] = None):
    """The checks-stage policy of the binding, with egress = Settings extra ∪ template egress
    (model hosts dropped) and no GPUs."""
    from .environments import filter_model_hosts
    from .policy import SandboxPolicy, with_build_environment
    base = SandboxPolicy.for_stage("checks", settings) if settings is not None else SandboxPolicy.for_stage("checks")
    policy = with_build_environment(base, job, state, stage="checks", settings=settings)
    egress = tuple(dict.fromkeys((*policy.egress_extra, *filter_model_hosts(state.get("egress_allow") or []))))
    return replace(policy, egress_extra=egress, gpus="", docker_socket="")


def setup_container_args(job: Any, state: Dict[str, Any], dirs: Sequence[Path], name: str, policy) -> List[str]:
    """``docker create`` arguments of one setup command (the stage isolation; see module doc)."""
    from .policy import container_args
    args = container_args(name, "checks", job.worktree, *map(str, dirs), policy, "", role="job-setup")
    args[-1] = "mu.jobs.sandbox.check_worker"  # runs ``/bin/sh -c <command>`` (request.shell)
    return args


def _save(service, job_id: str, name: str, record: Dict[str, Any]) -> None:
    from .environments import save_meta
    records = setup_records(service.get(job_id))
    records[name] = record
    save_meta(service, job_id, **{META_KEY: records})


def _event(service, job_id: str, payload: Dict[str, Any], reason: str = "") -> None:
    try:
        service.store.append_event(job_id, "environment_setup", reason=reason, payload=payload)
    except Exception:  # noqa: BLE001 - events are informational
        pass


def _status_entries(worktree: str) -> set:
    try:
        from ..checks import status_entries
        return set(status_entries(worktree))
    except (OSError, subprocess.SubprocessError):
        return set()


def run_job_setup(service, job: Any, states: Iterable[Dict[str, Any]], *, runner: Optional[CommandRunner] = None,
                  settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """Run pending setup for every ready binding. Returns the bindings set up; raises
    ``EnvironmentBlocked`` (the job parks) when a command fails. No-op without a worktree."""
    worktree = str(getattr(job, "worktree", "") or "")
    if not worktree or not os.path.isdir(worktree):
        return []
    done = []
    for state in states:
        if needs_setup(service.get(job.id), state):
            run_binding_setup(service, job, state, runner=runner, settings=settings)
            done.append(str(state["name"]))
    return done


def run_binding_setup(service, job: Any, state: Dict[str, Any], *, runner: Optional[CommandRunner] = None,
                      settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from .cli import container_cli
    from .environments import EnvironmentBlocked
    runner = runner or CommandRunner()
    name = str(state["name"])
    commands = list(state.get("job_setup") or [])
    record: Dict[str, Any] = {"image": state.get("job_image"), "digest": commands_digest(commands), "status": "running",
                              "started_at": time.time(), "template": state.get("ref") if state.get("source") == "template" else "",
                              "commands": [{"index": i, "command": c, "status": "pending"} for i, c in enumerate(commands)],
                              "files": [], "error": ""}
    _save(service, job.id, name, record)
    policy = setup_policy(job, state, settings)
    docker = runner.require(container_cli(settings) if settings is not None else container_cli())
    before = _status_entries(job.worktree)
    service.store.append_event(job.id, "environment_setup_started",
                               reason=f"Job setup for build environment `{name}` ({len(commands)} command(s))…",
                               payload={"binding": name, "commands": commands})
    for index, command in enumerate(commands):
        record["commands"][index]["status"] = "running"
        _save(service, job.id, name, record)
        _event(service, job.id, {"binding": name, "command": command, "index": index, "status": "running"})
        result = _run_one(service, job, state, policy, docker, runner, command, index)
        code = result.get("return_code")
        ok = bool(result.get("passed"))
        tail = redact_secrets(f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip())[0][-TAIL_CHARS:]
        record["commands"][index].update(status="ok" if ok else "failed", exit_code=code,
                                         timed_out=bool(result.get("timed_out")))
        _event(service, job.id, {"binding": name, "command": command, "index": index, "status": "ok" if ok else "failed",
                                 "exit_code": code, "output_tail": tail[-2000:]})
        if not ok:
            why = "timed out" if result.get("timed_out") else f"exited {code}" if code is not None else "could not run"
            record.update(status="failed", finished_at=time.time(), error=tail[-2000:], failed_command=command,
                          failed_index=index)
            _save(service, job.id, name, record)
            message = (f"Job setup for build environment `{name}` failed: `{command}` {why}.\n\n{tail[-1500:]}\n\n"
                       "Retry, edit the template's job setup (Environments → template → Used for jobs), "
                       "or continue without setup.")
            error = EnvironmentBlocked(message)
            error.setup_failure = {"binding": name, "command": command, "index": index, "exit_code": code,  # type: ignore[attr-defined]
                                   "output_tail": tail, "template": record["template"]}
            raise error
    after = _status_entries(job.worktree)
    new_files = sorted({path for _code, path in after - before})[:500]
    record.update(status="ok", finished_at=time.time(), files=new_files)
    _save(service, job.id, name, record)
    if new_files:
        shown = ", ".join(new_files[:12]) + (f" (+{len(new_files) - 12} more)" if len(new_files) > 12 else "")
        service.store.append_event(job.id, "environment_setup_dirty",
                                   reason=f"Job setup left files that git does not ignore: {shown}. Add them to "
                                          ".gitignore; checks ignore them for this job.",
                                   payload={"binding": name, "files": new_files})
    service.store.append_event(job.id, "environment_setup_finished", reason=f"Job setup for `{name}` finished.",
                               payload={"binding": name, "commands": len(commands)})
    return record


def _run_one(service, job: Any, state: Dict[str, Any], policy, docker: str, runner, command: str,
             index: int) -> Dict[str, Any]:
    name = f"mucli-job-{str(job.id)[:12]}-setup-{uuid.uuid4().hex[:8]}"
    bridge = None
    created = False
    sent = [0]
    with tempfile.TemporaryDirectory(prefix="mucli-setup-") as tmp:
        dirs = [Path(tmp) / n for n in ("input", "output", "relay")]
        for path in dirs:
            path.mkdir()
        (dirs[0] / "request.json").write_text(json.dumps({"command": command, "cwd": "", "timeout": policy.timeout,
                                                          "shell": True}))

        def on_line(stream, line):
            if stream != "stdout" or sent[0] >= MAX_LOG_EVENTS:
                return
            try:
                data = json.loads(line)
            except ValueError:
                return
            if isinstance(data, dict) and data.get("event") == "check_output":
                sent[0] += 1
                try:
                    service.store.append_event(job.id, "environment_setup_log", payload={
                        "binding": state["name"], "index": index,
                        "text": redact_secrets(str(data.get("text") or ""))[0][:4096]})
                except Exception:  # noqa: BLE001
                    pass
        try:
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(dirs[2], policy.egress_extra).start()
            args = setup_container_args(job, state, dirs, name, policy)
            runner.run([docker, *args], timeout=60)
            created = True
            run_with_output(runner, [docker, "start", "-a", name], timeout=policy.timeout + 30, output_callback=on_line)
            path = dirs[1] / "result.json"
            if path.is_file() and not path.is_symlink():
                return json.loads(path.read_text())
            return {"passed": False, "return_code": None, "stderr": "setup command produced no result"}
        except subprocess.TimeoutExpired:
            return {"passed": False, "return_code": None, "timed_out": True, "stderr": "setup command timed out"}
        except (ContainerRuntimeError, OSError, RuntimeError, ValueError) as exc:
            return {"passed": False, "return_code": None, "stderr": redact_secrets(str(exc))[0][-2000:]}
        finally:
            if created:
                runner.run([docker, "rm", "-f", name], check=False, timeout=30)
            if bridge is not None:
                bridge.close()


def skip_failed_setup(service, job_id: str) -> List[str]:
    """"Continue without setup": mark the failed bindings' setup skipped for their current image."""
    job = service.get(job_id)
    records = setup_records(job)
    skipped = []
    for name, record in records.items():
        if record.get("status") == "failed":
            record.update(status="skipped", skipped_at=time.time())
            skipped.append(name)
    if skipped:
        from .environments import save_meta
        save_meta(service, job_id, **{META_KEY: records})
        service.store.append_event(job_id, "environment_setup_skipped",
                                   reason="Continuing without job setup for " + ", ".join(f"`{n}`" for n in skipped),
                                   payload={"bindings": skipped})
    return skipped


def failed_setup(job: Any) -> Optional[Dict[str, Any]]:
    """The failed setup the job is parked on (``{binding, command, index, error, template}``) or None."""
    for name, record in setup_records(job).items():
        if record.get("status") == "failed":
            return {"binding": name, "command": record.get("failed_command") or "", "index": record.get("failed_index"),
                    "error": record.get("error") or "", "template": record.get("template") or ""}
    return None


def describe_setup(job: Any, name: str) -> Dict[str, Any]:
    """Job VM: one binding's setup (``status`` '' when the binding has none)."""
    record = setup_records(job).get(str(name)) or {}
    return {"status": str(record.get("status") or ""), "commands": list(record.get("commands") or []),
            "files": list(record.get("files") or [])[:100], "error": str(record.get("error") or "")[-2000:],
            "template": str(record.get("template") or ""), "finished_at": record.get("finished_at")}
