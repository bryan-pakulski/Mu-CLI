"""``run_in_environment``: commands in another build environment of the job (monorepos, §4).

The implement agent runs in its primary binding. For components owned by another binding it
calls ``run_in_environment(env, command, cwd)``; the in-container tool posts to the stage relay
and this host-side broker starts a *sibling* container from that binding's job image with the
same isolation (``--network none``, read-only rootfs, ``--cap-drop ALL``, host uid:gid), the
same worktree mount and that binding's ``/cache`` and egress. Output streams back to the agent
and every call is recorded as an ``environment_command`` event.
"""
from __future__ import annotations

import json
import posixpath
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets

MAX_OUTPUT = 256 * 1024


def describe(states: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the agent sees about each environment (tool description + enum)."""
    return [{"name": s["name"], "paths": list(s.get("paths") or []), "docker": bool(s.get("docker")),
             "gpus": s.get("gpus") or ""} for s in states if s.get("status") == "ready"]


class EnvironmentBroker:
    def __init__(self, service, job: Any, states: Sequence[Dict[str, Any]], *, stage: str,
                 runner: Optional[CommandRunner] = None, settings: Optional[Dict[str, Any]] = None):
        self.service = service
        self.job = job
        self.states = {s["name"]: s for s in states if s.get("status") == "ready"}
        self.stage = stage
        self.runner = runner or CommandRunner()
        self.settings = settings
        self.lock = threading.Lock()

    def run(self, env: str, command: str, cwd: str = "", timeout: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Yield ``{"event": "output", ...}`` chunks, then one ``{"event": "result", ...}``."""
        from .engine import attach_docker
        from .policy import SandboxPolicy, container_args, with_build_environment
        state = self.states.get(str(env or ""))
        if state is None:
            yield {"event": "result", "ok": False, "return_code": None,
                   "error": f"unknown build environment {env!r}; this job has: {', '.join(self.states) or 'none'}"}
            return
        command = str(command or "").strip()
        if not command:
            yield {"event": "result", "ok": False, "return_code": None, "error": "command is required"}
            return
        cwd = posixpath.normpath(str(cwd or ".").strip() or ".").strip("/")
        if cwd.startswith("..") or str(cwd).startswith("/"):
            yield {"event": "result", "ok": False, "return_code": None, "error": "cwd must be a path inside the repository"}
            return
        base = SandboxPolicy.for_stage("checks", self.settings) if self.settings is not None else SandboxPolicy.for_stage("checks")
        policy = with_build_environment(base, self.job, state, stage="checks", settings=self.settings)
        limit = max(1, min(int(timeout or policy.timeout), policy.timeout))
        started = time.monotonic()
        try:
            self.service.store.append_event(self.job.id, "environment_command_started", payload={
                "stage": self.stage, "env": state["name"], "command": command[:2000], "cwd": cwd})
        except Exception:  # noqa: BLE001
            pass
        from .cli import container_cli
        docker = self.runner.require(container_cli(self.settings))
        name = f"mucli-job-{str(self.job.id)[:12]}-env-{state['name'][:20]}-{uuid.uuid4().hex[:6]}"
        result: Dict[str, Any] = {}
        bridge = proxy = None
        created = False
        with tempfile.TemporaryDirectory(prefix="mucli-env-cmd-") as tmp:
            dirs = [Path(tmp) / n for n in ("input", "output", "relay")]
            for path in dirs:
                path.mkdir()
            (dirs[0] / "request.json").write_text(json.dumps({"command": command, "cwd": "" if cwd == "." else cwd,
                                                              "timeout": limit, "shell": True}))
            try:
                if policy.egress_extra:
                    from .egress import EgressBridge
                    bridge = EgressBridge(dirs[2], policy.egress_extra).start()
                policy, proxy = attach_docker(policy, self.job, state, list(self.states.values()), dirs[2],
                                              service=self.service, settings=self.settings)
                from ..multi_source import container_mounts
                args = container_args(name, "checks", self.job.worktree, *map(str, dirs), policy, "", role="job-broker",
                                      extra_mounts=tuple(container_mounts(self.job, "checks")))
                args[-1] = "mu.jobs.sandbox.check_worker"
                self.runner.run([docker, *args], timeout=60)
                created = True
                chunks: List[Dict[str, Any]] = []
                done = threading.Event()
                errors: List[BaseException] = []

                def on_line(label, line):
                    if label != "stdout":
                        return
                    try:
                        data = json.loads(line)
                    except ValueError:
                        return
                    if isinstance(data, dict) and data.get("event") == "check_output":
                        with self.lock:
                            chunks.append({"event": "output", "stream": data.get("stream") or "stdout",
                                           "text": redact_secrets(str(data.get("text") or ""))[0]})

                def execute():
                    try:
                        run_with_output(self.runner, [docker, "start", "-a", name], timeout=limit + 30, output_callback=on_line)
                    except BaseException as exc:  # noqa: BLE001
                        errors.append(exc)
                    finally:
                        done.set()
                threading.Thread(target=execute, daemon=True).start()
                while True:
                    finished = done.wait(0.2)
                    with self.lock:
                        pending, chunks[:] = list(chunks), []
                    yield from pending
                    if finished:
                        break
                try:
                    path = dirs[1] / "result.json"
                    result = json.loads(path.read_text()) if path.is_file() and not path.is_symlink() else {}
                except ValueError:
                    result = {}
                if not result and errors:
                    result = {"passed": False, "return_code": None, "stderr": redact_secrets(str(errors[0]))[0][-2000:]}
            except (ContainerRuntimeError, OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
                result = {"passed": False, "return_code": None, "stderr": redact_secrets(str(exc))[0][-2000:]}
            finally:
                if created:
                    self.runner.run([docker, "rm", "-f", name], check=False, timeout=30)
                if bridge is not None:
                    bridge.close()
                if proxy is not None:
                    proxy.close()
                self.runner.commands.clear()
        duration = int((time.monotonic() - started) * 1000)
        final = {"event": "result", "ok": bool(result.get("passed")), "env": state["name"], "cwd": cwd,
                 "return_code": result.get("return_code"), "timed_out": bool(result.get("timed_out")),
                 "missing_executable": result.get("missing_executable") or "",
                 "stdout": redact_secrets(str(result.get("stdout") or ""))[0][-MAX_OUTPUT:],
                 "stderr": redact_secrets(str(result.get("stderr") or ""))[0][-MAX_OUTPUT:],
                 "duration_ms": duration}
        try:
            self.service.store.append_event(self.job.id, "environment_command", payload={
                "stage": self.stage, "env": state["name"], "command": command[:2000], "cwd": cwd,
                "return_code": final["return_code"], "timed_out": final["timed_out"], "duration_ms": duration,
                "container": name, "image": policy.image})
        except Exception:  # noqa: BLE001
            pass
        yield final
