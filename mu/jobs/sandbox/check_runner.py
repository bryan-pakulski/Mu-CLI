"""Host-launched check containers, with no agent and no provider relay."""
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from mu.container.docker_cli import CommandRunner
from .policy import SandboxPolicy, container_args
from .runner import SandboxUnavailable, _read_output


def run_suite_in_container(job, workspace, suite, *, runner=None):
    runner = runner or CommandRunner()
    policy = SandboxPolicy.for_stage("checks")
    docker = runner.require("docker")
    runner.run([docker, "image", "inspect", policy.image], timeout=15)
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
            runner.run([docker, "start", "-a", name], timeout=policy.timeout + 15)
            return _read_output(dirs[1], "result.json")
        except subprocess.TimeoutExpired:
            return {"passed": False, "timed_out": True, "return_code": None, "stderr": "check container timed out"}
        finally:
            runner.run([docker, "rm", "-f", name], check=False, timeout=20)
            if bridge:
                bridge.close()
