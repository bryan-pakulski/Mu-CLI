"""TUI control plane for orchestration: ``/orch`` (documentation/orchestration.md).

The terminal is an operator surface like the web page and the phone: it shows what needs you
(runs to approve, paused runs, reviews, nodes to accept), the runs and the fleet, and offers the
decisions — approve or reject, continue, roll back or cancel, accept or quarantine, the kill
switch and agent updates. Every change goes through ``mu.orchestration.commands.dispatch`` as
the operator, exactly like ``mucli orch`` and the GUI; nothing here is available to the AI.

Reads never start orchestration on a machine that hasn't used it (``attention.peek``). The
scheduler runs in the GUI (or ``mucli orch serve``): approving here records the decision, and
that master dispatches it.
"""

from __future__ import annotations

import json
import shlex
import time
from typing import Any, Dict, List, Optional

from . import CommandResult, command

GLYPH = {"ok": "✓", "danger": "✗", "attention": "◆", "running": "●", "neutral": "○"}
RUN_ACTIONS = {"approve": "approveRun", "reject": "rejectRun", "continue": "continueRun", "pause-run": "pauseRun",
               "cancel": "cancelRun", "rollback": "rollbackRun"}
NODE_ACTIONS = {"accept": "acceptNode", "quarantine": "quarantineNode", "release": "unquarantineNode"}
NOT_USED = ("Orchestration isn't set up on this machine yet. Open Orchestration in the GUI "
            "(mucli --gui) to configure it, or see `mucli orch --help`.")


