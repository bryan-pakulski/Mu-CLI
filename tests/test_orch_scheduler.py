"""Rollout engine (plan §5): pure ``tick`` / ``health`` / batching, no database."""

from __future__ import annotations

import itertools

import pytest

from mu.orchestration import scheduler
from mu.orchestration.models import Execution, Run
from mu.orchestration.states import (EXEC_TRANSITIONS, RUN_TRANSITIONS, TOOL_TRANSITIONS, TransitionError,
                                     check_exec, check_run, check_tool)

_ids = itertools.count(1)


def step(name="main", on_failure="stop", rollback=""):
    return {"name": name, "tool_id": "t.t", "version": 1, "on_failure": on_failure, "rollback": rollback}


def run(nodes, *, steps=None, state="running", batch=0, strategy=None, summary=None, batches=None,
        batch_finished_at=None):
    strategy = dict({"canary": 1, "batch": 2, "max_parallel": 10, "failure_threshold": 10, "pause_between_s": 0},
                    **(strategy or {}))
    batches = batches if batches is not None else scheduler.make_batches(nodes, strategy)
    return Run(id="r_1", nodes=list(nodes), state=state, batch=batch, strategy=strategy, summary=summary or {},
               plan={"steps": steps or [step()], "batches": batches}, batch_finished_at=batch_finished_at)


def ex(node, state, step_=0, *, rollback_of="", check_mode=False, idempotent=False, attempts=1, created=None,
       started=None, dispatched=None):
    n = next(_ids)
    return Execution(id=f"x_{n}", run_id="r_1", node_id=node, step=step_, state=state, rollback_of=rollback_of,
                     check_mode=check_mode, idempotent=idempotent, attempts=attempts,
                     created_at=created if created is not None else float(n), started_at=started,
                     dispatched_at=dispatched, tool_id="t.t", version=1)


def kinds(actions):
    return [(a.kind, a.node_id) if a.node_id else (a.kind, a.state or a.batch) for a in actions]


# --------------------------------------------------------------------- state tables

def test_transition_tables_are_closed_and_checked():
    for table in (TOOL_TRANSITIONS, RUN_TRANSITIONS, EXEC_TRANSITIONS):
        for src, targets in table.items():
            assert targets <= set(table), src
    assert check_tool("in_review", "published") == "published"
    with pytest.raises(TransitionError):
        check_tool("draft", "published")
    with pytest.raises(TransitionError):
        check_run("succeeded", "running")
    with pytest.raises(TransitionError):
        check_exec("succeeded", "failed")
    with pytest.raises(TransitionError):
        check_exec("bogus", "failed")


# --------------------------------------------------------------------- batching

@pytest.mark.parametrize("n,canary,batch,expected", [
    (1, 1, 10, [1]), (5, 1, 2, [1, 2, 2]), (5, 0, 2, [2, 2, 1]), (10, 2, 3, [2, 3, 3, 2]), (3, 5, 10, [3]),
])
def test_make_batches(n, canary, batch, expected):
    nodes = [f"n{i}" for i in range(n)]
    batches = scheduler.make_batches(nodes, {"canary": canary, "batch": batch})
    assert [len(b) for b in batches] == expected
    assert sum(batches, []) == nodes


def test_canary_labelled_nodes_go_first():
    batches = scheduler.make_batches(["a", "b", "c"], {"canary": 1, "batch": 5}, canary_ids=["c"])
    assert batches == [["c"], ["a", "b"]]


def test_normalize_strategy_clamps():
    s = scheduler.normalize_strategy({"batch": 0, "failure_threshold": 400, "max_parallel": "x"}, {"default_batch": 7})
    assert s["batch"] == 1 and s["failure_threshold"] == 100 and s["max_parallel"] == 1
    assert scheduler.normalize_strategy({}, {"default_batch": 7, "canary": 2})["batch"] == 7


# --------------------------------------------------------------------- tick

def test_first_tick_queues_the_canary_only():
    r = run(["a", "b", "c", "d", "e"])
    assert kinds(scheduler.tick(r, [], 0)) == [("queue", "a")]


def test_canary_failure_pauses():
    r = run(["a", "b", "c"])
    actions = scheduler.tick(r, [ex("a", "failed")], 0)
    assert [a.kind for a in actions] == ["pause"] and "canary" in actions[0].reason


