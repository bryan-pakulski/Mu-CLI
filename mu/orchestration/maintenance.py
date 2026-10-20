"""Node maintenance: the node page's control panel (reboot, security updates, full upgrade,
restart a service, check for updates, refresh facts) without writing custom tools.

Every action is an ordinary run of a built-in starter tool (``os.*`` / ``sys.pending-updates``)
proposed by the operator through ``commands.dispatch``: the access policy decides the
confirmation (mutating → confirm), the node's own ``policy.yaml`` still refuses what it doesn't
allow, signing and audit are unchanged, and check mode (a dry run) is offered for every change.
Nothing here bypasses a gate; the panel only makes the right run one click away and says up
front, from the node's reported policy and facts, why an action can't run there.

``reboot`` facts come from the node agent (``collectors.reboot_status``, agent ≥ 1.2.0).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

from .models import Node

# key → (tool id, label, verb for a run title, default params, rolling: one node at a time)
ACTIONS: List[Dict[str, Any]] = [
    {"key": "check_updates", "tool": "sys.pending-updates", "label": "Check for updates",
     "title": "Check for updates", "params": {}, "danger": False,
     "help": "Count pending package updates (read-only)."},
    {"key": "security_updates", "tool": "os.security-updates", "label": "Install security updates",
     "title": "Security updates", "params": {"refresh": True}, "danger": False,
     "help": "Install packages with security updates; refused packages are skipped and reported."},
    {"key": "full_upgrade", "tool": "os.full-upgrade", "label": "Upgrade all packages",
     "title": "Full upgrade", "params": {"allow_removals": False}, "danger": False,
     "help": "Upgrade every package. No removals unless allowed."},
    {"key": "reboot", "tool": "os.reboot", "label": "Reboot", "title": "Reboot",
     "params": {"only_if_required": False, "delay_s": 30}, "danger": True, "rolling": True,
     "help": "Schedule a reboot 30 seconds out; the node comes back on its own."},
    {"key": "service", "tool": "os.service", "label": "Restart a service", "title": "Service",
     "params": {"action": "restart"}, "danger": False, "needs": "unit",
     "help": "Restart, reload, start or stop one systemd unit."},
]
BY_KEY = {a["key"]: a for a in ACTIONS}
TOOL_IDS = [a["tool"] for a in ACTIONS]


def reboot_info(node: Node) -> Dict[str, Any]:
    """{required: bool|None, reasons, packages, since} — None when the agent doesn't report it."""
    data = (node.facts or {}).get("reboot")
    if not isinstance(data, Mapping):
        return {"required": None, "reasons": [], "packages": [], "since": None}
    return {"required": bool(data.get("required")), "reasons": list(data.get("reasons") or [])[:10],
            "packages": list(data.get("packages") or [])[:50], "since": data.get("since")}


def _policy(node: Node):
    from .node.policy import RUNTIMES, NodePolicy
    raw = dict(node.policy or {})
    if not raw:
        return None
    fields = NodePolicy.__dataclass_fields__
    kwargs: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in fields:
            continue
        default = fields[key].default
        kwargs[key] = tuple(value) if isinstance(default, tuple) and isinstance(value, (list, tuple)) else value
    kwargs.setdefault("allowed_runtimes", RUNTIMES)
    try:
        return NodePolicy(**kwargs)
    except TypeError:
        return None


def blocked_reason(store, node: Node, tool_id: str, *, check_mode: bool = False) -> Optional[str]:
    """Why ``tool_id`` can't run on ``node`` now (None when it can), from what the node reported."""
    from . import runs
    from .node.policy import decide
    tv = store.latest_version(tool_id, published_only=True)
    if tv is None:
        return "not installed — install the built-in tools (Settings › Starter pack)"
    if node.status != "active":
        return f"the node is {node.status}"
    mismatch = runs.target_mismatch(tv.manifest, node)
    if mismatch:
        return mismatch
    policy = _policy(node)
    if policy is not None:
        verdict = decide({"check_mode": check_mode}, tv.manifest, policy, now=time.time())
        if not verdict.allowed:
            hint = ""
            if verdict.rule == "max_effect":
                hint = " — allow it in /etc/mucli-node/policy.yaml (max_effect: mutating)"
            return f"this node refuses it: {verdict.reason}{hint}"
    return None


def _latest_output(store, node_id: str, tool_id: str) -> Optional[Dict[str, Any]]:
    best = None
    for ex in store.list_executions(node_id=node_id, states=["succeeded"]):
        if ex.tool_id == tool_id and not getattr(ex, "check_mode", False) and isinstance(ex.output, dict):
            if best is None or (ex.finished_at or 0) >= (best.finished_at or 0):
                best = ex
    if best is None:
        return None
    return {"output": best.output, "at": best.finished_at, "run_id": best.run_id, "exec_id": best.id}


