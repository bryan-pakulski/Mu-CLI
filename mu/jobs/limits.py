"""Job caps that pause and ask instead of stopping.

Every cost, token, time and iteration cap in job mode works the same way: when
a stage reaches one, the stage pauses resumably (its conversation, transcript
and harness session are kept), the spend so far is recorded and the job waits
on a human with attention ``limit_reached``. The human either approves more
(``raiseLimit``) and the paused stage continues, or cancels the job.

``limit_detail`` builds the structured record carried in the pause event and
``metadata.limit_reached``; ``raise_limit`` applies an approval. Job-wide caps
live in the job row (``max_cost_usd``, ``max_runtime_seconds``); stage caps
(Settings → sandbox budgets, stage time limits, iteration limits) get per-job
extra allowances in ``metadata.limit_grants`` — global Settings are never
changed. ``with_grants`` / ``iteration_limit`` are the only readers.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional

KINDS = ("cost", "tokens", "time", "iterations")
SCOPES = ("job", "stage")
STAGES = ("plan", "implement", "review")
UNITS = {"cost": "usd", "tokens": "tokens", "time": "seconds", "iterations": "iterations"}
# Job-wide caps that exist (tokens and iterations are always per stage).
JOB_KINDS = ("cost", "time")
ATTENTION = "limit_reached"
MAX_JOB_COST_USD = 1_000_000.0
MAX_JOB_RUNTIME_S = 7 * 24 * 3600
_STAGE_ALIASES = {"planning": "plan", "implementing": "implement", "reviewing": "review"}


class LimitError(ValueError):
    """An invalid raise request (bad kind, scope, stage or amount)."""


def stage_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STAGE_ALIASES.get(text, text)


# ------------------------------------------------------------------ formatting
def fmt(kind: str, value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if kind == "cost":
        return f"${number:,.2f}"
    if kind == "time":
        return duration(number)
    if kind == "tokens":
        return f"{int(round(number)):,} tokens"
    return f"{int(round(number)):,} iterations"


def duration(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0))))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m" + (f" {secs}s" if secs else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h" + (f" {minutes}m" if minutes else "")


def amount_text(kind: str, value: float) -> str:
    """An amount as the user would type it (input prefill): ``100``, ``30m``, ``5M``, ``1000``."""
    value = float(value or 0)
    if kind == "cost":
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if kind == "time":
        minutes = max(1, int(round(value / 60)))
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"
    number = int(round(value))
    if kind == "tokens":
        if number >= 1_000_000 and number % 100_000 == 0:
            return f"{number / 1_000_000:g}M"
        if number >= 1_000 and number % 1_000 == 0:
            return f"{number // 1_000}k"
    return str(number)


_NOUNS = {("job", "cost"): "cost limit", ("job", "time"): "time limit",
          ("stage", "cost"): "cost budget", ("stage", "tokens"): "token budget",
          ("stage", "time"): "time limit", ("stage", "iterations"): "iteration limit"}


def suggestion(kind: str, scope: str, limit: float) -> float:
    """Prefill for "Approve more": the limit again; half of it (at least 5 minutes) for time."""
    limit = max(0.0, float(limit or 0))
    if kind == "time":
        return float(max(300, int(math.ceil(limit * 0.5 / 60.0)) * 60))
    if kind == "cost":
        return round(limit, 2) if limit > 0 else 10.0
    return float(int(limit)) if limit >= 1 else 1.0


def limit_detail(kind: str, scope: str, stage: str, used: float, limit: float) -> Dict[str, Any]:
    """The structured record of a reached cap (pause event payload, ``metadata.limit_reached``)."""
    if kind not in KINDS or scope not in SCOPES:
        raise ValueError(f"unknown limit {scope}/{kind}")
    stage = stage_name(stage)
    used, limit = float(used or 0), float(limit or 0)
    if kind == "cost":
        used, limit = round(used, 6), round(limit, 6)
    else:
        used, limit = float(int(round(used))), float(int(round(limit)))
    noun = _NOUNS[(scope, kind)]
    where = f" during {stage}" if stage else ""
    if scope == "job":
        message = f"Job reached its {fmt(kind, limit)} {noun}{where} ({fmt(kind, used)} used)."
    else:
        message = f"The {stage or 'current'} stage reached its {fmt(kind, limit)} {noun} ({fmt(kind, used)} used)."
    message += " Approve more to continue, or cancel."
    label = {"cost": "cost", "tokens": "tokens", "time": "time", "iterations": "iterations"}[kind]
    short = {"cost": lambda v: fmt("cost", v), "tokens": lambda v: f"{int(v):,}",
             "time": duration, "iterations": lambda v: f"{int(v):,}"}[kind]
    suggested = suggestion(kind, scope, limit)
    return {"kind": kind, "scope": scope, "stage": stage, "used": used, "limit": limit, "unit": UNITS[kind],
            "message": message, "summary": f"Limit reached: {label} {short(used)} / {short(limit)}",
            "suggested": suggested, "suggested_text": amount_text(kind, suggested)}


# --------------------------------------------------------------------- parsing
_SUFFIX_RE = re.compile(r"^\s*\$?\s*([0-9][0-9_,]*(?:\.[0-9]+)?|\.[0-9]+)\s*([a-zA-Z$]*)\s*([a-zA-Z]*)\s*$")
_TIME_UNITS = {"": 60, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60,
               "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
               "d": 86400, "day": 86400, "days": 86400}
_COUNT_UNITS = {"": 1, "k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000}


def parse_amount(kind: str, value: Any) -> float:
    """Amount in the limit's unit (USD, tokens, seconds, iterations) from typed text or a number.

    Cost: ``25``, ``$25``, ``25usd``. Time: ``90s``, ``30m``, ``2h``, ``1d``; a bare number is
    minutes. Tokens/iterations: ``5000000``, ``5M``, ``500k``, ``5M tokens``. Raises ``LimitError``."""
    if kind not in KINDS:
        raise LimitError(f"unknown limit kind {kind!r}; use cost, tokens, time or iterations")
    if isinstance(value, bool):
        raise LimitError("amount must be a number")
    if isinstance(value, (int, float)):
        number = float(value)
        amount = number * 60 if kind == "time" else number
    else:
        match = _SUFFIX_RE.match(str(value or ""))
        if not match:
            raise LimitError(f"cannot read amount {value!r}")
        number = float(match.group(1).replace(",", "").replace("_", ""))
        unit, trailing = match.group(2).lower(), match.group(3).lower()
        if kind == "cost":
            if unit not in ("", "usd", "$", "dollars") or trailing:
                raise LimitError(f"cannot read cost {value!r}; use e.g. 25 or 25usd")
            amount = number
        elif kind == "time":
            if unit not in _TIME_UNITS or trailing:
                raise LimitError(f"cannot read time {value!r}; use e.g. 30m, 2h or 90s")
            amount = number * _TIME_UNITS[unit]
        else:
            noun = "tokens" if kind == "tokens" else "iterations"
            if unit in (noun, noun[:-1]) and not trailing:
                unit, trailing = "", ""
            if unit not in _COUNT_UNITS or trailing not in ("", noun, noun[:-1]):
                raise LimitError(f"cannot read {noun} {value!r}; use e.g. 5M or 500k")
            amount = number * _COUNT_UNITS[unit]
    if not math.isfinite(amount) or amount <= 0:
        raise LimitError("amount must be positive")
    if kind in ("tokens", "iterations", "time"):
        amount = float(int(round(amount)))
        if amount <= 0:
            raise LimitError("amount must be positive")
    return amount


# --------------------------------------------------------------------- grants
def grants(job: Any) -> Dict[str, Dict[str, float]]:
    raw = (getattr(job, "metadata", None) or {}).get("limit_grants")
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for kind, by_stage in raw.items():
        if kind in KINDS and isinstance(by_stage, dict):
            out[kind] = {}
            for stage, value in by_stage.items():
                try:
                    out[kind][str(stage)] = max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
    return out


def grant(job: Any, kind: str, stage: str) -> float:
    return float(grants(job).get(kind, {}).get(stage_name(stage), 0.0))


def with_grants(policy, job: Any, stage: str):
    """``policy`` with this job's approved extra stage cost, tokens and time added."""
    from dataclasses import replace
    stage = stage_name(stage)
    cost, tokens, seconds = grant(job, "cost", stage), grant(job, "tokens", stage), grant(job, "time", stage)
    if not (cost or tokens or seconds):
        return policy
    return replace(policy, max_cost_usd=float(policy.max_cost_usd) + cost,
                   max_tokens=int(policy.max_tokens) + int(tokens), timeout=int(policy.timeout) + int(seconds))


