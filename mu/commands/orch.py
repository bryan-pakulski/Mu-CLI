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
from typing import Any, Dict, List, Optional, Tuple

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
    lines += ["", "Commands: /orch runs [state] · /orch run <id> · /orch nodes · /orch dashboard [group] · /orch history [node|group] [range] · /orch groups · "
                  "/orch pause [reason] · /orch resume · /orch update-agents [node...]"]
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


def _pct(value: Any) -> str:
    return "–" if not isinstance(value, (int, float)) else f"{value:.0f}%"


def _health_counts(health: Dict[str, int]) -> str:
    parts = [f"{health[k]} {k}" for k in ("critical", "warning", "pending") if health.get(k)]
    return ", ".join(parts) or "all healthy"


def _sign(style: Dict[str, Any]) -> str:
    return f"{style.get('glyph', '')} {style.get('label', '')}".strip()


def _scope_arg(value: Optional[str]) -> Optional[str]:
    """``/orch dashboard`` scope: none = the fleet, "-" (or "ungrouped") = nodes in no group."""
    if value is None or value == "":
        return None
    return "-" if value.lower() in ("-", "ungrouped") else value


def _history(orch, rest: List[str]) -> List[str]:
    """``/orch history [NODE|GROUP|-|all] [RANGE]``: sparklines of a node's (or a group's) CPU,
    memory, disk, load, network and disk I/O (mu/orchestration/metrics.py)."""
    from mu.orchestration import metrics, vm
    ranges = set(metrics.RANGES)
    words = [w for w in rest if w not in ranges]
    range_key = next((w for w in rest if w in ranges), "6h")
    target = words[0] if words else "all"
    node = orch.store.get_node(target) or next((n for n in orch.store.list_nodes() if n.name == target), None)
    if node is not None:
        nodes, title = [node], node.name
    else:
        group = None if target in ("all", "*") else _scope_arg(target)
        try:
            board = vm.fleet_dashboard(orch.store, group)
        except KeyError:
            raise ValueError(f"no node or group called {target!r}; /orch nodes and /orch groups list them") from None
        ids = {n["id"] for n in board["nodes"] if n["status"] != "pending"}
        nodes, title = [n for n in orch.store.list_nodes() if n.id in ids], board["scope"]["label"]
    data = {**metrics.series(orch.store, [n.id for n in nodes], range_key), **metrics.reporting(nodes)}
    lines = [f"{title} · last {data['range']}" + ("" if len(nodes) == 1 else f" · {len(nodes)} nodes (CPU, memory, load averaged; network and I/O summed)")]
    lines += metrics.text_lines(data) or ["No history yet: nodes report a sample about every 20 seconds."]
    if data["needs_update"]:
        lines.append(f"{GLYPH['attention']} No history from {', '.join(data['needs_update'])}: their agent predates "
                     f"{metrics.METRICS_SINCE} — /orch update-agents")
    return lines