def node_panel(store, node: Node) -> Dict[str, Any]:
    """Everything the node page's Maintenance panel shows."""
    from . import vm
    actions = []
    for spec in ACTIONS:
        tv = store.latest_version(spec["tool"], published_only=True)
        reason = blocked_reason(store, node, spec["tool"])
        check_reason = blocked_reason(store, node, spec["tool"], check_mode=True) if tv else reason
        actions.append({
            "key": spec["key"], "label": spec["label"], "help": spec["help"], "danger": spec["danger"],
            "tool": tv.ref if tv else spec["tool"], "installed": tv is not None,
            "effect": tv.effect if tv else None, "available": reason is None, "reason": reason or "",
            "check_available": tv is not None and bool((tv.manifest.get("implementation") or {}).get("check_mode"))
            and check_reason is None and (tv.effect != "read_only"),
            "needs": spec.get("needs") or "", "params": dict(spec["params"]),
        })
    pending = _latest_output(store, node.id, "sys.pending-updates")
    recent = []
    for ex in store.list_executions(node_id=node.id)[-200:][::-1]:
        if ex.tool_id in TOOL_IDS and len(recent) < 8:
            view = vm.execution_vm(ex)
            recent.append({"run_id": ex.run_id, "exec_id": ex.id, "tool": ex.tool_id, "state": ex.state,
                           "style": view.get("style"), "check_mode": ex.check_mode, "finished_at": ex.finished_at,
                           "created_at": ex.created_at})
    summary = vm.facts_summary(node.facts)
    return {
        "open_runs": open_runs(store, node),
        "node_id": node.id, "name": node.name,
        "reboot": reboot_info(node),
        "uptime_days": summary.get("uptime_days"), "kernel": summary.get("kernel"), "os": summary.get("os"),
        "pending_updates": ({"count": (pending["output"] or {}).get("count"), "manager": (pending["output"] or {}).get("manager"),
                             "at": pending["at"], "run_id": pending["run_id"]} if pending else None),
        "services": list((node.facts or {}).get("services") or [])[:256],
        "installed": all(a["installed"] for a in actions),
        "actions": actions, "recent": recent,
    }


def open_runs(store, node: Node) -> List[Dict[str, Any]]:
    """Runs that include this node and aren't finished: the node page shows them with their
    decision buttons, so approving or watching a run never means leaving the node."""
    from . import vm
    out = []
    for run in store.list_runs(states=["proposed", "approved", "running", "paused", "rolling_back"], limit=200):
        if node.id not in (run.nodes or []):
            continue
        detail = vm.run_detail(store, run)
        row = next((g for g in detail.get("grid") or [] if g.get("node_id") == node.id), None)
        here = [((c.get("exec") or {}).get("state") or "not started") for c in (row or {}).get("cells") or []]
        out.append({k: detail.get(k) for k in ("id", "title", "state", "style", "check_mode", "effect", "effect_style",
                                                "decision", "proposed_by", "created_at", "actions", "nodes",
                                                "paused_reason", "summary")}
                   | {"here": here[-1] if here else "not started", "progress": (row or {}).get("progress") or ""})
        if len(out) >= 10:
            break
    return out


def fleet_reboot_summary(store) -> Dict[str, Any]:
    """Active nodes that need a reboot (for the fleet board, Now and fleet_overview)."""
    nodes = []
    for node in store.list_nodes(statuses=["active"]):
        info = reboot_info(node)
        if info["required"]:
            nodes.append({"node_id": node.id, "name": node.name, "reasons": info["reasons"], "since": info["since"]})
    return {"count": len(nodes), "nodes": nodes}


def propose(orch, *, actor: str, action: str, node_ids: List[str], params: Optional[Mapping[str, Any]] = None,
            check_mode: bool = False):
    """Propose the maintenance run (the access policy decides whether it waits for approval)."""
    spec = BY_KEY.get(action)
    if spec is None:
        raise ValueError(f"unknown maintenance action {action!r}")
    if not node_ids:
        raise ValueError("choose at least one node")
    tv = orch.store.latest_version(spec["tool"], published_only=True)
    if tv is None:
        raise ValueError(f"{spec['tool']} is not installed — install the built-in tools first (installStarterPack)")
    merged = dict(spec["params"])
    merged.update({k: v for k, v in dict(params or {}).items() if v is not None})
    if spec.get("needs") and not str(merged.get(spec["needs"]) or "").strip():
        raise ValueError(f"{spec['label']} needs a {spec['needs']}")
    names = {n.id: n.name for n in orch.store.list_nodes()}
    who = names.get(node_ids[0], node_ids[0]) if len(node_ids) == 1 else f"{len(node_ids)} nodes"
    detail = f" {merged['unit']} ({merged.get('action', 'restart')})" if action == "service" else ""
    title = f"{spec['title']}{detail} on {who}" + (" (check)" if check_mode else "")
    # Reboots roll one node at a time and never all at once; others use the master's defaults.
    strategy = {"canary": 0, "batch": 1, "max_parallel": 1} if spec.get("rolling") else {"canary": 0}
    return orch.create_run(actor=actor, kind="tool", target=tv.ref, params=merged, selector={"nodes": list(node_ids)},
                           strategy=strategy, check_mode=check_mode, title=title)

