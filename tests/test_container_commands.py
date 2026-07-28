from mu.container.builder import build_create_command
from mu.container.docker_cli import CommandRunner
from mu.container.network import create_isolated_network
from mu.container.ref import ContainerRef, MountSpec


def test_create_command_has_no_privileged_or_docker_socket():
    ref = ContainerRef(
        container_id="",
        name="mucli-demo",
        image="mucli/demo:test",
        dockerfile_hash="hash",
        mounts=[MountSpec("/host/project", "/workspace/project", "ro")],
        network_name="mucli-demo-net",
        session_volume="/home/user/.mucli/sessions/demo",
        worker_token="secret",
        supervisor_url="http://host.docker.internal:30311",
        attached_sessions=["demo"],
        root_volume="mucli-demo-home",
        workspace_volume="mucli-demo-workspace",
    )
    command = build_create_command(ref, environment={"OPENAI_API_KEY": "key"})
    joined = " ".join(command)
    assert "--privileged" not in command
    assert "/var/run/docker.sock" not in joined
    assert "NET_ADMIN" in command
    assert "mucli-demo-home:/root/.mucli:rw" in command
    assert "/root/.mucli/sessions/demo:rw" in joined
    assert "/host/project:/workspace/project:ro" in command


def test_network_policy_is_default_deny_in_dry_run():
    runner = CommandRunner(dry_run=True)
    policy = create_isolated_network(
        "mucli-demo-net", ["api.openai.com"], runner=runner
    )
    flattened = [" ".join(command) for command in runner.commands]
    assert any("DOCKER-USER" in command for command in flattened)
    assert any(" INPUT " in f" {command} " for command in flattened)
    assert sum(command.endswith("-j DROP") for command in flattened) >= 2
    assert not any("--dport 53" in command for command in flattened)
    assert policy.subnet == "172.31.0.0/24"


def test_detach_recreates_shared_worker_without_removed_session(tmp_path, monkeypatch):
    from mu.container import builder
    from mu.container.registry import ContainerRegistry
    from mu.container.runner import detach_session_folder

    monkeypatch.setattr(builder, "HISTORY_DIR", str(tmp_path / "mucli"))
    ref = ContainerRef(
        container_id="abc",
        name="mucli-shared",
        image="mucli/shared:test",
        dockerfile_hash="hash",
        network_name="mucli-shared-net",
        worker_token="secret",
        supervisor_url="http://host.docker.internal:30311",
        attached_sessions=["one", "two"],
        root_volume="mucli-shared-home",
        workspace_volume="mucli-shared-workspace",
    )
    registry = ContainerRegistry(str(tmp_path / "containers"))
    registry.upsert(ref)
    runner = CommandRunner(dry_run=True)
    updated = detach_session_folder(
        ref,
        "one",
        registry=registry,
        runner=runner,
    )
    assert updated.attached_sessions == ["two"]
    create = next(command for command in runner.commands if "create" in command)
    joined = " ".join(create)
    assert "/sessions/two:rw" in joined
    assert "/sessions/one:rw" not in joined