def iteration_limit(job: Any, stage: str) -> int:
    """Per-run agent iteration limit of ``stage``, including approved extra iterations."""
    from .models import stage_iteration_limit
    return int(stage_iteration_limit(job, stage)) + int(grant(job, "iterations", stage))


def stage_limit(job: Any, kind: str, stage: str) -> float:
    """Effective stage cap (Settings value plus this job's grants)."""
    stage = stage_name(stage)
    if kind == "iterations":
        return float(iteration_limit(job, stage))
    from .sandbox.policy import SandboxPolicy
    policy = with_grants(SandboxPolicy.for_stage(stage), job, stage)
    return float({"cost": policy.max_cost_usd, "tokens": policy.max_tokens, "time": policy.timeout}[kind])


def remaining_runtime(service, job, *, now: Optional[float] = None) -> Optional[float]:
    """Seconds left of the job's runtime limit (worker-active time), or None without a limit."""
    limit = getattr(job, "max_runtime_seconds", None)
    if not limit:
        return None
    try:
        used = service.store.active_runtime_seconds(job.id, now=now)
    except Exception:  # noqa: BLE001 - fall back to wall time
        used = None
    if used is None:
        import time
        started = getattr(job, "started_at", None)
        used = (float(now if now is not None else time.time()) - float(started)) if started else 0.0
    return float(limit) - float(used)


