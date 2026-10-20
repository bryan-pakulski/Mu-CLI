"""Human-readable timelines derived at read time from the audit log (jobs ``notes.py`` style)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

_TEMPLATES = {
    "run.propose": ("Proposed by {actor}", "info"),
    "run.approve": ("Approved by {actor}{rule}", "ok"),
    "run.start": ("Started", "running"),
    "run.batch": ("Batch {batch_n} started", "running"),
    "run.pause": ("Paused: {reason}", "attention"),
    "run.continue": ("Continued by {actor}", "ok"),
    "run.rollback": ("Rolling back ({rollbacks} change(s))", "attention"),
    "run.cancel": ("Cancelled by {actor}", "neutral"),
    "run.reject": ("Rejected by {actor}", "neutral"),
    "run.succeeded": ("Succeeded: {reason}", "ok"),
    "run.failed": ("Failed: {reason}", "danger"),
    "run.rolled_back": ("Rolled back", "ok"),
    "run.expire": ("Expired without approval", "neutral"),
    "run.retry_node": ("{actor} retried a node", "info"),
    "execution.timeout": ("A node timed out: {reason}", "danger"),
    "execution.unreachable": ("A node became unreachable: {reason}", "attention"),
    "execution.requeue": ("Re-dispatched an idempotent execution: {reason}", "attention"),
    "tool.draft.create": ("Draft created by {actor}", "info"),
    "tool.draft.update": ("Draft updated by {actor}", "info"),
    "tool.lint": ("Lint {lint}", "info"),
    "tool.submit": ("Submitted for review by {actor}: {headline}", "attention"),
    "tool.request_changes": ("Changes requested: {notes}", "attention"),
    "tool.publish": ("Published and signed (key {key_id})", "ok"),
    "tool.deprecated": ("Deprecated by {actor}", "neutral"),
    "tool.retired": ("Retired by {actor}", "neutral"),
    "tool.published": ("Restored by {actor}", "ok"),
    "runbook.draft.create": ("Draft created by {actor}", "info"),
    "runbook.draft.update": ("Draft saved by {actor}{unparsed}", "info"),
    "runbook.draft.discard": ("Draft discarded by {actor}", "neutral"),
    "runbook.new_version": ("New version started by {actor} from {from}", "info"),
    "runbook.in_review": ("Submitted for review by {actor}", "attention"),
    "runbook.draft": ("Changes requested by {actor}", "attention"),
    "runbook.published": ("Published by {actor}", "ok"),
    "runbook.deprecated": ("Deprecated by {actor}", "neutral"),
    "runbook.retired": ("Retired by {actor}", "neutral"),
}


def _actor(actor: str) -> str:
    if actor.startswith("ai:"):
        return f"the AI ({actor[3:]})"
    if actor == "policy":
        return "access policy"
    if actor.startswith("schedule:"):
        return f"schedule {actor[9:]}"
    if actor.startswith("node:"):
        return f"node {actor[5:]}"
    return actor


def note(entry: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(entry.get("payload") or {})
    template, tone = _TEMPLATES.get(entry["action"], (entry["action"].replace(".", " "), "neutral"))
    values = {k: v for k, v in payload.items()}
    values["actor"] = _actor(str(entry.get("actor") or ""))
    values["rule"] = f" (policy rule: {payload['rule']})" if payload.get("rule") else ""
    values["batch_n"] = int(payload.get("batch", 0)) + 1
    values["lint"] = "passed" if payload.get("ok") else f"found {(payload.get('counts') or {}).get('error', 0)} error(s)"
    values.setdefault("reason", "")
    values.setdefault("headline", "")
    values.setdefault("notes", "")
    values.setdefault("rollbacks", 0)
    values.setdefault("key_id", "")
    values.setdefault("from", "")
    values["unparsed"] = " (YAML doesn't parse yet)" if payload.get("parses") is False else ""
    try:
        text = template.format(**values)
    except (KeyError, IndexError, ValueError):
        text = template
    return {"seq": entry["seq"], "at": entry["at"], "text": text.rstrip(": "), "tone": tone,
            "actor": entry.get("actor"), "action": entry["action"]}


def run_notes(store, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    entries = store.audit_entries(subject=run_id, limit=2000)
    exec_ids = {e.id for e in store.list_executions(run_id=run_id)}
    out = [note(e) for e in entries if e["subject"] == run_id or
           (e["subject"] in exec_ids and e["action"] in ("execution.timeout", "execution.unreachable", "execution.requeue"))]
    return out[-limit:]


def runbook_notes(store, runbook_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    entries = store.audit_entries(subject=f"{runbook_id}@", limit=2000)
    return [note(e) for e in entries if str(e["subject"]).startswith(f"{runbook_id}@")
            and str(e["action"]).startswith("runbook.")][-limit:]


def tool_notes(store, tool_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    entries = store.audit_entries(subject=f"{tool_id}@", limit=2000)
    return [note(e) for e in entries if str(e["subject"]).startswith(f"{tool_id}@")][-limit:]