def _emit(session: Any, text: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if not allow_prompt or ui is None:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(text)


def _short(text: Any, width: int) -> str:
    text = str(text or "")
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return "never"
    d = max(0.0, time.time() - float(ts))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if d >= size:
            return f"{int(d // size)}{unit} ago"
    return f"{int(d)}s ago"


def attention_banner() -> str:
    """Startup / toolbar text: what orchestration needs from you (empty when nothing, or when
    orchestration was never used here). Never raises and never starts orchestration."""
    try:
        from mu.orchestration.attention import describe, peek
        att = peek(max_age_s=20.0)
    except Exception:  # noqa: BLE001 - startup must not depend on orchestration
        return ""
    if not att:
        return ""
    lines = []
    if att.get("total"):
        lines.append(f"{GLYPH['attention']} Orchestration needs you: {describe(att)} — /orch")
    if att.get("dispatch_paused"):
        lines.append(f"{GLYPH['danger']} Orchestration dispatch is paused — /orch resume")
    return "\n".join(lines)


def toolbar_segment() -> str:
    """Short bottom-toolbar text (cached peek; empty when nothing waits)."""
    try:
        from mu.orchestration.attention import peek
        att = peek(max_age_s=20.0)
    except Exception:  # noqa: BLE001
        return ""
    if not att:
        return ""
    parts = []
    if att.get("total"):
        parts.append(f"{GLYPH['attention']} {att['total']} orchestration need{'s' if att['total'] == 1 else ''} you [/orch]")
    if att.get("dispatch_paused"):
        parts.append(f"{GLYPH['danger']} dispatch paused")
    return " | ".join(parts)


def _orch():
    from mu.orchestration.service import Orchestrator
    return Orchestrator()


def _master_running(orch) -> bool:
    lease = orch.store.get_setting("scheduler_lease") or {}
    return float(lease.get("until") or 0) > orch.store.now()


def _cmd(orch, ctype: str, **args) -> Dict[str, Any]:
    from mu.orchestration.commands import dispatch
    return dispatch(orch, {"type": ctype, "args": args}, actor="operator").data


def _run_line(r: Dict[str, Any]) -> str:
    decision = (r.get("decision") or {}).get("level") or ""
    extra = f" · {decision}" if r.get("state") == "proposed" and decision else ""
    return (f"  {r['id']}  {_short(r['style']['label'], 18):<18} {_short(r.get('effect', ''), 11):<11} "
            f"{_short(r.get('target'), 26):<26} {_short(r.get('title'), 44)}{extra}")


def _status(orch) -> List[str]:
    from mu.orchestration import vm
    from mu.orchestration.attention import counts, describe
    store = orch.store
    att = counts(store)
    lines = [f"Orchestration · {describe(att) or 'nothing needs you'}"]
    if att["dispatch_paused"]:
        lines.append(f"{GLYPH['danger']} Dispatch is paused: no new work goes to any node. /orch resume")
    if not _master_running(orch):
        lines.append(f"{GLYPH['neutral']} No master is running here: approved runs start when the GUI "
                     f"(mucli --gui) or `mucli orch serve` is up.")
    proposed = store.list_runs(states=["proposed"], limit=50)
    if proposed:
        lines += ["", f"Runs awaiting approval ({len(proposed)}) — /orch approve <id> | /orch reject <id> [reason]"]
        for run in proposed:
            brief = vm.run_brief(run)
            lines.append(_run_line(brief))
            lines.append(f"      {brief.get('blast_radius') or ''} · {(brief.get('decision') or {}).get('summary') or ''}"
                         f" · proposed by {brief.get('proposed_by')}")
            typed = (run.decision or {}).get("level") == "typed"
            if typed:
                lines.append(f"      approve by typing the target: /orch approve {run.id} {run.target_id}")
    paused = store.list_runs(states=["paused"], limit=50)
    if paused:
        lines += ["", f"Paused runs ({len(paused)}) — /orch continue|rollback|cancel <id>"]
        for run in paused:
            lines.append(_run_line(vm.run_brief(run)))
            lines.append(f"      {run.paused_reason}")
    pending = store.list_nodes(statuses=["pending"])
    if pending:
        lines += ["", f"Nodes awaiting acceptance ({len(pending)}) — /orch accept <node> | /orch revoke on the web"]
        for node in pending:
            labels = ",".join(f"{k}={v}" for k, v in node.labels.items()) or "no labels"
            lines.append(f"  {node.id}  {_short(node.name, 20):<20} {labels}  {node.address or ''}")
    reviews = [tv.ref for tv in store.list_versions(statuses=["in_review"])] + \
              [rb.ref for rb in store.list_runbook_versions(statuses=["in_review"])]
    if reviews:
        lines += ["", f"In review ({len(reviews)}) — review the diff and lint on the web Orchestration page",
                  "  " + ", ".join(reviews)]
    if att["offline_nodes"]:
        lines += ["", f"{GLYPH['danger']} {att['offline_nodes']} active node(s) offline — /orch nodes"]
    lines += ["", "Commands: /orch runs [state] · /orch run <id> · /orch nodes · /orch pause [reason] · "
                  "/orch resume · /orch update-agents [node...]"]
    return lines


def _runs(orch, state: str) -> List[str]:
    from mu.orchestration import vm
    states = [s for s in state.split(",") if s] or None
    runs = [vm.run_brief(r) for r in orch.store.list_runs(states=states, limit=30)]
    if not runs:
        return ["No runs" + (f" in state {state}." if state else " yet.")]
    return [f"Runs{' · ' + state if state else ''}", ""] + [_run_line(r) for r in runs]


def _run(orch, run_id: str) -> List[str]:
    from mu.orchestration import vm
    run = orch.store.get_run(run_id)
    if run is None:
        raise ValueError(f"unknown run {run_id}")
    d = vm.run_detail(orch.store, run)
    lines = [f"{d['title']}", f"  {d['id']} · {d['style']['label']} · {d['effect']} · {d['target']} · "
                              f"{d.get('blast_radius') or ''}",
             f"  decision: {(d.get('decision') or {}).get('summary') or '-'} · proposed by {d['proposed_by']}"
             + (f" · approved by {d['approved_by']}" if d.get("approved_by") else "")]
    if d.get("paused_reason"):
        lines.append(f"  {GLYPH['attention']} paused: {d['paused_reason']}")
    if d.get("reason"):
        lines.append(f"  {d['reason']}")
    for row in d.get("grid") or []:
        cells = "  ".join(f"{c.get('name')}:{(c.get('exec') or {}).get('state', 'not started')}" for c in row["cells"])
        lines.append(f"  {_short(row['name'], 18):<18} {row['progress']:<10} {cells}")
    for x in d.get("excluded") or []:
        lines.append(f"  excluded {x.get('name') or x.get('node_id')}: {x.get('reason')}")
    available = [a["label"] for a in d.get("actions") or [] if a.get("enabled")]
    if available:
        lines.append(f"  available: {', '.join(available)}")
    return lines


def _nodes(orch) -> List[str]:
    from mu.orchestration import vm
    nodes = [vm.node_vm(orch.store, n) for n in orch.store.list_nodes()]
    if not nodes:
        return ["No nodes yet. Set one up in the GUI: Orchestration › Nodes › Set up a node."]
    lines = ["Nodes", ""]
    for n in nodes:
        agent = n.get("agent") or {}
        flag = " (agent outdated)" if agent.get("outdated") else ""
        lines.append(f"  {n['id']}  {_short(n['name'], 18):<18} {_short(n['style']['label'], 18):<18} "
                     f"seen {n['last_seen_ago']:<9} agent {n['agent_version'] or '-'}{flag}  "
                     f"{','.join(f'{k}={v}' for k, v in n['labels'].items())}")
    return lines


def _results(orch, target: str, view: str, sort: str) -> List[str]:
    """Fleet results as a terminal table (mu/orchestration/results.py, fleet_tools.compact)."""
    from mu.orchestration import results
    from mu.orchestration.fleet_tools import compact
    if target.startswith("r_"):
        run = orch.store.get_run(target)
        if run is None:
            raise ValueError(f"unknown run {target}")
        steps = results.run_results(orch.store, run)["steps"]
        tables = steps[-1]["tables"] if steps else []
        title = f"{run.title} · {run.state}"
    else:
        if orch.store.latest_version(target) is None:
            raise ValueError(f"unknown tool {target}")
        tables = results.latest_results(orch.store, target)["tables"]
        title = f"{target} · latest result per node"
    if not tables or not any(t["rows"] for t in tables):
        return [title, "", "No results yet."]
    table = next((t for t in tables if t["key"] == view), None)
    if table is None:
        raise ValueError(f"unknown view {view!r}; available: {', '.join(t['key'] for t in tables)}")
    data = compact(table, sort=sort, limit=60)
    fmt = {c["label"]: c["format"] for c in table["columns"]}

    def show(label, value):
        if value is None:
            return "—"
        kind = fmt.get(label)
        if kind == "bytes" and isinstance(value, (int, float)):
            size, unit = float(value), "B"
            for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
                if abs(size) < 1024 or unit == "PB":
                    break
                size /= 1024
            return f"{size:.0f} {unit}" if unit == "B" or size >= 100 else f"{size:.1f} {unit}"
        if kind == "percent" and isinstance(value, (int, float)):
            return f"{value:g}%"
        if kind == "days" and isinstance(value, (int, float)):
            return f"{value:g}d"
        return str(value)

    cells = [[show(label, v) for label, v in zip(data["columns"], row)] for row in data["rows"]]
    widths = [min(32, max([len(h)] + [len(r[i]) for r in cells])) for i, h in enumerate(data["columns"])]
    line = lambda vals: "  ".join(_short(v, w).ljust(w) for v, w in zip(vals, widths)).rstrip()
    out = [f"{title} · {table['label']} ({data['matched']} rows, sorted by {data['sorted_by'] or 'node'})", "",
           line(data["columns"]), line(["─" * w for w in widths])]
    out += [line(r) for r in cells]
    if data["truncated"]:
        out.append(f"… {data['matched'] - len(cells)} more (the web Results tab has every row, with CSV export)")
    others = [t["key"] for t in tables if t["key"] != table["key"]]
    if others:
        out += ["", f"Other views: {', '.join(others)}  (/orch results {target} <view> [sort column])"]
    return out


@command(
    "/orch",
    "/orchestration",
    help=(
        "Fleet orchestration: /orch shows what needs you (runs to approve, paused runs, reviews, nodes to accept); "
        "runs [state], run <id>, nodes, results <tool|run> [view] [sort]; approve <run> [typed-id], reject <run> [reason], continue|pause-run|cancel|"
        "rollback <run>, accept|quarantine|release <node>, pause [reason] / resume (the kill switch), "
        "update-agents [node...]. Reviews and authoring happen on the web Orchestration page."
    ),
)
def orch_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.orchestration.attention import peek
    try:
        parts = shlex.split(str(args or ""))
    except ValueError as exc:
        _emit(session, str(exc), allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))
    sub = (parts[0].lower() if parts else "status")
    rest = parts[1:]
    if peek() is None:
        _emit(session, NOT_USED, allow_prompt)
        return CommandResult(ok=True, message=NOT_USED, data={"used": False})
    try:
        orch = _orch()
        if sub in ("status", "inbox", "needs"):
            lines = _status(orch)
        elif sub == "runs":
            lines = _runs(orch, rest[0] if rest else "")
        elif sub == "run":
            if not rest:
                raise ValueError("usage: /orch run <id>")
            lines = _run(orch, rest[0])
        elif sub == "nodes":
            lines = _nodes(orch)
        elif sub == "results":
            if not rest:
                raise ValueError("usage: /orch results <tool|run id> [view] [sort column]")
            lines = _results(orch, rest[0], rest[1] if len(rest) > 1 else "summary", " ".join(rest[2:]))
        elif sub in RUN_ACTIONS:
            if not rest:
                raise ValueError(f"usage: /orch {sub} <run id>")
            args_: Dict[str, Any] = {"run_id": rest[0]}
            if sub == "approve" and len(rest) > 1:
                args_["typed"] = rest[1]
            if sub in ("reject", "cancel") and len(rest) > 1:
                args_["reason"] = " ".join(rest[1:])
            run = _cmd(orch, RUN_ACTIONS[sub], **args_)["run"]
            lines = [f"{GLYPH['ok']} {run['id']}: {run['state']}"]
            if sub == "approve" and not _master_running(orch):
                lines.append("No master is running here: it starts when the GUI (mucli --gui) or `mucli orch serve` is up.")
        elif sub in NODE_ACTIONS:
            if not rest:
                raise ValueError(f"usage: /orch {sub} <node id>")
            args_ = {"node_id": rest[0]}
            if sub == "quarantine" and len(rest) > 1:
                args_["reason"] = " ".join(rest[1:])
            node = _cmd(orch, NODE_ACTIONS[sub], **args_)["node"]
            lines = [f"{GLYPH['ok']} {node['name']}: {node['status']}"]
        elif sub == "pause":
            _cmd(orch, "pauseDispatch", reason=" ".join(rest))
            lines = [f"{GLYPH['danger']} Dispatch paused: no new work goes to any node until /orch resume."]
        elif sub == "resume":
            _cmd(orch, "resumeDispatch")
            lines = [f"{GLYPH['ok']} Dispatch resumed."]
        elif sub in ("update-agents", "update"):
            run = _cmd(orch, "proposeAgentUpdate", nodes=rest)["run"]
            lines = [f"{GLYPH['ok']} {run['id']}: {run['state']} — {(run.get('decision') or {}).get('summary') or ''}",
                     f"  {len(run['nodes'])} node(s); {len(run['excluded'])} excluded"]
            lines += [f"  excluded {x['name']}: {x['reason']}" for x in run["excluded"]]
            if run["state"] == "proposed":
                lines.append(f"  approve it with /orch approve {run['id']}")
        else:
            raise ValueError(f"unknown /orch command {sub!r}; try /orch, /orch runs, /orch nodes")
    except Exception as exc:  # noqa: BLE001 - command errors are shown, not raised
        message = str(exc)
        _emit(session, message, allow_prompt, error=True)
        return CommandResult(ok=False, message=message)
    text = "\n".join(lines)
    _emit(session, text, allow_prompt)
    return CommandResult(ok=True, message=lines[0] if lines else "", data={"text": text})


