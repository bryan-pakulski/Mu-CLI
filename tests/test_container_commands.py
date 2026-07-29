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
        proxy_name="mucli-demo-proxy",
        proxy_port=3128,
        egress_network_name="mucli-demo-net-egress",
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
    assert "HTTP_PROXY=http://mucli-demo-proxy:3128" in command
    assert "HTTPS_PROXY=http://mucli-demo-proxy:3128" in command


def test_network_policy_uses_internal_bridge_and_unprivileged_proxy():
    runner = CommandRunner(dry_run=True)
    policy = create_isolated_network(
        "mucli-demo-net",
        ["api.openai.com"],
        proxy_image="mucli/demo:test",
        runner=runner,
    )
    flattened = [" ".join(command) for command in runner.commands]
    assert any("network create --driver bridge --internal mucli-demo-net" in command for command in flattened)
    assert any("network create --driver bridge mucli-demo-net-egress" in command for command in flattened)
    proxy_create = next(command for command in flattened if "io.mucli.role=egress-proxy" in command)
    assert "--cap-drop ALL" in proxy_create
    assert "no-new-privileges:true" in proxy_create
    assert "--read-only" in proxy_create
    assert "--user 65534:65534" in proxy_create
    assert not any("iptables" in command or "sudo" in command for command in flattened)
    assert policy.proxy_name == "mucli-demo-proxy"
    assert policy.egress_network_name == "mucli-demo-net-egress"
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
