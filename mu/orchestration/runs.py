"""Runs and executions on the master (plan §3.4, §5).

``build_plan`` is the plan view: it resolves the target selector against current nodes and
facts (excluded nodes are listed with the reason — no silent partial runs), validates params,
expands preconditions and runbook steps, computes batches and the access decision. ``propose``
freezes a plan as a ``proposed`` run; ``approve`` re-plans, freezes the node list and starts it.
``apply_tick`` applies ``scheduler`` actions for one run in one transaction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import access, manifest as mf, runbook as rb_mod, scheduler, selectors
from .models import EFFECT_RANK, Execution, Node, Run, ToolVersion
from .node import schema as jschema
from .states import (DISPATCHABLE, EDITABLE, EXEC_IN_FLIGHT, EXEC_LIVE, RUN_TERMINAL, check_exec, check_run, check_tool)
from .store import new_id

PROPOSAL_TTL_S = 24 * 3600.0
RUN_KINDS = ("tool", "runbook", "test", "runbook_test", "agent_update")
AGENT_TOOL = "mucli-node"      # the target id of agent update runs (not a catalog tool)
TEST_KINDS = frozenset({"test", "runbook_test"})       # canary tests of drafts
RUNBOOK_KINDS = frozenset({"runbook", "runbook_test"})  # steps render ${{ }} templates


def is_canary(node: Node) -> bool:
    return bool(node.canary or node.labels.get("canary") == "true")


class RunError(ValueError):
    pass


# --------------------------------------------------------------------- targeting

def target_mismatch(manifest: Mapping[str, Any], node: Node) -> Optional[str]:
    facts = node.facts or {}
    if facts.get("system") and facts.get("system") != "linux":
        return f"not Linux ({facts.get('system')})"
    if node.kind != "container" and facts and facts.get("systemd") is False:
        return "systemd is not running"
    provides = set(facts.get("provides") or [])
    missing = [r for r in (manifest.get("targets") or {}).get("requires") or [] if r not in provides]
    if missing:
        return "missing " + ", ".join(missing)
    return None


def resolve_selector(store, selector: Mapping[str, Any]) -> Tuple[List[Node], Optional[str]]:
    """Nodes the selector names (any status), or an error message."""
    selector = dict(selector or {})
    nodes = store.list_nodes()
    explicit = [str(n) for n in (selector.get("nodes") or [])]
    labels = str(selector.get("labels") or "").strip()
    groups = [str(g) for g in (selector.get("groups") or [])]
    if not explicit and not labels and not groups and not selector.get("all"):
        return [], "choose target nodes (labels, groups or explicit nodes)"
    try:
        label_match = selectors.compile_selector(labels)
    except selectors.SelectorError as exc:
        return [], str(exc)
    group_rows = {g["name"]: g for g in store.list_groups()}
    unknown = [g for g in groups if g not in group_rows]
    if unknown:
        return [], f"unknown group(s): {', '.join(unknown)}"
    chosen = [group_rows[g] for g in groups]
    if explicit:
        known = {n.id for n in nodes}
        missing = [n for n in explicit if n not in known]
        if missing:
            return [], f"unknown node(s): {', '.join(missing)}"
        nodes = [n for n in nodes if n.id in explicit]
    nodes = [n for n in nodes if label_match(n.labels)]
    if chosen:
        nodes = [n for n in nodes if any(selectors.in_group(g, n.id, n.labels) for g in chosen)]
    return nodes, None


def agent_update_block(node: Node) -> Optional[str]:
    """Why this node's agent can't be updated from here, or None."""
    from . import node as agent
    have = node.agent_version or "unknown"
    policy = node.policy or {}
    if agent.version_tuple(have) >= agent.version_tuple(agent.AGENT_VERSION):
        return f"already runs agent {have}"
    if node.kind == "container" or policy.get("sandbox") == "none":
        return "runs without the root helper (sandbox none): replace its agent or image instead"
    if agent.version_tuple(have) < agent.version_tuple(agent.SELF_UPDATE_SINCE):
        return f"agent {have} can't update itself: update it once by hand"
    if policy.get("errors") or policy.get("deny_all"):
        return "its local policy is invalid"
    if not policy.get("allow_agent_updates"):
        return "its policy.yaml doesn't allow agent updates (allow_agent_updates: true)"
    return None


def partition(nodes: Sequence[Node], *, test: bool = False, manifests: Sequence[Mapping[str, Any]] = (),
              agent_update: bool = False) -> Tuple[List[Node], List[Dict[str, Any]]]:
    """Split matched nodes into eligible ones and exclusions with the reason (no silent skips)."""
    included: List[Node] = []
    excluded: List[Dict[str, Any]] = []
    for node in nodes:
        reason = None
        if node.status != "active":
            reason = f"node is {node.status}"
        elif agent_update:
            reason = agent_update_block(node)
        elif test and not is_canary(node):
            reason = "test runs only target canary nodes"
        else:
            for m in manifests:
                reason = target_mismatch(m, node)
                if reason:
                    break
        if reason:
            excluded.append({"node_id": node.id, "name": node.name, "reason": reason})
        else:
            included.append(node)
    return included, excluded


def preview_selector(store, selector: Any, *, test: bool = False) -> Dict[str, Any]:
    """The nodes a selector reaches now (the plan view's resolution, without a tool)."""
    if isinstance(selector, str):
        selector = {"labels": selector} if selector.strip() not in ("", "*") else {"all": True}
    matched, err = resolve_selector(store, selector or {})
    included, excluded = partition(matched, test=test)
    from .vm import node_online
    now = store.now()
    return {"ok": err is None, "error": err or "", "count": len(included), "excluded_count": len(excluded),
            "nodes": [{"id": n.id, "name": n.name, "labels": n.labels, "online": node_online(n, now),
                       "canary": is_canary(n)} for n in included],
            "excluded": excluded}


def node_groups(store, node: Node, groups: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """The groups a node belongs to (a member, or the group's selector matches its labels)."""
    return [g["name"] for g in (store.list_groups() if groups is None else groups)
            if selectors.in_group(g, node.id, node.labels)]


# --------------------------------------------------------------------- plan

def _tool(store, ref: str, *, allow_draft: bool = False) -> Tuple[Optional[ToolVersion], Optional[str]]:
    parsed = mf.parse_ref(ref)
    if not parsed:
        return None, f"{ref!r} is not a pinned tool reference (id@version)"
    tv = store.get_version(*parsed)
    if tv is None:
        return None, f"tool {ref} does not exist"
    if allow_draft:
        if tv.status not in EDITABLE | {"in_review"} | DISPATCHABLE:
            return None, f"{ref} is {tv.status}"
    elif tv.status not in DISPATCHABLE:
        return None, f"{ref} is {tv.status}; only published versions run on the fleet"
    return tv, None


def _step(tv: ToolVersion, name: str, params: Any, *, when: str = "", on_failure: str = "stop", nodes: str = "",
          precondition: bool = False, test: bool = False) -> Dict[str, Any]:
    step = {"name": name, "tool_id": tv.tool_id, "version": tv.version, "params": params, "when": when,
            "on_failure": "stop" if precondition else on_failure, "nodes": nodes,
            "rollback": str(tv.manifest.get("rollback") or ""), "precondition": precondition, "test": test,
            "effect": tv.effect, "title": str(tv.manifest.get("title") or tv.tool_id)}
    if test:
        step["manifest_digest"] = tv.manifest_digest
        step["blobs"] = dict(tv.blobs)
    return step


def _expand(store, steps: List[Dict[str, Any]], tv: ToolVersion, errors: List[str], seen: set) -> None:
    for ref in tv.manifest.get("preconditions") or []:
        if ref in seen:
            continue
        pre, err = _tool(store, ref)
        if err:
            errors.append(f"precondition {err}")
            continue
        if pre.effect != "read_only":
            errors.append(f"precondition {ref} must be read-only")
            continue
        seen.add(ref)
        steps.append(_step(pre, f"pre-{pre.tool_id.replace('.', '-')}"[:48], {}, precondition=True))


def build_plan(store, *, actor: str, kind: str, target: str, params: Optional[Mapping[str, Any]] = None,
               selector: Optional[Mapping[str, Any]] = None, strategy: Optional[Mapping[str, Any]] = None,
               check_mode: bool = False) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    params = dict(params or {})
    if kind not in RUN_KINDS:
        raise RunError(f"kind must be one of {', '.join(RUN_KINDS)}")
    steps: List[Dict[str, Any]] = []
    manifests: List[Mapping[str, Any]] = []
    rollout: Mapping[str, Any] = {}
    target_info: Dict[str, Any] = {"ref": target, "kind": kind}
    policy_subject: Dict[str, Any] = {}
    if kind == "agent_update":
        # Not a catalog tool: the node agent itself, frozen by version and the zipapp digest.
        from .node import AGENT_VERSION
        from .zipapp import build_zipapp
        data, sha = build_zipapp()
        steps.append({"name": "update", "tool_id": AGENT_TOOL, "version": 0, "when": "", "on_failure": "stop",
                      "nodes": "", "rollback": "", "precondition": False, "test": False, "effect": "mutating",
                      "title": f"Update the node agent to {AGENT_VERSION}",
                      "params": {"version": AGENT_VERSION, "sha256": sha, "size": len(data)}})
        target_info.update(id=AGENT_TOOL, version=0, title=f"mucli-node {AGENT_VERSION}", status="published",
                           effect="mutating", agent_version=AGENT_VERSION, sha256=sha)
        policy_subject = {"id": AGENT_TOOL, "effect": "mutating", "category": "maintenance"}
        if check_mode:
            errors.append("agent updates have no check mode")
    elif kind in ("tool", "test"):
        tv, err = _tool(store, target, allow_draft=(kind == "test"))
        if err:
            raise RunError(err)
        filled, perr = mf.validate_params(tv.manifest, params)
        errors += perr
        params = filled
        if kind == "tool":
            _expand(store, steps, tv, errors, set())
        steps.append(_step(tv, "main", params, test=(kind == "test")))
        manifests.append(tv.manifest)
        rollout = tv.manifest.get("rollout") or {}
        target_info.update(id=tv.tool_id, version=tv.version, title=tv.manifest.get("title"), status=tv.status,
                           effect=tv.effect, idempotent=tv.manifest.get("idempotent"))
        policy_subject = dict(tv.manifest)
        if check_mode and not (tv.manifest.get("implementation") or {}).get("check_mode") and tv.effect != "read_only":
            errors.append(f"{tv.ref} does not support check mode")
    else:
        parsed = mf.parse_ref(target)
        rb = store.get_runbook_version(*parsed) if parsed else None
        if rb is None:
            raise RunError(f"runbook {target} does not exist")
        if kind == "runbook" and rb.status != "published":
            raise RunError(f"runbook {rb.ref} is {rb.status}; only published runbooks run")
        if kind == "runbook_test":
            from . import catalog
            if rb.status not in ("draft", "in_review"):
                raise RunError(f"runbook {rb.ref} is {rb.status}; tests are for drafts (run published runbooks)")
            if rb.source:
                raise RunError(f"{rb.ref}: the draft's YAML doesn't parse; fix it before testing")
            problems = catalog.runbook_problems(store, rb)
            if problems:
                raise RunError(f"{rb.ref} has problems to fix before testing: " + "; ".join(problems[:3]))
        filled = jschema.apply_defaults(params, rb.spec.get("inputs") or {})
        errors += jschema.validate(filled, rb.spec.get("inputs") or {}, "params")
        params = filled
        seen: set = set()
        effect = "read_only"
        approval = "none"
        for item in rb.spec.get("steps") or []:
            tv, err = _tool(store, item["tool"])
            if err:
                errors.append(f"step {item['name']}: {err}")
                continue
            _expand(store, steps, tv, errors, seen)
            steps.append(_step(tv, item["name"], item.get("params") or {}, when=item.get("when") or "",
                               on_failure=item.get("on_failure") or "stop", nodes=item.get("nodes") or ""))
            manifests.append(tv.manifest)
            if EFFECT_RANK[tv.effect] > EFFECT_RANK[effect]:
                effect = tv.effect
            if tv.manifest.get("approval") == "confirm":
                approval = "confirm"
            if check_mode and not (tv.manifest.get("implementation") or {}).get("check_mode") \
                    and tv.effect != "read_only":
                errors.append(f"step {item['name']}: {tv.ref} does not support check mode")
        target_info.update(id=rb.runbook_id, version=rb.version, title=rb.spec.get("title"), status=rb.status,
                           effect=effect, digest=rb.digest)
        policy_subject = {"id": rb.runbook_id, "effect": effect, "category": "other", "approval": approval}

    matched, sel_err = resolve_selector(store, selector or {})
    if sel_err:
        errors.append(sel_err)
    test = kind in TEST_KINDS
    included, excluded = partition(matched, test=test, manifests=manifests, agent_update=kind == "agent_update")
    if matched and not included:
        why = "; ".join(f"{x['name']}: {x['reason']}" for x in excluded[:5])
        errors.append(f"no eligible nodes: every matched node was excluded ({why})")
    max_nodes = int(rollout.get("max_nodes") or 10000)
    if len(included) > max_nodes:
        errors.append(f"{len(included)} nodes exceed this tool's rollout limit of {max_nodes}")
    strat = scheduler.normalize_strategy(strategy, rollout)
    if test:
        strat.update(canary=0, batch=max(1, len(included)), max_parallel=max(1, len(included)))
    canary_ids = [n.id for n in included if is_canary(n)]
    batches = scheduler.make_batches([n.id for n in included], strat, canary_ids)
    rules = effective_rules(store)
    # Tests are decided by the policy's ``run: test`` rules (default: confirm); canary-only
    # targeting, test-key signing, the digest freeze and typed confirmation for destructive
    # effects are enforced here and in the hub whatever the rules say.
    decision = access.decide(rules, actor, policy_subject, [{"id": n.id, "labels": n.labels} for n in included],
                             run="test" if test else "run")
    if decision.denied:
        errors.append("denied by access policy: " + decision.summary())
    secrets_needed = sorted({s for m in manifests for s in (m.get("secrets") or [])})
    rollback_available = all(bool(s.get("rollback")) for s in steps if not s["precondition"]
                             and s.get("effect") != "read_only") and any(s.get("rollback") for s in steps)
    effect = target_info.get("effect", "read_only")
    n_batches = len(batches)
    envs = sorted({n.labels.get("env", "") for n in included} - {""})
    radius = (f"{len(included)} node{'s' if len(included) != 1 else ''}"
              f"{' (' + ', '.join(envs) + ')' if envs else ''} in {n_batches} batch{'es' if n_batches != 1 else ''}")
    if kind == "agent_update":
        warnings.append("each node keeps its previous agent as /usr/local/lib/mucli-node/mucli-node.pyz.prev; "
                        "there is no automatic rollback (copy it back and restart mucli-node)")
    elif not test and effect != "read_only" and not check_mode and not rollback_available:
        warnings.append("no rollback tool: changes cannot be undone automatically")
    return {
        "ok": not errors, "errors": errors, "warnings": warnings, "kind": kind, "target": target_info,
        "params": params, "selector": dict(selector or {}), "check_mode": bool(check_mode),
        "steps": steps, "nodes": [n.id for n in included],
        "node_rows": [{"id": n.id, "name": n.name, "labels": n.labels} for n in included],
        "excluded": excluded, "batches": batches, "strategy": strat, "decision": decision.to_dict(),
        "effect": effect, "blast_radius": radius, "rollback_available": rollback_available,
        "secrets": secrets_needed,
    }


def effective_rules(store) -> List[Dict[str, Any]]:
    version, rules = store.access_rules()
    return rules if version else list(access.DEFAULT_RULES)


# --------------------------------------------------------------------- lifecycle

def propose(store, *, actor: str, kind: str, target: str, params=None, selector=None, strategy=None,
            check_mode: bool = False, title: str = "", parent_run: str = "", schedule_id: str = "") -> Run:
    plan = build_plan(store, actor=actor, kind=kind, target=target, params=params, selector=selector,
                      strategy=strategy, check_mode=check_mode)
    if not plan["ok"]:
        store.audit(actor, "run.refused", target, {"errors": plan["errors"][:10]})
        raise RunError("; ".join(plan["errors"][:5]))
    now = store.now()
    run = Run(id=new_id("r"), kind=kind, target_id=plan["target"]["id"], target_version=plan["target"]["version"],
              params=plan["params"], selector=plan["selector"], nodes=plan["nodes"], excluded=plan["excluded"],
              strategy=plan["strategy"], check_mode=bool(check_mode), state="proposed", proposed_by=actor,
              created_at=now, decision=plan["decision"], plan=_frozen(plan), parent_run=parent_run,
              schedule_id=schedule_id,
              title=title or f"{plan['target']['id']}@{plan['target']['version']} on {plan['blast_radius']}")
    with store.tx() as conn:
        store.insert_run(run, conn)
        store.audit(actor, "run.propose", run.id, {"target": run.target_ref, "kind": kind, "params": run.params,
                                                    "nodes": run.nodes, "check_mode": run.check_mode,
                                                    "decision": plan["decision"]["summary"]}, conn=conn)
        store.change("run", run.id, ["state"], conn=conn)
    return store.get_run(run.id)


def _frozen(plan: Mapping[str, Any]) -> Dict[str, Any]:
    keep = ("steps", "batches", "effect", "blast_radius", "rollback_available", "warnings", "secrets", "target",
            "node_rows")
    return {k: plan[k] for k in keep}


def approve(store, run_id: str, *, actor: str, typed: str = "", auto_rule: str = "",
            secrets_ready=None) -> Run:
    """Operator approval (or ``auto_rule`` when the access policy allows the run outright)."""
    if str(actor).startswith("ai"):
        raise RunError("only the operator can approve runs")
    run = store.get_run(run_id)
    if run is None:
        raise RunError(f"unknown run {run_id}")
    check_run(run.state, "approved")
    target = f"{run.target_id}@{run.target_version}"
    plan = build_plan(store, actor=run.proposed_by, kind=run.kind, target=target, params=run.params,
                      selector=run.selector, strategy=run.strategy, check_mode=run.check_mode)
    if not plan["ok"]:
        raise RunError("the run can no longer start: " + "; ".join(plan["errors"][:5]))
    frozen_digest = ((run.plan or {}).get("target") or {}).get("digest")
    if run.kind == "runbook_test" and frozen_digest and plan["target"].get("digest") != frozen_digest:
        raise RunError("the draft changed since this test was proposed; propose the test again")
    frozen_sha = ((run.plan or {}).get("target") or {}).get("sha256")
    if run.kind == "agent_update" and frozen_sha and plan["target"].get("sha256") != frozen_sha:
        raise RunError("the master's node agent changed since this update was proposed; propose it again")
    level = plan["decision"]["level"]
    if level == "deny":
        raise RunError("denied by access policy")
    if level == "typed" and typed.strip() != run.target_id:
        raise RunError(f"type the {'tool' if run.kind != 'runbook' else 'runbook'} id ({run.target_id}) to confirm")
    if plan["secrets"] and secrets_ready is not None:
        problem = secrets_ready(plan["secrets"])
        if problem:
            raise RunError(problem)
    now = store.now()
    frozen = _frozen(plan)
    for key in ("expect", "test_name"):
        if key in (run.plan or {}):
            frozen[key] = run.plan[key]
    with store.tx() as conn:
        store.update_run(run_id, conn, state="approved", approved_by=auto_rule and f"policy:{auto_rule}" or actor,
                         approved_at=now, nodes=plan["nodes"], excluded=plan["excluded"], plan=frozen,
                         decision=plan["decision"], strategy=plan["strategy"])
        if run.kind == "test":
            tv = store.get_version(run.target_id, run.target_version)
            if tv is not None and tv.status == "draft":
                check_tool("draft", "testing")
                store.update_version(tv.tool_id, tv.version, conn, status="testing", stage="test")
        store.audit(actor, "run.approve", run_id, {"typed": bool(typed), "rule": auto_rule,
                                                    "nodes": plan["nodes"]}, conn=conn)
        store.change("run", run_id, ["state"], conn=conn)
    return store.get_run(run_id)


def reject(store, run_id: str, *, actor: str, reason: str = "") -> Run:
    run = _get(store, run_id)
    check_run(run.state, "rejected")
    with store.tx() as conn:
        store.update_run(run_id, conn, state="rejected", finished_at=store.now(), reason=str(reason)[:500])
        store.audit(actor, "run.reject", run_id, {"reason": reason[:500]}, conn=conn)
        store.change("run", run_id, ["state"], conn=conn)
    return store.get_run(run_id)


def cancel(store, run_id: str, *, actor: str, reason: str = "") -> Run:
    run = _get(store, run_id)
    check_run(run.state, "cancelled")
    now = store.now()
    with store.tx() as conn:
        for ex in store.list_executions(run_id=run_id, states=EXEC_LIVE, conn=conn):
            if ex.state == "queued":
                store.update_execution(ex.id, conn, state="cancelled", finished_at=now, reason="run cancelled")
            else:
                store.update_execution(ex.id, conn, cancel_requested=True)
        store.update_run(run_id, conn, state="cancelled", finished_at=now, reason=str(reason or "cancelled")[:500])
        store.audit(actor, "run.cancel", run_id, {"reason": reason[:500]}, conn=conn)
        store.change("run", run_id, ["state", "executions"], conn=conn)
    return store.get_run(run_id)


def resume(store, run_id: str, *, actor: str) -> Run:
    run = _get(store, run_id)
    check_run(run.state, "running")
    summary = dict(run.summary or {})
    summary["evaluated_batch"] = int(run.batch)
    with store.tx() as conn:
        store.update_run(run_id, conn, state="running", paused_reason="", summary=summary)
        store.audit(actor, "run.continue", run_id, {"batch": run.batch}, conn=conn)
        store.change("run", run_id, ["state"], conn=conn)
    return store.get_run(run_id)


def pause(store, run_id: str, *, actor: str, reason: str = "paused by the operator") -> Run:
    run = _get(store, run_id)
    check_run(run.state, "paused")
    with store.tx() as conn:
        store.update_run(run_id, conn, state="paused", paused_reason=reason)
        store.audit(actor, "run.pause", run_id, {"reason": reason}, conn=conn)
        store.change("run", run_id, ["state"], conn=conn)
    return store.get_run(run_id)


def start_rollback(store, run_id: str, *, actor: str) -> Run:
    run = _get(store, run_id)
    check_run(run.state, "rolling_back")
    steps = list((run.plan or {}).get("steps") or [])
    with store.tx() as conn:
        execs = store.list_executions(run_id=run_id, conn=conn)
        for ex in execs:
            if ex.state == "queued" and not ex.rollback_of:
                store.update_execution(ex.id, conn, state="cancelled", finished_at=store.now(),
                                       reason="run is rolling back")
            elif ex.state in EXEC_IN_FLIGHT and not ex.rollback_of:
                store.update_execution(ex.id, conn, cancel_requested=True)
        by_node: Dict[str, List[Execution]] = {}
        for ex in execs:
            by_node.setdefault(ex.node_id, []).append(ex)
        queued = 0
        for node_id, node_execs in by_node.items():
            for target in scheduler.rollback_targets(steps, node_execs):
                _insert_rollback(store, conn, run, steps, target)
                queued += 1
        store.update_run(run_id, conn, state="rolling_back", paused_reason="")
        store.audit(actor, "run.rollback", run_id, {"rollbacks": queued}, conn=conn)
        store.change("run", run_id, ["state", "executions"], conn=conn)
    return store.get_run(run_id)


def retry_node(store, run_id: str, node_id: str, *, actor: str) -> Run:
    """Re-run a node's failed/unreachable step (a human decision after an unreachable node)."""
    run = _get(store, run_id)
    if run.state not in ("running", "paused"):
        raise RunError("only running or paused runs can retry a node")
    steps = list((run.plan or {}).get("steps") or [])
    with store.tx() as conn:
        execs = [e for e in store.list_executions(run_id=run_id, node_id=node_id, conn=conn) if not e.rollback_of]
        progress = scheduler.node_progress(steps, execs)
        if progress.status != "failed" or progress.step is None:
            raise RunError("that node has nothing to retry")
        latest = max((e for e in execs if e.step == progress.step), key=lambda e: e.created_at)
        _queue_step(store, conn, run, steps, node_id, progress.step, attempts=latest.attempts)
        store.audit(actor, "run.retry_node", run_id, {"node": node_id, "step": progress.step}, conn=conn)
        store.change("run", run_id, ["executions"], conn=conn)
    return store.get_run(run_id)


def _get(store, run_id: str) -> Run:
    run = store.get_run(run_id)
    if run is None:
        raise RunError(f"unknown run {run_id}")
    return run


# --------------------------------------------------------------------- queueing

def _step_context(store, conn, run: Run, steps, node_id: str) -> Dict[str, Any]:
    node = store.get_node(node_id)
    outputs: Dict[str, Any] = {}
    for ex in store.list_executions(run_id=run.id, node_id=node_id, conn=conn):
        if ex.rollback_of or ex.step >= len(steps):
            continue
        outputs[steps[ex.step]["name"]] = {"outputs": ex.output, "state": ex.state, "exit_code": ex.exit_code}
    return {"inputs": run.params, "steps": outputs,
            "node": {"id": node_id, "name": node.name if node else "", "labels": node.labels if node else {},
                     "facts": node.facts if node else {}}}


def _queue_step(store, conn, run: Run, steps, node_id: str, index: int, *, attempts: int = 0) -> Execution:
    step = steps[index]
    now = store.now()
    if run.kind == "agent_update":
        ex = Execution(id=new_id("x"), run_id=run.id, node_id=node_id, tool_id=AGENT_TOOL, version=0, step=index,
                       step_name=step["name"], batch=int(run.batch), created_at=now, attempts=attempts,
                       params=dict(step.get("params") or {}))
        store.insert_execution(ex, conn)
        return ex
    tv = store.get_version(step["tool_id"], int(step["version"]))
    ex = Execution(id=new_id("x"), run_id=run.id, node_id=node_id, tool_id=step["tool_id"],
                   version=int(step["version"]), step=index, step_name=step["name"], batch=int(run.batch),
                   created_at=now, check_mode=bool(run.check_mode) and not step.get("precondition"),
                   idempotent=bool(tv and tv.manifest.get("idempotent")), attempts=attempts)
    node = store.get_node(node_id)
    try:
        if step.get("nodes") and not selectors.matches(step["nodes"], node.labels if node else {}):
            ex.state, ex.reason, ex.finished_at = "skipped", f"node does not match {step['nodes']}", now
        else:
            ctx = _step_context(store, conn, run, steps, node_id)
            if step.get("when") and not rb_mod.evaluate(step["when"], ctx):
                ex.state, ex.reason, ex.finished_at = "skipped", f"when: {step['when']} is false", now
            else:
                rendered = rb_mod.render(step.get("params") or {}, ctx) if run.kind in RUNBOOK_KINDS \
                    else step.get("params") or {}
                if tv is None:
                    raise RunError(f"tool {step['tool_id']}@{step['version']} vanished")
                if run.kind == "runbook_test":
                    _check_runbook_frozen(store, run)
                filled, errors = mf.validate_params(tv.manifest, rendered)
                if errors:
                    ex.state, ex.reason, ex.finished_at = "failed", "invalid parameters: " + "; ".join(errors[:3]), now
                ex.params = filled
    except (rb_mod.ExprError, RunError) as exc:
        ex.state, ex.reason, ex.finished_at = "failed", str(exc), now
    store.insert_execution(ex, conn)
    return ex


def _check_runbook_frozen(store, run: Run) -> None:
    """A runbook test runs exactly the draft that was approved."""
    frozen = ((run.plan or {}).get("target") or {}).get("digest")
    rb = store.get_runbook_version(run.target_id, run.target_version)
    if rb is None or not frozen or rb.digest != frozen:
        raise RunError("the draft changed after this test was approved; propose the test again")


def _insert_rollback(store, conn, run: Run, steps, target: Execution) -> Optional[Execution]:
    ref = steps[target.step].get("rollback") if target.step < len(steps) else ""
    parsed = mf.parse_ref(ref or "")
    tv = store.get_version(*parsed) if parsed else None
    now = store.now()
    ex = Execution(id=new_id("x"), run_id=run.id, node_id=target.node_id, tool_id=parsed[0] if parsed else "",
                   version=parsed[1] if parsed else 0, step=target.step, step_name=f"rollback {steps[target.step]['name']}",
                   batch=target.batch, created_at=now, rollback_of=target.id,
                   idempotent=bool(tv and tv.manifest.get("idempotent")))
    if tv is None or tv.status not in DISPATCHABLE:
        ex.state, ex.reason, ex.finished_at = "failed", f"rollback tool {ref} is not published", now
    else:
        props = set(((tv.manifest.get("inputs") or {}).get("properties") or {}).keys())
        params = {k: v for k, v in (target.params or {}).items() if k in props}
        filled, errors = mf.validate_params(tv.manifest, params)
        if errors:
            ex.state, ex.reason, ex.finished_at = "failed", "rollback parameters: " + "; ".join(errors[:3]), now
        ex.params = filled
    store.insert_execution(ex, conn)
    return ex


# --------------------------------------------------------------------- scheduler application

def _timeouts(store, steps, execs) -> Dict[Tuple[str, int], float]:
    out: Dict[Tuple[str, int], float] = {}
    for key in {(e.tool_id, e.version) for e in execs}:
        tv = store.get_version(*key)
        out[key] = float(((tv.manifest.get("limits") or {}).get("timeout_s")) if tv else 300)
    return out


def apply_tick(store, run_id: str, *, now: float, dispatch_paused: bool = False,
               config: Optional[Mapping[str, float]] = None, on_attention=None) -> List[scheduler.Action]:
    applied: List[scheduler.Action] = []
    with store.tx() as conn:
        run = store.get_run(run_id, conn)
        if run is None or run.state in RUN_TERMINAL:
            return []
        if run.state == "approved":
            check_run("approved", "running")
            store.update_run(run_id, conn, state="running", started_at=now, batch=0)
            store.audit("system", "run.start", run_id, {}, conn=conn)
            store.change("run", run_id, ["state"], conn=conn)
            run = store.get_run(run_id, conn)
        execs = store.list_executions(run_id=run_id, conn=conn)
        nodes = {n.id: {"last_seen": n.last_seen, "status": n.status} for n in store.list_nodes()}
        steps = list((run.plan or {}).get("steps") or [])
        health = scheduler.health(execs, nodes, now, timeouts=_timeouts(store, steps, execs), config=config)
        for action in health:
            ex = next(e for e in execs if e.id == action.exec_id)
            if action.kind == "timeout":
                check_exec(ex.state, "timed_out")
                store.update_execution(ex.id, conn, state="timed_out", finished_at=now, reason=action.reason,
                                       cancel_requested=True)
            elif action.kind == "requeue":
                check_exec(ex.state, "queued")
                store.update_execution(ex.id, conn, state="queued", dispatched_at=None, started_at=None,
                                       reason=action.reason)
            elif action.kind == "unreachable":
                check_exec(ex.state, "unreachable")
                store.update_execution(ex.id, conn, state="unreachable", finished_at=now, reason=action.reason)
            store.audit("system", f"execution.{action.kind}", ex.id, {"run": run_id, "node": ex.node_id,
                                                                       "reason": action.reason}, conn=conn)
            applied.append(action)
        if health:
            execs = store.list_executions(run_id=run_id, conn=conn)
            store.change("run", run_id, ["executions"], conn=conn)
        if run.state in ("running", "rolling_back"):
            ineligible = {nid: f"node is {info['status']}" for nid, info in nodes.items()
                          if info["status"] in ("quarantined", "revoked")}
            actions = scheduler.tick(run, execs, now, dispatch_paused=dispatch_paused, eligible=ineligible)
            for action in actions:
                _apply(store, conn, run, steps, action, now, on_attention)
                applied.append(action)
            if actions:
                store.change("run", run_id, ["state", "executions"], conn=conn)
        execs = store.list_executions(run_id=run_id, conn=conn)
        run = store.get_run(run_id, conn)
        summary = dict(run.summary or {})
        summary.update(scheduler.summarize(run, execs))
        store.update_run(run_id, conn, summary=summary)
    return applied


def _apply(store, conn, run: Run, steps, action: scheduler.Action, now: float, on_attention) -> None:
    kind = action.kind
    if kind == "queue":
        _queue_step(store, conn, run, steps, action.node_id, action.step)
    elif kind == "skip":
        step = steps[action.step]
        ex = Execution(id=new_id("x"), run_id=run.id, node_id=action.node_id, tool_id=step["tool_id"],
                       version=int(step["version"]), step=action.step, step_name=step["name"], batch=int(run.batch),
                       created_at=now, finished_at=now, state=action.state or "cancelled", reason=action.reason)
        store.insert_execution(ex, conn)
    elif kind == "rollback":
        target = store.get_execution(action.exec_id, conn)
        if target is not None:
            _insert_rollback(store, conn, run, steps, target)
            store.audit("system", "execution.rollback", target.id, {"run": run.id, "reason": action.reason},
                        conn=conn)
    elif kind == "pause":
        check_run(run.state, "paused")
        store.update_run(run.id, conn, state="paused", paused_reason=action.reason)
        store.audit("system", "run.pause", run.id, {"reason": action.reason, "batch": action.batch}, conn=conn)
        if on_attention:
            on_attention({"run_id": run.id, "state": "paused", "reason": action.reason})
    elif kind == "evaluated":
        summary = dict(run.summary or {})
        summary["evaluated_batch"] = action.batch
        store.update_run(run.id, conn, summary=summary)
        run.summary = summary
    elif kind == "batch_finished":
        store.update_run(run.id, conn, batch_finished_at=now)
    elif kind == "advance":
        store.update_run(run.id, conn, batch=action.batch, batch_finished_at=None)
        store.audit("system", "run.batch", run.id, {"batch": action.batch}, conn=conn)
    elif kind == "finish":
        check_run(run.state, action.state)
        store.update_run(run.id, conn, state=action.state, finished_at=now, reason=action.reason)
        store.audit("system", f"run.{action.state}", run.id, {"reason": action.reason}, conn=conn)
        if run.kind == "test":
            _record_test(store, conn, run, now)
        elif run.kind == "runbook_test":
            _record_runbook_test(store, conn, run, now)
        if on_attention and action.state in ("failed",):
            on_attention({"run_id": run.id, "state": action.state, "reason": action.reason})


def expire_proposals(store, now: float) -> int:
    count = 0
    for run in store.list_runs(states=["proposed"], limit=1000):
        if now - run.created_at > PROPOSAL_TTL_S:
            with store.tx() as conn:
                store.update_run(run.id, conn, state="expired", finished_at=now, reason="not approved within 24h")
                store.audit("system", "run.expire", run.id, {}, conn=conn)
                store.change("run", run.id, ["state"], conn=conn)
            count += 1
    return count


# --------------------------------------------------------------------- tool tests

def evaluate_expectation(expect: Mapping[str, Any], ex: Execution, manifest: Mapping[str, Any]) -> List[str]:
    problems = []
    if "exit_code" in expect and ex.exit_code != expect["exit_code"]:
        problems.append(f"exit code {ex.exit_code} != {expect['exit_code']}")
    if expect.get("output_schema") and (ex.output_errors or (manifest.get("outputs") and ex.output is None)):
        problems.append("output does not match the declared schema")
    for key, value in (expect.get("output") or {}).items():
        if not isinstance(ex.output, dict) or ex.output.get(key) != value:
            problems.append(f"output.{key} != {value!r}")
    if expect.get("state", "succeeded") != ex.state:
        problems.append(f"state {ex.state} != {expect.get('state', 'succeeded')}")
    return problems


def _record_test(store, conn, run: Run, now: float) -> None:
    tv = store.get_version(run.target_id, run.target_version)
    if tv is None:
        return
    execs = store.list_executions(run_id=run.id, conn=conn)
    expect = dict((run.plan or {}).get("expect") or {})
    results = []
    for ex in execs:
        problems = evaluate_expectation(expect, ex, tv.manifest) if expect else \
            ([] if ex.state == "succeeded" else [ex.reason or ex.state])
        results.append({"node_id": ex.node_id, "state": ex.state, "passed": not problems, "problems": problems,
                        "exit_code": ex.exit_code, "duration_s": ex.duration_s})
    review = dict(tv.review or {})
    tests = list(review.get("tests") or [])
    tests.append({"run_id": run.id, "at": now, "name": (run.plan or {}).get("test_name") or "ad-hoc",
                  "check_mode": run.check_mode, "digest": tv.manifest_digest,
                  "passed": bool(results) and all(r["passed"] for r in results), "results": results})
    review["tests"] = tests[-20:]
    store.update_version(tv.tool_id, tv.version, conn, review=review)
    store.change("tool", tv.tool_id, ["catalog"], conn=conn)


def _record_runbook_test(store, conn, run: Run, now: float) -> None:
    rb = store.get_runbook_version(run.target_id, run.target_version)
    if rb is None:
        return
    steps = list((run.plan or {}).get("steps") or [])
    execs = [e for e in store.list_executions(run_id=run.id, conn=conn) if not e.rollback_of]
    results = []
    for node_id in run.nodes or []:
        mine = [e for e in execs if e.node_id == node_id]
        problems = []
        for ex in mine:
            step = steps[ex.step] if ex.step < len(steps) else {}
            if ex.state in ("succeeded", "skipped"):
                continue
            if step.get("on_failure") == "continue" and ex.state != "refused":
                continue
            problems.append(f"{ex.step_name}: {ex.reason or ex.state}")
        if not mine:
            problems.append("nothing ran on this node")
        results.append({"node_id": node_id, "state": scheduler.node_progress(steps, mine).status,
                        "passed": not problems, "problems": problems,
                        "steps": [{"name": e.step_name, "state": e.state, "reason": e.reason} for e in mine],
                        "duration_s": sum(e.duration_s or 0 for e in mine)})
    review = dict(rb.review or {})
    tests = list(review.get("tests") or [])
    tests.append({"run_id": run.id, "at": now, "name": run.title or "ad-hoc", "check_mode": run.check_mode,
                  "digest": ((run.plan or {}).get("target") or {}).get("digest") or rb.digest,
                  "passed": bool(results) and all(r["passed"] for r in results), "results": results})
    review["tests"] = tests[-20:]
    store.update_runbook_version(rb.runbook_id, rb.version, conn, review=review)
    store.change("tool", rb.runbook_id, ["catalog"], conn=conn)


def propose_runbook_test(store, *, actor: str, runbook_id: str, version: int, node_ids: Sequence[str],
                         params: Optional[Mapping[str, Any]] = None, check_mode: bool = True) -> Run:
    """A canary run of a draft runbook. Its steps use published tools, so dispatch is unchanged;
    the plan freezes the runbook digest and dispatch refuses if the draft changes afterwards."""
    rb = store.get_runbook_version(runbook_id, version)
    if rb is None:
        raise RunError(f"unknown runbook {runbook_id}@{version}")
    return propose(store, actor=actor, kind="runbook_test", target=rb.ref, params=params or {},
                   selector={"nodes": list(node_ids)}, check_mode=check_mode,
                   title=f"Test {rb.ref}{' (check mode)' if check_mode else ''}")


def propose_test(store, *, actor: str, tool_id: str, version: int, node_ids: Sequence[str],
                 params: Optional[Mapping[str, Any]] = None, check_mode: bool = True, test_name: str = "") -> Run:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise RunError(f"unknown tool version {tool_id}@{version}")
    expect: Dict[str, Any] = {}
    if test_name:
        case = next((t for t in tv.manifest.get("tests") or [] if t.get("name") == test_name), None)
        if case is None:
            raise RunError(f"{tv.ref} has no test named {test_name!r}")
        params = case.get("params") or {}
        check_mode = bool(case.get("check_mode", True))
        expect = dict(case.get("expect") or {})
    if check_mode and not (tv.manifest.get("implementation") or {}).get("check_mode") and tv.effect != "read_only":
        raise RunError(f"{tv.ref} does not support check mode; test it with check_mode false on a disposable node")
    run = propose(store, actor=actor, kind="test", target=tv.ref, params=params or {},
                  selector={"nodes": list(node_ids)}, check_mode=check_mode,
                  title=f"Test {tv.ref}{' — ' + test_name if test_name else ''}")
    plan = dict(run.plan or {})
    plan["expect"] = expect
    plan["test_name"] = test_name
    with store.tx() as conn:
        store.update_run(run.id, conn, plan=plan)
        if tv.stage in ("intent", "spec", "discover", "implement"):
            store.update_version(tv.tool_id, tv.version, conn, stage="test")
    return store.get_run(run.id)
