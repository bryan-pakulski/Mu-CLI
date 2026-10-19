"""Schedules and triggers (plan §3.5): cron/interval runs and fact-driven triggers.

* ``cron``: five fields (minute hour day-of-month month day-of-week), local time, supporting
  ``*``, ``*/n``, ``a-b``, ``a-b/n`` and lists.
* ``interval``: ``{"every_s": N}``.
* ``trigger``: evaluated when a node reports new facts, per node, firing on the false→true edge
  (with a cooldown): ``{"metric": "max_disk_used_pct", "op": ">", "value": 90}`` or drift
  detection ``{"metric": "kernel", "op": "changed"}``. Metrics are derived from facts
  (``metric_value``) or are dotted fact paths (``memory.used_pct``).

A schedule's target is ``{kind, target, params, selector, strategy, check_mode}``. Runs it creates
are proposed by ``schedule:<id>`` and, like an operator's, start only when the access policy
allows them outright; otherwise they wait in the approvals inbox.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, List, Mapping, Optional, Set

KINDS = ("cron", "interval", "trigger")
OPS = (">", ">=", "<", "<=", "==", "!=", "changed")
DEFAULT_COOLDOWN_S = 3600.0


class ScheduleError(ValueError):
    pass


_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _field(text: str, lo: int, hi: int) -> Set[int]:
    out: Set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ScheduleError(f"bad step in {text!r}")
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif re.fullmatch(r"\d+-\d+", part):
            start, end = (int(x) for x in part.split("-"))
        elif part.isdigit():
            start = end = int(part)
            if step != 1:
                end = hi
        else:
            raise ScheduleError(f"bad cron field {text!r}")
        if hi == 6 and end == 7:
            end = 6
            out.add(0)
        if start < lo or end > hi or start > end:
            raise ScheduleError(f"cron field {text!r} out of range {lo}-{hi}")
        out.update(range(start, end + 1, step))
    return out


def parse_cron(expr: str) -> List[Set[int]]:
    parts = str(expr or "").split()
    if len(parts) != 5:
        raise ScheduleError("cron needs five fields: minute hour day-of-month month day-of-week")
    return [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES)]


def next_cron(expr: str, after: float) -> float:
    fields = parse_cron(expr)
    t = _dt.datetime.fromtimestamp(after).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    dom_any = expr.split()[2] == "*"
    dow_any = expr.split()[4] == "*"
    for _ in range(366 * 24 * 60):
        dow = (t.weekday() + 1) % 7  # cron: 0 = Sunday
        dom_ok, dow_ok = t.day in fields[2], dow in fields[4]
        day_ok = (dom_ok and dow_ok) if (dom_any or dow_any) else (dom_ok or dow_ok)
        if t.month in fields[3] and day_ok and t.hour in fields[1] and t.minute in fields[0]:
            return t.timestamp()
        t += _dt.timedelta(minutes=1)
    raise ScheduleError("cron expression never fires")


def next_fire(item: Mapping[str, Any], after: float) -> Optional[float]:
    kind = item.get("kind")
    spec = item.get("spec") or {}
    if kind == "cron":
        return next_cron(str(spec.get("cron") or ""), after)
    if kind == "interval":
        return after + max(60.0, float(spec.get("every_s") or 3600))
    return None


def validate(item: Mapping[str, Any]) -> List[str]:
    errors = []
    if not str(item.get("name") or "").strip():
        errors.append("name is required")
    kind = item.get("kind")
    if kind not in KINDS:
        return errors + [f"kind must be one of {', '.join(KINDS)}"]
    spec = item.get("spec") or {}
    try:
        if kind == "cron":
            parse_cron(str(spec.get("cron") or ""))
        elif kind == "interval":
            if float(spec.get("every_s") or 0) < 60:
                errors.append("interval must be at least 60 seconds")
        elif kind == "trigger":
            if not spec.get("metric"):
                errors.append("trigger needs a metric")
            if spec.get("op") not in OPS:
                errors.append(f"op must be one of {', '.join(OPS)}")
            if spec.get("op") != "changed" and not isinstance(spec.get("value"), (int, float, str, bool)):
                errors.append("trigger needs a value to compare with")
    except (ScheduleError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    target = item.get("target") or {}
    if target.get("kind") not in ("tool", "runbook"):
        errors.append("target.kind must be tool or runbook")
    if not target.get("target"):
        errors.append("target.target (id@version) is required")
    if kind != "trigger" and not (target.get("selector") or {}):
        errors.append("target.selector is required")
    return errors


def metric_value(facts: Mapping[str, Any], metric: str) -> Any:
    facts = facts or {}
    if metric == "max_disk_used_pct":
        values = [d.get("used_pct") for d in facts.get("disks") or [] if isinstance(d, dict)]
        values = [v for v in values if isinstance(v, (int, float))]
        return max(values) if values else None
    if metric == "memory_used_pct":
        return (facts.get("memory") or {}).get("used_pct")
    if metric == "load1":
        load = (facts.get("uptime") or {}).get("load") or []
        return load[0] if load else None
    if metric == "load_per_cpu":
        load = (facts.get("uptime") or {}).get("load") or []
        cpus = facts.get("cpu_count") or 0
        return round(load[0] / cpus, 2) if load and cpus else None
    current: Any = facts
    for part in metric.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def condition(spec: Mapping[str, Any], new: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    metric = str(spec.get("metric") or "")
    value = metric_value(new, metric)
    op = spec.get("op")
    if op == "changed":
        previous = metric_value(old, metric)
        return bool(old) and previous is not None and value != previous
    if value is None:
        return False
    target = spec.get("value")
    try:
        if op == ">":
            return value > target
        if op == ">=":
            return value >= target
        if op == "<":
            return value < target
        if op == "<=":
            return value <= target
        if op == "==":
            return value == target
        if op == "!=":
            return value != target
    except TypeError:
        return False
    return False
