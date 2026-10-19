"""The AI's orchestration tools (plan §4), available only in ``orchestration`` sessions.

Every tool goes through ``commands.dispatch`` with actor ``ai:<session>``, so the AI is held to
``commands.AI_COMMANDS``: it can search and describe the catalog, read inventory and facts,
write and lint drafts, propose tests and runs, submit for review and read redacted results.
There is no tool that publishes, approves, edits node or access policy, reads secrets or
dispatches without a person; ``orch_run_propose`` starts a run only when the operator's access
policy says ``allow`` for the AI, otherwise the proposal waits in the approvals inbox.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from mu.tools import tool

MAX_FACTS_CHARS = 20000


def _orch():
    from .service import default_orchestrator
    return default_orchestrator()


def _actor(context) -> str:
    session = getattr(context, "session", None)
    name = ""
    manager = getattr(session, "session_manager", None)
    if manager is not None:
        name = str(getattr(manager, "current_session_name", "") or "")
    variables = getattr(context, "variables", None) or {}
    return f"ai:{name or variables.get('session_name') or 'session'}"


def _session_name(context) -> str:
    return _actor(context)[3:]


def _ok(message: str, data: Any) -> Dict[str, Any]:
    return {"ok": True, "message": message, "data": data}


def _error(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error_code": "orchestration_error", "message": str(exc), "data": {}}


def _cmd(context, ctype: str, **args) -> Dict[str, Any]:
    from .commands import dispatch
    return dispatch(_orch(), {"type": ctype, "args": args}, actor=_actor(context)).data


def _brief(tv) -> Dict[str, Any]:
    m = tv.manifest
    props = ((m.get("inputs") or {}).get("properties") or {})
    return {"ref": tv.ref, "status": tv.status, "title": m.get("title"), "summary": m.get("summary"),
            "category": m.get("category"), "effect": tv.effect, "idempotent": m.get("idempotent"),
            "check_mode": (m.get("implementation") or {}).get("check_mode"),
            "inputs": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "enum", "default", "description")}
                       for k, v in props.items() if isinstance(v, dict)}}


_REF = {"type": "string", "description": "Tool reference id@version, e.g. sys.disk-usage@1"}


@tool(name="orch_catalog_search", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Search the orchestration tool catalog (published tools, optionally drafts). Always search "
                  "before drafting a new tool so you reuse or extend an existing one.",
      parameters={"type": "object", "properties": {
          "query": {"type": "string", "description": "Words to match against id, title, summary, category"},
          "include_drafts": {"type": "boolean", "default": False}}})
def orch_catalog_search(args, context):
    try:
        from . import catalog
        orch = _orch()
        found = catalog.search(orch.store, str(args.get("query") or ""), include_drafts=bool(args.get("include_drafts")))
        runbooks = [{"ref": rb.ref, "title": rb.spec.get("title"), "status": rb.status,
                     "steps": [s.get("tool") for s in rb.spec.get("steps") or []]}
                    for rb in orch.store.list_runbook_versions(statuses=["published"])]
        return _ok(f"{len(found)} tool(s).", {"tools": [_brief(tv) for tv in found], "runbooks": runbooks[:50]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_tool_describe", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Describe one tool version: manifest, files (scripts), lint findings, test results and review "
                  "state. Published tools show the house style.",
      parameters={"type": "object", "properties": {"ref": _REF}, "required": ["ref"]})
def orch_tool_describe(args, context):
    try:
        from . import catalog
        from .manifest import parse_ref, to_yaml
        orch = _orch()
        parsed = parse_ref(str(args.get("ref") or ""))
        tv = orch.store.get_version(*parsed) if parsed else None
        if tv is None and args.get("ref") and "@" not in str(args.get("ref")):
            tv = orch.store.latest_version(str(args["ref"]))
        if tv is None:
            raise ValueError(f"unknown tool {args.get('ref')}")
        review = dict(tv.review or {})
        return _ok(tv.ref, {"ref": tv.ref, "status": tv.status, "stage": tv.stage, "manifest_yaml": to_yaml(tv.manifest),
                            "files": catalog.text_files(orch.store, tv), "lint": tv.lint, "lint_current": catalog.lint_current(tv),
                            "tests": review.get("tests") or [], "review_history": review.get("history") or [],
                            "versions": [f"{v.ref} ({v.status})" for v in orch.store.list_versions(tv.tool_id)]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_nodes_list", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="List fleet nodes with labels, status, online state, a facts summary and each node's local "
                  "policy (what the node will refuse). Optional label selector like 'env=dev,role=web'.",
      parameters={"type": "object", "properties": {"selector": {"type": "string"}}})
def orch_nodes_list(args, context):
    try:
        from . import selectors, vm
        orch = _orch()
        match = selectors.compile_selector(str(args.get("selector") or ""))
        nodes = []
        for node in orch.store.list_nodes():
            if not match(node.labels):
                continue
            view = vm.node_vm(orch.store, node)
            nodes.append({k: view[k] for k in ("id", "name", "labels", "status", "online", "canary", "kind",
                                                "summary", "policy", "groups", "last_seen_ago")})
        return _ok(f"{len(nodes)} node(s).", {"nodes": nodes})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_node_facts", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Read one node's collected facts (OS, kernel, CPU, memory, disks, uptime, running services, "
                  "installed binaries). Redacted and size-capped. This is how you look at a system.",
      parameters={"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]})
def orch_node_facts(args, context):
    try:
        from .hub import master_redact
        orch = _orch()
        node = orch.store.get_node(str(args.get("node_id") or ""))
        if node is None:
            node = next((n for n in orch.store.list_nodes() if n.name == args.get("node_id")), None)
        if node is None:
            raise ValueError("unknown node")
        text, _ = master_redact(json.dumps(node.facts, sort_keys=True, default=str), [])
        truncated = len(text) > MAX_FACTS_CHARS
        return _ok(f"facts for {node.name}", {"node_id": node.id, "name": node.name, "facts_at": node.facts_at,
                                               "facts": json.loads(text) if not truncated else text[:MAX_FACTS_CHARS],
                                               "truncated": truncated})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


_MANIFEST = {"description": "Tool manifest as an object or YAML text (id, title, summary, category, effect, "
                            "idempotent, targets, privilege, inputs, outputs, implementation{runtime, entry, "
                            "check_mode}, limits, rollback, preconditions, tests, secrets)"}
_FILES = {"type": "object", "description": "File name -> full text content (e.g. the entry script)",
          "additionalProperties": {"type": "string"}}


@tool(name="orch_tool_draft", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Create a new DRAFT tool version (drafts can never run on the fleet). Returns its id@version "
                  "and any manifest problems.",
      parameters={"type": "object", "properties": {"manifest": _MANIFEST, "files": _FILES,
                                                   "intent": {"type": "string", "description": "The user's goal in one paragraph"}},
                  "required": ["manifest"]})
def orch_tool_draft(args, context):
    try:
        data = _cmd(context, "createDraft", manifest=args.get("manifest"), files=args.get("files") or {},
                    intent=str(args.get("intent") or ""), session_name=_session_name(context))
        version = data["version"]
        return _ok(f"Draft {data['ref']} created.", {"ref": data["ref"], "stage": version["stage"],
                                                      "problems": [f["message"] for f in (version.get("lint") or {}).get("findings", [])]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_tool_update", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Update a draft: replace the manifest and/or add or replace files, remove files, or set the "
                  "discovery stage (intent, spec, discover, implement, test, review).",
      parameters={"type": "object", "properties": {"ref": _REF, "manifest": _MANIFEST, "files": _FILES,
                                                   "remove_files": {"type": "array", "items": {"type": "string"}},
                                                   "stage": {"type": "string"}},
                  "required": ["ref"]})
def orch_tool_update(args, context):
    try:
        data = _cmd(context, "updateDraft", ref=args.get("ref"), manifest=args.get("manifest"),
                    files=args.get("files"), remove_files=args.get("remove_files"), stage=args.get("stage") or "")
        return _ok(f"Draft {data['ref']} updated.", {"ref": data["ref"], "status": data["version"]["status"],
                                                      "stage": data["version"]["stage"]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_tool_lint", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Run static checks on a draft on the master (manifest validation, banned patterns, secret "
                  "scan, shellcheck/ruff). Fix every error before testing or submitting.",
      parameters={"type": "object", "properties": {"ref": _REF}, "required": ["ref"]})
def orch_tool_lint(args, context):
    try:
        lint = _cmd(context, "lintDraft", ref=args.get("ref"))["lint"]
        return _ok("lint passed" if lint["ok"] else f"lint found {lint['counts']['error']} error(s)", lint)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_tool_test", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Propose a test of a draft on a canary node (check mode by default). The operator approves "
                  "it unless their access policy allows such tests; this returns the run id. Use test_name to run a test case from the "
                  "manifest. Poll orch_run_status for the outcome.",
      parameters={"type": "object", "properties": {
          "ref": _REF, "node_id": {"type": "string", "description": "A canary node (default: the first one)"},
          "params": {"type": "object"}, "check_mode": {"type": "boolean", "default": True},
          "test_name": {"type": "string"}}, "required": ["ref"]})
def orch_tool_test(args, context):
    try:
        run = _cmd(context, "proposeTest", ref=args.get("ref"), node_id=args.get("node_id") or "",
                   params=args.get("params") or {}, check_mode=args.get("check_mode", True),
                   test_name=args.get("test_name") or "")["run"]
        started = run["state"] != "proposed"
        return _ok(f"Test {run['id']} {'started (allowed by the access policy)' if started else 'proposed; waiting for the operator to approve it'}.",
                   {"run_id": run["id"], "state": run["state"], "nodes": run["nodes"]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_tool_submit", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Submit a linted draft for the operator's review, with a one-line headline and the risks "
                  "(what could go wrong on a real machine). Only the operator can publish.",
      parameters={"type": "object", "properties": {
          "ref": _REF, "headline": {"type": "string"}, "risks": {"type": "array", "items": {"type": "string"}},
          "notes": {"type": "string"}}, "required": ["ref", "headline"]})
def orch_tool_submit(args, context):
    try:
        data = _cmd(context, "submitReview", ref=args.get("ref"), headline=args.get("headline") or "",
                    risks=args.get("risks") or [], notes=args.get("notes") or "")
        return _ok(f"{args.get('ref')} is in review; the operator decides whether to publish it.",
                   {"status": data["version"]["status"]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_run_propose", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Propose running a PUBLISHED tool or runbook on nodes chosen by labels, groups or ids. The "
                  "operator's access policy decides: allowed runs start at once, others wait for approval, denied "
                  "runs are refused. Set dry_run to only see the plan (nodes, exclusions, batches, decision).",
      parameters={"type": "object", "properties": {
          "kind": {"type": "string", "enum": ["tool", "runbook"], "default": "tool"},
          "target": {"type": "string", "description": "id@version"},
          "params": {"type": "object"},
          "selector": {"type": "object", "description": "{labels: 'env=dev', groups: [...], nodes: [...]}"},
          "strategy": {"type": "object", "description": "{canary, batch, max_parallel, failure_threshold, pause_between_s}"},
          "check_mode": {"type": "boolean", "default": False},
          "title": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}},
          "required": ["target", "selector"]})
def orch_run_propose(args, context):
    try:
        common = dict(kind=args.get("kind") or "tool", target=args.get("target"), params=args.get("params") or {},
                      selector=args.get("selector") or {}, strategy=args.get("strategy") or {},
                      check_mode=bool(args.get("check_mode")))
        if args.get("dry_run"):
            plan = _cmd(context, "planRun", **common)["plan"]
            keep = ("ok", "errors", "warnings", "nodes", "excluded", "batches", "blast_radius", "effect", "steps")
            out = {k: plan[k] for k in keep}
            out["decision"] = plan["decision"]["summary"]
            return _ok("plan only (nothing was proposed)", out)
        run = _cmd(context, "proposeRun", title=args.get("title") or "", **common)["run"]
        decision = run.get("decision") or {}
        message = ("Run started (allowed by the access policy)." if run["state"] in ("approved", "running")
                   else "Run proposed; waiting for the operator's approval.")
        return _ok(message, {"run_id": run["id"], "state": run["state"], "decision": decision.get("summary"),
                             "nodes": run["nodes"], "excluded": run["excluded"]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_run_status", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Status of a run (state, per-node progress, pause reason), or the recent runs this session "
                  "proposed when run_id is omitted.",
      parameters={"type": "object", "properties": {"run_id": {"type": "string"}}})
def orch_run_status(args, context):
    try:
        from . import vm
        orch = _orch()
        if not args.get("run_id"):
            actor = _actor(context)
            mine = [vm.run_brief(r) for r in orch.store.list_runs(limit=100) if r.proposed_by == actor][:20]
            return _ok(f"{len(mine)} recent run(s).", {"runs": mine})
        run = orch.store.get_run(str(args["run_id"]))
        if run is None:
            raise ValueError("unknown run")
        detail = vm.run_detail(orch.store, run)
        grid = [{"node": row["name"], "progress": row["progress"], "failed": row["failed"],
                 "steps": [{"step": c["name"], "state": (c["exec"] or {}).get("state", "not started")} for c in row["cells"]]}
                for row in detail["grid"]]
        return _ok(f"{run.id} is {run.state}.", {
            "run_id": run.id, "state": run.state, "title": run.title, "paused_reason": run.paused_reason,
            "reason": run.reason, "summary": run.summary, "decision": (run.decision or {}).get("summary"),
            "excluded": run.excluded, "nodes": grid})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_run_results", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Results of a run: per execution the declared structured outputs, exit code, state and a "
                  "capped, redacted log tail. Raw logs and secret values are never returned.",
      parameters={"type": "object", "properties": {"run_id": {"type": "string"}, "node_id": {"type": "string"}},
                  "required": ["run_id"]})
def orch_run_results(args, context):
    try:
        from . import vm
        orch = _orch()
        run = orch.store.get_run(str(args.get("run_id") or ""))
        if run is None:
            raise ValueError("unknown run")
        names = {n.id: n.name for n in orch.store.list_nodes()}
        execs = orch.store.list_executions(run_id=run.id, node_id=str(args.get("node_id") or "") or None)
        out = []
        for ex in execs:
            view = vm.execution_vm(ex, for_ai=True)
            view["node"] = names.get(ex.node_id, ex.node_id)
            out.append(view)
        return _ok(f"{len(out)} execution(s) for {run.id} ({run.state}).", {"run_id": run.id, "state": run.state,
                                                                             "executions": out})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


_SPEC = {"description": "Runbook as an object or YAML text (id, title, summary, inputs, steps)"}


def _problems(items) -> list:
    return [f"{p['path']}: {p['message']}" if p.get("path") else p["message"] for p in items or []]


@tool(name="orch_runbook_draft", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Create or update a DRAFT runbook: ordered steps over published tool versions, with params "
                  "(literals or ${{ inputs.x }} / ${{ steps.<name>.outputs.y }}), optional when: conditions, "
                  "on_failure (stop | continue | rollback) and per-step node selectors. Validate first with "
                  "orch_runbook_validate. Pass base_digest (from a previous save) to refuse overwriting edits "
                  "the operator made in the editor since then.",
      parameters={"type": "object", "properties": {"spec": _SPEC,
                                                   "base_digest": {"type": "string",
                                                                   "description": "Digest from the last save"}},
                  "required": ["spec"]})
def orch_runbook_draft(args, context):
    try:
        data = _cmd(context, "saveRunbookDraft", spec=args.get("spec"), base_digest=args.get("base_digest") or "")
        return _ok(f"Runbook draft {data['ref']} saved.", {"ref": data["ref"], "digest": data["digest"],
                                                            "problems": _problems(data["problems"]),
                                                            "warnings": _problems(data["warnings"])})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_runbook_validate", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="read", preview_policy="none",
      description="Check a runbook spec without saving it: problems with their field path (unknown or later "
                  "step references, undeclared inputs, params the tool's schema rejects, missing required "
                  "params), the computed effect and the access decision, and each step's resolved tool "
                  "(inputs, outputs, newer published version).",
      parameters={"type": "object", "properties": {"spec": _SPEC}, "required": ["spec"]})
def orch_runbook_validate(args, context):
    try:
        data = _cmd(context, "validateRunbook", spec=args.get("spec"))
        steps = [{k: s.get(k) for k in ("name", "tool", "ok", "title", "effect", "status", "inputs", "outputs",
                                         "rollback", "latest_ref")} for s in data.get("steps_info") or []]
        errors = [p for p in data["problems"] if p.get("severity", "error") == "error"]
        warnings = [p for p in data["problems"] if p.get("severity") == "warning"]
        return _ok("valid" if data["ok"] else f"{len(errors)} problem(s)", {
            "ok": data["ok"], "problems": _problems(errors), "warnings": _problems(warnings),
            "effect": data.get("effect"), "decision": (data.get("decision") or {}).get("summary"), "steps": steps})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@tool(name="orch_runbook_test", group="orchestration", phase="orchestration", requires_approval=False,
      execution_kind="mutate", preview_policy="none",
      description="Propose a test of a DRAFT runbook on a canary node (check mode by default). Its steps must "
                  "use published tools. The operator approves it unless their access policy allows such tests; "
                  "poll orch_run_status for the outcome.",
      parameters={"type": "object", "properties": {
          "ref": {"type": "string", "description": "Runbook reference id@version"},
          "node_id": {"type": "string", "description": "A canary node (default: the first one)"},
          "params": {"type": "object", "description": "Runbook inputs"},
          "check_mode": {"type": "boolean", "default": True}}, "required": ["ref"]})
def orch_runbook_test(args, context):
    try:
        run = _cmd(context, "proposeRunbookTest", ref=args.get("ref"), node_id=args.get("node_id") or "",
                   params=args.get("params") or {}, check_mode=args.get("check_mode", True))["run"]
        started = run["state"] != "proposed"
        return _ok(f"Test {run['id']} {'started (allowed by the access policy)' if started else 'proposed; waiting for the operator to approve it'}.",
                   {"run_id": run["id"], "state": run["state"], "nodes": run["nodes"]})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