def _dashboard(orch, group: Optional[str]) -> List[str]:
    """The node dashboard (mu/orchestration/vm.py fleet_dashboard) as terminal lines."""
    from mu.orchestration import vm
    try:
        board = vm.fleet_dashboard(orch.store, group)
    except KeyError:
        raise ValueError(f"unknown group {group!r}; /orch groups lists them") from None
    t, r, day = board["totals"], board["resources"], board["runs_24h"]
    p = t["presence"]
    lines = [f"{board['scope']['label']} · {t['nodes']} node(s) · {p.get('online', 0)} online · "
             f"{p.get('connecting', 0)} connecting · {p.get('offline', 0)} offline · {t['busy']} busy",
             f"Health: {_health_counts(t['health'])}",
             f"Memory avg {_pct(r['memory_used_avg'])} max {_pct(r['memory_used_max'])} · disk avg {_pct(r['disk_used_avg'])} "
             f"max {_pct(r['disk_used_max'])} · load/CPU max "
             f"{r['load_per_cpu_max'] if r['load_per_cpu_max'] is not None else '–'}",
             f"Last 24h: {day['executions']} execution(s) · {day['succeeded']} succeeded · {day['failed']} failed"]
    if board["nodes"]:
        from mu.orchestration import metrics
        spark = metrics.per_node(orch.store, [n["id"] for n in board["nodes"]], "6h")
        lines += ["", f"  {'NODE':<18} {'HEALTH':<14} {'STATUS':<20} {'CPU (last 6h)':<22} {'MEM':>5} {'DISK':>5}  {'GROUPS':<18} WHY"]
        for n in board["nodes"]:
            busy = " ● busy" if n.get("busy") else ""
            h = spark.get(n["id"]) or {}
            cpu = metrics.sparkline([p.get("cpu_pct") for p in h.get("points") or []], 16, 100.0) if h.get("points") else ""
            now_cpu = _pct((h.get("latest") or {}).get("cpu_pct"))
            lines.append(f"  {_short(n['name'], 18):<18} {_short(_sign(n['health']['style']), 14):<14} "
                         f"{_short(_sign(n['style']), 20):<20} {cpu:<16} {now_cpu:>5} {_pct(n['memory_used_pct']):>5} {_pct(n['max_disk_used_pct']):>5}  "
                         f"{_short(','.join(n['groups']) or '-', 18):<18} {'; '.join(n['health']['reasons'])}{busy}")
    else:
        lines += ["", "No nodes here."]
    if day["recent"]:
        lines += ["", "Recent runs"]
        lines += [f"  {x['id']}  {_short(_sign(x['style']), 16):<16} {_short(x['title'], 48)}  {x['created_ago']}"
                  for x in day["recent"]]
    if group is None:
        lines += ["", "Groups — /orch dashboard <group> · /orch dashboard - (ungrouped)"]
        for g in board["groups"]:
            lines.append(f"  {_short(g['name'], 18):<18} {g['nodes']:>3} node(s)  {_health_counts(g['health'])}")
        lines.append(f"  {'(ungrouped)':<18} {board['ungrouped']['nodes']:>3} node(s)  {_health_counts(board['ungrouped']['health'])}")
    return lines


def _groups(orch) -> List[str]:
    from mu.orchestration import vm
    board = vm.fleet_dashboard(orch.store)
    if not board["groups"]:
        lines = ["No groups yet. /orch group create <name> [--selector S] [--description D]"]
    else:
        lines = ["Groups", ""]
        for g in board["groups"]:
            how = g["selector"] or "members only"
            lines.append(f"  {_short(g['name'], 18):<18} {g['nodes']:>3} node(s) · {g['online']} online · "
                         f"{_health_counts(g['health'])} · {how}" + (f" · {g['description']}" if g["description"] else ""))
    lines.append(f"  {'(ungrouped)':<18} {board['ungrouped']['nodes']:>3} node(s) · {_health_counts(board['ungrouped']['health'])}")
    lines += ["", "Commands: /orch group create|add|remove|rename|delete … · /orch dashboard <group>"]
    return lines


GROUP_USAGE = ("usage: /orch group create NAME [--selector S] [--description D] | add NAME NODE… [--from GROUP] | "
               "remove NAME NODE… | rename OLD NEW | delete NAME")


def _options(rest: List[str], names: Tuple[str, ...]) -> Tuple[List[str], Dict[str, str]]:
    """Positional words and --name VALUE options (a missing value is an error)."""
    words: List[str] = []
    opts: Dict[str, str] = {}
    i = 0
    while i < len(rest):
        word = rest[i]
        if word.startswith("--"):
            key = word[2:]
            if key not in names:
                raise ValueError(f"unknown option {word}; {GROUP_USAGE}")
            if i + 1 >= len(rest):
                raise ValueError(f"{word} needs a value")
            opts[key] = rest[i + 1]
            i += 2
            continue
        words.append(word)
        i += 1
    return words, opts


def _node_ids(orch, refs: List[str]) -> List[str]:
    """Node ids from ids or names (names are friendlier to type)."""
    nodes = orch.store.list_nodes()
    by_name = {n.name: n.id for n in nodes}
    ids = {n.id for n in nodes}
    out = []
    for ref in refs:
        if ref in ids:
            out.append(ref)
        elif ref in by_name:
            out.append(by_name[ref])
        else:
            raise ValueError(f"unknown node {ref!r}; /orch nodes lists them")
    return out


