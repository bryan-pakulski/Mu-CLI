"""View models for every surface (web, mobile, CLI, AI): one shape, built here."""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Mapping, Optional

from mu.design import load_vocabulary, table_style

from . import actions as act, catalog, notes, runbook, runs, scheduler
from .manifest import to_yaml
from .models import Execution, Node, Run, RunbookVersion, ToolVersion

OFFLINE_AFTER_S = 90.0
AI_LOG_TAIL = 4000


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "never"
    delta = max(0.0, now - float(ts))
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def effect_style(effect: str) -> Dict[str, str]:
    row = load_vocabulary().get("orch_effect", {}).get(effect) or {"semantic": "danger", "label": effect}
    return {"semantic": row["semantic"], "label": row["label"]}


def node_online(node: Node, now: float) -> bool:
    return bool(node.last_seen) and now - float(node.last_seen) <= OFFLINE_AFTER_S


def node_presence(store, node: Node, now: float) -> str:
    """online | connecting | offline (see ``attention.presence``: just after the master starts, a
    node it hasn't heard from yet is connecting, not offline)."""
    from .attention import master_started_at, presence
    return presence(node, now, master_started_at(store))


def facts_summary(facts: Mapping[str, Any]) -> Dict[str, Any]:
    facts = facts or {}
    disks = [d for d in facts.get("disks") or [] if isinstance(d, dict)]
    mem = facts.get("memory") or {}
    up = facts.get("uptime") or {}
    os_ = facts.get("os") or {}
    return {
        "os": os_.get("pretty_name") or os_.get("id") or "", "kernel": facts.get("kernel", ""),
        "arch": facts.get("arch", ""), "cpus": facts.get("cpu_count"), "hostname": facts.get("hostname", ""),
        "memory_gb": round(mem.get("total_bytes", 0) / 2**30, 1) if mem.get("total_bytes") else None,
        "memory_used_pct": mem.get("used_pct"),
        "max_disk_used_pct": max((d.get("used_pct") or 0 for d in disks), default=None),
        "uptime_days": round(up.get("uptime_s", 0) / 86400, 1) if up.get("uptime_s") else None,
        "load": (up.get("load") or [None])[0], "systemd": facts.get("systemd"),
        "services": len(facts.get("services") or []), "provides": facts.get("provides") or [],
        # None: the agent predates the reboot check (1.2.0).
        "reboot_required": (bool((facts.get("reboot") or {}).get("required")) if isinstance(facts.get("reboot"), dict) else None),
        "reboot_reasons": list((facts.get("reboot") or {}).get("reasons") or [])[:5] if isinstance(facts.get("reboot"), dict) else [],
    }


def node_vm(store, node: Node, now: Optional[float] = None, *, detail: bool = False) -> Dict[str, Any]:
    now = store.now() if now is None else now
    online = node_online(node, now)
    where = node_presence(store, node, now)
    status_key = where if node.status == "active" and where != "online" else node.status
    out = {
        "id": node.id, "name": node.name, "labels": node.labels, "status": node.status,
        "style": table_style("orch_node_status", status_key), "online": online, "presence": where, "kind": node.kind,
        "canary": node.canary, "last_seen": node.last_seen, "last_seen_ago": _ago(node.last_seen, now),
        "agent_version": node.agent_version, "address": node.address, "facts_at": node.facts_at,
        "summary": facts_summary(node.facts), "groups": runs.node_groups(store, node),
        "policy": node.policy, "sandbox": (node.policy or {}).get("sandbox", ""),
        "policy_errors": list((node.policy or {}).get("errors") or []),
        "actions": act.as_dicts(act.node_actions(node)), "created_at": node.created_at,
        "accepted_at": node.accepted_at, "agent": agent_status(node),
        "remote_policy": _remote_policy(store, node),
        "busy": node_busy(store, node),
    }
    if detail:
        out["facts"] = node.facts
        out["recent_executions"] = [execution_vm(e) for e in store.list_executions(node_id=node.id)[-30:]][::-1]
    return out


