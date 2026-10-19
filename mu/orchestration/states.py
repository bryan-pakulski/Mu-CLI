"""Pure transition tables (plan §3.2, §3.4), in the style of ``mu.jobs.core.states``.

Every status write in ``commands.py`` and ``scheduler.py`` goes through ``check`` so an illegal
move raises ``TransitionError`` instead of silently corrupting a run.
"""

from __future__ import annotations

from typing import Dict, FrozenSet


class TransitionError(ValueError):
    pass


def _table(spec: Dict[str, tuple]) -> Dict[str, FrozenSet[str]]:
    return {k: frozenset(v) for k, v in spec.items()}


TOOL_STATES = ("draft", "testing", "in_review", "published", "deprecated", "retired")
TOOL_TRANSITIONS = _table({
    "draft": ("testing", "in_review"),
    "testing": ("draft", "in_review"),
    "in_review": ("draft", "published"),
    "published": ("deprecated",),
    "deprecated": ("published", "retired"),
    "retired": (),
})
# Versions the catalog may dispatch to non-canary nodes.
DISPATCHABLE = frozenset({"published"})
EDITABLE = frozenset({"draft", "testing"})

RUNBOOK_STATES = ("draft", "in_review", "published", "deprecated", "retired")
RUNBOOK_TRANSITIONS = _table({
    "draft": ("in_review",),
    "in_review": ("draft", "published"),
    "published": ("deprecated",),
    "deprecated": ("published", "retired"),
    "retired": (),
})

RUN_STATES = ("proposed", "approved", "running", "paused", "rolling_back", "succeeded", "failed",
              "cancelled", "rejected", "rolled_back", "expired")
RUN_TRANSITIONS = _table({
    "proposed": ("approved", "rejected", "cancelled", "expired"),
    "approved": ("running", "cancelled"),
    "running": ("paused", "succeeded", "failed", "cancelled", "rolling_back"),
    "paused": ("running", "cancelled", "rolling_back", "failed"),
    "rolling_back": ("rolled_back", "failed", "paused"),
    "succeeded": ("rolling_back",),
    "failed": ("rolling_back",),
    "cancelled": (),
    "rejected": (),
    "rolled_back": (),
    "expired": (),
})
RUN_ACTIVE = frozenset({"approved", "running", "rolling_back"})
RUN_WAITING = frozenset({"proposed", "paused"})
RUN_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "rejected", "rolled_back", "expired"})

EXEC_STATES = ("queued", "dispatched", "running", "succeeded", "failed", "timed_out", "cancelled",
               "unreachable", "refused", "skipped")
EXEC_TRANSITIONS = _table({
    "queued": ("dispatched", "cancelled", "unreachable", "skipped", "failed"),
    "dispatched": ("running", "succeeded", "failed", "refused", "timed_out", "cancelled", "unreachable", "queued"),
    "running": ("succeeded", "failed", "timed_out", "cancelled", "unreachable", "queued"),
    "succeeded": (), "failed": (), "timed_out": (), "cancelled": (), "unreachable": (), "refused": (),
    "skipped": (),
})
EXEC_LIVE = frozenset({"queued", "dispatched", "running"})
EXEC_IN_FLIGHT = frozenset({"dispatched", "running"})
EXEC_TERMINAL = frozenset(EXEC_STATES) - EXEC_LIVE
EXEC_FAILED = frozenset({"failed", "timed_out", "unreachable", "refused", "cancelled"})


def _check(table: Dict[str, FrozenSet[str]], kind: str, current: str, target: str) -> str:
    if current not in table:
        raise TransitionError(f"unknown {kind} state {current!r}")
    if target not in table:
        raise TransitionError(f"unknown {kind} state {target!r}")
    if target not in table[current]:
        raise TransitionError(f"{kind} cannot move from {current} to {target}")
    return target


def check_tool(current: str, target: str) -> str:
    return _check(TOOL_TRANSITIONS, "tool version", current, target)


def check_runbook(current: str, target: str) -> str:
    return _check(RUNBOOK_TRANSITIONS, "runbook version", current, target)


def check_run(current: str, target: str) -> str:
    return _check(RUN_TRANSITIONS, "run", current, target)


def check_exec(current: str, target: str) -> str:
    return _check(EXEC_TRANSITIONS, "execution", current, target)


def can(table: Dict[str, FrozenSet[str]], current: str, target: str) -> bool:
    return target in table.get(current, frozenset())