# ----------------------------------------------------------------- launcher menu
# ``mucli`` with no --session opens a launcher (mucli.choose_session); its Orchestration entry
# lands here: the same decisions as /orch, picked from menus instead of typed ids.

def launcher_hint() -> str:
    """Launcher description for the Orchestration entry (never starts orchestration)."""
    try:
        from mu.orchestration.attention import describe, peek
        att = peek(max_age_s=20.0)
    except Exception:  # noqa: BLE001 - the launcher must not depend on orchestration
        att = None
    if att is None:
        return "Fleet runs and nodes (not set up yet: configure it in the GUI)"
    parts = [describe(att)] if att.get("total") else []
    if att.get("dispatch_paused"):
        parts.append("dispatch paused")
    return "Needs you: " + "; ".join(parts) if parts else "Fleet runs and nodes; nothing needs you"


def _print(lines) -> None:
    from mu.ui.theme import get_console
    console = get_console()
    for line in lines:
        console.print(line, markup=False, highlight=False)


def _decide(orch, ctype: str, **args) -> None:
    try:
        data = _cmd(orch, ctype, **args)
    except Exception as exc:  # noqa: BLE001 - shown, the menu stays open
        _print([f"{GLYPH['danger']} {exc}"])
        return
    item = data.get("run") or data.get("node") or {}
    state = item.get("state") or item.get("status") or "done"
    _print([f"{GLYPH['ok']} {item.get('id') or item.get('name') or ctype}: {state}"])
    if ctype == "approveRun" and not _master_running(orch):
        _print(["No master is running here: it starts when the GUI (mucli --gui) or `mucli orch serve` is up."])


