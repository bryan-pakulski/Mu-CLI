"""``mucli orch ...``: orchestration from the terminal, for automation and CI (plan §8).

Every mutation goes through ``commands.dispatch`` as the ``operator``, exactly like the GUI.
``serve`` runs a headless master (scheduler + node endpoint) when the GUI is not running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, List, Optional


def _print(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))


def _table(rows: List[List[str]], headers: List[str]) -> None:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for row in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))


def _cmd(orch, ctype: str, **args):
    from .commands import dispatch
    return dispatch(orch, {"type": ctype, "args": args}, actor="operator").data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mucli orch", description="mucli orchestration")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_nodes = sub.add_parser("nodes", help="list, accept, quarantine or revoke nodes")
    p_nodes.add_argument("action", nargs="?", default="list",
                         choices=["list", "show", "accept", "quarantine", "unquarantine", "revoke", "token", "update", "history"])
    p_nodes.add_argument("node", nargs="?")
    p_nodes.add_argument("--labels", default="", help="for token: k=v,k=v")
    p_nodes.add_argument("--canary", action="store_true")
    p_nodes.add_argument("--range", default="6h", help="history: 1h, 6h, 24h, 7d or 30d")

    p_groups = sub.add_parser("groups", help="node groups: list, show, create, delete, add, remove, rename")
    p_groups.add_argument("action", nargs="?", default="list",
                          choices=["list", "show", "create", "delete", "add", "remove", "rename"])
    p_groups.add_argument("name", nargs="?", help="group name")
    p_groups.add_argument("nodes", nargs="*", help="add/remove: node ids; rename: the new name")
    p_groups.add_argument("--selector", default=None, help="create: a label selector (empty = members only)")
    p_groups.add_argument("--description", default=None)
    p_groups.add_argument("--from", dest="from_group", default="", help="add: move the nodes out of this group")

    p_dash = sub.add_parser("dashboard", help="node health: the fleet, one group (--group) or ungrouped (--group -)")
    p_dash.add_argument("--group", default=None)
    p_dash.add_argument("--history", action="store_true", help="also chart CPU, memory, disk, load, network and disk I/O")
    p_dash.add_argument("--range", default="6h", help="history: 1h, 6h, 24h, 7d or 30d")

    p_tools = sub.add_parser("tools", help="catalog")
    p_tools.add_argument("action", nargs="?", default="list", choices=["list", "show", "publish", "deprecate", "starter"])
    p_tools.add_argument("ref", nargs="?")
    p_tools.add_argument("--all", action="store_true", help="include drafts")
    p_tools.add_argument("--typed", default="")

    p_rb = sub.add_parser("runbooks", help="runbooks as code: list, show, validate, save, submit, publish, test")
    p_rb.add_argument("action", nargs="?", default="list",
                      choices=["list", "show", "validate", "save", "submit", "publish", "test"])
    p_rb.add_argument("target", nargs="?", help="FILE or directory (validate/save), id@version (show/submit/publish/test)")
    p_rb.add_argument("--nodes", default="", help="test: comma-separated canary node ids (default: the first canary)")
    p_rb.add_argument("--params", default="{}", help="test: runbook inputs as a JSON object")
    p_rb.add_argument("--no-check", action="store_true", help="test: a real run instead of check mode")
    p_rb.add_argument("--all", action="store_true", help="list: include drafts and retired versions")

    p_runs = sub.add_parser("runs", help="runs")
    p_runs.add_argument("action", nargs="?", default="list",
                        choices=["list", "show", "plan", "start", "approve", "reject", "cancel", "continue", "rollback"])
    p_runs.add_argument("target", nargs="?", help="run id, or id@version for plan/start")
    p_runs.add_argument("--labels", default="")
    p_runs.add_argument("--nodes", default="", help="comma-separated node ids")
    p_runs.add_argument("--groups", default="")
    p_runs.add_argument("--params", default="{}", help="JSON object")
    p_runs.add_argument("--runbook", action="store_true")
    p_runs.add_argument("--check", action="store_true", help="check mode (dry run)")
    p_runs.add_argument("--typed", default="")
    p_runs.add_argument("--wait", action="store_true", help="wait for the run to finish (start/approve)")

    p_export = sub.add_parser("export", help="export a signed bundle or a config-as-code directory")
    p_export.add_argument("path", nargs="?", default="")
    p_export.add_argument("--dir", default="")
    p_export.add_argument("--drafts", action="store_true")
    p_export.add_argument("--inventory", action="store_true")

    p_import = sub.add_parser("import", help="import a bundle or directory (shows a diff first)")
    p_import.add_argument("path", nargs="?", default="")
    p_import.add_argument("--dir", default="")
    p_import.add_argument("--git", default="", help="git URL to clone and import")
    p_import.add_argument("--ref", default="", help="git ref")
    p_import.add_argument("--yes", action="store_true", help="apply without asking")
    p_import.add_argument("--policies", action="store_true", help="also apply the bundle's access policy")

    p_lint = sub.add_parser("lint", help="lint a bundle or directory (CI); exit 1 on errors")
    p_lint.add_argument("path")

    p_audit = sub.add_parser("audit", help="audit log")
    p_audit.add_argument("action", choices=["verify", "list", "export"])
    p_audit.add_argument("--limit", type=int, default=50)

    p_serve = sub.add_parser("serve", help="headless master: scheduler + node endpoint")
    p_serve.add_argument("--bind", default="")
    p_serve.add_argument("--port", type=int, default=0)

    p_pause = sub.add_parser("pause", help="kill switch: stop all dispatch")
    p_pause.add_argument("--reason", default="")
    sub.add_parser("resume", help="resume dispatch")

    p_backup = sub.add_parser("backup", help="consistent backup of the orchestration store, keys and blobs")
    p_backup.add_argument("path")
    p_restore = sub.add_parser("restore", help="restore a backup into an empty orchestration home")
    p_restore.add_argument("path")

    sub.add_parser("overlay", help="WireGuard overlay helper").add_argument(
        "action", choices=["init", "add", "master-config", "hub-config"])

    args, rest = parser.parse_known_args(argv)
    from .service import Orchestrator
    from .commands import CommandError, CommandRejected

    if args.cmd == "runbooks" and args.action == "validate":
        return _validate_runbooks(args)

    if args.cmd == "lint":
        from .bundles import lint_source
        report = lint_source(args.path)
        if args.json:
            _print(report, True)
        else:
            for ref, result in {**report["tools"], **report["runbooks"]}.items():
                status = "ok" if result.get("ok") else "FAIL"
                print(f"{status:4}  {ref}")
                for f in result.get("findings", []):
                    if f["severity"] in ("error", "warning"):
                        print(f"      {f['severity']}: {f['rule']}: {f['message']} {f['file']}:{f['line']}".rstrip(": "))
                for e in result.get("errors", []):
                    print(f"      error: {e}")
            if "access_policy" in report:
                print(("ok  " if report["access_policy"]["ok"] else "FAIL") + "  access policy")
        return 0 if report["ok"] else 1

    if args.cmd == "restore":
        from .backup import restore
        print(json.dumps(restore(args.path), indent=2))
        return 0

    orch = Orchestrator()
    try:
        return _dispatch(orch, args, rest)
    except (CommandError, CommandRejected, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _health(style: dict) -> str:
    return f"{style.get('glyph', '')} {style.get('label', '')}".strip()


def _groups(orch, args, as_json: bool) -> int:
    from . import vm
    action = args.action
    if action == "list":
        board = vm.fleet_dashboard(orch.store)
        if as_json:
            _print({"groups": board["groups"], "ungrouped": board["ungrouped"]}, True)
            return 0
        rows = [[g["name"], str(g["nodes"]), str(g["online"]), _counts(g["health"]), g["selector"] or "(members only)",
                 g["description"]] for g in board["groups"]]
        rows.append(["(ungrouped)", str(board["ungrouped"]["nodes"]), str(board["ungrouped"]["online"]),
                     _counts(board["ungrouped"]["health"]), "", ""])
        _table(rows, ["GROUP", "NODES", "ONLINE", "HEALTH", "SELECTOR", "DESCRIPTION"])
        return 0
    if not args.name:
        print("group name required", file=sys.stderr)
        return 2
    if action == "show":
        return _dashboard(orch, argparse.Namespace(group=args.name), as_json)
    if action == "create":
        data = _cmd(orch, "putGroup", name=args.name, selector=args.selector or "",
                    description=args.description or "", **({"members": args.nodes} if args.nodes else {}))
    elif action == "delete":
        data = _cmd(orch, "deleteGroup", name=args.name)
    elif action == "add":
        data = _cmd(orch, "addToGroup", name=args.name, node_ids=args.nodes, **({"from": args.from_group} if args.from_group else {}))
    elif action == "remove":
        data = _cmd(orch, "removeFromGroup", name=args.name, node_ids=args.nodes)
    else:  # rename
        if not args.nodes:
            print("rename needs the new name", file=sys.stderr)
            return 2
        data = _cmd(orch, "renameGroup", name=args.name, new_name=args.nodes[0])
    if as_json:
        _print(data, True)
    else:
        print(f"{action}: {data.get('name')}")
        for n in data.get("still_matched_by_selector") or []:
            print(f"  {n} stays in the group: its labels match the group's selector")
    return 0


def _history(orch, nodes, title: str, range_key: str, as_json: bool) -> int:
    from . import metrics
    data = {**metrics.series(orch.store, [n.id for n in nodes], range_key), **metrics.reporting(nodes)}
    if as_json:
        _print(data, True)
        return 0
    print(title)
    lines = metrics.text_lines(data)
    print("\n".join(lines) if lines else "  no history yet")
    if data["needs_update"]:
        print(f"  no history from {', '.join(data['needs_update'])}: their agent predates {metrics.METRICS_SINCE} "
              f"(mucli orch nodes update)")
    return 0


def _counts(health: dict) -> str:
    parts = [f"{health[k]} {k}" for k in ("critical", "warning", "pending") if health.get(k)]
    return ", ".join(parts) or "all healthy"


def _dashboard(orch, args, as_json: bool) -> int:
    from . import vm
    try:
        board = vm.fleet_dashboard(orch.store, args.group)
    except KeyError:
        print(f"unknown group {args.group!r}", file=sys.stderr)
        return 1
    if as_json:
        _print(board, True)
        return 0
    t, r, runs24 = board["totals"], board["resources"], board["runs_24h"]
    p = t["presence"]
    print(f"{board['scope']['label']}: {t['nodes']} node(s) · {p.get('online', 0)} online · "
          f"{p.get('connecting', 0)} connecting · {p.get('offline', 0)} offline · {t['busy']} busy")
    print(f"health: {_counts(t['health'])}")
    pct = lambda v: "–" if v is None else f"{v:.0f}%"
    print(f"memory avg {pct(r['memory_used_avg'])} max {pct(r['memory_used_max'])} · disk avg {pct(r['disk_used_avg'])} "
          f"max {pct(r['disk_used_max'])} · load/cpu max {r['load_per_cpu_max'] if r['load_per_cpu_max'] is not None else '–'}")
    print(f"last 24h: {runs24['executions']} execution(s), {runs24['succeeded']} succeeded, {runs24['failed']} failed")
    if board["nodes"]:
        print()
        from . import metrics
        spark = metrics.per_node(orch.store, [n["id"] for n in board["nodes"]], "6h")

        def cpu(n):
            h = spark.get(n["id"]) or {}
            if not h.get("points"):
                return "-"
            return metrics.sparkline([p.get("cpu_pct") for p in h["points"]], 16, 100.0) + " " + pct(h["latest"].get("cpu_pct"))
        _table([[n["name"], _health(n["health"]["style"]), _health(n["style"]), cpu(n), pct(n["memory_used_pct"]),
                 pct(n["max_disk_used_pct"]), ",".join(n["groups"]) or "-", "; ".join(n["health"]["reasons"])]
                for n in board["nodes"]],
               ["NODE", "HEALTH", "STATUS", "CPU (6H)", "MEM", "DISK", "GROUPS", "WHY"])
    if getattr(args, "history", False):
        print()
        ids = {n["id"] for n in board["nodes"] if n["status"] != "pending"}
        _history(orch, [n for n in orch.store.list_nodes() if n.id in ids], f"{board['scope']['label']} · last {args.range}",
                 args.range, False)
    if args.group is None and board["groups"]:
        print()
        _table([[g["name"], str(g["nodes"]), _counts(g["health"])] for g in board["groups"]]
               + [["(ungrouped)", str(board["ungrouped"]["nodes"]), _counts(board["ungrouped"]["health"])]],
               ["GROUP", "NODES", "HEALTH"])
    return 0


def _runbook_files(path: str) -> List[str]:
    import os
    if os.path.isdir(path):
        return sorted(os.path.join(root, f) for root, _dirs, files in os.walk(path) for f in files
                      if f.endswith((".yaml", ".yml")))
    return [path]


def _validate_runbooks(args) -> int:
    """CI: validate runbook YAML files (or every *.yaml under a directory); exit 1 on errors."""
    import os
    from . import catalog
    from .service import Orchestrator
    if not args.target or not os.path.exists(args.target):
        print("a runbook file or directory is required", file=sys.stderr)
        return 2
    orch = Orchestrator()
    report, ok = {}, True
    for path in _runbook_files(args.target):
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        result = catalog.validate_runbook(orch.store, text, actor="operator")
        errors = [p for p in result["problems"] if p.get("severity", "error") == "error"]
        warnings = [p for p in result["problems"] if p.get("severity") == "warning"]
        ok = ok and not errors
        report[path] = {"ok": not errors, "effect": result.get("effect"), "errors": errors, "warnings": warnings}
        if not args.json:
            print(f"{'ok  ' if not errors else 'FAIL'}  {path}" + (f"  ({result['effect']})" if result.get("effect") else ""))
            for p in errors + warnings:
                where = f"{path}:{p['line']}" if p.get("line") else path
                print(f"      {p.get('severity', 'error')}: {where}: {p['path'] + ': ' if p.get('path') else ''}{p['message']}")
    if args.json:
        _print({"ok": ok, "files": report}, True)
    return 0 if ok else 1


def _runbooks(orch, args, as_json: bool) -> int:
    from . import catalog, vm
    action = args.action
    if action == "list":
        rows = [vm.runbook_vm(orch.store, rb) for rb in orch.store.list_runbook_versions()
                if args.all or rb.status not in ("retired",)]
        if as_json:
            _print(rows, True)
        else:
            _table([[r["ref"], r["status"], r["effect"], len(r["steps"]), len(r["problems"]), r["title"][:50]]
                    for r in rows], ["REF", "STATUS", "EFFECT", "STEPS", "PROBLEMS", "TITLE"])
        return 0
    if action == "save":
        if not args.target:
            print("a runbook file or directory is required", file=sys.stderr)
            return 2
        failed = 0
        for path in _runbook_files(args.target):
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            data = _cmd(orch, "saveRunbookDraft", spec=text)
            problems = data["problems"]
            failed += bool(problems)
            print(json.dumps(data, default=str) if as_json else
                  f"{data['ref']}: saved as a draft" + (f" with {len(problems)} problem(s)" if problems else ""))
        return 1 if failed else 0
    from .manifest import parse_ref
    parsed = parse_ref(args.target or "")
    if not parsed:
        print("runbook reference id@version required", file=sys.stderr)
        return 2
    if action == "show":
        rb = orch.store.get_runbook_version(*parsed)
        _print(vm.runbook_vm(orch.store, rb, detail=True) if rb else {}, True)
        return 0 if rb else 1
    if action in ("submit", "publish"):
        data = _cmd(orch, "submitRunbook" if action == "submit" else "publishRunbook", ref=args.target)
        print(f"{args.target}: {data['runbook']['status']}")
        return 0
    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    run = _cmd(orch, "proposeRunbookTest", ref=args.target, nodes=nodes, params=json.loads(args.params or "{}"),
               check_mode=not args.no_check)["run"]
    print(json.dumps(run, default=str) if as_json else f"{run['id']}: {run['state']} ({run['decision'].get('summary')})")
    return 0


def _selector(args) -> dict:
    sel: dict = {}
    if args.labels:
        sel["labels"] = args.labels
    if args.nodes:
        sel["nodes"] = [n.strip() for n in args.nodes.split(",") if n.strip()]
    if args.groups:
        sel["groups"] = [g.strip() for g in args.groups.split(",") if g.strip()]
    return sel


def _wait(orch, run_id: str, as_json: bool) -> int:
    from .states import RUN_TERMINAL
    while True:
        orch.tick()
        run = orch.store.get_run(run_id)
        if run.state in RUN_TERMINAL or run.state in ("paused", "proposed"):
            break
        time.sleep(1)
    if not as_json:
        print(f"{run.id}: {run.state} {run.reason or run.paused_reason}")
    return 0 if run.state == "succeeded" else 2


def _dispatch(orch, args, rest) -> int:
    from . import vm
    from .audit import verify_store
    as_json = args.json
    if args.cmd == "nodes":
        if args.action == "list":
            nodes = [vm.node_vm(orch.store, n) for n in orch.store.list_nodes()]
            if as_json:
                _print(nodes, True)
            else:
                _table([[n["id"], n["name"], n["style"]["label"], ",".join(f"{k}={v}" for k, v in n["labels"].items()),
                         n["last_seen_ago"], n["agent_version"]] for n in nodes],
                       ["ID", "NAME", "STATUS", "LABELS", "SEEN", "AGENT"])
            return 0
        if args.action == "token":
            labels = dict(p.split("=", 1) for p in args.labels.split(",") if "=" in p)
            data = _cmd(orch, "createJoinToken", labels=labels, canary=args.canary)
            if as_json:
                _print(data, True)
            else:
                print(f"join token (one-time, expires in 1h): {data['token']}")
                print(f"master fingerprint: {data['fingerprint']}")
                if data.get("command"):
                    print(f"install on the node:\n  {data['command']}")
            return 0
        if args.action == "update":
            data = _cmd(orch, "proposeAgentUpdate", nodes=[args.node] if args.node else [])
            run = data["run"]
            print(json.dumps(run, default=str) if as_json else
                  f"{run['id']}: {run['state']} ({run['decision'].get('summary')}); "
                  f"{len(run['nodes'])} node(s), {len(run['excluded'])} excluded")
            for x in run["excluded"]:
                if not as_json:
                    print(f"  excluded {x['name']}: {x['reason']}")
            return 0
        if not args.node:
            print("node id required", file=sys.stderr)
            return 2
        if args.action == "history":
            node = orch.store.get_node(args.node) or next((n for n in orch.store.list_nodes() if n.name == args.node), None)
            if node is None:
                print(f"unknown node {args.node!r}", file=sys.stderr)
                return 1
            return _history(orch, [node], f"{node.name} · last {args.range}", args.range, as_json)
        if args.action == "show":
            node = orch.store.get_node(args.node)
            _print(vm.node_vm(orch.store, node, detail=True) if node else {}, True)
            return 0 if node else 1
        ctype = {"accept": "acceptNode", "quarantine": "quarantineNode", "unquarantine": "unquarantineNode",
                 "revoke": "revokeNode"}[args.action]
        data = _cmd(orch, ctype, node_id=args.node)
        print(json.dumps(data, default=str) if as_json else f"{args.node}: {data['node']['status']}")
        return 0
    if args.cmd == "groups":
        return _groups(orch, args, as_json)
    if args.cmd == "dashboard":
        return _dashboard(orch, args, as_json)
    if args.cmd == "tools":
        if args.action == "starter":
            print("\n".join(_cmd(orch, "installStarterPack", force=True)["installed"]) or "already installed")
            return 0
        if args.action == "list":
            statuses = None if args.all else ["published", "deprecated"]
            rows = [vm.tool_vm(orch.store, tv) for tv in orch.store.list_versions(statuses=statuses)]
            if as_json:
                _print(rows, True)
            else:
                _table([[r["ref"], r["status"], r["effect"], r["category"], r["title"]] for r in rows],
                       ["REF", "STATUS", "EFFECT", "CATEGORY", "TITLE"])
            return 0
        from .manifest import parse_ref
        parsed = parse_ref(args.ref or "")
        if not parsed:
            print("tool reference id@version required", file=sys.stderr)
            return 2
        if args.action == "show":
            tv = orch.store.get_version(*parsed)
            _print(vm.tool_detail(orch.store, tv) if tv else {}, True)
            return 0 if tv else 1
        ctype = {"publish": "publishVersion", "deprecate": "deprecateVersion"}[args.action]
        data = _cmd(orch, ctype, ref=args.ref, typed=args.typed)
        print(f"{args.ref}: {data['version']['status']}")
        return 0
    if args.cmd == "runbooks":
        return _runbooks(orch, args, as_json)
    if args.cmd == "runs":
        if args.action == "list":
            runs_ = [vm.run_brief(r) for r in orch.store.list_runs(limit=50)]
            if as_json:
                _print(runs_, True)
            else:
                _table([[r["id"], r["state"], r["target"], r["nodes"], r["title"][:60]] for r in runs_],
                       ["ID", "STATE", "TARGET", "NODES", "TITLE"])
            return 0
        if args.action in ("plan", "start"):
            common = dict(kind="runbook" if args.runbook else "tool", target=args.target,
                          params=json.loads(args.params or "{}"), selector=_selector(args), check_mode=args.check)
            if args.action == "plan":
                plan = _cmd(orch, "planRun", **common)["plan"]
                if as_json:
                    _print(plan, True)
                else:
                    print(f"{'OK' if plan['ok'] else 'NOT RUNNABLE'}: {plan['blast_radius']} — {plan['decision']['summary']}")
                    for e in plan["errors"]:
                        print(f"  error: {e}")
                    for x in plan["excluded"]:
                        print(f"  excluded {x['name']}: {x['reason']}")
                return 0 if plan["ok"] else 1
            run = _cmd(orch, "proposeRun", **common)["run"]
            print(json.dumps(run, default=str) if as_json else f"{run['id']}: {run['state']} ({run['decision'].get('summary')})")
            return _wait(orch, run["id"], as_json) if args.wait else 0
        if args.action == "show":
            run = orch.store.get_run(args.target or "")
            _print(vm.run_detail(orch.store, run) if run else {}, True)
            return 0 if run else 1
        ctype = {"approve": "approveRun", "reject": "rejectRun", "cancel": "cancelRun", "continue": "continueRun",
                 "rollback": "rollbackRun"}[args.action]
        data = _cmd(orch, ctype, run_id=args.target, typed=args.typed)
        print(f"{args.target}: {data['run']['state']}")
        return _wait(orch, args.target, as_json) if args.wait and args.action == "approve" else 0
    if args.cmd == "export":
        from .bundles import export_bundle, export_dir
        if args.dir:
            out = export_dir(orch, args.dir, include_drafts=args.drafts, include_inventory=args.inventory)
        else:
            path = args.path or time.strftime("mucli-orch-%Y%m%d-%H%M%S.mucli-orch.tar.zst")
            out = export_bundle(orch, path, include_drafts=args.drafts, include_inventory=args.inventory)
        print(json.dumps(out, indent=2, default=str) if as_json else f"exported to {out['path']} ({out['manifest']['digest']})")
        return 0
    if args.cmd == "import":
        from .bundles import import_bundle
        path = args.dir or args.path
        cleanup = None
        if args.git:
            import subprocess
            import tempfile
            cleanup = tempfile.mkdtemp(prefix="mucli-orch-git-")
            clone = ["git", "clone", "--depth", "1"] + (["--branch", args.ref] if args.ref else []) + [args.git, cleanup]
            subprocess.run(clone, check=True, capture_output=True, timeout=300)
            path = cleanup
        try:
            preview = import_bundle(orch, path=path, apply=False)
            trust = preview["trust"]
            print(f"signed: {trust['signed']}  trusted: {trust['trusted']}  intact: {trust['intact']}  key: {trust.get('key_id') or '-'}")
            for item in preview["diff"]["tools"] + preview["diff"]["runbooks"]:
                print(f"  {item['status']:9}  {item['ref']}")
            if "access" in preview["diff"]["policies"]:
                print(f"  access policy {'differs' if preview['diff']['policies']['access']['changed'] else 'matches'}")
            if not args.yes:
                if not sys.stdin.isatty() or input("apply this import? [y/N] ").strip().lower() != "y":
                    print("nothing imported")
                    return 0
            result = import_bundle(orch, path=path, apply=True, apply_policies=args.policies)
            print(f"imported {len(result['tools'])} tool version(s), {len(result['runbooks'])} runbook(s); "
                  f"{'published (trusted signature)' if result['published'] else 'awaiting review'}")
            return 0
        finally:
            if cleanup:
                import shutil
                shutil.rmtree(cleanup, ignore_errors=True)
    if args.cmd == "audit":
        if args.action == "verify":
            result = verify_store(orch.store)
            print(json.dumps(result.to_dict()) if as_json else
                  (f"audit chain OK ({result.entries} entries)" if result.ok else
                   f"audit chain BROKEN at seq {result.seq}: {result.problem}"))
            return 0 if result.ok else 1
        entries = orch.store.audit_recent(args.limit)
        if args.action == "export":
            _print(orch.store.audit_entries(limit=10**9), True)
            return 0
        if as_json:
            _print(entries, True)
        else:
            for e in reversed(entries):
                print(f"{e['seq']:>6}  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['at']))}  "
                      f"{e['actor']:<22} {e['action']:<26} {e['subject']}")
        return 0
    if args.cmd == "pause":
        _cmd(orch, "pauseDispatch", reason=args.reason)
        print("dispatch paused")
        return 0
    if args.cmd == "resume":
        _cmd(orch, "resumeDispatch")
        print("dispatch resumed")
        return 0
    if args.cmd == "backup":
        from .backup import backup
        print(json.dumps(backup(orch, args.path), indent=2))
        return 0
    if args.cmd == "overlay":
        from . import overlay
        config = orch.store.get_setting("overlay")
        if args.action == "init":
            endpoint = input("Public UDP endpoint of the master or hub (host:51820): ").strip()
            hub = input("Hub endpoint (empty if nodes reach the master directly): ").strip()
            data = _cmd(orch, "overlayInit", endpoint=endpoint, hub_endpoint=hub)
            print(json.dumps(data, indent=2))
            return 0
        if not config:
            print("run `mucli orch overlay init` first", file=sys.stderr)
            return 1
        if args.action == "add":
            name = rest[0] if rest else input("peer name: ").strip()
            data = _cmd(orch, "overlayAddPeer", name=name)
            print(data["node_config"])
            return 0
        peers = orch.store.overlay_peers()
        print(overlay.render_master(config, peers) if args.action == "master-config" else overlay.render_hub(config, peers))
        return 0
    if args.cmd == "serve":
        return serve(orch, bind=args.bind, port=args.port)
    return 2


def serve(orch, *, bind: str = "", port: int = 0) -> int:
    from .server import NodeEndpoint
    net = orch.network()
    bind = bind or net.get("bind") or ""
    port = port or int(net.get("port") or 30312)
    endpoint = None
    if bind:
        endpoint = NodeEndpoint(orch)
        status = endpoint.start(bind, port)
        if not status["running"]:
            print(f"node endpoint failed: {status['error']}", file=sys.stderr)
            return 1
        print(f"node endpoint https://{bind}:{port} ({status['kind']}), fingerprint {status['fingerprint']}")
    else:
        print("no node endpoint configured (set one with --bind or in Settings > Network); scheduling only")
    orch.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        orch.stop()
        if endpoint:
            endpoint.stop()
    return 0
