"""Read-only fleet tools for every interactive session (chat, workspace, container, orchestration).

The ``orch_*`` tools design, test and propose — they stay in ``orchestration`` sessions, where the
operator expects fleet changes to be discussed. These ``fleet_*`` tools only *read* what the fleet
already reported, so any session can answer "which nodes are low on disk?" or "which certificates
expire this month?" from the same tables the GUI shows (``mu/orchestration/results.py``):

* ``fleet_overview`` — what needs you, every node (online, disk/memory, agent), recent runs and
  the tools that have results to read.
* ``fleet_results`` — a tool's latest results across the fleet, or one run's results, as a
  compact table: choose the view, sort, filter rows with simple conditions, cap the row count.

They never mutate, never start orchestration on a machine that hasn't used it (the store must
already exist — ``attention.peek``), and return only what the master stored: outputs that were
redacted on the node and again on the master, never logs, envelopes or secret values. Jobs run in
network-isolated containers without the store, so these tools report "not set up" there.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from mu.tools import tool

MAX_ROWS = 200
DEFAULT_ROWS = 40
_COND = re.compile(r"^\s*([A-Za-z0-9_ %.\-]+?)\s*(>=|<=|!=|=|>|<|~)\s*(.+?)\s*$")


def _ok(message: str, data: Any) -> Dict[str, Any]:
    return {"ok": True, "message": message, "data": data}


def _error(message: str, code: str = "fleet_error") -> Dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message, "data": {}}


def _store():
    """The orchestration store, or None when orchestration was never used here (never creates it)."""
    from .attention import peek
    if peek(max_age_s=5.0) is None:
        return None
    from . import agent_tools
    return agent_tools._orch().store


_NOT_SET_UP = ("Orchestration isn't set up on this machine, so there are no fleet results. "
               "The operator can set it up from the GUI's Orchestration page.")


def _match_column(table: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    wanted = name.strip().lower()
    if wanted in ("node", "state"):
        return {"key": f"__{wanted}", "label": wanted.capitalize(), "kind": "string"}
    for col in table["columns"]:
        if wanted in (str(col["key"]).lower(), str(col["label"]).lower()):
            return col
    return None


def _value(row: Dict[str, Any], col: Dict[str, Any]) -> Any:
    if col["key"] == "__node":
        return row["node"]
    if col["key"] == "__state":
        return row["state"]
    return row["values"].get(col["key"])


def _passes(value: Any, op: str, raw: str) -> bool:
    if value is None:
        return op == "!="
    if op == "~":
        return raw.lower() in str(value).lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            target = float(raw.rstrip("%"))
        except ValueError:
            return False
        return {">": value > target, ">=": value >= target, "<": value < target, "<=": value <= target,
                "=": value == target, "!=": value != target}[op]
    text, target = str(value).lower(), raw.strip("'\"").lower()
    return {"=": text == target, "!=": text != target, ">": text > target, ">=": text >= target,
            "<": text < target, "<=": text <= target}[op]


def compact(table: Dict[str, Any], *, sort: str = "", descending: Optional[bool] = None,
            where: Optional[List[str]] = None, limit: int = DEFAULT_ROWS) -> Dict[str, Any]:
    """A table as columns + row lists for a model: node, state, then the table's columns."""
    rows = list(table["rows"])
    problems = []
    for cond in where or []:
        m = _COND.match(str(cond))
        col = _match_column(table, m.group(1)) if m else None
        if not m or col is None:
            problems.append(f"ignored condition {cond!r} (use 'column op value', op one of > >= < <= = != ~; "
                            f"columns: node, state, {', '.join(c['label'] for c in table['columns'])})")
            continue
        rows = [r for r in rows if _passes(_value(r, col), m.group(2), m.group(3))]
    if sort:
        col = _match_column(table, sort)
        if col is None:
            problems.append(f"unknown sort column {sort!r}")
        else:
            desc = True if descending is None and col.get("kind") in ("integer", "number") else bool(descending)
            present = [r for r in rows if _value(r, col) is not None]
            missing = [r for r in rows if _value(r, col) is None]
            present.sort(key=lambda r: (str(_value(r, col)).lower() if not isinstance(_value(r, col), (int, float))
                                        else _value(r, col)), reverse=desc)
            rows = present + missing
    limit = max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
    units = {c["label"]: c["format"] for c in table["columns"] if c["format"] in ("bytes", "percent", "days", "seconds")}
    return {
        "view": table["key"], "label": table["label"], "per": table["per"],
        "sorted_by": sort or next((c["label"] for c in table["columns"] if c["key"] == table.get("metric")), None),
        "columns": ["node", "state"] + [c["label"] for c in table["columns"]],
        "units": units,
        "rows": [[r["node"], r["state"]] + [r["values"].get(c["key"]) for c in table["columns"]] for r in rows[:limit]],
        "matched": len(rows), "total": len(table["rows"]), "truncated": len(rows) > limit,
        **({"notes": problems} if problems else {}),
    }


