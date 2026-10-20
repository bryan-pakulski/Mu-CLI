"""Node-local policy (plan §6.7): parsing (fail closed) and an exhaustive ``decide``."""

from __future__ import annotations

import itertools
import time
from datetime import datetime

import pytest

from mu.orchestration.node import miniyaml
from mu.orchestration.node.policy import (DEFAULT_POLICY, EFFECTS, NodePolicy, decide, in_window, parse_policy,
                                          parse_window)


def manifest(effect="read_only", category="diagnostics", run_as="root", runtime="python3", check=True,
             tool_id="demo.tool", version=1):
    return {"id": tool_id, "version": version, "effect": effect, "category": category,
            "privilege": {"run_as": run_as}, "implementation": {"runtime": runtime, "check_mode": check}}


def env(check_mode=False, test=False):
    return {"check_mode": check_mode, "test": test}


NOW = time.time()


# --------------------------------------------------------------------- miniyaml

def test_miniyaml_parses_the_documented_policy():
    text = """
# comment
accept_categories: [inventory, diagnostics, maintenance]   # e.g. never "deploy" here
max_effect: mutating          # refuse destructive tools outright
max_privilege: root
allowed_tools: ["*"]
denied_tools: ["db.*"]
require_check_mode_first: true
quiet_hours: "Mon-Fri 09:00-17:00 Australia/Sydney"
"""
    data = miniyaml.loads(text)
    assert data == {"accept_categories": ["inventory", "diagnostics", "maintenance"], "max_effect": "mutating",
                    "max_privilege": "root", "allowed_tools": ["*"], "denied_tools": ["db.*"],
                    "require_check_mode_first": True, "quiet_hours": "Mon-Fri 09:00-17:00 Australia/Sydney"}


def test_miniyaml_block_lists_nested_maps_and_json():
    assert miniyaml.loads("a:\n  - x\n  - 'y z'\nb:\n  c: 1\n  d: [1, 2.5, null]\n") == \
        {"a": ["x", "y z"], "b": {"c": 1, "d": [1, 2.5, None]}}
    assert miniyaml.loads('{"a": [1]}') == {"a": [1]}
    assert miniyaml.loads("- k: v\n  j: 2\n- 3\n") == [{"k": "v", "j": 2}, 3]


@pytest.mark.parametrize("bad", ["a: [1, 2", "a: 'x", "  a: 1\nb: 2", "a: &anchor x", "\ta: 1"])
def test_miniyaml_rejects_what_it_does_not_understand(bad):
    with pytest.raises(ValueError):
        miniyaml.loads(bad)


# --------------------------------------------------------------------- parsing fails closed

@pytest.mark.parametrize("text", [
    "max_effect: everything", "max_privilege: god", "unknown_key: 1", "accept_categories: [a, b",
    "require_check_mode_first: maybe", "quiet_hours: ['whenever']", "allowed_runtimes: [perl]",
    "quiet_hours: ['Mon-Fri 09:00-17:00 Mars/Olympus']",
])
def test_invalid_policies_deny_everything(text):
    policy = parse_policy(text)
    assert policy.deny_all and policy.errors
    assert not decide(env(), manifest(), policy, now=NOW).allowed


def test_missing_policy_file_uses_the_read_only_default(tmp_path):
    from mu.orchestration.node.policy import load_policy_file
    assert load_policy_file(str(tmp_path / "missing.yaml")) == DEFAULT_POLICY
    assert decide(env(), manifest("read_only"), DEFAULT_POLICY, now=NOW).allowed
    assert not decide(env(), manifest("mutating"), DEFAULT_POLICY, now=NOW).allowed


# --------------------------------------------------------------------- exhaustive decide

@pytest.mark.parametrize("max_effect,effect,check_mode,supports_check", list(itertools.product(
    EFFECTS, EFFECTS, (False, True), (False, True))))
def test_effect_ceiling_for_every_combination(max_effect, effect, check_mode, supports_check):
    policy = NodePolicy(max_effect=max_effect)
    d = decide(env(check_mode=check_mode), manifest(effect, check=supports_check), policy, now=NOW)
    rank = EFFECTS.index
    if check_mode and not supports_check and effect != "read_only":
        assert not d.allowed and d.rule == "check_mode"
    else:
        effective = "read_only" if (check_mode and supports_check) else effect
        assert d.allowed == (rank(effective) <= rank(max_effect)), (max_effect, effect, check_mode, supports_check)