def _group(orch, rest: List[str], confirm) -> List[str]:
    """/orch group <action> …: every change goes through dispatch as the operator."""
    if not rest:
        raise ValueError(GROUP_USAGE)
    action, words = rest[0].lower(), rest[1:]
    words, opts = _options(words, ("selector", "description", "from"))
    if not words:
        raise ValueError(GROUP_USAGE)
    name = words[0]
    if action == "create":
        data = _cmd(orch, "putGroup", name=name, selector=opts.get("selector", ""),
                    description=opts.get("description", ""))
        return [f"{GLYPH['ok']} Group {data['name']} {'created' if data.get('created') else 'updated'}."]
    if action == "add":
        if len(words) < 2:
            raise ValueError("usage: /orch group add NAME NODE… [--from GROUP]")
        args: Dict[str, Any] = {"name": name, "node_ids": _node_ids(orch, words[1:])}
        if opts.get("from"):
            args["from"] = opts["from"]
        data = _cmd(orch, "addToGroup", **args)
        moved = f", moved out of {opts['from']}" if opts.get("from") else ""
        return [f"{GLYPH['ok']} {len(args['node_ids'])} node(s) in {data['name']}{moved}."]
    if action == "remove":
        if len(words) < 2:
            raise ValueError("usage: /orch group remove NAME NODE…")
        data = _cmd(orch, "removeFromGroup", name=name, node_ids=_node_ids(orch, words[1:]))
        lines = [f"{GLYPH['ok']} {data['removed']} node(s) taken out of {data['name']}."]
        for node_id in data.get("still_matched_by_selector") or []:
            lines.append(f"  {node_id} stays in {data['name']}: its labels match the group's selector")
        return lines
    if action == "rename":
        if len(words) < 2:
            raise ValueError("usage: /orch group rename OLD NEW")
        data = _cmd(orch, "renameGroup", name=name, new_name=words[1])
        return [f"{GLYPH['ok']} Group {name} is now {data['name']}."]
    if action == "delete":
        if not confirm(f"Delete group {name}? Its nodes stay; runs targeting it will need a new target."):
            return ["Not deleted."]
        _cmd(orch, "deleteGroup", name=name)
        return [f"{GLYPH['ok']} Group {name} deleted."]
    raise ValueError(f"unknown group action {action!r}; {GROUP_USAGE}")


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
        "runs [state], run <id>, nodes, dashboard [group|-] (node health), history [node|group|-] [1h|6h|24h|7d|30d] "
        "(CPU, memory, disk, load, network and disk I/O over time), groups, group create|add|remove|rename|delete …, "
        "results <tool|run> [view] [sort]; approve <run> [typed-id], reject <run> [reason], continue|pause-run|cancel|"
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
        elif sub in ("dashboard", "health"):
            lines = _dashboard(orch, _scope_arg(rest[0] if rest else None))
        elif sub == "history":
            lines = _history(orch, rest)
        elif sub == "groups":
            lines = _groups(orch)
        elif sub == "group":
            def confirm(question: str) -> bool:
                if not allow_prompt:
                    return True
                from mu.ui.choice_picker import prompt_confirm
                return prompt_confirm(question, default=False)
            lines = _group(orch, rest, confirm)
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
            raise ValueError(f"unknown /orch command {sub!r}; try /orch, /orch runs, /orch nodes, /orch dashboard, /orch groups")
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
    from mu.orchestration import runs as orch_runs
    member_of = orch_runs.node_groups(orch.store, node)
    groups = [g["name"] for g in orch.store.list_groups()]
    if [g for g in groups if g not in member_of]:
        options.append(("__group_add__", "Add to group…", "Put this node in a group"))
    if member_of:
        options.append(("__group_remove__", "Remove from group…", "In: " + ", ".join(member_of)))
    choice = prompt_choice(f"Node {node.name}", options + [("__back__", "Back", "Return without deciding")],
                           default=options[0][0] if options else "__back__",
                           subtitle=f"{node.id} · {node.status} · {labels} · {node.address or ''}")
    if choice == "__back__":
        return
    if choice in ("__group_add__", "__group_remove__"):
        adding = choice == "__group_add__"
        picks = [(g, g, "") for g in (groups if adding else member_of) if (g not in member_of) == adding]
        group = prompt_choice("Add to group" if adding else "Remove from group",
                              picks + [("__back__", "Back", "Return without changing")], default=picks[0][0])
        if group != "__back__":
            _group_change(orch, "addToGroup" if adding else "removeFromGroup", name=group, node_ids=[node.id])
        return
    args: Dict[str, Any] = {"node_id": node.id}
    if choice == "quarantineNode":
        reason = Prompt.ask("Reason (optional)", default="").strip()
        if reason:
            args["reason"] = reason
    _decide(orch, choice, **args)