BUSY_STATES = ("dispatched", "running")


def node_busy(store, node: Node) -> Optional[Dict[str, Any]]:
    """What the node is doing now: executions handed to it (dispatched) or running there, and
    the queued ones waiting for its next poll. None when it's idle."""
    live = store.list_executions(node_id=node.id, states=[*BUSY_STATES, "queued"], limit=50)
    if not live:
        return None
    working = [e for e in live if e.state in BUSY_STATES]
    lead = (working or live)[0]
    run = store.get_run(lead.run_id)
    return {"state": "running" if working else "queued", "count": len(working), "queued": len(live) - len(working),
            "run_id": lead.run_id, "exec_id": lead.id, "title": run.title if run else lead.run_id,
            "tool": lead.tool_id, "step_name": lead.step_name, "check_mode": bool(lead.check_mode),
            "since": lead.started_at or lead.dispatched_at or lead.created_at}


def _remote_policy(store, node: Node) -> Dict[str, Any]:
    from .node_policy import status
    return status(store, node)


def agent_status(node: Node) -> Dict[str, Any]:
    """The node's agent against the master's: current, outdated (updatable or not, and why)."""
    from .node import AGENT_VERSION, version_tuple
    outdated = bool(node.agent_version) and version_tuple(node.agent_version) < version_tuple(AGENT_VERSION)
    block = runs.agent_update_block(node) if outdated else None
    return {"version": node.agent_version, "current": AGENT_VERSION, "outdated": outdated,
            "updatable": outdated and block is None and node.status == "active", "reason": block or ""}


def lint_summary(tv: ToolVersion) -> Dict[str, Any]:
    lint = tv.lint or {}
    return {"ok": bool(lint.get("ok")), "current": catalog.lint_current(tv), "counts": lint.get("counts") or {},
            "stale": bool(lint.get("stale"))}


def tool_vm(store, tv: ToolVersion, *, has_canary: bool = False) -> Dict[str, Any]:
    m = tv.manifest
    tests = (tv.review or {}).get("tests") or []
    return {
        "ref": tv.ref, "tool_id": tv.tool_id, "version": tv.version, "title": m.get("title") or tv.tool_id,
        "summary": m.get("summary") or "", "category": m.get("category") or "", "effect": tv.effect,
        "effect_style": effect_style(tv.effect), "status": tv.status, "style": table_style("orch_tool_status", tv.status),
        "stage": tv.stage, "origin": tv.origin, "created_by": tv.created_by, "created_at": tv.created_at,
        "updated_at": tv.updated_at, "published_at": tv.published_at, "key_id": tv.key_id,
        "run_as": (m.get("privilege") or {}).get("run_as"), "idempotent": bool(m.get("idempotent")),
        "check_mode": bool((m.get("implementation") or {}).get("check_mode")), "rollback": m.get("rollback") or "",
        "secrets": m.get("secrets") or [], "lint": lint_summary(tv),
        "tests": {"count": len(tests), "passed": sum(1 for t in tests if t.get("passed")),
                  "last": tests[-1] if tests else None},
        "session_name": tv.session_name, "intent": tv.intent,
        "actions": act.as_dicts(act.tool_actions(tv, lint_ok=catalog.lint_current(tv), has_canary=has_canary)),
    }


def _diff(a: str, b: str, name: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile=f"a/{name}", tofile=f"b/{name}"))