def runtime_used(service, job) -> float:
    left = remaining_runtime(service, job)
    return float(job.max_runtime_seconds or 0) - float(left or 0) if left is not None else 0.0


# ---------------------------------------------------------------------- pause
def record_pause(service, job_id: str, detail: Dict[str, Any]) -> None:
    """Remember the reached cap on the job (``metadata.limit_reached``) and log it."""
    try:
        service.store.patch_metadata(job_id, {"limit_reached": dict(detail)})
        service.store.append_event(job_id, "limit_reached", reason=str(detail.get("message") or "")[:500],
                                   payload=dict(detail))
    except Exception:  # noqa: BLE001 - the pause itself still happens
        pass


def pending(job: Any, attention: Any = None) -> Optional[Dict[str, Any]]:
    """The cap this job is paused on, or None."""
    reason = attention if attention is not None else getattr(job, "attention_reason", "")
    if str(getattr(reason, "value", reason) or "") != ATTENTION:
        return None
    detail = (getattr(job, "metadata", None) or {}).get("limit_reached")
    return dict(detail) if isinstance(detail, dict) and detail.get("kind") in KINDS else None


# ---------------------------------------------------------------------- raise
def raise_limit(service, job, *, kind: str, scope: str, amount: Any, stage: str = "",
                by: str = "human") -> Dict[str, Any]:
    """Approve ``amount`` more of a cap. Returns the ``limit_raised`` record.

    Job caps update the job row; stage caps add to ``metadata.limit_grants`` (Settings stay as
    they are). Raises ``LimitError`` for invalid requests."""
    kind, scope, stage = str(kind or "").strip().lower(), str(scope or "").strip().lower(), stage_name(stage)
    if kind not in KINDS:
        raise LimitError(f"unknown limit kind {kind!r}; use cost, tokens, time or iterations")
    if scope not in SCOPES:
        raise LimitError("scope must be job or stage")
    value = parse_amount(kind, amount)
    if scope == "job":
        if kind not in JOB_KINDS:
            raise LimitError(f"{kind} limits are per stage; use scope stage")
        field = "max_cost_usd" if kind == "cost" else "max_runtime_seconds"
        old = getattr(job, field, None)
        if not old:
            raise LimitError(f"this job has no {kind} limit to raise")
        new = float(old) + value
        if kind == "cost":
            if new > MAX_JOB_COST_USD:
                raise LimitError(f"the job cost limit cannot exceed {fmt('cost', MAX_JOB_COST_USD)}")
            service.store.update_runtime_fields(job.id, max_cost_usd=round(new, 6))
        else:
            if new > MAX_JOB_RUNTIME_S:
                raise LimitError("the job runtime limit cannot exceed 7 days")
            new = float(int(new))
            service.store.update_runtime_fields(job.id, max_runtime_seconds=int(new))
    else:
        if stage not in STAGES:
            raise LimitError("stage must be plan, implement or review")
        old = stage_limit(job, kind, stage)
        new = old + value

        def add(meta: Dict[str, Any]) -> None:
            table = dict(meta.get("limit_grants") or {})
            row = dict(table.get(kind) or {})
            row[stage] = float(row.get(stage) or 0.0) + value
            table[kind] = row
            meta["limit_grants"] = table
        service.store.patch_metadata(job.id, mutate=add)
    record = {"kind": kind, "scope": scope, "stage": stage, "unit": UNITS[kind], "amount": value,
              "old": float(old), "new": float(new), "old_text": fmt(kind, old), "new_text": fmt(kind, new),
              "by": str(by or "human")[:80]}
    service.store.append_event(
        job.id, "limit_raised",
        reason=f"{scope} {kind} limit raised from {fmt(kind, old)} to {fmt(kind, new)}"[:500], payload=record)
    return record


__all__ = [
    "ATTENTION", "KINDS", "LimitError", "SCOPES", "STAGES", "UNITS", "amount_text", "fmt", "grant", "grants",
    "iteration_limit", "limit_detail", "parse_amount", "pending", "raise_limit", "record_pause",
    "remaining_runtime", "stage_limit", "suggestion", "with_grants",
]