def _run_menu(orch, run_id: str) -> None:
    from mu.orchestration import vm
    from mu.ui.choice_picker import prompt_choice, prompt_confirm
    from rich.prompt import Prompt
    run = orch.store.get_run(run_id)
    if run is None:
        return
    _print([""] + _run(orch, run_id))
    detail = vm.run_detail(orch.store, run)
    enabled = {a["id"]: a for a in detail.get("actions") or [] if a.get("enabled")}
    labels = {"approveRun": "Approve and start", "rejectRun": "Reject", "continueRun": "Continue",
              "pauseRun": "Pause", "rollbackRun": "Roll back", "cancelRun": "Cancel run"}
    options = [(cid, label, (enabled[cid].get("confirm") or "")) for cid, label in labels.items() if cid in enabled]
    if not options:
        return
    choice = prompt_choice(f"Run {run.id}", options + [("__back__", "Back", "Return without deciding")],
                           default=options[0][0], subtitle=detail.get("title") or "")
    if choice == "__back__":
        return
    args: Dict[str, Any] = {"run_id": run.id}
    if choice == "approveRun" and (run.decision or {}).get("level") == "typed":
        args["typed"] = Prompt.ask(f"Type {run.target_id} to confirm").strip()
    elif choice in ("rejectRun", "cancelRun"):
        reason = Prompt.ask("Reason (optional)", default="").strip()
        if reason:
            args["reason"] = reason
    elif choice in ("rollbackRun",) and not prompt_confirm(f"Roll back run {run.id}?", default=False):
        return
    _decide(orch, choice, **args)


