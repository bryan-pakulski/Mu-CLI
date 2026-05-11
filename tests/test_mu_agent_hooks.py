"""Tests for `mu.agent.hooks` — hook registry contract.

These pin: registration, priority ordering, exception isolation,
short-circuit detection at pre_tool, removal by name.
"""

import pytest

from mu.agent.hooks import (
    HOOK_POINTS,
    HookContext,
    HookRegistry,
    HookResult,
    HookSpec,
)


def test_register_decorator_runs_handler():
    reg = HookRegistry()
    seen = []

    @reg.register("post_tool")
    def handler(ctx: HookContext):
        seen.append(ctx.tool_name)

    reg.fire("post_tool", HookContext(point="post_tool", tool_name="read_file"))
    assert seen == ["read_file"]


def test_handlers_fire_in_priority_order():
    reg = HookRegistry()
    order = []

    @reg.register("pre_tool", priority=50)
    def b(ctx):
        order.append("b")

    @reg.register("pre_tool", priority=10)
    def a(ctx):
        order.append("a")

    @reg.register("pre_tool", priority=100)
    def c(ctx):
        order.append("c")

    reg.fire("pre_tool", HookContext(point="pre_tool"))
    assert order == ["a", "b", "c"]


def test_unknown_hook_point_rejected():
    reg = HookRegistry()
    with pytest.raises(ValueError):
        reg.register("not_a_real_point")(lambda ctx: None)
    with pytest.raises(ValueError):
        reg.fire("not_a_real_point", HookContext(point="not_a_real_point"))


def test_short_circuit_at_pre_tool():
    reg = HookRegistry()

    @reg.register("pre_tool")
    def block(ctx: HookContext):
        if ctx.tool_name == "write_file":
            return HookResult(action="short_circuit", payload={"blocked": True})
        return None

    blocked = reg.first_short_circuit(
        "pre_tool", HookContext(point="pre_tool", tool_name="write_file")
    )
    assert blocked is not None
    assert blocked.payload == {"blocked": True}

    allowed = reg.first_short_circuit(
        "pre_tool", HookContext(point="pre_tool", tool_name="read_file")
    )
    assert allowed is None


def test_exception_in_one_hook_does_not_kill_others():
    reg = HookRegistry()
    seen = []

    @reg.register("post_tool", priority=10)
    def boom(ctx):
        raise RuntimeError("intentional")

    @reg.register("post_tool", priority=20)
    def survivor(ctx):
        seen.append("ran")

    reg.fire("post_tool", HookContext(point="post_tool"))
    assert seen == ["ran"]


def test_remove_by_name():
    reg = HookRegistry()
    reg.add(HookSpec(name="x", point="pre_tool", priority=1, handler=lambda c: None))
    reg.add(HookSpec(name="y", point="pre_tool", priority=2, handler=lambda c: None))
    assert len(reg.list("pre_tool")) == 2
    removed = reg.remove("x")
    assert removed == 1
    assert [s.name for s in reg.list("pre_tool")] == ["y"]


def test_list_all_returns_every_point():
    reg = HookRegistry()
    reg.add(HookSpec(name="a", point="pre_tool", priority=1, handler=lambda c: None))
    reg.add(HookSpec(name="b", point="post_tool", priority=1, handler=lambda c: None))
    names = sorted(s.name for s in reg.list())
    assert names == ["a", "b"]


def test_dict_return_becomes_continue_result():
    reg = HookRegistry()

    @reg.register("post_provider_call")
    def patch(ctx):
        return {"hello": "world"}

    results = reg.fire("post_provider_call", HookContext(point="post_provider_call"))
    assert len(results) == 1
    assert results[0].action == "continue"
    assert results[0].data == {"hello": "world"}


def test_hook_points_are_canonical():
    expected = {
        "pre_provider_call",
        "post_provider_call",
        "pre_tool",
        "post_tool",
        "on_stop",
    }
    assert set(HOOK_POINTS) == expected
