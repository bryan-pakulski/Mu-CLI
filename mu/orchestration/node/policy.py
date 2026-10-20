"""Node-local policy: the node owner's final veto (plan §6.7).

``/etc/mucli-node/policy.yaml`` is owned by root and edited on the node, never from the master.
``decide`` is pure (modelled on ``docker_proxy.decide``): it sees only the verified envelope,
its manifest, the policy and a few node-local facts, and returns ``Decision(allowed, reason)``.
A fully compromised master can therefore only run what each node's owner allowed.

A policy file that fails to parse or validate yields ``DENY_ALL``: a broken policy must never
read as permissive.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from . import miniyaml

EFFECTS = ("read_only", "mutating", "destructive")
EFFECT_RANK = {name: i for i, name in enumerate(EFFECTS)}
PRIVILEGES = ("unprivileged", "root")
RUNTIMES = ("bash", "python3", "builtin")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    rule: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NodePolicy:
    accept_categories: Tuple[str, ...] = ("*",)
    max_effect: str = "read_only"
    max_privilege: str = "root"
    allowed_run_as: Tuple[str, ...] = ("*",)
    allowed_tools: Tuple[str, ...] = ("*",)
    denied_tools: Tuple[str, ...] = ()
    allowed_runtimes: Tuple[str, ...] = RUNTIMES
    require_check_mode_first: bool = False
    quiet_hours: Tuple[str, ...] = ()
    canary: bool = False
    allow_agent_updates: bool = False   # the master may replace this agent (signed, digest-checked)
    # The master may replace this policy file (signed orders, operator only). None (unset): follows
    # allow_agent_updates — a node that accepts master-signed agents already trusts the master that far.
    allow_remote_policy: Optional[bool] = None
    deny_all: bool = False
    errors: Tuple[str, ...] = ()

    def summary(self) -> Dict[str, Any]:
        out = asdict(self)
        for key, value in list(out.items()):
            if isinstance(value, tuple):
                out[key] = list(value)
        return out


DEFAULT_POLICY = NodePolicy()
DENY_ALL = NodePolicy(deny_all=True, max_effect="read_only", allowed_tools=())

_LIST_FIELDS = ("accept_categories", "allowed_run_as", "allowed_tools", "denied_tools",
                "allowed_runtimes", "quiet_hours")
_KNOWN = set(_LIST_FIELDS) | {"max_effect", "max_privilege", "require_check_mode_first", "canary",
                              "allow_agent_updates", "allow_remote_policy"}


def _as_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise ValueError("expected a list of strings")


def parse_policy(text: str) -> NodePolicy:
    """Parse and validate a policy document; any problem yields ``DENY_ALL`` with ``errors``."""
    try:
        data = miniyaml.loads(text or "")
    except ValueError as exc:
        return _deny(f"policy does not parse: {exc}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _deny("policy must be a mapping")
    errors: List[str] = []
    for key in data:
        if key not in _KNOWN:
            errors.append(f"unknown policy key {key!r}")
    values: Dict[str, Any] = {}
    for key in _LIST_FIELDS:
        if key in data:
            try:
                values[key] = _as_tuple(data[key])
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
    if "max_effect" in data:
        if data["max_effect"] not in EFFECTS:
            errors.append(f"max_effect must be one of {', '.join(EFFECTS)}")
        values["max_effect"] = str(data["max_effect"])
    if "max_privilege" in data:
        if data["max_privilege"] not in PRIVILEGES:
            errors.append(f"max_privilege must be one of {', '.join(PRIVILEGES)}")
        values["max_privilege"] = str(data["max_privilege"])
    for key in ("require_check_mode_first", "canary", "allow_agent_updates", "allow_remote_policy"):
        if key in data:
            if not isinstance(data[key], bool):
                errors.append(f"{key} must be true or false")
            values[key] = bool(data[key])
    for runtime in values.get("allowed_runtimes", ()):
        if runtime not in RUNTIMES:
            errors.append(f"unknown runtime {runtime!r}")
    for window in values.get("quiet_hours", ()):
        try:
            parse_window(window)
        except ValueError as exc:
            errors.append(f"quiet_hours: {exc}")
    if errors:
        return _deny("; ".join(errors))
    return NodePolicy(**values)


def _deny(message: str) -> NodePolicy:
    return NodePolicy(deny_all=True, max_effect="read_only", allowed_tools=(), errors=(message,))


def load_policy_file(path: str) -> NodePolicy:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_policy(fh.read())
    except FileNotFoundError:
        return DEFAULT_POLICY
    except OSError as exc:
        return _deny(f"cannot read policy: {exc}")


# --------------------------------------------------------------------- quiet hours

_WINDOW = re.compile(r"^\s*(?P<days>[A-Za-z,\-]+)\s+(?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2})"
                     r"(?:\s+(?P<tz>[A-Za-z_][A-Za-z0-9_+\-/]*))?\s*$")


def _parse_days(spec: str) -> frozenset:
    days = set()
    for part in spec.lower().split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a[:3] not in _DAYS or b[:3] not in _DAYS:
                raise ValueError(f"bad day range {part!r}")
            i, j = _DAYS.index(a[:3]), _DAYS.index(b[:3])
            k = i
            while True:
                days.add(k)
                if k == j:
                    break
                k = (k + 1) % 7
        elif part[:3] in _DAYS:
            days.add(_DAYS.index(part[:3]))
        elif part in ("daily", "every"):
            days.update(range(7))
        else:
            raise ValueError(f"bad day {part!r}")
    return frozenset(days)


def _minutes(hhmm: str) -> int:
    h, _, m = hhmm.partition(":")
    h_i, m_i = int(h), int(m)
    if not (0 <= h_i <= 24 and 0 <= m_i < 60) or (h_i == 24 and m_i):
        raise ValueError(f"bad time {hhmm!r}")
    return h_i * 60 + m_i


def parse_window(text: str):
    match = _WINDOW.match(str(text or ""))
    if not match:
        raise ValueError(f"expected 'Mon-Fri 09:00-17:00 [Area/City]', got {text!r}")
    tz = None
    if match.group("tz"):
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(match.group("tz"))
        except Exception as exc:  # noqa: BLE001 - unknown zone
            raise ValueError(f"unknown time zone {match.group('tz')!r}") from exc
    return _parse_days(match.group("days")), _minutes(match.group("start")), _minutes(match.group("end")), tz


def in_window(text: str, now: float) -> bool:
    days, start, end, tz = parse_window(text)
    moment = datetime.fromtimestamp(now, tz or timezone.utc) if tz else datetime.fromtimestamp(now).astimezone()
    minute = moment.hour * 60 + moment.minute
    weekday = moment.weekday()
    if start <= end:
        return weekday in days and start <= minute < end
    # Wraps midnight: the part after midnight belongs to the previous day's window.
    if minute >= start:
        return weekday in days
    return minute < end and (weekday - 1) % 7 in days


# --------------------------------------------------------------------- decide

def _matches(name: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        if fnmatch.fnmatchcase(name, pattern):
            return pattern
    return None


def decide(envelope: Mapping[str, Any], manifest: Mapping[str, Any], policy: NodePolicy, *,
           now: float, check_mode_passed: Iterable[str] = ()) -> Decision:
    """Whether this node will run the (already authenticated) envelope.

    ``check_mode_passed`` holds ``"<tool>@<version>"`` keys that have succeeded in check mode
    on this node (for ``require_check_mode_first``).
    """
    tool_id = str(manifest.get("id") or "")
    version = manifest.get("version")
    effect = str(manifest.get("effect") or "destructive")
    category = str(manifest.get("category") or "")
    run_as = str(((manifest.get("privilege") or {}).get("run_as")) or "root")
    runtime = str(((manifest.get("implementation") or {}).get("runtime")) or "")
    check_mode = bool(envelope.get("check_mode"))

    if policy.deny_all:
        return Decision(False, "node policy is invalid or denies everything: " + "; ".join(policy.errors),
                        "deny_all")
    if effect not in EFFECT_RANK:
        return Decision(False, f"unknown effect {effect!r}", "max_effect")
    denied = _matches(tool_id, policy.denied_tools)
    if denied:
        return Decision(False, f"tool {tool_id} is denied on this node ({denied})", "denied_tools")
    if not _matches(tool_id, policy.allowed_tools):
        return Decision(False, f"tool {tool_id} is not in this node's allowed_tools", "allowed_tools")
    if not _matches(category, policy.accept_categories):
        return Decision(False, f"category {category or '(none)'} is not accepted on this node",
                        "accept_categories")
    if runtime not in policy.allowed_runtimes:
        return Decision(False, f"runtime {runtime or '(none)'} is not allowed on this node", "allowed_runtimes")
    # A check-mode run of a tool that supports it changes nothing, so it is judged as read-only.
    supports_check = bool((manifest.get("implementation") or {}).get("check_mode"))
    effective = "read_only" if (check_mode and supports_check) else effect
    if check_mode and not supports_check and effect != "read_only":
        return Decision(False, f"{tool_id} does not support check mode", "check_mode")
    if EFFECT_RANK[effective] > EFFECT_RANK[policy.max_effect]:
        return Decision(False, f"{effect} tools exceed this node's max_effect ({policy.max_effect})",
                        "max_effect")
    if run_as == "root":
        if policy.max_privilege != "root":
            return Decision(False, "root tools are not allowed on this node", "max_privilege")
    elif not _matches(run_as, policy.allowed_run_as):
        return Decision(False, f"run_as {run_as} is not allowed on this node", "allowed_run_as")
    if envelope.get("test") and not policy.canary:
        return Decision(False, "test tools run only on canary nodes", "canary")
    if effective != "read_only":
        if policy.require_check_mode_first and f"{tool_id}@{version}" not in set(check_mode_passed):
            return Decision(False, f"{tool_id}@{version} must succeed in check mode on this node first",
                            "require_check_mode_first")
        for window in policy.quiet_hours:
            if in_window(window, now):
                return Decision(False, f"quiet hours ({window}): changes are not allowed now", "quiet_hours")
    return Decision(True, "allowed by node policy")


def remote_policy_allowed(policy: NodePolicy) -> bool:
    if policy.deny_all:
        return False
    if policy.allow_remote_policy is not None:
        return bool(policy.allow_remote_policy)
    return bool(policy.allow_agent_updates)


def decide_policy_update(policy: NodePolicy) -> Decision:
    """Whether the master may replace this node's policy file (the operator edits it on the master).
    Decided by the policy in force now; an invalid local policy is only fixed on the node."""
    if policy.deny_all:
        return Decision(False, "this node's policy is invalid; fix /etc/mucli-node/policy.yaml on the node",
                        "deny_all")
    if not remote_policy_allowed(policy):
        return Decision(False, "this node keeps its policy local (allow_remote_policy: false, or agent updates "
                               "off); set allow_remote_policy: true in /etc/mucli-node/policy.yaml once",
                        "allow_remote_policy")
    return Decision(True)


def decide_update(policy: NodePolicy, *, now: float) -> Decision:
    """Whether the master may replace this node's agent now. Off unless the owner opted in:
    an agent update runs as root and outlives every per-tool check."""
    if policy.deny_all:
        return Decision(False, "node policy is invalid or denies everything: " + "; ".join(policy.errors), "deny_all")
    if not policy.allow_agent_updates:
        return Decision(False, "agent updates are off on this node (set allow_agent_updates: true in "
                               "/etc/mucli-node/policy.yaml)", "allow_agent_updates")
    for window in policy.quiet_hours:
        if in_window(window, now):
            return Decision(False, f"quiet hours ({window})", "quiet_hours")
    return Decision(True)
