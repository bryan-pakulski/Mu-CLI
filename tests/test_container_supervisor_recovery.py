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
        if command[1:3] == ["inspect", "-f"] and "NetworkSettings.Networks" in command[3]:
            return CommandResult(command, 0, "172.31.0.2\n", "")
        return CommandResult(command, 0, "", "")


def make_ref(name: str = "mucli-demo") -> ContainerRef:
    return ContainerRef(
        container_id="stale-id",
        name=name,
        image="mucli/demo:test",
        dockerfile_hash="hash",
        network_name=f"{name}-net",
        proxy_name=f"{name}-proxy",
        proxy_ip="172.31.0.2",
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


def test_network_ip_rejects_malformed_docker_inspect_output(tmp_path):
    class InvalidIpRunner(FakeRunner):
        def run(self, args, *, check=True, input_text=None, timeout=None):
            command = [str(item) for item in args]
            self.commands.append(command)
            if command[1:3] == ["inspect", "-f"] and "NetworkSettings.Networks" in command[3]:
                return CommandResult(command, 0, "invalid IP\n", "")
            return super().run(args, check=check, input_text=input_text, timeout=timeout)

    registry = ContainerRegistry(str(tmp_path / "containers"))
    supervisor = ContainerSupervisor(
        registry=registry,
        runner=InvalidIpRunner(container_exists=True),
    )

    assert supervisor._network_ip("mucli-demo-proxy", "mucli-demo-net") == ""


def test_attach_session_rebuilds_invalid_proxy_topology(tmp_path, monkeypatch):
    monkeypatch.setattr("mu.container.supervisor._config.HISTORY_DIR", str(tmp_path))
    session_dir = tmp_path / "sessions" / "new-session"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text("{}", encoding="utf-8")

    class InvalidProxyRunner(FakeRunner):
        def run(self, args, *, check=True, input_text=None, timeout=None):
            command = [str(item) for item in args]
            if command[1:3] == ["inspect", "-f"] and "NetworkSettings.Networks" in command[3]:
                self.commands.append(command)
                return CommandResult(command, 0, "invalid IP\n", "")
            return super().run(args, check=check, input_text=input_text, timeout=timeout)

    registry = ContainerRegistry(str(tmp_path / "containers"))
    stale = make_ref("mucli-existing")
    stale.status = "running"
    stale.attached_sessions = []
    registry.upsert(stale)
    runner = InvalidProxyRunner(container_exists=True)
    supervisor = ContainerSupervisor(registry=registry, runner=runner)
    rebuilt = make_ref("mucli-existing")
    rebuilt.status = "running"
    rebuilt.attached_sessions = []
    stages: list[str] = []
    output: list[tuple[str, str]] = []
    recorded: dict = {}

    monkeypatch.setattr(
        supervisor,
        "configuration",
        lambda _name: {
            "dockerfile": "FROM ubuntu:24.04",
            "template_name": None,
            "mounts": [],
            "egress_allow": ["api.openai.com"],
            "egress_deny": [],
        },
    )

    def fake_reconfigure(name, **kwargs):
        recorded["name"] = name
        recorded.update(kwargs)
        registry.upsert(rebuilt)
        return rebuilt

    def fake_attach(ref, session_name, **kwargs):
        ref.attached_sessions.append(session_name)
        return registry.upsert(ref)

    monkeypatch.setattr(supervisor, "reconfigure_environment", fake_reconfigure)
    monkeypatch.setattr("mu.container.supervisor.attach_session_folder", fake_attach)

    result = supervisor.attach_session(
        "mucli-existing",
        "new-session",
        supervisor_url="http://host.docker.internal:30311",
        progress=lambda stage, _message: stages.append(stage),
        output=lambda stream, text: output.append((stream, text)),
    )

    assert result.name == "mucli-existing"
    assert "new-session" in result.attached_sessions
    assert recorded["name"] == "mucli-existing"
    assert recorded["supervisor_url"] == "http://host.docker.internal:30311"
    assert "recovering_container" in stages
    assert any("rebuilding the worker topology" in text for _stream, text in output)


def test_worker_create_command_pins_host_docker_internal_to_internal_gateway():
    from mu.container.builder import build_create_command

    ref = make_ref()
    ref.host_gateway = "172.31.0.1"
    command = build_create_command(ref)
    assert command[command.index("--add-host") + 1] == "host.docker.internal:172.31.0.1"
    assert "host.docker.internal:host-gateway" not in command

    legacy = make_ref()  # record predating host_gateway → docker keyword fallback
    assert legacy.host_gateway == ""
    legacy_command = build_create_command(legacy)
    assert legacy_command[legacy_command.index("--add-host") + 1] == (
        "host.docker.internal:host-gateway"
    )


def test_host_gateway_round_trips_through_registry_and_tolerates_missing_key(tmp_path):
    registry = ContainerRegistry(str(tmp_path / "containers"))
    ref = make_ref()
    ref.host_gateway = "172.31.0.1"
    registry.upsert(ref)
    loaded = registry.get(ref.name)
    assert loaded is not None and loaded.host_gateway == "172.31.0.1"

    legacy = {k: v for k, v in ref.to_dict().items() if k != "host_gateway"}
    assert ContainerRef.from_dict(legacy).host_gateway == ""


def test_ensure_running_backfills_host_gateway_from_network_inspect(tmp_path, monkeypatch):
    class GatewayRunner(FakeRunner):
        def run(self, args, *, check=True, input_text=None, timeout=None):
            command = [str(item) for item in args]
            if command[1:3] == ["network", "inspect"]:
                self.commands.append(command)
                payload = '{"IPAM": {"Config": [{"Subnet": "172.31.0.0/24", "Gateway": "172.31.0.1"}]}}'
                return CommandResult(command, 0, payload, "")
            return super().run(args, check=check, input_text=input_text, timeout=timeout)

    registry = ContainerRegistry(str(tmp_path / "containers"))
    ref = make_ref()
    ref.container_id = "live"
    registry.upsert(ref)
    runner = GatewayRunner(container_exists=True)
    supervisor = ContainerSupervisor(registry=registry, runner=runner)
    monkeypatch.setattr(supervisor, "wait_worker_ready", lambda ref: None)
    monkeypatch.setattr(supervisor, "_ensure_proxy_running", lambda ref: None)

    updated = supervisor.ensure_running(ref)
    assert updated.host_gateway == "172.31.0.1"
    assert registry.get(ref.name).host_gateway == "172.31.0.1"
    assert any(cmd[1:4] == ["network", "inspect", ref.network_name] for cmd in runner.commands)