def tool_detail(store, tv: ToolVersion, *, has_canary: bool = False) -> Dict[str, Any]:
    out = tool_vm(store, tv, has_canary=has_canary)
    files = catalog.text_files(store, tv)
    previous = None
    for other in store.list_versions(tv.tool_id):
        if other.version < tv.version and other.status in ("published", "deprecated", "retired"):
            previous = other
    diff = ""
    if previous is not None:
        diff += _diff(to_yaml(previous.manifest), to_yaml(tv.manifest), "tool.yaml")
        prev_files = catalog.text_files(store, previous)
        for name in sorted(set(files) | set(prev_files)):
            diff += _diff(prev_files.get(name, ""), files.get(name, ""), name)
    out.update({
        "manifest": tv.manifest, "manifest_yaml": to_yaml(tv.manifest), "files": files,
        "blobs": tv.blobs, "manifest_digest": tv.manifest_digest, "signature": tv.signature[:24] + "…" if tv.signature else "",
        "lint_result": tv.lint, "review": tv.review, "diff": diff,
        "previous": previous.ref if previous else "",
        "versions": [{"ref": v.ref, "version": v.version, "status": v.status,
                      "style": table_style("orch_tool_status", v.status)} for v in store.list_versions(tv.tool_id)],
        "notes": notes.tool_notes(store, tv.tool_id),
        "usage": [run_brief(r) for r in store.list_runs(target_id=tv.tool_id, limit=10)],
    })
    return out


def runbook_vm(store, rb: RunbookVersion, *, has_canary: bool = False, detail: bool = False) -> Dict[str, Any]:
    problem_objects = catalog.runbook_problem_objects(store, rb)
    problems = [rb_problem_text(p) for p in problem_objects if p.get("severity", "error") == "error"]
    tests = (rb.review or {}).get("tests") or []
    out = {
        "ref": rb.ref, "runbook_id": rb.runbook_id, "version": rb.version, "title": rb.spec.get("title") or rb.runbook_id,
        "summary": rb.spec.get("summary") or "", "status": rb.status, "style": table_style("orch_tool_status", rb.status),
        "effect": rb.spec.get("effect", "read_only"), "effect_style": effect_style(rb.spec.get("effect", "read_only")),
        "steps": rb.spec.get("steps") or [], "inputs": rb.spec.get("inputs") or {}, "spec": rb.spec,
        "spec_yaml": to_yaml(rb.spec), "problems": problems,
        "warnings": [rb_problem_text(p) for p in problem_objects if p.get("severity") == "warning"],
        "digest": rb.digest, "parses": not rb.source, "created_by": rb.created_by, "created_at": rb.created_at,
        "updated_at": rb.updated_at, "published_at": rb.published_at,
        "tests": {"count": len(tests), "passed": sum(1 for t in tests if t.get("passed")),
                  "last": tests[-1] if tests else None},
        "actions": act.as_dicts(catalog.runbook_action_list(store, rb, has_canary=has_canary)),
    }
    if detail:
        positions = runbook.yaml_positions(rb.source or out["spec_yaml"])
        previous = None
        versions = store.list_runbook_versions(rb.runbook_id)
        for other in versions:
            if other.version < rb.version and other.status in ("published", "deprecated", "retired"):
                previous = other
        out.update({
            "source": rb.source, "problem_details": catalog.runbook_problem_objects(store, rb, positions=positions),
            "review": rb.review or {}, "previous": previous.ref if previous else "",
            "diff": _diff(to_yaml(previous.spec), to_yaml(rb.spec), "runbook.yaml") if previous else "",
            "versions": [{"ref": v.ref, "version": v.version, "status": v.status,
                          "style": table_style("orch_tool_status", v.status)} for v in versions],
            "notes": notes.runbook_notes(store, rb.runbook_id),
            "usage": [run_brief(r) for r in store.list_runs(target_id=rb.runbook_id, limit=10)],
        })
    return out


def rb_problem_text(p: Mapping[str, Any]) -> str:
    return runbook.problem_text(p)


OPTION_PURPOSES = ("run", "step", "test", "schedule")