def test_canary_success_advances_then_queues_next_batch():
    r = run(["a", "b", "c"])
    assert [a.kind for a in scheduler.tick(r, [ex("a", "succeeded")], 0)] == ["evaluated", "advance"]
    r2 = run(["a", "b", "c"], batch=1, summary={"evaluated_batch": 0})
    assert kinds(scheduler.tick(r2, [ex("a", "succeeded")], 0)) == [("queue", "b"), ("queue", "c")]


def test_failure_threshold_pauses_between_batches():
    nodes = [f"n{i}" for i in range(7)]
    r = run(nodes, strategy={"canary": 0, "batch": 4, "failure_threshold": 25}, batch=0)
    execs = [ex("n0", "failed"), ex("n1", "failed"), ex("n2", "succeeded"), ex("n3", "succeeded")]
    actions = scheduler.tick(r, execs, 0)
    assert actions[0].kind == "pause" and "50%" in actions[0].reason
    # One failure of four (25%) is within a 25% threshold.
    execs = [ex("n0", "failed"), ex("n1", "succeeded"), ex("n2", "succeeded"), ex("n3", "succeeded")]
    assert [a.kind for a in scheduler.tick(r, execs, 0)] == ["evaluated", "advance"]


def test_resumed_batch_is_not_re_evaluated():
    nodes = ["a", "b", "c"]
    r = run(nodes, summary={"evaluated_batch": 0})
    assert [a.kind for a in scheduler.tick(r, [ex("a", "failed")], 0)] == ["advance"]


def test_last_batch_failures_finish_failed_not_paused():
    r = run(["a", "b"], strategy={"canary": 0, "batch": 2})
    actions = scheduler.tick(r, [ex("a", "failed"), ex("b", "succeeded")], 0)
    assert actions[-1].kind == "finish" and actions[-1].state == "failed"


def test_all_succeed_finishes_succeeded():
    r = run(["a"], strategy={"canary": 0})
    assert scheduler.tick(r, [ex("a", "succeeded")], 0)[-1].state == "succeeded"


def test_max_parallel_limits_nodes_in_flight():
    nodes = [f"n{i}" for i in range(6)]
    r = run(nodes, strategy={"canary": 0, "batch": 6, "max_parallel": 2})
    assert len([a for a in scheduler.tick(r, [], 0) if a.kind == "queue"]) == 2
    execs = [ex("n0", "running"), ex("n1", "succeeded")]
    queued = [a.node_id for a in scheduler.tick(r, execs, 0) if a.kind == "queue"]
    assert queued == ["n2"]


def test_pause_between_batches():
    r = run(["a", "b"], strategy={"canary": 1, "batch": 1, "pause_between_s": 60})
    execs = [ex("a", "succeeded")]
    assert [a.kind for a in scheduler.tick(r, execs, 100)] == ["evaluated", "batch_finished"]
    r = run(["a", "b"], strategy={"canary": 1, "batch": 1, "pause_between_s": 60}, summary={"evaluated_batch": 0},
            batch_finished_at=100)
    assert scheduler.tick(r, execs, 130) == []
    assert [a.kind for a in scheduler.tick(r, execs, 161)] == ["advance"]


def test_dispatch_paused_holds_new_work():
    assert scheduler.tick(run(["a", "b"]), [], 0, dispatch_paused=True) == []


def test_ineligible_nodes_are_cancelled_not_queued():
    r = run(["a", "b"], strategy={"canary": 0})
    actions = scheduler.tick(r, [], 0, eligible={"b": "node is quarantined"})
    assert ("queue", "a") in kinds(actions) and ("skip", "b") in kinds(actions)
    assert next(a for a in actions if a.kind == "skip").state == "cancelled"


def test_paused_and_terminal_runs_do_nothing():
    for state in ("paused", "proposed", "succeeded", "cancelled"):
        assert scheduler.tick(run(["a"], state=state), [], 0) == []


# --------------------------------------------------------------------- multi-step pipelines

def test_steps_run_in_order_per_node():
    steps = [step("one"), step("two")]
    r = run(["a"], steps=steps, strategy={"canary": 0})
    assert [(a.kind, a.step) for a in scheduler.tick(r, [ex("a", "succeeded", 0)], 0)] == [("queue", 1)]
    assert scheduler.tick(r, [ex("a", "succeeded", 0), ex("a", "running", 1)], 0) == []


