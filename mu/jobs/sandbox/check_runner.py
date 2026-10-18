"""Host-launched check containers, with no agent and no provider relay."""
import json
import re
import subprocess
import tempfile
import time
import uuid
import threading
from pathlib import Path

from mu.container.docker_cli import CommandRunner, ContainerRuntimeError, run_with_output
from mu.security.secret_paths import redact_secrets
from .policy import SandboxPolicy, container_args, with_build_environment
from .runner import SandboxUnavailable, _guard, _read_output, check_restart, require_image


_MISSING_RE = re.compile(r"FileNotFoundError: \[Errno 2\] No such file or directory: '([^']+)'")


def _missing_executable(text):
    """Name of the command a check container could not find (older runtime images crash instead)."""
    match = _MISSING_RE.search(text or "")
    return match.group(1) if match and "check_worker" in (text or "") else None


def run_suite_in_container(job, workspace, suite, *, runner=None, service=None, restart_cursor=None,
                           environment=None, states=(), guard=True):
    """Run one suite in the build environment that owns it (``environment``; None = shared runtime).

    ``guard=False``: the caller (the checks stage) guards the worktree itself, after it has
    discarded the suites' byproducts, so a leftover the stage deletes anyway never blocks."""
    return _run_container(job, workspace, [suite], runner=runner, service=service, restart_cursor=restart_cursor,
                          environment=environment, states=states, guard=guard)[0]


def run_suites_in_container(job, workspace, suites, *, runner=None, service=None, restart_cursor=None,
                            environment=None, states=(), guard=True):
    """Run several suites of one build environment in a single container (one start-up, not one
    per suite). Falls back to one container per suite for a runtime image that predates batching."""
    suites = list(suites)
    if len(suites) <= 1:
        return [run_suite_in_container(job, workspace, s, runner=runner, service=service, restart_cursor=restart_cursor,
                                       environment=environment, states=states, guard=guard) for s in suites]
    results = _run_container(job, workspace, suites, runner=runner, service=service, restart_cursor=restart_cursor,
                             environment=environment, states=states, guard=guard)
    if all(r.get("batch_unsupported") for r in results):
        return [run_suite_in_container(job, workspace, s, runner=runner, service=service, restart_cursor=restart_cursor,
                                       environment=environment, states=states, guard=guard) for s in suites]
    return results


def _spec(suite, timeout):
    return {"command": suite.command, "junit": suite.junit, "timeout": timeout, "cwd": str(getattr(suite, "cwd", "") or "")}


def _run_container(job, workspace, suites, *, runner=None, service=None, restart_cursor=None,
                   environment=None, states=(), guard=True):
    runner = runner or CommandRunner()
    policy = with_build_environment(SandboxPolicy.for_stage("checks"), job, environment, stage="checks")
    docker = require_image(runner, policy, environment)
    where = f"build environment `{environment['name']}` ({policy.image})" if environment else f"the job sandbox image {policy.image}"
    batch = len(suites) > 1
    total_timeout = policy.timeout * len(suites) + 15
    with tempfile.TemporaryDirectory(prefix="mucli-suite-") as tmp:
        dirs = [Path(tmp) / n for n in ("input", "output", "relay")]
        for path in dirs:
            path.mkdir()
        request = ({"suites": [_spec(s, policy.timeout) for s in suites], "timeout": policy.timeout} if batch
                   else _spec(suites[0], policy.timeout))
        (dirs[0] / "request.json").write_text(json.dumps(request))
        # Named like stage containers so Activity can attribute it to the job.
        name = f"mucli-job-{str(job.id)[:12]}-checks-{uuid.uuid4().hex[:8]}"
        args = container_args(name, "checks", workspace, *map(str, dirs), policy, "")
        args[-1] = "mu.jobs.sandbox.check_worker"  # entrypoint (args[-3]) is the policy's Python
        bridge = proxy = None

        def label(chunk):
            index = chunk.get("suite_index")
            return suites[index].name if isinstance(index, int) and 0 <= index < len(suites) else suites[0].name

        should_guard = guard

        def guard():
            # Checks mount the worktree read-write: the same host guard as after implement
            # (changed paths, secret files, credentials in the diff). Not for the baseline copy.
            if should_guard and workspace == job.worktree and getattr(job, "worktree", ""):
                _guard(job, policy)

        def finish(result):
            if result.get("missing_executable"):
                result["stderr"] = f"{result['missing_executable']}: not installed in {where}"
            return result
        try:
            if policy.egress_extra:
                from .egress import EgressBridge
                bridge = EgressBridge(dirs[2], policy.egress_extra).start()
            if environment and environment.get("docker"):
                from .engine import attach_docker
                policy, proxy = attach_docker(policy, job, environment, list(states), dirs[2], service=service,
                                              stage="checks")
                args = container_args(name, "checks", workspace, *map(str, dirs), policy, "")
                args[-1] = "mu.jobs.sandbox.check_worker"
            runner.run([docker, *args], timeout=30)
            if service is None:
                runner.run([docker, "start", "-a", name], timeout=total_timeout)
            else:
                cursor = service.store.event_cursor() if restart_cursor is None else restart_cursor
                completed, errors = threading.Event(), []
                def output(stream, line):
                    if stream != "stdout":
                        return
                    try:
                        chunk = json.loads(line)
                        if isinstance(chunk, dict) and chunk.get("event") == "check_output":
                            service.store.append_event(job.id, "checks_output", payload={
                                "suite": label(chunk), "baseline": workspace != job.worktree,
                                "text": redact_secrets(str(chunk.get("text") or ""))[0][:8192]})
                    except ValueError:
                        pass
                def execute():
                    try:
                        run_with_output(runner, [docker, "start", "-a", name], timeout=total_timeout, output_callback=output)
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
                if errors and not batch:
                    raise errors[0]
            guard()
            if not batch:
                return [finish(_read_output(dirs[1], "result.json"))]
            results = []
            for index in range(len(suites)):
                try:
                    results.append(finish(_read_output(dirs[1], f"result-{index}.json")))
                except RuntimeError:
                    results.append({"passed": False, "return_code": None, "timed_out": False, "stdout": "",
                                    "stderr": "suite produced no result", "batch_unsupported": True})
            if not all(r.get("batch_unsupported") for r in results):
                for r in results:
                    r.pop("batch_unsupported", None)
            return results
        except ContainerRuntimeError as exc:
            missing = _missing_executable(str(exc))
            if missing is None:
                raise
            return [{"passed": False, "return_code": 127, "timed_out": False, "stdout": "",
                     "stderr": f"{missing}: not installed in {where}", "missing_executable": missing}
                    for _ in suites]
        except subprocess.TimeoutExpired:
            return [{"passed": False, "timed_out": True, "return_code": None, "stderr": "check container timed out"}
                    for _ in suites]
        finally:
            runner.run([docker, "rm", "-f", name], check=False, timeout=20)
            if bridge:
                bridge.close()
            if proxy:
                proxy.close()