def catalog_options(store, purpose: str = "run") -> Dict[str, Any]:
    """Compact picker options (plan runbook_editor §4.1): one entry per tool with the versions that
    suit ``purpose`` — runs, runbook steps and schedules get dispatchable versions (deprecated ones
    are listed, flagged, so a pinned one stays visible), tests get drafts and versions in review."""
    if purpose not in OPTION_PURPOSES:
        raise ValueError(f"purpose must be one of {', '.join(OPTION_PURPOSES)}")
    test = purpose == "test"
    wanted = ("draft", "testing", "in_review") if test else ("published", "deprecated")
    by_tool: Dict[str, List[ToolVersion]] = {}
    for tv in store.list_versions():
        by_tool.setdefault(tv.tool_id, []).append(tv)
    tools = []
    for tool_id, versions in sorted(by_tool.items()):
        published = [v for v in versions if v.status == "published"]
        latest = published[-1].version if published else None
        chosen = [v for v in versions if v.status in wanted]
        if not chosen:
            continue
        head = (published[-1] if published and not test else chosen[-1])
        m = head.manifest
        tools.append({
            "kind": "tool", "id": tool_id, "title": m.get("title") or tool_id, "summary": m.get("summary") or "",
            "category": m.get("category") or "other", "effect": head.effect, "effect_style": effect_style(head.effect),
            "latest": latest, "default": head.ref,
            "versions": [{"ref": v.ref, "version": v.version, "status": v.status,
                          "style": table_style("orch_tool_status", v.status), "effect": v.effect,
                          "effect_style": effect_style(v.effect), "title": v.manifest.get("title") or tool_id,
                          "inputs": v.manifest.get("inputs") or {}, "outputs": v.manifest.get("outputs") or {},
                          "check_mode": bool((v.manifest.get("implementation") or {}).get("check_mode")),
                          "rollback": v.manifest.get("rollback") or "",
                          "preconditions": list(v.manifest.get("preconditions") or []),
                          "dispatchable": v.status == "published"} for v in reversed(chosen)],
        })
    runbooks = []
    if purpose != "step":
        wanted_rb = ("draft", "in_review") if test else ("published",)
        by_rb: Dict[str, List[RunbookVersion]] = {}
        for rb in store.list_runbook_versions():
            by_rb.setdefault(rb.runbook_id, []).append(rb)
        for rid, versions in sorted(by_rb.items()):
            chosen = [v for v in versions if v.status in wanted_rb]
            if not chosen:
                continue
            head = chosen[-1]
            effect = head.spec.get("effect", "read_only")
            runbooks.append({
                "kind": "runbook", "id": rid, "title": head.spec.get("title") or rid,
                "summary": head.spec.get("summary") or "", "category": "runbook", "effect": effect,
                "effect_style": effect_style(effect), "latest": head.version if head.status == "published" else None,
                "default": head.ref,
                "versions": [{"ref": v.ref, "version": v.version, "status": v.status,
                              "style": table_style("orch_tool_status", v.status),
                              "effect": v.spec.get("effect", "read_only"),
                              "effect_style": effect_style(v.spec.get("effect", "read_only")),
                              "title": v.spec.get("title") or rid, "inputs": v.spec.get("inputs") or {},
                              "outputs": {}, "check_mode": True, "rollback": "", "preconditions": [],
                              "steps": len(v.spec.get("steps") or []), "dispatchable": v.status == "published"}
                             for v in reversed(chosen)],
            })
    return {"purpose": purpose, "tools": tools, "runbooks": runbooks}


