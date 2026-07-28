from mu.container.ref import ContainerRef
from mu.container.registry import ContainerRegistry


def make_ref(name="mucli-demo"):
    return ContainerRef(
        container_id="abc",
        name=name,
        image="mucli/demo:test",
        dockerfile_hash="hash",
        network_name=f"{name}-net",
        session_volume="/tmp/.mucli",
        worker_token="secret",
    )


def test_many_to_one_attachment_persists(tmp_path):
    registry = ContainerRegistry(str(tmp_path))
    registry.upsert(make_ref())
    registry.attach_session("mucli-demo", "one")
    registry.attach_session("mucli-demo", "two")
    loaded = ContainerRegistry(str(tmp_path)).get("mucli-demo")
    assert loaded is not None
    assert loaded.attached_sessions == ["one", "two"]
    registry.detach_session("mucli-demo", "one")
    assert registry.get("mucli-demo").attached_sessions == ["two"]


def test_remove_refuses_attached_sessions(tmp_path):
    registry = ContainerRegistry(str(tmp_path))
    ref = make_ref()
    ref.attached_sessions = ["one"]
    registry.upsert(ref)
    try:
        registry.remove(ref.name)
    except RuntimeError as exc:
        assert "attached" in str(exc)
    else:
        raise AssertionError("expected attached-session refusal")
