"""Tracker intake, duplicate protection, writeback and the ticket watcher.

* :func:`import_tickets` creates one job per ticket with duplicate
  protection: a lookup over every job linked to ``(tracker, key)`` (no row
  cap) under a process + file lock. Tickets with a live job are skipped;
  tickets whose latest job is DONE are skipped unless ``force``.
* :func:`notify` reports job progress back to the ticket (comment and/or a
  configured status transition). Best-effort: it logs and never raises.
* :func:`poll_watches` imports new tickets from saved queries at the
  configured interval (off by default).

Settings live in ``$MUCLI_HOME/integrations/jira.json`` (``writeback`` and
``trackers.watch``); see :mod:`mu.integrations.jira_settings`.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

log = logging.getLogger(__name__)

EVENTS = ("created", "plan_ready", "pushed", "done", "failed", "needs_human")
# One-shot events are sent once per job; the others once per distinct detail.
_ONE_SHOT = frozenset({"created", "done"})
_LEDGER = "tracker/writeback.json"
_IMPORT_LOCK = threading.RLock()
_WATCH_LOCK = threading.Lock()
_LEDGER_LOCK = threading.Lock()


# ------------------------------------------------------------------ lookup

def _ref(job: Any) -> Dict[str, str]:
    from mu.integrations.jira import ticket_ref

    return ticket_ref(getattr(job, "metadata", None) or {})


def _rows(service, key: str = "") -> List[sqlite3.Row]:
    """Every job row linked to a ticket (optionally one key) — read-only, uncapped."""
    store = service.store
    key_expr = ("COALESCE(json_extract(metadata_json, '$.ticket.key'), "
                "json_extract(metadata_json, '$.jira.key'), json_extract(metadata_json, '$.jira_key'))")
    columns = (f"id, title, status, attention_reason, created_at, updated_at, {key_expr} AS ticket_key, "
               "COALESCE(json_extract(metadata_json, '$.ticket.tracker'), "
               "CASE WHEN json_extract(metadata_json, '$.jira.key') IS NOT NULL "
               "OR json_extract(metadata_json, '$.jira_key') IS NOT NULL THEN 'jira' END) AS ticket_tracker")
    where = f"{key_expr} = ?" if key else f"{key_expr} IS NOT NULL"
    params: List[Any] = [key] if key else []
    archived = ("EXISTS (SELECT 1 FROM job_management m WHERE m.job_id = jobs.id "
                "AND m.archived_at IS NOT NULL) AS archived")
    conn = store._connect()
    try:
        for cols in (f"{columns}, state, {archived}", f"{columns}, state, 0 AS archived",
                     f"{columns}, '' AS state, 0 AS archived"):
            try:
                return conn.execute(f"SELECT {cols} FROM jobs WHERE {where} ORDER BY created_at DESC",
                                    params).fetchall()
            except sqlite3.OperationalError:
                continue  # job_management / state column not created yet
        return []
    finally:
        conn.close()


def _is_live(row: sqlite3.Row) -> bool:
    from .models import TERMINAL_STATUSES

    terminal = {s.value for s in TERMINAL_STATUSES}
    state = str(row["state"] or "")
    return (row["status"] not in terminal and state not in {"done", "cancelled"}
            and not bool(row["archived"]))


def _is_done(row: sqlite3.Row) -> bool:
    return str(row["state"] or "") == "done" or row["status"] == "merged"


def ticket_jobs(service, tracker: str, key: str) -> List[Dict[str, Any]]:
    """Jobs linked to ``(tracker, key)``, newest first."""
    out = []
    for row in _rows(service, str(key or "").strip()):
        if str(row["ticket_tracker"] or "jira") != tracker:
            continue
        out.append({"job_id": row["id"], "title": row["title"], "status": row["status"],
                    "state": str(row["state"] or ""), "live": _is_live(row), "done": _is_done(row),
                    "created_at": float(row["created_at"] or 0.0)})
    return out


def duplicate_of(service, tracker: str, key: str, *, force: bool = False) -> Optional[Dict[str, str]]:
    """Why a new job for this ticket would be a duplicate, or None."""
    jobs = ticket_jobs(service, tracker, key)
    live = next((j for j in jobs if j["live"]), None)
    if live:
        return {"job_id": live["job_id"], "reason": "active job exists"}
    if jobs and jobs[0]["done"] and not force:
        return {"job_id": jobs[0]["job_id"], "reason": "latest job is done (choose Run again, or --force, to start a new job)"}
    return None


def linked_jobs(service) -> Dict[str, Dict[str, Any]]:
    """Map ticket key -> most relevant linked job (live beats terminal, then newest)."""
    found: Dict[str, Dict[str, Any]] = {}
    for row in _rows(service):
        key = str(row["ticket_key"] or "").strip()
        if not key:
            continue
        live = _is_live(row)
        entry = {
            "job_id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "attention_reason": str(row["attention_reason"] or ""),
            "live": live,
            "updated_at": float(row["updated_at"] or 0.0),
        }
        current = found.get(key)
        if current is None or (live, entry["updated_at"]) > (current["live"], current["updated_at"]):
            found[key] = entry
    return found


@contextmanager
def _import_lock(service) -> Iterator[None]:
    """Serialize lookup+create across threads and processes (GUI, TUI, controller)."""
    directory = os.path.join(os.path.dirname(service.store.path), ".command-locks")
    os.makedirs(directory, exist_ok=True)
    with _IMPORT_LOCK:
        with open(os.path.join(directory, "tracker-import.lock"), "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


# ------------------------------------------------------------------ import

def import_tickets(
    service,
    tracker,
    *,
    keys: Iterable[str] = (),
    query: str = "",
    defaults: Optional[Dict[str, Any]] = None,
    max_results: int = 20,
    force: bool = False,
) -> Dict[str, Any]:
    """Create one durable job per ticket (keys and/or a tracker query)."""
    from mu.integrations.http import TrackerError

    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for key in [str(k).strip() for k in keys if str(k).strip()]:
        try:
            issues.append(tracker.fetch(key))
        except (TrackerError, ValueError) as exc:
            errors.append({"key": key, "error": str(exc)})
    if str(query or "").strip():
        issues.extend(tracker.search(query, max_results=max_results))
    seen = set()
    defaults = dict(defaults or {})
    for issue in issues:
        key = tracker.key_of(issue)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            issue_payload = tracker.to_payload(issue)
        except (TrackerError, ValueError, TypeError) as exc:
            errors.append({"key": key, "error": f"cannot read ticket: {exc}"})
            continue
        payload = {**defaults, **issue_payload}
        # Merge, don't replace: defaults carry metadata such as the job's origin session.
        payload["metadata"] = {**dict(defaults.get("metadata") or {}), **dict(issue_payload.get("metadata") or {})}
        payload["execution"] = dict(defaults.get("execution") or {})
        with _import_lock(service):
            dup = duplicate_of(service, tracker.name, key, force=force)
            if dup:
                skipped.append({"key": key, **dup})
                continue
            try:
                job = service.create_from_payload(payload)
            except (TypeError, ValueError) as exc:
                errors.append({"key": key, "error": str(exc)})
                continue
        created.append({"key": key, "job_id": job.id, "title": job.title})
        result = notify(service, job, "created", tracker=tracker)
        if result and result.get("error"):
            errors.append({"key": key, "error": f"writeback failed: {result['error']}"})
    return {"created": created, "skipped": skipped, "errors": errors, "tracker": tracker.name}


# ------------------------------------------------------------------ writeback

def _message(job: Any, event: str, info: Dict[str, Any]) -> str:
    short = str(getattr(job, "id", ""))[:10]
    title = str(getattr(job, "title", "") or "")
    detail = str(info.get("reason") or info.get("detail") or "").strip()[:1000]
    if event == "created":
        return f"MuCLI durable job {short} created for this issue: {title}"
    if event == "plan_ready":
        version = info.get("version")
        return f"MuCLI job {short}: plan{f' v{version}' if version else ''} is ready for review."
    if event == "pushed":
        url = str(info.get("pr_url") or "").strip()
        return f"MuCLI job {short} pushed its change" + (f" for review: {url}" if url else ".")
    if event == "done":
        return f"MuCLI job {short} is done."
    if event == "failed":
        return f"MuCLI job {short} failed" + (f": {detail}" if detail else ".")
    if event == "needs_human":
        return f"MuCLI job {short} needs a human" + (f": {detail}" if detail else ".")
    return f"MuCLI job {short}: {event}"


def _dedupe_key(event: str, info: Dict[str, Any]) -> str:
    if event in _ONE_SHOT:
        return event
    detail = info.get("pr_url") or info.get("version") or info.get("reason") or info.get("detail") or ""
    digest = hashlib.sha1(str(detail).encode()).hexdigest()[:12] if detail else ""
    return f"{event}:{digest}"


def tracker_for(name: str):
    """Tracker built from saved settings (monkeypatch point for tests)."""
    from mu.integrations.tracker import get_tracker

    return get_tracker(name)


def notify(service, job: Any, event: str, *, tracker=None, **info: Any) -> Optional[Dict[str, Any]]:
    """Report a job event to its ticket. Never raises.

    Events: ``created``, ``plan_ready`` (``version=``), ``pushed``
    (``pr_url=``), ``done``, ``failed`` (``reason=``), ``needs_human``
    (``reason=``). Returns a small result dict (or None when the job has no
    ticket) for logging/tests.
    """
    try:
        return _notify(service, job, event, tracker=tracker, info=info)
    except Exception as exc:  # noqa: BLE001 - writeback must never fail a job
        log.warning("tracker writeback %s for job %s failed: %s", event, getattr(job, "id", "?"),
                    exc.__class__.__name__)
        return {"event": event, "error": str(exc)[:300]}


def _notify(service, job: Any, event: str, *, tracker, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from mu.integrations.jira_settings import writeback_settings

    if event not in EVENTS:
        return {"event": event, "skipped": "unknown event"}
    if isinstance(job, str):
        job = service.get(job)
    ref = _ref(job)
    if not ref:
        return None
    settings = writeback_settings()
    if tracker is None:
        if not settings.get("enabled"):
            return {"event": event, "skipped": "writeback off"}
        tracker = tracker_for(ref["tracker"])
    if not tracker.writeback_enabled:
        return {"event": event, "skipped": "writeback off"}
    rule = dict((settings.get("events") or {}).get(event) or {})
    from mu.jobs.core.artifacts import artifacts_for

    art = artifacts_for(job)
    dedupe = _dedupe_key(event, info)
    with _LEDGER_LOCK:
        ledger = art.read_json(_LEDGER, {}) or {}
        if dedupe in ledger:
            return {"event": event, "skipped": "already sent"}
    key = ref["key"]
    done: List[str] = []
    errors: List[str] = []

    def attempt(label: str, fn, *args) -> None:
        try:
            if fn(*args):
                done.append(label)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {str(exc)[:200]}")
            log.warning("tracker %s %s on %s failed: %s", tracker.name, label, key, exc.__class__.__name__)

    if rule.get("comment", True):
        attempt("comment", tracker.comment, key, _message(job, event, info))
    if event == "pushed" and info.get("pr_url") and settings.get("link_pr", True):
        if getattr(tracker, "name", "") == "jira":
            attempt("link_pr", tracker.link_pr, key, str(info["pr_url"]), f"Pull request ({getattr(job, 'id', '')[:10]})")
    status = rule.get("jira_status") if getattr(tracker, "name", "") == "jira" else rule.get("github")
    if status:
        attempt(f"transition:{status}", tracker.transition, key, status)
    if done or not errors:
        with _LEDGER_LOCK:
            ledger = art.read_json(_LEDGER, {}) or {}
            ledger[dedupe] = {"at": time.time(), "done": done, "errors": errors}
            art.write_json(_LEDGER, ledger)
    result: Dict[str, Any] = {"event": event, "tracker": tracker.name, "key": key, "done": done}
    if errors:
        result["error"] = "; ".join(errors)
    return result


# ------------------------------------------------------------------ watcher

def _watch_state_path() -> str:
    root = os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/")
    return os.path.join(root, "integrations", "tracker_watch.json")


def _load_watch_state() -> Dict[str, Any]:
    try:
        with open(_watch_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_watch_state(state: Dict[str, Any]) -> None:
    path = _watch_state_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def watch_id(entry: Dict[str, Any]) -> str:
    raw = f"{entry.get('tracker')}|{entry.get('query')}|{entry.get('workspace_root')}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def poll_watches(service, now: Optional[float] = None) -> Dict[str, Any]:
    """Import new tickets from the saved watch queries that are due. Never raises.

    Off unless ``trackers.watch.enabled``. Each query runs at most once per
    ``interval_seconds``; imports use the normal duplicate protection (live
    jobs and done tickets are skipped).
    """
    try:
        from mu.integrations.jira_settings import watch_settings

        config = watch_settings()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker watch settings unreadable: %s", exc.__class__.__name__)
        return {"enabled": False, "error": str(exc)[:300], "ran": []}
    if not config.get("enabled") or not config.get("queries"):
        return {"enabled": bool(config.get("enabled")), "ran": []}
    now = time.time() if now is None else float(now)
    interval = float(config.get("interval_seconds") or 900)
    ran: List[Dict[str, Any]] = []
    if not _WATCH_LOCK.acquire(blocking=False):
        return {"enabled": True, "ran": [], "skipped": "already polling"}
    try:
        state = _load_watch_state()
        for entry in config["queries"]:
            wid = watch_id(entry)
            last = float((state.get(wid) or {}).get("last_run") or 0.0)
            if now - last < interval:
                continue
            outcome: Dict[str, Any] = {"id": wid, "tracker": entry["tracker"], "query": entry["query"]}
            try:
                if not entry.get("workspace_root"):
                    raise ValueError("watch has no workspace_root")
                defaults: Dict[str, Any] = {
                    "workspace_root": entry["workspace_root"],
                    "metadata": {"origin": "tracker-watch", "tracker_watch": wid},
                }
                if entry.get("session_name"):
                    defaults["metadata"]["origin_session"] = entry["session_name"]
                if entry.get("template"):
                    # The entry's "Run in" template (job_templates.md §3.2).
                    defaults["execution"] = {"template": entry["template"]}
                result = import_tickets(service, tracker_for(entry["tracker"]), query=entry["query"],
                                        defaults=defaults, max_results=int(entry.get("max_results") or 10))
                outcome.update(created=[c["key"] for c in result["created"]],
                               skipped=len(result["skipped"]), errors=result["errors"])
            except Exception as exc:  # noqa: BLE001 - the controller tick must survive
                log.warning("tracker watch %s failed: %s", wid, exc.__class__.__name__)
                outcome["error"] = str(exc)[:300]
            state[wid] = {"last_run": now, "created": len(outcome.get("created") or []),
                          "error": outcome.get("error", "")}
            ran.append(outcome)
        if ran:
            try:
                _save_watch_state(state)
            except OSError as exc:
                log.warning("tracker watch state not saved: %s", exc.__class__.__name__)
    finally:
        _WATCH_LOCK.release()
    return {"enabled": True, "ran": ran}


# ------------------------------------------------------------------ state hook

# Core state -> writeback event. PUSHED reads the PR URL publish stores in
# ``metadata.pr`` before it transitions.
_STATE_EVENTS = {
    "plan_review": "plan_ready",
    "pushed": "pushed",
    "done": "done",
    "failed": "failed",
    "needs_input": "needs_human",
}
# Tests flip this to dispatch inline instead of on a daemon thread.
SYNC_DISPATCH = False


def state_event(job: Any, new_state: str, *, reason: str = "") -> Optional[tuple]:
    """``(event, info)`` for a core state change, or None when nothing is sent."""
    event = _STATE_EVENTS.get(str(new_state or ""))
    if not event:
        return None
    meta = dict(getattr(job, "metadata", None) or {})
    info: Dict[str, Any] = {}
    if event == "plan_ready":
        try:
            from .core.plan_ops import plan_store

            version = plan_store(job).latest_version()
        except Exception:  # noqa: BLE001 - the version only labels the comment
            version = None
        if version:
            info["version"] = version
    elif event == "pushed":
        pr = meta.get("pr") if isinstance(meta.get("pr"), dict) else {}
        if pr.get("url"):
            info["pr_url"] = pr["url"]
    elif event in {"failed", "needs_human"}:
        detail = str(getattr(job, "attention_detail", "") or reason or "").strip()
        if detail:
            info["reason"] = detail
    return event, info


def on_state_change(store, job: Any, prior_state: str, new_state: str, *, reason: str = "") -> None:
    """Store hook: report ticket-linked state changes. Cheap and non-blocking.

    Jobs without a ticket or with writeback off return before any network or
    thread work; otherwise ``notify`` runs on a daemon thread so a slow
    tracker never holds up a worker or the controller.
    """
    try:
        if str(prior_state or "") == str(new_state or ""):
            return
        mapped = state_event(job, new_state, reason=reason)
        if mapped is None or not _ref(job):
            return
        from mu.integrations.jira_settings import writeback_settings

        if not writeback_settings().get("enabled"):
            return
        event, info = mapped
        from .service import JobService

        service = JobService(store)

        def send() -> None:
            notify(service, job, event, **info)

        if SYNC_DISPATCH:
            send()
        else:
            threading.Thread(target=send, name=f"mucli-tracker-{event}", daemon=True).start()
    except Exception as exc:  # noqa: BLE001 - a hook must never break a transition
        log.warning("tracker state hook failed: %s", exc.__class__.__name__)