@pytest.mark.parametrize("on_failure,expected", [("stop", "finish"), ("continue", "queue")])
def test_on_failure_stop_and_continue(on_failure, expected):
    steps = [step("one", on_failure=on_failure), step("two")]
    r = run(["a"], steps=steps, strategy={"canary": 0})
    actions = scheduler.tick(r, [ex("a", "failed", 0)], 0)
    assert actions[-1].kind == expected
    if expected == "finish":
        assert actions[-1].state == "failed"


def test_on_failure_rollback_queues_compensations_newest_first():
    steps = [step("one", rollback="t.undo@1"), step("two", rollback="t.undo@1"), step("three", on_failure="rollback")]
    r = run(["a"], steps=steps, strategy={"canary": 0})
    e1, e2, e3 = ex("a", "succeeded", 0), ex("a", "succeeded", 1), ex("a", "failed", 2)
    actions = scheduler.tick(r, [e1, e2, e3], 0)
    assert [(a.kind, a.exec_id) for a in actions] == [("rollback", e2.id), ("rollback", e1.id)]
    # Once rollbacks exist the node is waiting for them, then failed.
    rb2, rb1 = ex("a", "running", 1, rollback_of=e2.id), ex("a", "queued", 0, rollback_of=e1.id)
    assert scheduler.tick(r, [e1, e2, e3, rb2, rb1], 0) == []
    rb2.state, rb1.state = "succeeded", "succeeded"
    assert scheduler.tick(r, [e1, e2, e3, rb2, rb1], 0)[-1].state == "failed"


def test_check_mode_executions_are_never_rolled_back():
    steps = [step("one", rollback="t.undo@1")]
    targets = scheduler.rollback_targets(steps, [ex("a", "succeeded", 0, check_mode=True)])
    assert targets == []


def test_rolling_back_finishes_when_compensations_end():
    r = run(["a"], state="rolling_back")
    assert scheduler.tick(r, [ex("a", "running", rollback_of="x_1")], 0) == []
    assert scheduler.tick(r, [ex("a", "succeeded", rollback_of="x_1")], 0)[0].state == "rolled_back"
    assert scheduler.tick(r, [ex("a", "failed", rollback_of="x_1")], 0)[0].state == "failed"


# --------------------------------------------------------------------- health

NODES_ONLINE = {"a": {"last_seen": 1000.0}}
NODES_OFFLINE = {"a": {"last_seen": 0.0}}


def test_timeouts_and_grace():
    e = ex("a", "running", started=0.0)
    assert scheduler.health([e], NODES_ONLINE, 300.0 + 29, timeouts={("t.t", 1): 300}) == []
    actions = scheduler.health([e], NODES_ONLINE, 300.0 + 31, timeouts={("t.t", 1): 300})
    assert [a.kind for a in actions] == ["timeout"]


@pytest.mark.parametrize("idempotent,attempts,expected", [
    (True, 1, "requeue"), (True, 3, "unreachable"), (False, 1, "unreachable"),
])
def test_offline_nodes_requeue_only_idempotent_tools(idempotent, attempts, expected):
    e = ex("a", "running", started=990.0, idempotent=idempotent, attempts=attempts)
    actions = scheduler.health([e], NODES_OFFLINE, 1000.0, timeouts={("t.t", 1): 300})
    assert [a.kind for a in actions] == [expected]
    if not idempotent:
        assert "decide" in actions[0].reason


def test_queued_on_an_offline_node_becomes_unreachable_after_the_queue_timeout():
    e = ex("a", "queued", created=0.0)
    assert scheduler.health([e], NODES_OFFLINE, 200.0, timeouts={}) == []
    assert [a.kind for a in scheduler.health([e], NODES_OFFLINE, 400.0, timeouts={})] == ["unreachable"]
    assert scheduler.health([e], {"a": {"last_seen": 399.0}}, 400.0, timeouts={}) == []


def test_summarize_counts():
    r = run(["a", "b", "c"], strategy={"canary": 0, "batch": 3})
    s = scheduler.summarize(r, [ex("a", "succeeded"), ex("b", "failed"), ex("c", "running")])
    assert s["nodes"] == {"done": 1, "failed": 1, "active": 1, "pending": 0}
    assert s["failed_states"] == ["failed"]
