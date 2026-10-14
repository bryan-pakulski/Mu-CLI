"""Activity read-model: running agents, checks and job containers (contract §5).

Everything here is best effort and bounded: Docker missing or unreachable
yields ``engine: "off"``/``"down"`` with empty container lists, never an
exception. ``docker ps`` results are cached for ``CACHE_TTL`` seconds and
resource stats are sampled on a background thread (joined for at most
``STATS_WAIT_S``) so a call stays well under three seconds.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from .states import ACTIVE, AGENT_STAGES, WAITING, JobState

CACHE_TTL = 2.0
PS_TIMEOUT_S = 2.0
STATS_WAIT_S = 0.6
CONTAINER_RE = re.compile(r"^mucli-job-([0-9a-f]{12})-")
REMOVABLE_RE = re.compile(r"^mucli-job-([0-9a-f]{12})-[A-Za-z0-9_.-]+$")

_STAGE = {
    JobState.PLANNING: "plan", JobState.IMPLEMENTING: "implement", JobState.REVIEWING: "review",
    JobState.CHECKING: "checks", JobState.PUSHING: "push",
}
_KIND = {
    JobState.PROVISIONING: "provision", JobState.PLANNING: "agent", JobState.IMPLEMENTING: "agent",
    JobState.REVIEWING: "agent", JobState.CHECKING: "checks", JobState.PUSHING: "push",
}


class _DockerCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.ps_at = 0.0
        self.engine = "off"
        self.containers: List[Dict[str, Any]] = []
        self.stats: Dict[str, Dict[str, Any]] = {}
        self.stats_at = 0.0
        self.stats_thread: Optional[threading.Thread] = None
        self.collector = None

    def reset(self):
        with self.lock:
            self.ps_at = 0.0
            self.stats_at = 0.0
            self.containers = []
            self.stats = {}


_CACHE = _DockerCache()


def reset_cache() -> None:
    """Drop cached Docker observations (tests, and after a removal)."""
    _CACHE.reset()


def _docker() -> Optional[str]:
    return shutil.which("docker")


def _parse_created(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    # docker ps: "2026-10-13 10:00:00 +0000 UTC"
    parts = text.split(" ")
    for candidate, fmt in ((" ".join(parts[:3]), "%Y-%m-%d %H:%M:%S %z"), (text, "%Y-%m-%dT%H:%M:%S%z")):
        try:
            return datetime.strptime(candidate, fmt).timestamp()
        except ValueError:
            continue
    return None


def _stage_from_name(name: str) -> str:
    match = CONTAINER_RE.match(name)
    if not match:
        return ""
    rest = name[match.end():]
    return rest.rsplit("-", 1)[0] if "-" in rest else rest


def _labels(text: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in str(text or "").split(","):
        key, sep, value = item.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


_ROLE_KIND = {"job-engine": "engine", "job-broker": "broker", "job-stage": "stage"}


def _ps(docker: str) -> Tuple[str, List[Dict[str, Any]]]:
    import json

    try:
        proc = subprocess.run(
            [docker, "ps", "-a", "--no-trunc", "--filter", "name=mucli-job-", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=PS_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "down", []
    if proc.returncode != 0:
        return "down", []
    rows: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("Names") or "").split(",")[0].lstrip("/")
        match = CONTAINER_RE.match(name)
        if not match:
            continue
        labels = _labels(item.get("Labels"))
        stage = _stage_from_name(name)
        role = _ROLE_KIND.get(labels.get("io.mucli.role", ""), "engine" if stage == "engine" else
                              "broker" if stage.startswith("env-") else "stage")
        rows.append({
            "name": name, "id": str(item.get("ID") or "")[:12], "image": str(item.get("Image") or ""),
            "status": str(item.get("State") or "").lower() or str(item.get("Status") or ""),
            "status_text": str(item.get("Status") or ""),
            "stage": stage, "started_at": _parse_created(item.get("CreatedAt")),
            "role": role, "binding": labels.get("io.mucli.binding", ""), "gpus": labels.get("io.mucli.gpus", ""),
            "job_prefix": match.group(1),
        })
    return "up", rows


def _refresh_stats(names: List[str]) -> None:
    try:
        from mu.container.docker_cli import CommandRunner
        from mu.container.stats import ContainerStatsCollector

        if _CACHE.collector is None:
            _CACHE.collector = ContainerStatsCollector(CommandRunner(), min_interval=CACHE_TTL)
        refs = [SimpleNamespace(name=n, status="running", gpu_request="", devices=[]) for n in names]
        payload = _CACHE.collector.collect(refs)
        _CACHE.collector.runner.commands.clear()  # diagnostics ledger; keep it bounded
        stats = {}
        for name, metric in (payload.get("containers") or {}).items():
            if metric.get("error"):
                continue
            stats[name] = {"cpu_percent": float(metric.get("cpu_percent") or 0.0),
                           "mem_bytes": int(metric.get("memory_used_bytes") or 0),
                           "mem_limit": int(metric.get("memory_limit_bytes") or 0) or None}
        with _CACHE.lock:
            _CACHE.stats = stats
            _CACHE.stats_at = time.time()
    except Exception:  # noqa: BLE001 - telemetry is best effort
        with _CACHE.lock:
            _CACHE.stats_at = time.time()


def containers(*, now: Optional[float] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """``(engine, containers)`` with cached ``docker ps`` + sampled stats."""
    now = time.time() if now is None else now
    docker = _docker()
    if not docker:
        return "off", []
    with _CACHE.lock:
        fresh = now - _CACHE.ps_at < CACHE_TTL
        engine, rows = _CACHE.engine, [dict(r) for r in _CACHE.containers]
    if not fresh:
        engine, rows = _ps(docker)
        with _CACHE.lock:
            _CACHE.engine, _CACHE.containers, _CACHE.ps_at = engine, [dict(r) for r in rows], now
    running = [r["name"] for r in rows if r["status"] == "running"]
    thread = None
    with _CACHE.lock:
        stale = time.time() - _CACHE.stats_at >= CACHE_TTL
        busy = _CACHE.stats_thread is not None and _CACHE.stats_thread.is_alive()
        if running and stale and not busy:
            thread = threading.Thread(target=_refresh_stats, args=(running,), name="mucli-activity-stats",
                                      daemon=True)
            _CACHE.stats_thread = thread
    if thread is not None:
        thread.start()
        thread.join(STATS_WAIT_S)
    with _CACHE.lock:
        stats = dict(_CACHE.stats)
    for row in rows:
        metric = stats.get(row["name"]) if row["status"] == "running" else None
        row["cpu_percent"] = metric["cpu_percent"] if metric else None
        row["mem_bytes"] = metric["mem_bytes"] if metric else None
        row["mem_limit"] = metric["mem_limit"] if metric else None
    return engine, rows


def _public(container: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in container.items() if k != "job_prefix"}


def _since(service, job) -> float:
    try:
        events = service.store.list_events_by_type(job.id, ("status_changed", "job_created"))
    except Exception:  # noqa: BLE001
        events = []
    for event in reversed(events):
        return float(event.created_at)
    return float(job.updated_at or job.created_at or 0.0)


_LAST_TYPES = ("runtime_status", "tool_call_ui", "runtime_info", "runtime_error", "tool_result_ui")


def _last_line(service, job) -> str:
    try:
        events = service.store.recent_events(job.id, limit=60)
    except Exception:  # noqa: BLE001
        return ""
    for event in reversed(events):
        kind, payload = event.event_type, dict(event.payload or {})
        if kind == "sandbox_event":
            kind = str(payload.get("event") or "")
            payload = dict(payload.get("payload") or {})
        text = ""
        if kind == "tool_call_ui":
            text = f"tool: {payload.get('tool_name') or 'tool'}"
        elif kind in _LAST_TYPES:
            text = str(payload.get("text") or payload.get("preview") or "")
        elif kind == "checks_output":
            lines = [ln for ln in str(payload.get("text") or "").splitlines() if ln.strip()]
            text = f"{payload.get('suite') or 'checks'}: {lines[-1]}" if lines else ""
        text = " ".join(text.split())
        if text:
            return text[:200]
    return ""


def _jobs(service, limit: int = 500):
    out = []
    for job in service.list_unarchived(limit=limit):
        try:
            core = service.store.core_state(job.id)
        except Exception:  # noqa: BLE001
            continue
        out.append((job, core))
    return out


def _cost(job) -> float:
    from .artifacts import artifacts_for
    try:
        return max(float(job.cost_usd or 0.0), sum(artifacts_for(job).stage_costs().values()))
    except (OSError, ValueError):
        return float(job.cost_usd or 0.0)


def activity(service, *, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    engine, found = containers(now=now)
    by_prefix: Dict[str, List[Dict[str, Any]]] = {}
    for container in found:
        by_prefix.setdefault(container["job_prefix"], []).append(container)
    rows: List[Dict[str, Any]] = []
    attached: set = set()
    needs_you = 0
    for job, core in _jobs(service):
        state = core.state
        if state in WAITING or state == JobState.FAILED:
            needs_you += 1
        owned = by_prefix.get(job.id[:12], [])
        if state not in ACTIVE and not (state in WAITING and owned):
            continue
        effective = core.paused_from if state == JobState.NEEDS_INPUT and core.paused_from else state
        meta = dict(job.metadata or {})
        jira = {**dict(meta.get("jira") or {}), **dict(meta.get("ticket") or {})}
        attached.update(c["name"] for c in owned)
        rows.append({
            "id": job.id, "key": str(jira.get("key") or meta.get("jira_key") or ""), "title": job.title,
            "state": state.value, "stage": _STAGE.get(effective, ""),
            "worker_id": job.worker_id or None, "heartbeat_at": job.heartbeat_at,
            "lease_expires_at": job.lease_expires_at, "since": _since(service, job),
            "cost_usd": _cost(job), "kind": _KIND.get(state, "waiting"),
            "last": _last_line(service, job), "containers": [_public(c) for c in owned],
        })
    orphans = [_public(c) for c in found if c["name"] not in attached]
    return {
        "generated_at": now, "engine": engine,
        "counts": {"agents": sum(r["kind"] == "agent" for r in rows),
                   "checks": sum(r["kind"] == "checks" for r in rows),
                   "containers": len(found), "needs_you": needs_you},
        "jobs": rows, "orphans": orphans,
    }


def status(service) -> Dict[str, Any]:
    """Header-chip counts: store-only plus the cached engine state."""
    agents = checks = needs = 0
    for _job, core in _jobs(service):
        if core.state in AGENT_STAGES:
            agents += 1
        elif core.state == JobState.CHECKING:
            checks += 1
        elif core.state in WAITING or core.state == JobState.FAILED:
            needs += 1
    if not _docker():
        engine = "off"
    else:
        with _CACHE.lock:
            fresh = time.time() - _CACHE.ps_at < 30.0
            engine = _CACHE.engine
        if not fresh:
            engine, _ = containers()
    return {"agents": agents, "checks": checks, "needs_you": needs, "engine": engine}


def job_logs(service, job_id: str, *, tail_bytes: int = 65536) -> Dict[str, Any]:
    from mu.security.secret_paths import redact_secrets

    from ..diagnostics import _read_tail

    job = service.get(job_id)  # KeyError -> 404
    take = max(1024, min(int(tail_bytes or 65536), 512 * 1024))
    path = os.path.join(os.path.dirname(service.store.path), "logs", f"{job.id}.log")
    text, size, truncated = _read_tail(path, max_bytes=take)
    if text.strip():
        return {"text": redact_secrets(text)[0], "truncated": truncated, "source": "worker"}
    docker = _docker()
    if docker:
        _, rows = containers()
        owned = sorted((c for c in rows if c["job_prefix"] == job.id[:12]),
                       key=lambda c: c.get("started_at") or 0.0)
        if owned:
            try:
                proc = subprocess.run([docker, "logs", "--tail", "2000", owned[-1]["name"]],
                                      capture_output=True, text=True, timeout=PS_TIMEOUT_S, check=False)
                raw = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode == 0 and raw.strip():
                    encoded = raw.encode("utf-8", errors="replace")
                    clipped = encoded[-take:].decode("utf-8", errors="replace")
                    return {"text": redact_secrets(clipped)[0], "truncated": len(encoded) > take,
                            "source": "container"}
            except (OSError, subprocess.SubprocessError):
                pass
    return {"text": redact_secrets(text)[0], "truncated": truncated, "source": "worker"}


def remove_container(service, name: str) -> Dict[str, Any]:
    from .commands import CommandRejected

    name = str(name or "")
    match = REMOVABLE_RE.match(name)
    if not match:
        raise ValueError("only mucli-job-<id>-* containers can be removed")
    prefix = match.group(1)
    for job in service.list(limit=5000):  # archived rows included: never kill live work
        if not job.id.startswith(prefix):
            continue
        core = service.store.core_state(job.id)
        if core.state in ACTIVE:
            raise CommandRejected(f"job {job.id[:12]} is {core.state.value}; its containers are in use")
    docker = _docker()
    if not docker:
        raise RuntimeError("docker is not available")
    try:
        proc = subprocess.run([docker, "rm", "-f", name], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"docker rm failed: {exc}") from exc
    if proc.returncode != 0:
        if "No such container" in (proc.stderr or ""):
            raise KeyError(name)
        raise RuntimeError((proc.stderr or "docker rm failed").strip()[:300])
    reset_cache()
    return {"ok": True}
