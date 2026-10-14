"""Real Docker integration for job build environments (opt-in: ``MUCLI_ENV_DOCKER_TESTS=1``).

Needs Docker and the locally built ``mucli-job-runtime:latest`` image (it doubles as a cheap
"container-mode environment" base: Python 3.10+, git and mucli's requirements are present, so the
job layer builds quickly). GPU and Sysbox cases skip when the host lacks them.
"""
import json
import os
import subprocess
import uuid
from types import SimpleNamespace

import pytest

RUNTIME = "mucli-job-runtime:latest"

pytestmark = pytest.mark.skipif(os.environ.get("MUCLI_ENV_DOCKER_TESTS") != "1",
                                reason="set MUCLI_ENV_DOCKER_TESTS=1 to run Docker build-environment tests")


def sh(*args, check=True, timeout=600):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=check)


@pytest.fixture(scope="module")
def docker_ready():
    try:
        if sh("docker", "image", "inspect", RUNTIME, check=False, timeout=15).returncode:
            pytest.skip("mucli-job-runtime image unavailable")
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    monkeypatch.setenv("MUCLI_HOME", str(root))
    import utils.config as config
    import mu.container.registry as registry
    import mu.container.templates as templates
    for module in (config, registry, templates):
        monkeypatch.setattr(module, "HISTORY_DIR", str(root))
    return root


@pytest.fixture
def make_env(docker_ready, home):
    """A running 'container-mode environment' registered in this test's MUCLI_HOME."""
    from mu.container.ref import ContainerRef
    from mu.container.registry import ContainerRegistry
    created = []

    def make(short, setup, *, gpus=""):
        name = f"mucli-envtest-{short}-{uuid.uuid4().hex[:6]}"
        sh("docker", "run", "-d", "--name", name, "--entrypoint", "sleep", RUNTIME, "infinity")
        created.append(name)
        sh("docker", "exec", name, "sh", "-c", setup)
        ContainerRegistry().upsert(ContainerRef(container_id=name, name=name, image=RUNTIME, dockerfile_hash="",
                                                gpu_request=gpus, egress_allow=["crates.io", "api.openai.com"],
                                                root_volume=f"{name}-home", status="running"))
        return name
    yield make
    for name in created:
        sh("docker", "rm", "-f", name, check=False)


@pytest.fixture
def job_service(home, tmp_path):
    from mu.jobs import JobService, JobSpec, JobStore
    from mu.jobs.worktree import JobWorktreeManager
    repo = tmp_path / "repo"
    (repo / "svc").mkdir(parents=True)
    (repo / "web").mkdir()
    (repo / "svc" / "main.rs").write_text("fn main() {}\n")
    (repo / "web" / "index.js").write_text("console.log(1)\n")
    git = lambda *a: sh("git", "-C", str(repo), *a)
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))

    def create(environments):
        job = service.create(JobSpec(title="env job", repository=str(repo),
                                     execution={"provider": "openai", "model": "m", "environments": environments}))
        JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
        return service.get(job.id)
    yield service, create
    from mu.jobs.sandbox.environments import cleanup_job
    for job in service.list(limit=100):
        cleanup_job(job.id)


TOOLCHAIN = ("mkdir -p /root/.fakecargo/bin && printf '#!/bin/sh\\necho fake-cargo-ok \"$@\"\\n' > /root/.fakecargo/bin/fcargo "
             "&& chmod +x /root/.fakecargo/bin/fcargo && echo 'export PATH=/root/.fakecargo/bin:$PATH' >> /root/.bashrc "
             "&& mkdir -p /root/.ssh && echo SECRET > /root/.ssh/id_test && echo 'ls' > /root/.bash_history")


def labelled(job_id, kind="image"):
    args = {"image": ["image", "ls", "-q"], "container": ["ps", "-aq"]}[kind]
    return sh("docker", *args, "--filter", f"label=io.mucli.job={job_id}").stdout.split()