def _node_menu(orch, node_id: str) -> None:
    from mu.ui.choice_picker import prompt_choice
    from rich.prompt import Prompt
    node = orch.store.get_node(node_id)
    if node is None:
        return
    labels = ",".join(f"{k}={v}" for k, v in node.labels.items()) or "no labels"
    options = []
    if node.status == "pending":
        options.append(("acceptNode", "Accept", "Let this node receive work"))
    if node.status == "quarantined":
        options.append(("unquarantineNode", "Release", "Let this node receive work again"))
    elif node.status in ("pending", "active"):
        options.append(("quarantineNode", "Quarantine", "Stop this node receiving work"))
    choice = prompt_choice(f"Node {node.name}", options + [("__back__", "Back", "Return without deciding")],
                           default=options[0][0] if options else "__back__",
                           subtitle=f"{node.id} · {node.status} · {labels} · {node.address or ''}")
    if choice == "__back__":
        return
    args: Dict[str, Any] = {"node_id": node.id}
    if choice == "quarantineNode":
        reason = Prompt.ask("Reason (optional)", default="").strip()
        if reason:
            args["reason"] = reason
    _decide(orch, choice, **args)


def run_orchestration_menu() -> None:
    """Interactive orchestration inbox for the launcher. Acts as the operator through
    ``dispatch`` like /orch; reviews and authoring stay on the web page."""
    from mu.orchestration.attention import counts, describe, peek
    from mu.ui.choice_picker import prompt_choice, prompt_confirm
    from rich.prompt import Prompt
    if peek() is None:
        _print(["", NOT_USED])
        return
    orch = _orch()
    while True:
        store = orch.store
        att = counts(store)
        options = []
        for run in store.list_runs(states=["proposed"], limit=20):
            options.append((f"run:{run.id}", f"{GLYPH['attention']} Approve? {_short(run.title, 48)}",
                            f"{run.id} · {run.target_id} · proposed by {run.proposed_by}"))
        for run in store.list_runs(states=["paused"], limit=20):
            options.append((f"run:{run.id}", f"{GLYPH['attention']} Paused: {_short(run.title, 48)}",
                            f"{run.id} · {_short(run.paused_reason, 70)}"))
        for node in store.list_nodes(statuses=["pending"]):
            options.append((f"node:{node.id}", f"{GLYPH['attention']} Accept node? {node.name}",
                            f"{node.id} · {node.address or 'no address'}"))
        options += [
            ("status", "Status", "Everything that needs you, with details"),
            ("runs", "Runs", "Recent runs and their state"),
            ("nodes", "Nodes", "The fleet: status, last seen, agent versions"),
            ("update", "Update outdated agents", "Propose an agent update job for nodes behind the master"),
            ("resume", "Resume dispatch", "Let work go to nodes again") if att["dispatch_paused"] else
            ("pause", "Pause all dispatch", "Kill switch: no new work goes to any node"),
            ("__back__", "Back", "Return to the MuCLI launcher"),
        ]
        choice = prompt_choice("Orchestration", options, default=options[0][0],
                               subtitle=describe(att) or "Nothing needs you.")
        if choice == "__back__":
            return
        if choice.startswith("run:"):
            _run_menu(orch, choice[4:])
        elif choice.startswith("node:"):
            _node_menu(orch, choice[5:])
        elif choice == "status":
            _print([""] + _status(orch))
        elif choice == "runs":
            _print([""] + _runs(orch, ""))
        elif choice == "nodes":
            fleet = store.list_nodes()
            _print([""] + _nodes(orch))
            picks = [(n.id, n.name, f"{n.status} · {n.id}") for n in fleet]
            if picks:
                node_id = prompt_choice("Nodes", picks + [("__back__", "Back", "Return to orchestration")],
                                        default="__back__")
                if node_id != "__back__":
                    _node_menu(orch, node_id)
        elif choice == "update":
            try:
                run = _cmd(orch, "proposeAgentUpdate", nodes=[])["run"]
            except Exception as exc:  # noqa: BLE001
                _print([f"{GLYPH['danger']} {exc}"])
                continue
            _print([f"{GLYPH['ok']} {run['id']}: {run['state']} · {len(run['nodes'])} node(s)"])
        elif choice == "pause":
            if prompt_confirm("Pause all dispatch? No new work goes to any node.", default=False):
                _decide(orch, "pauseDispatch", reason=Prompt.ask("Reason (optional)", default="").strip())
        elif choice == "resume":
            _decide(orch, "resumeDispatch")