def label_index(store) -> Dict[str, Any]:
    """Labels in use with node counts, groups with member counts and a compact node list
    (revoked nodes excluded) for the selector builder and node pickers."""
    now = store.now()
    nodes = [n for n in store.list_nodes() if n.status != "revoked"]
    labels: Dict[str, Dict[str, int]] = {}
    for n in nodes:
        for k, v in (n.labels or {}).items():
            labels.setdefault(k, {})
            labels[k][v] = labels[k].get(v, 0) + 1
    groups = []
    from . import selectors
    for g in store.list_groups():
        groups.append(dict(g, count=sum(1 for n in nodes if selectors.in_group(g, n.id, n.labels))))
    return {"labels": {k: dict(sorted(v.items())) for k, v in sorted(labels.items())}, "groups": groups,
            "nodes": [{"id": n.id, "name": n.name, "status": n.status, "online": node_online(n, now),
                       "presence": node_presence(store, n, now),
                       "canary": runs.is_canary(n), "kind": n.kind, "labels": n.labels,
                       "style": table_style("orch_node_status", node_presence(store, n, now) if n.status == "active"
                                            and not node_online(n, now) else n.status)} for n in nodes]}


def run_brief(run: Run) -> Dict[str, Any]:
    decision = run.decision or {}
    return {
        "id": run.id, "title": run.title, "kind": run.kind,
        "target": run.target_ref if run.target_version else run.target_id, "state": run.state,
        "style": table_style("orch_run_state", run.state), "proposed_by": run.proposed_by,
        "approved_by": run.approved_by, "created_at": run.created_at, "started_at": run.started_at,
        "finished_at": run.finished_at, "check_mode": run.check_mode, "nodes": len(run.nodes or []),
        "summary": run.summary or {}, "paused_reason": run.paused_reason, "reason": run.reason,
        "effect": (run.plan or {}).get("effect", "read_only"),
        "effect_style": effect_style((run.plan or {}).get("effect", "read_only")),
        "decision": {"level": decision.get("level"), "summary": decision.get("summary"), "rule": decision.get("rule")},
        "blast_radius": (run.plan or {}).get("blast_radius", ""),
    }


GRID_LOG_TAIL = 8000


def execution_vm(ex: Execution, *, for_ai: bool = False, brief: bool = False) -> Dict[str, Any]:
    """``for_ai``: outputs and a capped log tail only. ``brief``: the run grid's view, whose log is
    capped too (the full log is ``GET /api/orchestration/executions/{id}``)."""
    out = {
        "id": ex.id, "run_id": ex.run_id, "node_id": ex.node_id,
        "tool": f"{ex.tool_id}@{ex.version}" if ex.version else ex.tool_id,
        "step": ex.step, "step_name": ex.step_name, "batch": ex.batch, "state": ex.state,
        "style": table_style("orch_exec_state", ex.state), "exit_code": ex.exit_code, "reason": ex.reason,
        "duration_s": ex.duration_s, "attempts": ex.attempts, "check_mode": ex.check_mode,
        "rollback_of": ex.rollback_of, "output": ex.output, "output_errors": ex.output_errors,
        "created_at": ex.created_at, "dispatched_at": ex.dispatched_at, "started_at": ex.started_at,
        "finished_at": ex.finished_at, "redactions": ex.redactions,
    }
    if for_ai:
        # The AI sees declared outputs and a capped, already-redacted log tail — never more.
        log = ex.log or ""
        out["log_tail"] = log[-AI_LOG_TAIL:]
        out["log_truncated"] = len(log) > AI_LOG_TAIL or ex.log_truncated
        out.pop("params", None)
    else:
        log = ex.log or ""
        cap = GRID_LOG_TAIL if brief else None
        out["log"] = log[-cap:] if cap else log
        out["log_truncated"] = ex.log_truncated or bool(cap and len(log) > cap)
        out["params"] = ex.params
    return out


