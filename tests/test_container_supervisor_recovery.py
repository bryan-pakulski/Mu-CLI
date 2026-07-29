from __future__ import annotations

from mu.container.docker_cli import CommandResult, ContainerRuntimeError
from mu.container.ref import ContainerRef
from mu.container.registry import ContainerRegistry
from mu.container.supervisor import ContainerSupervisor


class FakeRunner:
    def __init__(self, *, container_exists: bool):
        self.container_exists = container_exists
        self.commands: list[list[str]] = []

    def require(self, executable: str) -> str:
        return executable

    def run(self, args, *, check=True, input_text=None, timeout=None):
        command = [str(item) for item in args]
        self.commands.append(command)
        if command[1:4] == ["inspect", "-f", "{{.State.Running}}"]:
            if self.container_exists:
                return CommandResult(command, 0, "false\n", "")
            return CommandResult(command, 1, "", "No such container")
        if command[1:3] == ["network", "inspect"]:
            return CommandResult(command, 1, "", "No such network")
        return CommandResult(command, 0, "", "")


def make_ref(name: str = "mucli-demo") -> ContainerRef:
    return ContainerRef(
        container_id="stale-id",
        name=name,
        image="mucli/demo:test",
        dockerfile_hash="hash",
        network_name=f"{name}-net",
        proxy_name=f"{name}-proxy",
        egress_network_name=f"{name}-net-egress",
        worker_token="secret",
        status="error",
        attached_sessions=["demo"],
        root_volume=f"{name}-home",
        workspace_volume=f"{name}-workspace",
    )


def test_create_rebuilds_when_registry_ref_has_no_docker_container(
    tmp_path, monkeypatch
):
    registry = ContainerRegistry(str(tmp_path / "containers"))
    stale = make_ref()
    registry.upsert(stale)
    runner = FakeRunner(container_exists=False)
    rebuilt = make_ref()
    rebuilt.container_id = "new-id"
    rebuilt.status = "running"
    stages: list[str] = []

    def fake_build(name, dockerfile, **kwargs):
        assert name == "mucli-demo"
        assert kwargs["session_name"] == "demo"
        kwargs["registry"].upsert(rebuilt)
        return rebuilt

    monkeypatch.setattr("mu.container.supervisor.build_container", fake_build)
    monkeypatch.setattr("mu.container.supervisor.teardown_network", lambda *a, **k: None)

    supervisor = ContainerSupervisor(registry=registry, runner=runner)
    result = supervisor.create(
        container_name="mucli-demo",
        session_name="demo",
        supervisor_url="",
        progress=lambda stage, _message: stages.append(stage),
    )

    assert result.container_id == "new-id"
    assert registry.get("mucli-demo").container_id == "new-id"
    assert "recovering_container" in stages
    assert ["docker", "start", "mucli-demo"] not in runner.commands


def test_create_reuses_real_stopped_container(tmp_path, monkeypatch):
    registry = ContainerRegistry(str(tmp_path / "containers"))
    ref = make_ref()
    ref.status = "stopped"
    registry.upsert(ref)
    runner = FakeRunner(container_exists=True)

    def fail_build(*args, **kwargs):
        raise AssertionError("existing Docker container should be reused")

    monkeypatch.setattr("mu.container.supervisor.build_container", fail_build)
    supervisor = ContainerSupervisor(registry=registry, runner=runner)
    result = supervisor.create(
        container_name="mucli-demo",
        session_name="demo",
        supervisor_url="",
    )

    assert result.status == "running"
    assert ["docker", "start", "mucli-demo"] in runner.commands


def test_ensure_running_does_not_start_missing_container(tmp_path):
    registry = ContainerRegistry(str(tmp_path / "containers"))
    ref = make_ref()
    registry.upsert(ref)
    runner = FakeRunner(container_exists=False)
    supervisor = ContainerSupervisor(registry=registry, runner=runner)

    try:
        supervisor.ensure_running(ref)
    except ContainerRuntimeError as exc:
        assert "missing from Docker" in str(exc)
        assert "reload the session" in str(exc)
    else:
        raise AssertionError("missing container should raise")

    assert ["docker", "start", "mucli-demo"] not in runner.commands