def _group_change(orch, ctype: str, **args) -> None:
    try:
        data = _cmd(orch, ctype, **args)
    except Exception as exc:  # noqa: BLE001 - shown, the menu stays open
        _print([f"{GLYPH['danger']} {exc}"])
        return
    _print([f"{GLYPH['ok']} {data.get('name') or ctype}: done"])
    for node_id in data.get("still_matched_by_selector") or []:
        _print([f"  {node_id} stays in {data.get('name')}: its labels match the group's selector"])


def _dashboard_menu(orch) -> None:
    from mu.ui.choice_picker import prompt_choice
    groups = orch.store.list_groups()
    options = [("__all__", "All nodes", "The whole fleet")]
    options += [(f"g:{g['name']}", g["name"], g["description"] or g["selector"] or "members only") for g in groups]
    options += [("__ungrouped__", "Ungrouped", "Nodes in no group"), ("__back__", "Back", "Return to orchestration")]
    choice = prompt_choice("Node dashboard", options, default="__all__")
    if choice == "__back__":
        return
    scope = None if choice == "__all__" else "-" if choice == "__ungrouped__" else choice[2:]
    _print([""] + _dashboard(orch, scope))


def _group_menu(orch, name: str) -> None:
    from mu.ui.choice_picker import prompt_choice, prompt_confirm
    from rich.prompt import Prompt
    from mu.orchestration import runs as orch_runs
    while True:
        group = orch.store.get_group(name)
        if group is None:
            return
        nodes = [n for n in orch.store.list_nodes() if n.status != "revoked"]
        inside = [n for n in nodes if name in orch_runs.node_groups(orch.store, n)]
        outside = [n for n in nodes if n not in inside]
        options = [("dashboard", "Dashboard", "Health of this group's nodes")]
        if outside:
            options.append(("add", "Add nodes…", f"{len(outside)} node(s) not in {name}"))
        if inside:
            options.append(("remove", "Remove nodes…", f"{len(inside)} node(s) in {name}"))
        options += [("rename", "Rename", ""), ("delete", "Delete", "Its nodes stay"),
                    ("__back__", "Back", "Return to groups")]
        choice = prompt_choice(f"Group {name}", options, default="dashboard",
                               subtitle=f"{len(inside)} node(s) · {group['selector'] or 'members only'}")
        if choice == "__back__":
            return
        if choice == "dashboard":
            _print([""] + _dashboard(orch, name))
        elif choice in ("add", "remove"):
            pool = outside if choice == "add" else inside
            node_id = prompt_choice("Add which node?" if choice == "add" else "Remove which node?",
                                    [(n.id, n.name, f"{n.status} · {n.id}") for n in pool]
                                    + [("__back__", "Back", "Return without changing")], default=pool[0].id)
            if node_id != "__back__":
                _group_change(orch, "addToGroup" if choice == "add" else "removeFromGroup", name=name, node_ids=[node_id])
        elif choice == "rename":
            new = Prompt.ask("New name", default=name).strip()
            if new and new != name:
                _group_change(orch, "renameGroup", name=name, new_name=new)
                name = new
        elif choice == "delete":
            if prompt_confirm(f"Delete group {name}? Its nodes stay.", default=False):
                _group_change(orch, "deleteGroup", name=name)
                return


def _groups_menu(orch) -> None:
    from mu.ui.choice_picker import prompt_choice
    from rich.prompt import Prompt
    while True:
        _print([""] + _groups(orch))
        groups = orch.store.list_groups()
        options = [(f"g:{g['name']}", g["name"], g["description"] or g["selector"] or "members only") for g in groups]
        options += [("__create__", "+ Create group", "A name, then add nodes or a label selector"),
                    ("__back__", "Back", "Return to orchestration")]
        choice = prompt_choice("Groups", options, default=options[0][0])
        if choice == "__back__":
            return
        if choice == "__create__":
            name = Prompt.ask("Group name").strip()
            if not name:
                continue
            selector = Prompt.ask("Label selector (blank = members only)", default="").strip()
            description = Prompt.ask("Description (optional)", default="").strip()
            _group_change(orch, "putGroup", name=name, selector=selector, description=description)
            if orch.store.get_group(name):
                _group_menu(orch, name)
        else:
            _group_menu(orch, choice[2:])


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
            ("dashboard", "Dashboard", "Node health for the fleet, a group or the ungrouped nodes"),
            ("groups", "Groups", "Create groups, add or remove nodes, rename, delete"),
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
        elif choice == "dashboard":
            _dashboard_menu(orch)
        elif choice == "groups":
            _groups_menu(orch)
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
