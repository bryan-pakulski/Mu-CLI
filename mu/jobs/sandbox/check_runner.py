"""Host-launched check containers, with no agent and no provider relay."""
import json
import subprocess
import tempfile
import time
import uuid
import threading
from pathlib import Path

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets
from .policy import SandboxPolicy, container_args
from .runner import SandboxUnavailable, _read_output, check_restart


def run_suite_in_container(job, workspace, suite, *, runner=None, service=None, restart_cursor=None):
    runner = runner or CommandRunner()
    policy = SandboxPolicy.for_stage("checks")
    try:
        docker = runner.require("docker")
        runner.run([docker, "image", "inspect", policy.image], timeout=15)
    except (ContainerRuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable("Sandbox required: Docker and the shared " + policy.image +
            " image must be available. Build it with python3 -m mu.jobs.sandbox.build.") from exc
    with tempfile.TemporaryDirectory(prefix="mucli-suite-") as tmp:
        dirs = [Path(tmp) / n for n in ("input", "output", "relay")]
        for path in dirs:
            path.mkdir()
        (dirs[0] / "request.json").write_text(json.dumps({"command": suite.command, "junit": suite.junit,
                                                          "timeout": policy.timeout}))
        name = "mucli-check-" + uuid.uuid4().hex[:16]
        args = container_args(name, "checks", workspace, *map(str, dirs), policy, "")
        args[-1] = "mu.jobs.sandbox.check_worker"
        bridge = None
        try:
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(dirs[2], policy.egress_extra).start()
            runner.run([docker, *args], timeout=30)
            if service is None:
                runner.run([docker, "start", "-a", name], timeout=policy.timeout + 15)
            else:
                cursor = service.store.event_cursor() if restart_cursor is None else restart_cursor
                completed, errors = threading.Event(), []
                def output(label, line):
                    if label != "stdout":
                        return
                    try:
                        chunk = json.loads(line)
                        if isinstance(chunk, dict) and chunk.get("event") == "check_output":
                            service.store.append_event(job.id, "checks_output", payload={
                                "suite": suite.name, "baseline": workspace != job.worktree,
                                "text": redact_secrets(str(chunk.get("text") or ""))[0][:8192]})
                    except ValueError:
                        pass
                def execute():
                    try:
                        run_with_output(runner, [docker, "start", "-a", name], timeout=policy.timeout + 15, output_callback=output)
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        completed.set()
                threading.Thread(target=execute, daemon=True).start()
                while not completed.wait(.25):
                    if job.worker_id and not service.store.assert_lease(job.id, job.worker_id):
                        raise RuntimeError("check container stopped after losing job lease")
                    check_restart(service, job, "checks", cursor)
                check_restart(service, job, "checks", cursor)
                if errors:
                    raise errors[0]
            return _read_output(dirs[1], "result.json")
        except subprocess.TimeoutExpired:
            return {"passed": False, "timed_out": True, "return_code": None, "stderr": "check container timed out"}
        finally:
            runner.run([docker, "rm", "-f", name], check=False, timeout=20)
            if bridge:
                bridge.close()