def run_detail(store, run: Run) -> Dict[str, Any]:
    execs = store.list_executions(run_id=run.id)
    plan = run.plan or {}
    steps = list(plan.get("steps") or [])
    nodes = {n.id: n for n in store.list_nodes()}
    by_node: Dict[str, List[Execution]] = {}
    for ex in execs:
        by_node.setdefault(ex.node_id, []).append(ex)
    batch_of = {nid: i for i, batch in enumerate(plan.get("batches") or []) for nid in batch}
    grid = []
    now = store.now()
    for node_id in run.nodes or []:
        node = nodes.get(node_id)
        node_execs = by_node.get(node_id, [])
        cells = []
        for i, step in enumerate(steps):
            mine = [e for e in node_execs if e.step == i and not e.rollback_of]
            latest = max(mine, key=lambda e: e.created_at) if mine else None
            cells.append({"step": i, "name": step.get("name"), "exec": execution_vm(latest, brief=True) if latest else None,
                          "style": table_style("orch_exec_state", latest.state) if latest else table_style("orch_exec_state", "queued") | {"label": "not started", "status": "pending"},
                          "attempts": len(mine)})
        rollbacks = [execution_vm(e, brief=True) for e in node_execs if e.rollback_of]
        progress = scheduler.node_progress(steps, node_execs)
        grid.append({"node_id": node_id, "name": node.name if node else node_id, "labels": node.labels if node else {},
                     "online": node_online(node, now) if node else False, "batch": batch_of.get(node_id, 0),
                     "cells": cells, "rollbacks": rollbacks, "progress": progress.status, "failed": progress.failed})
    has_rollback = bool(scheduler.rollback_targets(steps, execs)) and run.state != "proposed"
    out = run_brief(run)
    out.update({
        "params": run.params, "selector": run.selector, "strategy": run.strategy, "excluded": run.excluded,
        "steps": steps, "batches": plan.get("batches") or [], "warnings": plan.get("warnings") or [],
        "rollback_available": plan.get("rollback_available"), "decision_full": run.decision,
        "grid": grid, "notes": notes.run_notes(store, run.id),
        "actions": act.as_dicts(act.run_actions(run, has_rollback=has_rollback)),
        "secrets": plan.get("secrets") or [],
    })
    return out


def overview(orch) -> Dict[str, Any]:
    store = orch.store
    now = store.now()
    nodes = store.list_nodes()
    counts: Dict[str, int] = {}
    for n in nodes:
        where = node_presence(store, n, now)
        key = where if n.status == "active" and where != "online" else n.status
        counts[key] = counts.get(key, 0) + 1
    tools = store.list_versions(statuses=["published"])
    recent = store.list_runs(limit=12)
    attention = orch.attention()
    unreachable = [execution_vm(e) for e in store.list_executions(states=["unreachable"], limit=50)
                   if (store.get_run(e.run_id) or Run(id="")).state in ("running", "paused")]
    return {
        "nodes": {"total": len(nodes), "by_status": counts},
        "catalog": {"published": len({t.tool_id for t in tools}),
                    "drafts": len(store.list_versions(statuses=["draft", "testing"])),
                    "in_review": len(store.list_versions(statuses=["in_review"]))},
        "attention": attention, "recent_runs": [run_brief(r) for r in recent],
        "approvals": [run_brief(r) for r in store.list_runs(states=["proposed"], limit=50)],
        "paused": [run_brief(r) for r in store.list_runs(states=["paused"], limit=50)],
        "unreachable": unreachable[:20], "dispatch_paused": orch.dispatch_paused(),
        "network": orch.network(), "time": now,
        # Nodes that report they need a reboot (agent ≥ 1.2.0; mu/orchestration/maintenance.py).
        "reboot": _reboot_summary(store),
    }


def _reboot_summary(store) -> Dict[str, Any]:
    from .maintenance import fleet_reboot_summary
    return fleet_reboot_summary(store)


# --------------------------------------------------------------------- the node dashboard
#
# Overall health of the fleet, or of one group: each node's health from its latest facts and
# presence, the reasons in words, resource figures, live work and the last day's run outcomes.
# The groups list beside it (web left-hand pane, phone, `mucli orch dashboard`) carries each
# group's health counts so a problem shows before the group is opened.