def test_snapshot_keeps_home_toolchain_and_path_but_not_secrets_then_checks_pass(make_env, job_service):
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suite_in_container
    from mu.jobs.sandbox.environments import ensure_environments, cleanup_job
    service, create = job_service
    env = make_env("rust", TOOLCHAIN)
    job = create([f"environment:{env}"])
    states = ensure_environments(service, job)
    state = states[0]
    assert state["status"] == "ready" and "/root/.fakecargo/bin" in state["probed_env"]["PATH"]
    assert any(p.startswith(".ssh") for p in state["skipped_paths"]) and any(".bash_history" in p for p in state["skipped_paths"])
    image = state["job_image"]
    assert sh("docker", "run", "--rm", "--entrypoint", "sh", image, "-c", "test ! -e /root/.ssh/id_test && test ! -e /root/.bashrc").returncode == 0
    env_vars = sh("docker", "image", "inspect", "--format", "{{json .Config.Env}}", image).stdout
    assert "MUCLI_CONTAINER_MODE=" in env_vars and "MUCLI_WORKER_TOKEN=secret" not in env_vars
    result = run_suite_in_container(job, job.worktree, Suite("cargo", "fcargo test"), service=service,
                                    environment=state, states=states)
    assert result["passed"], result
    assert "fake-cargo-ok test" in result["stdout"]
    assert labelled(job.id)
    cleanup_job(job.id, service=service, reason="test")
    assert labelled(job.id) == [] and labelled(job.id, "container") == []
    assert service.get(job.id).metadata["build_environments"][0]["status"] == "cleaned"


def test_secret_outside_home_excludes_blocks_the_job(make_env, job_service):
    from mu.jobs.sandbox.environments import EnvironmentBlocked, ensure_environments
    service, create = job_service
    env = make_env("leaky", "mkdir -p /etc/myapp && echo KEY > /etc/myapp/server.key")
    job = create([f"environment:{env}"])
    with pytest.raises(EnvironmentBlocked, match="/etc/myapp/server.key"):
        ensure_environments(service, job)
    state = service.get(job.id).metadata["build_environments"][0]
    assert state["status"] == "failed" and state["secret_paths"]
    assert labelled(job.id) == []  # blocked images are not left behind
    from mu.jobs.job_settings import save_settings
    save_settings({"environments": {"secret_scan_allow": ["/etc/myapp/*.key"]}})
    assert ensure_environments(service, service.get(job.id))[0]["status"] == "ready"


def test_two_bindings_run_suites_in_their_own_images_and_the_broker(make_env, job_service):
    from mu.jobs.checks import ChecksStage
    from mu.jobs.sandbox.broker import EnvironmentBroker
    service, create = job_service
    rust = make_env("rs", "echo rust > /opt/marker")
    node = make_env("js", "echo node > /opt/marker")
    job = create([f"environment:{rust}:svc", f"environment:{node}:web"])
    read = "python3 -c \"print(open('/opt/marker').read())\""
    service.store.update_runtime_fields(job.id, validation_json=[f"{read} svc/main.rs", f"{read} web/index.js"])
    run = ChecksStage(service, settings={}).verify(service.get(job.id))
    by_suite = {s["suite"]: (s["environment"], s["stdout"].split()[0]) for s in run.summary["suites"]}
    assert by_suite["check1"][1] == "rust" and by_suite["check2"][1] == "node", run.summary["suites"]
    states = service.get(job.id).metadata["build_environments"]
    broker = EnvironmentBroker(service, service.get(job.id), states, stage="implement")
    events = list(broker.run(states[1]["name"], "cat /opt/marker && pwd", "web"))
    final = events[-1]
    assert final["ok"] and "node" in final["stdout"] and "/workspace/web" in final["stdout"], final
    assert any(e.event_type == "environment_command" for e in service.store.recent_events(job.id))


def test_kept_job_survives_idle_retention_until_unkept(make_env, job_service, monkeypatch):
    import time
    from mu.jobs.core.states import CoreState, JobState
    from mu.jobs.sandbox import environments as envs
    service, create = job_service
    env = make_env("kept", "true")
    job = create([f"environment:{env}"])
    envs.ensure_environments(service, job)
    envs.save_meta(service, job.id, environments_kept=True)
    # The job waits (failed) far past the 72 h idle retention.
    monkeypatch.setattr(service.store, "core_state", lambda job_id: CoreState(JobState.FAILED))
    future = time.time() + 365 * 24 * 3600
    envs.sweep(service, now=future, settings={})
    assert labelled(job.id)
    envs.save_meta(service, job.id, environments_kept=False)
    envs.sweep(service, now=future, settings={})
    assert labelled(job.id) == []