@tool(name="fleet_overview", group="fleet", phase="fleet", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Read-only snapshot of the managed fleet: what needs the operator, every node (online, "
                  "labels, groups, health (ok/warning/critical) and why, disk and memory use, agent version, whether it needs a reboot "
                  "and why), node groups with their health counts, recent runs, and the tools whose results "
                  "fleet_results can read. Use it before answering questions about the machines.",
      parameters={"type": "object", "properties": {}})
def fleet_overview(args, context):
    try:
        store = _store()
        if store is None:
            return _error(_NOT_SET_UP, "not_set_up")
        from . import vm
        from .attention import counts, describe
        att = counts(store)
        board = vm.fleet_dashboard(store)
        health = {n["id"]: n for n in board["nodes"]}
        nodes = []
        for node in store.list_nodes():
            view = vm.node_vm(store, node)
            summary = view.get("summary") or {}
            nodes.append({"name": node.name, "id": node.id, "status": node.status, "online": view.get("online"),
                          "labels": node.labels, "os": summary.get("os"), "max_disk_used_pct": summary.get("max_disk_used_pct"),
                          "memory_used_pct": summary.get("memory_used_pct"), "uptime_days": summary.get("uptime_days"),
                          "agent": view.get("agent_version"), "last_seen": view.get("last_seen_ago"),
                          "reboot_required": summary.get("reboot_required"),
                          "reboot_reasons": summary.get("reboot_reasons") or [],
                          "groups": view.get("groups") or [],
                          "health": (health.get(node.id) or {}).get("health", {}).get("level"),
                          "health_reasons": (health.get(node.id) or {}).get("health", {}).get("reasons") or []})
        runs = [{k: b.get(k) for k in ("id", "title", "state", "target", "effect", "nodes")}
                for b in (vm.run_brief(r) for r in store.list_runs(limit=10))]
        tools: Dict[str, int] = {}
        for ex in store.list_executions(states=["succeeded"], limit=5000):
            if isinstance(ex.output, dict) and not getattr(ex, "check_mode", False):
                tools[ex.tool_id] = tools.get(ex.tool_id, 0) + 1
        return _ok(describe(att) or "Nothing needs the operator.", {
            "needs_you": {k: att[k] for k in ("approvals", "paused_runs", "reviews", "pending_nodes", "offline_nodes")},
            "dispatch_paused": att.get("dispatch_paused", False), "nodes": nodes, "recent_runs": runs,
            "needs_reboot": [n["name"] for n in nodes if n.get("reboot_required")],
            "groups": [{k: g[k] for k in ("name", "description", "nodes", "online", "health")} for g in board["groups"]],
            "ungrouped": board["ungrouped"],
            "tools_with_results": [{"tool": t, "results": n} for t, n in sorted(tools.items(), key=lambda kv: -kv[1])],
        })
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc))


@tool(name="fleet_results", group="fleet", phase="fleet", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Read fleet results as a table: a tool's latest output on every node (tool=, e.g. "
                  "sys.disk-usage) or one run's output (run_id=). Views: 'summary' (one row per node) or a "
                  "list in the output (e.g. 'disks', 'certs': one row per item). Sort by any column and filter "
                  "with conditions like 'Used % > 80', 'Days left < 30', 'node ~ web', 'state != succeeded'. "
                  "Values are the raw numbers (bytes, percentages, days). Read-only.",
      parameters={"type": "object", "properties": {
          "tool": {"type": "string", "description": "Tool id (latest result per node, any version)"},
          "run_id": {"type": "string", "description": "A run id instead of a tool"},
          "step": {"type": "integer", "description": "Runbook step for run results (default: the last step)"},
          "view": {"type": "string", "description": "summary (default) or a list view name such as disks"},
          "sort": {"type": "string", "description": "Column to sort by (default: the view's key metric)"},
          "descending": {"type": "boolean"},
          "where": {"type": "array", "items": {"type": "string"}, "description": "Row conditions, all must hold"},
          "limit": {"type": "integer", "default": DEFAULT_ROWS, "description": f"Rows to return (max {MAX_ROWS})"}}})
def fleet_results(args, context):
    try:
        store = _store()
        if store is None:
            return _error(_NOT_SET_UP, "not_set_up")
        from . import results
        tool_id, run_id = str(args.get("tool") or "").strip(), str(args.get("run_id") or "").strip()
        if not tool_id and not run_id:
            return _error("Pass tool= (latest results per node) or run_id= (one run). fleet_overview lists the "
                          "tools that have results.", "bad_arguments")
        if run_id:
            run = store.get_run(run_id)
            if run is None:
                return _error(f"Unknown run {run_id}.", "not_found")
            steps = results.run_results(store, run)["steps"]
            if not steps:
                return _ok(f"{run_id} has no results yet ({run.state}).", {"run_id": run_id, "state": run.state, "views": []})
            wanted = args.get("step")
            entry = next((s for s in steps if wanted is not None and s["step"] == int(wanted)), steps[-1])
            tables, source = entry["tables"], {"run_id": run_id, "state": run.state, "tool": entry["tool"], "step": entry["step"]}
        else:
            if store.latest_version(tool_id) is None:
                return _error(f"Unknown tool {tool_id}. fleet_overview lists the tools that have results.", "not_found")
            data = results.latest_results(store, tool_id)
            tables, source = data["tables"], {"tool": tool_id, "nodes": data["nodes"], "oldest_result_ts": data["oldest"]}
        if not tables or not any(t["rows"] for t in tables):
            return _ok("No results yet.", dict(source, views=[]))
        view = str(args.get("view") or "summary")
        table = next((t for t in tables if t["key"] == view or t["label"].lower() == view.lower()), None)
        if table is None:
            return _error(f"Unknown view {view!r}; available: {', '.join(t['key'] for t in tables)}.", "bad_arguments")
        out = compact(table, sort=str(args.get("sort") or ""), descending=args.get("descending"),
                      where=args.get("where") or [], limit=int(args.get("limit") or DEFAULT_ROWS))
        out.update(source, views=[t["key"] for t in tables])
        shown = len(out["rows"])
        return _ok(f"{table['label']}: {shown} of {out['matched']} row(s)" + (f" (filtered from {out['total']})" if out["matched"] != out["total"] else ""), out)
    except Exception as exc:  # noqa: BLE001
        return _error(str(exc))