DISK_WARN_PCT, DISK_CRIT_PCT = 85, 95
MEM_WARN_PCT, MEM_CRIT_PCT = 90, 95
LOAD_WARN_PER_CPU = 1.5
FACTS_STALE_S = 24 * 3600
HEALTH_ORDER = ("critical", "warning", "pending", "ok")
RECENT_WINDOW_S = 24 * 3600
FAILED_EXEC_STATES = ("failed", "timed_out", "unreachable")


def node_health(store, node: Node, now: float) -> Dict[str, Any]:
    """ok | warning | critical | pending, with the reasons in words (worst first)."""
    critical: List[str] = []
    warning: List[str] = []
    if node.status == "pending":
        return {"level": "pending", "reasons": ["waiting to be accepted"], "style": table_style("orch_node_health", "pending")}
    if node.status in ("quarantined",):
        critical.append("quarantined")
    where = node_presence(store, node, now)
    if node.status == "active" and where == "offline":
        critical.append(f"offline (last seen {_ago(node.last_seen, now)})")
    s = facts_summary(node.facts)
    disk, mem = s.get("max_disk_used_pct"), s.get("memory_used_pct")
    if isinstance(disk, (int, float)):
        if disk >= DISK_CRIT_PCT:
            critical.append(f"disk {disk:.0f}% full")
        elif disk >= DISK_WARN_PCT:
            warning.append(f"disk {disk:.0f}% full")
    if isinstance(mem, (int, float)):
        if mem >= MEM_CRIT_PCT:
            critical.append(f"memory {mem:.0f}% used")
        elif mem >= MEM_WARN_PCT:
            warning.append(f"memory {mem:.0f}% used")
    load, cpus = s.get("load"), s.get("cpus")
    if isinstance(load, (int, float)) and isinstance(cpus, int) and cpus > 0 and load / cpus > LOAD_WARN_PER_CPU:
        warning.append(f"load {load:.1f} on {cpus} CPUs")
    if s.get("reboot_required"):
        warning.append("needs a reboot")
    if agent_status(node).get("outdated"):
        warning.append(f"agent {node.agent_version} is outdated")
    if (node.policy or {}).get("errors"):
        warning.append("local policy has errors")
    if node.status == "active" and node.facts_at and now - node.facts_at > FACTS_STALE_S:
        warning.append(f"facts are {_ago(node.facts_at, now)} old")
    level = "critical" if critical else "warning" if warning else "ok"
    return {"level": level, "reasons": critical + warning, "style": table_style("orch_node_health", level)}


def _avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _health_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {level: 0 for level in HEALTH_ORDER}
    for r in rows:
        counts[r["health"]["level"]] += 1
    return counts