@pytest.mark.parametrize("max_privilege,run_as,allowed_run_as,expected", [
    ("root", "root", ("*",), True), ("unprivileged", "root", ("*",), False),
    ("unprivileged", "nobody", ("*",), True), ("root", "nobody", ("www-data",), False),
    ("unprivileged", "www-data", ("www-*",), True),
])
def test_privilege_caps(max_privilege, run_as, allowed_run_as, expected):
    policy = NodePolicy(max_privilege=max_privilege, allowed_run_as=allowed_run_as)
    d = decide(env(), manifest(run_as=run_as), policy, now=NOW)
    assert d.allowed is expected


@pytest.mark.parametrize("allowed,denied,tool,expected,rule", [
    (("*",), (), "db.backup", True, ""), (("*",), ("db.*",), "db.backup", False, "denied_tools"),
    (("sys.*",), (), "db.backup", False, "allowed_tools"), (("sys.*", "db.backup"), (), "db.backup", True, ""),
    (("*",), ("db.backup",), "db.backup-2", True, ""), ((), (), "sys.x", False, "allowed_tools"),
])
def test_tool_allow_and_deny_lists(allowed, denied, tool, expected, rule):
    d = decide(env(), manifest(tool_id=tool), NodePolicy(allowed_tools=allowed, denied_tools=denied), now=NOW)
    assert d.allowed is expected
    if not expected:
        assert d.rule == rule


@pytest.mark.parametrize("accept,category,expected", [
    (("*",), "deploy", True), (("inventory", "diagnostics"), "deploy", False),
    (("inventory", "diagnostics"), "diagnostics", True), (("*",), "", True),
])
def test_categories(accept, category, expected):
    assert decide(env(), manifest(category=category), NodePolicy(accept_categories=accept), now=NOW).allowed is expected


def test_runtimes_and_test_tools():
    assert not decide(env(), manifest(runtime="bash"), NodePolicy(allowed_runtimes=("python3",)), now=NOW).allowed
    assert not decide(env(test=True), manifest(), NodePolicy(canary=False), now=NOW).allowed
    assert decide(env(test=True), manifest(), NodePolicy(canary=True), now=NOW).allowed


def test_require_check_mode_first():
    policy = NodePolicy(max_effect="mutating", require_check_mode_first=True)
    m = manifest("mutating")
    assert not decide(env(), m, policy, now=NOW).allowed
    assert decide(env(check_mode=True), m, policy, now=NOW).allowed
    assert decide(env(), m, policy, now=NOW, check_mode_passed={"demo.tool@1"}).allowed
    assert not decide(env(), manifest("mutating", version=2), policy, now=NOW, check_mode_passed={"demo.tool@1"}).allowed
    # Read-only tools are never gated.
    assert decide(env(), manifest("read_only"), policy, now=NOW).allowed


def test_quiet_hours_block_changes_but_not_reads():
    monday_10 = datetime(2026, 10, 19, 10, 0).timestamp()   # local time, a Monday
    saturday_10 = datetime(2026, 10, 24, 10, 0).timestamp()
    policy = NodePolicy(max_effect="destructive", quiet_hours=("Mon-Fri 09:00-17:00",))
    assert not decide(env(), manifest("mutating"), policy, now=monday_10).allowed
    assert decide(env(), manifest("read_only"), policy, now=monday_10).allowed
    assert decide(env(check_mode=True), manifest("mutating"), policy, now=monday_10).allowed
    assert decide(env(), manifest("mutating"), policy, now=saturday_10).allowed


def test_quiet_hour_windows():
    assert parse_window("Mon-Fri 09:00-17:00 Australia/Sydney")[1:3] == (540, 1020)
    sat_23 = datetime(2026, 10, 24, 23, 30).timestamp()
    sun_01 = datetime(2026, 10, 25, 1, 0).timestamp()
    sun_03 = datetime(2026, 10, 25, 3, 0).timestamp()
    assert in_window("Sat 22:00-02:00", sat_23)
    assert in_window("Sat 22:00-02:00", sun_01)   # wraps midnight: belongs to Saturday's window
    assert not in_window("Sat 22:00-02:00", sun_03)
    assert in_window("Fri-Mon 00:00-24:00", sun_03)
    with pytest.raises(ValueError):
        parse_window("Funday 09:00-10:00")


def test_deny_all_and_unknown_effect():
    assert not decide(env(), manifest(), parse_policy("max_effect: nope"), now=NOW).allowed
    assert not decide(env(), manifest(effect="weird"), NodePolicy(max_effect="destructive"), now=NOW).allowed