def test_gpu_binding_suite_sees_the_gpu(make_env, job_service):
    from mu.container.hardware import detect_hardware
    if not detect_hardware().get("gpu", {}).get("supported"):
        pytest.skip("no usable NVIDIA runtime")
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suite_in_container
    from mu.jobs.sandbox.environments import ensure_environments
    service, create = job_service
    env = make_env("gpu", "true", gpus="all")
    plain = make_env("cpu", "true")
    job = create([f"environment:{env}:svc", f"environment:{plain}:web:gpus=none"])
    states = ensure_environments(service, job)
    assert states[0]["gpus"] == "all" and states[1]["gpus"] == ""
    ok = run_suite_in_container(job, job.worktree, Suite("gpu", "nvidia-smi -L"), service=service, environment=states[0], states=states)
    assert ok["passed"] and "GPU 0" in ok["stdout"], ok
    none = run_suite_in_container(job, job.worktree, Suite("gpu", "nvidia-smi -L"), service=service, environment=states[1], states=states)
    assert not none["passed"]


def test_sysbox_engine_compose_and_denied_bind(make_env, job_service):
    from mu.jobs.sandbox.environments import sysbox_available
    if not sysbox_available():
        pytest.skip("Sysbox is not installed")
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suite_in_container
    from mu.jobs.sandbox.environments import ensure_environments
    service, create = job_service
    env = make_env("dind", "command -v docker >/dev/null || (apt-get update && apt-get install -y docker.io)")
    job = create([f"environment:{env}:docker"])
    states = ensure_environments(service, job)
    ok = run_suite_in_container(job, job.worktree, Suite("ps", "docker ps"), service=service, environment=states[0], states=states)
    assert ok["passed"], ok
    denied = run_suite_in_container(job, job.worktree, Suite("bind", "docker run --rm -v /etc:/x alpine true"),
                                    service=service, environment=states[0], states=states)
    assert not denied["passed"] and "outside /workspace" in denied["stderr"]


def test_policy_proxy_with_the_real_docker_cli(docker_ready, tmp_path):
    """The proxy in front of the host daemon: attach/hijack, labelling and denials with the real CLI."""
    from mu.jobs.sandbox.docker_proxy import DockerPolicyProxy, ProxyContext
    (tmp_path / "wt").mkdir()
    events = []
    ctx = ProxyContext(job_id=f"proxytest-{uuid.uuid4().hex[:8]}", worktree=str(tmp_path / "wt"))
    proxy = DockerPolicyProxy(tmp_path / "docker.sock", ("unix", "/var/run/docker.sock"), ctx, on_event=events.append).start()
    env = {**os.environ, "DOCKER_HOST": f"unix://{tmp_path / 'docker.sock'}"}
    run = lambda *a: subprocess.run(["docker", *a], capture_output=True, text=True, timeout=120, env=env)
    try:
        hello = run("run", "--rm", "--entrypoint", "echo", RUNTIME, "hello-through-proxy")
        assert hello.returncode == 0 and "hello-through-proxy" in hello.stdout, hello.stderr
        created = run("create", "--entrypoint", "true", RUNTIME)
        cid = created.stdout.strip()
        try:
            labels = sh("docker", "inspect", "--format", "{{json .Config.Labels}}", cid).stdout
            assert ctx.job_id in labels
        finally:
            sh("docker", "rm", "-f", cid, check=False)
        denied = run("run", "--rm", "-v", "/etc:/x", "--entrypoint", "true", RUNTIME)
        assert denied.returncode != 0 and "outside /workspace" in denied.stderr
        assert any(not e["allowed"] for e in events) and any(e["action"] == "create" and e["allowed"] for e in events)
    finally:
        proxy.close()
        for cid in sh("docker", "ps", "-aq", "--filter", f"label=io.mucli.job={ctx.job_id}").stdout.split():
            sh("docker", "rm", "-f", cid, check=False)