def fleet_dashboard(store, group: Optional[str] = None) -> Dict[str, Any]:
    """The node dashboard: ``group`` None = the whole fleet, "" or "-" = ungrouped nodes,
    otherwise one group's nodes. Raises KeyError for an unknown group."""
    from . import selectors
    now = store.now()
    groups = store.list_groups()
    every = [n for n in store.list_nodes() if n.status != "revoked"]
    membership = {n.id: [g["name"] for g in groups if selectors.in_group(g, n.id, n.labels)] for n in every}
    if group is None:
        scope = every
    elif group in ("", "-"):
        scope = [n for n in every if not membership[n.id]]
    else:
        chosen = next((g for g in groups if g["name"] == group), None)
        if chosen is None:
            raise KeyError(group)
        scope = [n for n in every if group in membership[n.id]]

    rows = []
    for n in scope:
        where = node_presence(store, n, now)
        s = facts_summary(n.facts)
        status_key = where if n.status == "active" and where != "online" else n.status
        rows.append({
            "id": n.id, "name": n.name, "status": n.status, "presence": where, "canary": n.canary,
            "style": table_style("orch_node_status", status_key), "labels": n.labels,
            "groups": membership[n.id], "health": node_health(store, n, now), "busy": node_busy(store, n),
            "last_seen_ago": _ago(n.last_seen, now), "agent_version": n.agent_version,
            "memory_used_pct": s.get("memory_used_pct"), "max_disk_used_pct": s.get("max_disk_used_pct"),
            "load": s.get("load"), "cpus": s.get("cpus"), "os": s.get("os"), "uptime_days": s.get("uptime_days"),
        })
    rows.sort(key=lambda r: (HEALTH_ORDER.index(r["health"]["level"]), r["name"].lower()))

    ids = {n.id for n in scope}
    recent = [e for e in store.list_executions(limit=5000) if e.node_id in ids and (e.created_at or 0) >= now - RECENT_WINDOW_S]
    finished = [e for e in recent if e.state in ("succeeded", *FAILED_EXEC_STATES)]
    run_ids = []
    for e in sorted(recent, key=lambda e: e.created_at or 0, reverse=True):
        if e.run_id not in run_ids:
            run_ids.append(e.run_id)
    recent_runs = []
    for rid in run_ids[:8]:
        run = store.get_run(rid)
        if run is not None:
            recent_runs.append({"id": run.id, "title": run.title, "state": run.state,
                                "style": table_style("orch_run_state", run.state), "created_ago": _ago(run.created_at, now)})

    mems = [r["memory_used_pct"] for r in rows if isinstance(r["memory_used_pct"], (int, float))]
    disks = [r["max_disk_used_pct"] for r in rows if isinstance(r["max_disk_used_pct"], (int, float))]
    loads = [r["load"] / r["cpus"] for r in rows if isinstance(r["load"], (int, float)) and r["cpus"]]
    presence = {"online": 0, "connecting": 0, "offline": 0}
    for r in rows:
        if r["status"] == "active":
            presence[r["presence"]] = presence.get(r["presence"], 0) + 1

    def summary(group_rows):
        return {"nodes": len(group_rows), "health": _health_counts(group_rows),
                "online": sum(1 for r in group_rows if r["status"] == "active" and r["presence"] == "online")}

    all_rows = rows if group is None else None
    if all_rows is None:   # group summaries need every node's health, not just the scope's
        all_rows = []
        for n in every:
            all_rows.append({"id": n.id, "status": n.status, "presence": node_presence(store, n, now),
                             "health": node_health(store, n, now)})
    by_id = {r["id"]: r for r in all_rows}
    group_list = []
    for g in groups:
        members = [by_id[n.id] for n in every if g["name"] in membership[n.id]]
        group_list.append({"name": g["name"], "description": g["description"], "selector": g["selector"],
                           "members": g["members"], **summary(members)})
    ungrouped = [by_id[n.id] for n in every if not membership[n.id]]

    return {
        "scope": {"group": group, "label": "All nodes" if group is None else "Ungrouped" if group in ("", "-") else group},
        "totals": {"nodes": len(rows), "presence": presence, "health": _health_counts(rows),
                   "busy": sum(1 for r in rows if r["busy"]), "pending": sum(1 for r in rows if r["status"] == "pending")},
        "resources": {"memory_used_avg": _avg(mems), "memory_used_max": max(mems, default=None),
                      "disk_used_avg": _avg(disks), "disk_used_max": max(disks, default=None),
                      "load_per_cpu_avg": _avg(loads), "load_per_cpu_max": round(max(loads), 2) if loads else None},
        "runs_24h": {"executions": len(recent), "succeeded": sum(1 for e in finished if e.state == "succeeded"),
                     "failed": sum(1 for e in finished if e.state in FAILED_EXEC_STATES), "recent": recent_runs},
        "nodes": rows,
        "groups": group_list,
        "fleet": {"nodes": len(every), **summary(all_rows)},
        "ungrouped": summary(ungrouped),
        "thresholds": {"disk_warn": DISK_WARN_PCT, "disk_crit": DISK_CRIT_PCT, "memory_warn": MEM_WARN_PCT,
                       "memory_crit": MEM_CRIT_PCT, "load_per_cpu_warn": LOAD_WARN_PER_CPU},
    }
