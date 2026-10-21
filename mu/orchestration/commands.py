"""The only mutation path (plan §3.4, §6.1), shared by the web UI, mobile, CLI and AI tools.

``dispatch(orch, {"type": ..., "args": {...}}, actor=...)`` checks who is asking, re-checks the
subject's available actions, performs the change through the domain modules (which audit it
and record a change for the live views) and returns a ``Result``.

Actors: ``operator`` (GUI/CLI), ``ai:<session>`` (orchestration tools), ``schedule:<id>``.
The AI may only use ``AI_COMMANDS``: it can draft, lint, submit for review, propose runs and
request tests — never publish, approve, dispatch, discard, change policy, network or secrets.
A test proposal starts without a person only when the operator's access policy has a
``run: test`` rule that allows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping

from . import access, actions as act, catalog, enroll, overlay, runs, schedules, selectors
from .manifest import parse_ref
from .secrets import SecretsLocked
from .states import TransitionError

AI_COMMANDS = frozenset({"createDraft", "updateDraft", "lintDraft", "submitReview", "planRun", "proposeRun", "proposeTest",
                         "saveRunbookDraft", "validateRunbook", "newRunbookVersion", "proposeRunbookTest",
                         "upgradeRunbookTools", "previewSelector"})


class CommandRejected(RuntimeError):
    """Not allowed now (disabled action, wrong actor, illegal transition): HTTP 409."""

    def __init__(self, message: str, data=None):
        super().__init__(message)
        self.data = data


class CommandError(ValueError):
    """Malformed or invalid input: HTTP 400."""


@dataclass
class Result:
    ok: bool
    command: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "command": self.command, "data": self.data}


def _is_ai(actor: str) -> bool:
    return str(actor or "").startswith("ai")


def _arg(args: Mapping[str, Any], name: str, required: bool = True) -> Any:
    value = args.get(name)
    if required and (value is None or value == ""):
        raise CommandError(f"{name} is required")
    return value


def _version(args: Mapping[str, Any], id_key: str = "tool_id") -> tuple:
    ref = args.get("ref")
    if ref:
        parsed = parse_ref(str(ref))
        if not parsed:
            raise CommandError(f"{ref!r} is not an id@version reference")
        return parsed
    try:
        return str(_arg(args, id_key)), int(_arg(args, "version"))
    except (TypeError, ValueError):
        raise CommandError("version must be a number")


HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {}


def handler(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def dispatch(orch, payload: Mapping[str, Any], *, actor: str = "operator") -> Result:
    if not isinstance(payload, Mapping):
        raise CommandError("command must be an object")
    ctype = str(payload.get("type") or payload.get("command") or "").strip()
    args = payload.get("args")
    if args is None:
        args = {k: v for k, v in payload.items() if k not in ("type", "command")}
    if not isinstance(args, Mapping):
        raise CommandError("args must be an object")
    fn = HANDLERS.get(ctype)
    if fn is None:
        raise CommandError(f"unknown command {ctype!r}")
    if _is_ai(actor) and ctype not in AI_COMMANDS:
        orch.store.audit(actor, "command.refused", ctype, {"reason": "not available to the AI"})
        raise CommandRejected(f"{ctype} requires the operator; the AI cannot do this")
    try:
        data = fn(orch, dict(args), actor)
    except (CommandRejected, CommandError):
        raise
    except catalog.DraftConflict as exc:
        current = exc.current
        raise CommandRejected(str(exc), {"conflict": True, "runbook": current.to_dict() if current else None,
                                         "digest": current.digest if current else ""}) from exc
    except TransitionError as exc:
        raise CommandRejected(str(exc)) from exc
    except SecretsLocked as exc:
        raise CommandRejected(str(exc)) from exc
    except (catalog.CatalogError, runs.RunError, enroll.EnrollError, schedules.ScheduleError,
            selectors.SelectorError, overlay.BindRefused) as exc:
        raise CommandError(str(exc)) from exc
    orch.wake()
    return Result(True, ctype, data or {})


def _require(actions: List, command: str) -> None:
    reason = act.check_allowed(actions, command)
    if reason:
        raise CommandRejected(reason)


# --------------------------------------------------------------------- nodes

def _node(orch, args):
    node = orch.store.get_node(str(_arg(args, "node_id")))
    if node is None:
        raise CommandError("unknown node")
    return node


@handler("createJoinToken")
def _create_token(orch, args, actor):
    from .zipapp import install_command
    labels = args.get("labels") or {}
    if isinstance(labels, str):
        labels = dict(part.split("=", 1) for part in labels.split(",") if "=" in part)
    token = enroll.create_join_token(orch.store, labels=labels, canary=bool(args.get("canary")),
                                     ttl_s=float(args.get("ttl_s") or enroll.TOKEN_TTL_S),
                                     note=str(args.get("note") or "")[:200], actor=actor)
    url = orch.master_url()
    token["master_url"] = url
    token["command"] = install_command(orch, token["token"], str(args.get("name") or "")) if url else ""
    token["fingerprint"] = orch.keys.tls_identity([orch.network().get("bind") or "",
                                                   orch.network().get("advertise") or ""]).display_fingerprint
    orch.remember_setup_token(token, name=str(args.get("name") or ""))
    return token


def _node_status(orch, args, actor, cmd, status):
    node = _node(orch, args)
    _require(act.node_actions(node), cmd)
    return {"node": enroll.set_status(orch.store, node.id, status, actor=actor,
                                      reason=str(args.get("reason") or "")).to_dict()}


@handler("acceptNode")
def _accept(orch, args, actor):
    return _node_status(orch, args, actor, "acceptNode", "active")


@handler("quarantineNode")
def _quarantine(orch, args, actor):
    return _node_status(orch, args, actor, "quarantineNode", "quarantined")


@handler("unquarantineNode")
def _unquarantine(orch, args, actor):
    return _node_status(orch, args, actor, "unquarantineNode", "active")


@handler("revokeNode")
def _revoke(orch, args, actor):
    return _node_status(orch, args, actor, "revokeNode", "revoked")


@handler("setNodeLabels")
def _labels(orch, args, actor):
    node = _node(orch, args)
    _require(act.node_actions(node), "setNodeLabels")
    labels = args.get("labels") or {}
    if not isinstance(labels, Mapping):
        raise CommandError("labels must be an object")
    labels = {str(k): str(v) for k, v in labels.items()}
    problems = selectors.validate_labels(labels)
    if problems:
        raise CommandError("; ".join(problems))
    with orch.store.tx() as conn:
        orch.store.update_node(node.id, conn=conn, labels=labels)
        orch.store.audit(actor, "node.labels", node.id, {"from": node.labels, "to": labels}, conn=conn)
        orch.store.change("node", node.id, ["state"], conn=conn)
    return {"node": orch.store.get_node(node.id).to_dict()}


@handler("setNodeCanary")
def _canary(orch, args, actor):
    node = _node(orch, args)
    _require(act.node_actions(node), "setNodeCanary")
    value = bool(args.get("canary", not node.canary))
    with orch.store.tx() as conn:
        orch.store.update_node(node.id, conn=conn, canary=value)
        orch.store.audit(actor, "node.canary", node.id, {"canary": value}, conn=conn)
        orch.store.change("node", node.id, ["state"], conn=conn)
    return {"node": orch.store.get_node(node.id).to_dict()}


@handler("rotateCredential")
def _rotate(orch, args, actor):
    node = _node(orch, args)
    _require(act.node_actions(node), "rotateCredential")
    with orch.store.tx() as conn:
        orch.store.update_node(node.id, conn=conn, credential_rotated_at=0.0)
        orch.store.audit(actor, "node.rotate_requested", node.id, {}, conn=conn)
    return {"node_id": node.id, "note": "a new credential is offered on the node's next poll"}


@handler("verifyNode")
def _verify_node(orch, args, actor):
    """The setup wizard's round trip: an ordinary run of the read-only ``sys.facts`` on this node,
    planned, policy-checked, signed, dispatched and audited like any other run."""
    node = _node(orch, args)
    if node.status != "active":
        raise CommandRejected(f"accept the node first (it is {node.status})")
    tv = orch.store.latest_version("sys.facts", published_only=True)
    if tv is None:
        raise CommandRejected("sys.facts isn't installed: install the starter pack (Settings > Starter pack) and "
                              "try again", {"missing_tool": "sys.facts"})
    run = orch.create_run(actor=actor, kind="tool", target=tv.ref, selector={"nodes": [node.id]},
                          title=f"Setup check: {node.name}")
    orch.store.audit(actor, "node.verify", node.id, {"run": run.id, "tool": tv.ref})
    return {"run": run.to_dict(), "tool": tv.ref}


@handler("proposeAgentUpdate")
def _propose_agent_update(orch, args, actor):
    """An update job for out-of-date node agents (all updatable nodes, or ``nodes``). Operator only:
    an agent update replaces code that runs as root on the node."""
    nodes = [str(n) for n in (args.get("nodes") or ([args["node_id"]] if args.get("node_id") else []))]
    run = orch.propose_agent_update(actor=actor, node_ids=nodes or None, strategy=args.get("strategy") or {})
    return {"run": run.to_dict()}


@handler("setAgentUpdates")
def _set_agent_updates(orch, args, actor):
    settings = orch.agent_update_settings()
    settings["auto"] = bool(args.get("auto"))
    orch.store.set_setting("agent_updates", settings)
    orch.store.audit(actor, "settings.agent_updates", "", {"auto": settings["auto"]})
    orch.store.change("node", "", ["state"])
    return {"agent_updates": settings}


@handler("refreshFacts")
def _refresh(orch, args, actor):
    node = _node(orch, args)
    _require(act.node_actions(node), "refreshFacts")
    orch.store.update_node(node.id, facts_at=None)
    return {"node_id": node.id}


# --------------------------------------------------------------------- groups

def _group_name(name: str) -> str:
    name = str(name or "").strip()
    if not selectors.LABEL_KEY.match(name):
        raise CommandError("group names use letters, digits, '-', '_' and '.'")
    return name


def _known_nodes(orch, ids) -> List[str]:
    ids = [str(n) for n in (ids or []) if str(n)]
    known = {n.id for n in orch.store.list_nodes()}
    missing = [n for n in ids if n not in known]
    if missing:
        raise CommandError(f"unknown node(s): {', '.join(missing)}")
    return ids


def _existing_group(orch, name: str) -> Dict[str, Any]:
    group = orch.store.get_group(str(name or ""))
    if group is None:
        raise CommandError(f"unknown group {name!r}")
    return group


@handler("putGroup")
def _put_group(orch, args, actor):
    """Create or update a group: a name, an optional label selector (empty = members only), a
    description, and optionally the full member list."""
    name = _group_name(_arg(args, "name"))
    current = orch.store.get_group(name)
    selector = str(args.get("selector", current["selector"] if current else "") or "").strip()
    if selector:
        selectors.parse(selector)
    description = str(args.get("description", current["description"] if current else "") or "")
    members = _known_nodes(orch, args["members"]) if "members" in args else None
    with orch.store.conn() as conn:
        orch.store.put_group(name, selector, description, conn=conn)
        if members is not None:
            conn.execute("DELETE FROM group_members WHERE group_name=?", (name,))
            orch.store.add_group_members(name, members, conn=conn)
    orch.store.audit(actor, "group.put", name, {"selector": selector, "members": members})
    orch.store.change("node", "", ["state"])
    return {"name": name, "created": current is None}


@handler("addToGroup")
def _add_to_group(orch, args, actor):
    """Put nodes in a group (drag and drop). With ``from``, they leave that group too (a move)."""
    name = _existing_group(orch, _arg(args, "name"))["name"]
    nodes = _known_nodes(orch, _arg(args, "node_ids"))
    source = str(args.get("from") or "")
    if source and source != name:
        _existing_group(orch, source)
    with orch.store.conn() as conn:
        added = orch.store.add_group_members(name, nodes, conn=conn)
        removed = orch.store.remove_group_members(source, nodes, conn=conn) if source and source != name else 0
    orch.store.audit(actor, "group.add", name, {"nodes": nodes, "from": source or None})
    orch.store.change("node", "", ["state"])
    return {"name": name, "added": added, "removed_from_source": removed}


@handler("removeFromGroup")
def _remove_from_group(orch, args, actor):
    """Take nodes out of a group's members. A node the group's selector matches stays in it."""
    group = _existing_group(orch, _arg(args, "name"))
    nodes = [str(n) for n in (_arg(args, "node_ids") or [])]
    removed = orch.store.remove_group_members(group["name"], nodes)
    still = [n.id for n in orch.store.list_nodes()
             if n.id in nodes and selectors.in_group(dict(group, members=[]), n.id, n.labels)]
    orch.store.audit(actor, "group.remove", group["name"], {"nodes": nodes})
    orch.store.change("node", "", ["state"])
    return {"name": group["name"], "removed": removed, "still_matched_by_selector": still}


@handler("renameGroup")
def _rename_group(orch, args, actor):
    old = _existing_group(orch, _arg(args, "name"))["name"]
    new = _group_name(_arg(args, "new_name"))
    try:
        orch.store.rename_group(old, new)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    orch.store.audit(actor, "group.rename", old, {"to": new})
    orch.store.change("node", "", ["state"])
    return {"name": new}


@handler("deleteGroup")
def _delete_group(orch, args, actor):
    name = str(_arg(args, "name"))
    if not orch.store.delete_group(name):
        raise CommandError("unknown group")
    orch.store.audit(actor, "group.delete", name, {})
    orch.store.change("node", "", ["state"])
    return {"name": name}


# --------------------------------------------------------------------- tools

def _tv(orch, args):
    tool_id, version = _version(args)
    tv = orch.store.get_version(tool_id, version)
    if tv is None:
        raise CommandError(f"unknown tool version {tool_id}@{version}")
    return tv


def _has_canary(orch) -> bool:
    return any(n.canary or n.labels.get("canary") == "true" for n in orch.store.list_nodes(statuses=["active"]))


def _tool_actions(orch, tv):
    return act.tool_actions(tv, lint_ok=catalog.lint_current(tv), has_canary=_has_canary(orch))


@handler("createDraft")
def _create_draft(orch, args, actor):
    manifest = args.get("manifest")
    if isinstance(manifest, str):
        from .manifest import from_yaml
        try:
            manifest = from_yaml(manifest)
        except Exception as exc:  # noqa: BLE001 - YAML errors are user input errors
            raise CommandError(f"manifest YAML: {exc}")
    tv = catalog.create_draft(orch.store, actor=actor, manifest=manifest, files=args.get("files") or {},
                              tool_id=str(args.get("tool_id") or ""), intent=str(args.get("intent") or ""),
                              session_name=str(args.get("session_name") or ""))
    return {"version": tv.to_dict(), "ref": tv.ref}


@handler("updateDraft")
def _update_draft(orch, args, actor):
    tool_id, version = _version(args)
    manifest = args.get("manifest")
    if isinstance(manifest, str):
        from .manifest import from_yaml
        try:
            manifest = from_yaml(manifest)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"manifest YAML: {exc}")
    tv = catalog.update_draft(orch.store, tool_id, version, actor=actor, manifest=manifest,
                              files=args.get("files"), remove_files=args.get("remove_files"),
                              stage=str(args.get("stage") or ""), intent=args.get("intent"),
                              session_name=args.get("session_name"))
    return {"version": tv.to_dict(), "ref": tv.ref}


@handler("lintDraft")
def _lint(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "lintDraft")
    return {"lint": catalog.run_lint(orch.store, tv.tool_id, tv.version, actor=actor)}


@handler("submitReview")
def _submit(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "submitReview")
    out = catalog.submit_for_review(orch.store, tv.tool_id, tv.version, actor=actor,
                                    headline=str(args.get("headline") or ""), risks=args.get("risks") or [],
                                    notes=str(args.get("notes") or ""))
    orch._attention({"kind": "orchestration_review", "ref": out.ref})
    return {"version": out.to_dict()}


@handler("requestChanges")
def _request_changes(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "requestChanges")
    return {"version": catalog.request_changes(orch.store, tv.tool_id, tv.version, actor=actor,
                                               notes=str(args.get("notes") or "")).to_dict()}


@handler("publishVersion")
def _publish(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "publishVersion")
    return {"version": catalog.publish(orch.store, orch.keys, tv.tool_id, tv.version, actor=actor,
                                       typed=str(args.get("typed") or "")).to_dict()}


def _lifecycle(orch, args, actor, cmd, target):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), cmd)
    return {"version": catalog.set_lifecycle(orch.store, tv.tool_id, tv.version, target, actor=actor).to_dict()}


@handler("deprecateVersion")
def _deprecate(orch, args, actor):
    return _lifecycle(orch, args, actor, "deprecateVersion", "deprecated")


@handler("undeprecateVersion")
def _undeprecate(orch, args, actor):
    return _lifecycle(orch, args, actor, "undeprecateVersion", "published")


@handler("retireVersion")
def _retire(orch, args, actor):
    return _lifecycle(orch, args, actor, "retireVersion", "retired")


@handler("newVersion")
def _new_version(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "newVersion")
    out = catalog.new_version_from(orch.store, tv.tool_id, tv.version, actor=actor)
    return {"version": out.to_dict(), "ref": out.ref}


@handler("discardDraft")
def _discard(orch, args, actor):
    tv = _tv(orch, args)
    _require(_tool_actions(orch, tv), "discardDraft")
    catalog.delete_draft(orch.store, tv.tool_id, tv.version, actor=actor)
    return {"ref": tv.ref}


# --------------------------------------------------------------------- runbooks

def _rb(orch, args):
    rid, version = _version(args, "runbook_id")
    rb = orch.store.get_runbook_version(rid, version)
    if rb is None:
        raise CommandError(f"unknown runbook {rid}@{version}")
    return rb


def _parse_spec(spec):
    """``(mapping, None)`` or ``(None, raw_text)`` when YAML text doesn't parse to a mapping."""
    if isinstance(spec, str):
        from .runbook import parse_yaml
        data, err = parse_yaml(spec)
        if err is not None or not isinstance(data, Mapping):
            return None, spec
        return data, None
    if not isinstance(spec, Mapping):
        raise CommandError("spec must be a mapping or YAML text")
    return spec, None


@handler("saveRunbookDraft")
def _save_runbook(orch, args, actor):
    """Save a draft. Invalid drafts save (even YAML that doesn't parse); they can't be submitted.
    ``base_digest`` (the editor always sends it) refuses a save over someone else's change."""
    spec, source = _parse_spec(args.get("spec"))
    rid = str(args.get("runbook_id") or "")
    if not rid and args.get("ref"):
        parsed = parse_ref(str(args["ref"]))
        rid = parsed[0] if parsed else ""
    if source is not None and not rid:
        raise CommandError("the YAML doesn't parse and no runbook id was given; fix the YAML first")
    rb = catalog.save_runbook_draft(orch.store, actor=actor, spec=spec, source=source or "",
                                    base_digest=str(args.get("base_digest") or ""), runbook_id=rid)
    problems = catalog.runbook_problem_objects(orch.store, rb)
    return {"runbook": rb.to_dict(), "ref": rb.ref, "digest": rb.digest,
            "problems": [p for p in problems if p.get("severity", "error") == "error"],
            "warnings": [p for p in problems if p.get("severity") == "warning"]}


@handler("validateRunbook")
def _validate_runbook(orch, args, actor):
    """Read-only: the editor's live checks (nothing is stored, audited or announced)."""
    spec = args.get("spec")
    if spec is None:
        raise CommandError("spec is required")
    preview = args.get("preview") if isinstance(args.get("preview"), Mapping) else None
    return catalog.validate_runbook(orch.store, spec, actor=actor, preview=preview)


@handler("newRunbookVersion")
def _new_runbook_version(orch, args, actor):
    rb = _rb(orch, args)
    _require(_rb_actions(orch, rb), "newRunbookVersion")
    spec = args.get("spec") if isinstance(args.get("spec"), Mapping) else None
    out = catalog.new_runbook_version(orch.store, rb.runbook_id, rb.version, actor=actor, spec=spec)
    return {"runbook": out.to_dict(), "ref": out.ref}


@handler("discardRunbookDraft")
def _discard_runbook(orch, args, actor):
    rb = _rb(orch, args)
    _require(_rb_actions(orch, rb), "discardRunbookDraft")
    catalog.discard_runbook_draft(orch.store, rb.runbook_id, rb.version, actor=actor)
    return {"ref": rb.ref}


@handler("upgradeRunbookTools")
def _upgrade_runbook(orch, args, actor):
    """Read-only: the upgraded spec and the per-step impact report; nothing is stored."""
    spec = args.get("spec")
    if spec is None and (args.get("ref") or args.get("runbook_id")):
        spec = _rb(orch, args).spec
    spec, source = _parse_spec(spec)
    if source is not None:
        raise CommandError("the YAML doesn't parse; fix it before upgrading tools")
    steps = args.get("steps")
    return catalog.upgrade_runbook_tools(orch.store, spec, steps=list(steps) if isinstance(steps, list) else None,
                                         actor=actor)


@handler("proposeRunbookTest")
def _propose_runbook_test(orch, args, actor):
    rb = _rb(orch, args)
    _require(_rb_actions(orch, rb), "testRunbook")
    nodes = _test_nodes(orch, args)
    run = runs.propose_runbook_test(orch.store, actor=actor, runbook_id=rb.runbook_id, version=rb.version,
                                    node_ids=nodes, params=args.get("params") or {},
                                    check_mode=bool(args.get("check_mode", True)))
    return {"run": orch.start_if_allowed(run).to_dict()}


def _rb_actions(orch, rb):
    return catalog.runbook_action_list(orch.store, rb, has_canary=_has_canary(orch))


def _rb_status(orch, args, actor, cmd, target):
    rb = _rb(orch, args)
    _require(_rb_actions(orch, rb), cmd)
    return {"runbook": catalog.set_runbook_status(orch.store, rb.runbook_id, rb.version, target, actor=actor).to_dict()}


@handler("submitRunbook")
def _submit_rb(orch, args, actor):
    return _rb_status(orch, args, actor, "submitRunbook", "in_review")


@handler("requestRunbookChanges")
def _changes_rb(orch, args, actor):
    return _rb_status(orch, args, actor, "requestRunbookChanges", "draft")


@handler("publishRunbook")
def _publish_rb(orch, args, actor):
    return _rb_status(orch, args, actor, "publishRunbook", "published")


@handler("deprecateRunbook")
def _deprecate_rb(orch, args, actor):
    return _rb_status(orch, args, actor, "deprecateRunbook", "deprecated")


@handler("retireRunbook")
def _retire_rb(orch, args, actor):
    return _rb_status(orch, args, actor, "retireRunbook", "retired")


# --------------------------------------------------------------------- runs

@handler("planRun")
def _plan(orch, args, actor):
    """Read-only preview (registered here so the AI and UI share one validation path)."""
    return {"plan": runs.build_plan(orch.store, actor=actor, kind=str(args.get("kind") or "tool"),
                                    target=str(_arg(args, "target")), params=args.get("params") or {},
                                    selector=args.get("selector") or {}, strategy=args.get("strategy") or {},
                                    check_mode=bool(args.get("check_mode")))}


@handler("proposeRun")
def _propose(orch, args, actor):
    kind = str(args.get("kind") or "tool")
    if kind in runs.TEST_KINDS:
        raise CommandError("use proposeTest or proposeRunbookTest for draft tests")
    if kind == "agent_update":
        raise CommandError("use proposeAgentUpdate to update node agents")
    run = orch.create_run(actor=actor, kind=kind, target=str(_arg(args, "target")), params=args.get("params") or {},
                          selector=args.get("selector") or {}, strategy=args.get("strategy") or {},
                          check_mode=bool(args.get("check_mode")), title=str(args.get("title") or ""))
    return {"run": run.to_dict()}


def _test_nodes(orch, args) -> List[str]:
    nodes = [str(n) for n in (args.get("nodes") or ([args["node_id"]] if args.get("node_id") else []))]
    if not nodes:
        nodes = [n.id for n in orch.store.list_nodes(statuses=["active"]) if runs.is_canary(n)][:1]
    if not nodes:
        raise CommandError("no canary node is available; ask the operator to mark one (or start a test node)")
    return nodes


@handler("proposeTest")
def _propose_test(orch, args, actor):
    tool_id, version = _version(args)
    nodes = _test_nodes(orch, args)
    run = runs.propose_test(orch.store, actor=actor, tool_id=tool_id, version=version, node_ids=nodes,
                            params=args.get("params") or {}, check_mode=bool(args.get("check_mode", True)),
                            test_name=str(args.get("test_name") or ""))
    return {"run": orch.start_if_allowed(run).to_dict()}


@handler("previewSelector")
def _preview_selector(orch, args, actor):
    """Read-only: the nodes a selector reaches now, and those excluded with the reason."""
    selector = args.get("selector")
    if selector is None:
        selector = {k: args[k] for k in ("labels", "groups", "nodes", "all") if k in args}
    if not isinstance(selector, (str, Mapping)):
        raise CommandError("selector must be a label selector or {labels, groups, nodes}")
    return runs.preview_selector(orch.store, selector, test=bool(args.get("test")))


def _run(orch, args):
    run = orch.store.get_run(str(_arg(args, "run_id")))
    if run is None:
        raise CommandError("unknown run")
    return run


def _run_actions(orch, run):
    from . import scheduler
    steps = list((run.plan or {}).get("steps") or [])
    execs = orch.store.list_executions(run_id=run.id)
    has_rollback = bool(scheduler.rollback_targets(steps, execs)) if run.state not in ("proposed",) else False
    return act.run_actions(run, has_rollback=has_rollback)


@handler("approveRun")
def _approve(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "approveRun")
    return {"run": orch.approve_run(run.id, actor=actor, typed=str(args.get("typed") or "")).to_dict()}


@handler("rejectRun")
def _reject(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "rejectRun")
    return {"run": runs.reject(orch.store, run.id, actor=actor, reason=str(args.get("reason") or "")).to_dict()}


@handler("continueRun")
def _continue(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "continueRun")
    return {"run": runs.resume(orch.store, run.id, actor=actor).to_dict()}


@handler("pauseRun")
def _pause(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "pauseRun")
    return {"run": runs.pause(orch.store, run.id, actor=actor).to_dict()}


@handler("rollbackRun")
def _rollback(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "rollbackRun")
    return {"run": runs.start_rollback(orch.store, run.id, actor=actor).to_dict()}


@handler("cancelRun")
def _cancel(orch, args, actor):
    run = _run(orch, args)
    _require(_run_actions(orch, run), "cancelRun")
    return {"run": runs.cancel(orch.store, run.id, actor=actor, reason=str(args.get("reason") or "")).to_dict()}


@handler("retryNode")
def _retry(orch, args, actor):
    run = _run(orch, args)
    return {"run": runs.retry_node(orch.store, run.id, str(_arg(args, "node_id")), actor=actor).to_dict()}


@handler("pauseDispatch")
def _pause_all(orch, args, actor):
    orch.set_dispatch_paused(True, actor=actor, reason=str(args.get("reason") or ""))
    return {"dispatch_paused": True}


@handler("resumeDispatch")
def _resume_all(orch, args, actor):
    orch.set_dispatch_paused(False, actor=actor)
    return {"dispatch_paused": False}


# --------------------------------------------------------------------- settings

@handler("saveAccessPolicy")
def _save_policy(orch, args, actor):
    rules = args.get("rules")
    if isinstance(rules, str):
        import yaml
        try:
            rules = yaml.safe_load(rules)
        except yaml.YAMLError as exc:
            raise CommandError(f"policy YAML: {exc}")
    if isinstance(rules, Mapping) and "rules" in rules:
        rules = rules["rules"]   # the {rules: [...]} document Settings shows
    problems = access.validate_rules(rules)
    if problems:
        raise CommandError("; ".join(problems))
    with orch.store.tx() as conn:
        version = orch.store.save_access_rules(rules, actor, conn)
        orch.store.audit(actor, "policy.save", f"access@{version}", {"rules": rules}, conn=conn)
        orch.store.change("run", "", ["policy"], conn=conn)
    return {"version": version}


@handler("resetAccessPolicy")
def _reset_policy(orch, args, actor):
    return _save_policy(orch, {"rules": access.DEFAULT_RULES}, actor)


@handler("setDefaults")
def _defaults(orch, args, actor):
    from . import scheduler
    strategy = scheduler.normalize_strategy(args.get("strategy") or {})
    orch.store.set_setting("defaults", {"strategy": strategy})
    orch.store.audit(actor, "settings.defaults", "", {"strategy": strategy})
    return {"strategy": strategy}


@handler("putSecret")
def _put_secret(orch, args, actor):
    name = str(_arg(args, "name"))
    orch.secrets.put(name, str(_arg(args, "value")), str(args.get("description") or ""))
    orch.store.audit(actor, "secret.put", name, {})  # never the value
    orch.store.change("run", "", ["secrets"])
    return {"name": name}


@handler("deleteSecret")
def _delete_secret(orch, args, actor):
    name = str(_arg(args, "name"))
    if not orch.secrets.delete(name):
        raise CommandError("unknown secret")
    orch.store.audit(actor, "secret.delete", name, {})
    orch.store.change("run", "", ["secrets"])
    return {"name": name}


@handler("setSecretPassphrase")
def _passphrase(orch, args, actor):
    try:
        orch.secrets.set_passphrase(str(_arg(args, "passphrase")))
    except ValueError as exc:
        raise CommandError(str(exc))
    orch.store.audit(actor, "secret.passphrase_set", "", {})
    return {"protected": True}


@handler("unlockSecrets")
def _unlock(orch, args, actor):
    if not orch.secrets.unlock(str(_arg(args, "passphrase"))):
        orch.store.audit(actor, "secret.unlock_failed", "", {})
        raise CommandRejected("wrong passphrase")
    orch.store.audit(actor, "secret.unlock", "", {})
    return {"locked": False}


@handler("lockSecrets")
def _lock(orch, args, actor):
    orch.secrets.lock()
    orch.store.audit(actor, "secret.lock", "", {})
    return {"locked": orch.secrets.locked}


@handler("putSchedule")
def _put_schedule(orch, args, actor):
    item = dict(args.get("schedule") or args)
    item.pop("type", None)
    return {"schedule": orch.put_schedule(item, actor=actor)}


@handler("deleteSchedule")
def _delete_schedule(orch, args, actor):
    sid = str(_arg(args, "id"))
    if not orch.store.delete_schedule(sid):
        raise CommandError("unknown schedule")
    orch.store.audit(actor, "schedule.delete", sid, {})
    return {"id": sid}


@handler("setScheduleEnabled")
def _toggle_schedule(orch, args, actor):
    item = orch.store.get_schedule(str(_arg(args, "id")))
    if item is None:
        raise CommandError("unknown schedule")
    item["enabled"] = bool(args.get("enabled"))
    return {"schedule": orch.put_schedule(item, actor=actor)}


@handler("addTrustedKey")
def _trust(orch, args, actor):
    from .node.envelope import b64d, key_id
    public = str(_arg(args, "public_key"))
    try:
        raw = b64d(public)
    except ValueError:
        raise CommandError("public_key must be base64")
    if len(raw) != 32:
        raise CommandError("public_key must be a 32-byte Ed25519 key")
    kid = key_id(raw)
    orch.store.add_trusted_key(kid, public, str(args.get("label") or "")[:100])
    orch.store.audit(actor, "trust.add", kid, {"label": args.get("label") or ""})
    return {"key_id": kid}


@handler("removeTrustedKey")
def _untrust(orch, args, actor):
    kid = str(_arg(args, "key_id"))
    if not orch.store.remove_trusted_key(kid):
        raise CommandError("unknown key")
    orch.store.audit(actor, "trust.remove", kid, {})
    return {"key_id": kid}


@handler("setNetwork")
def _network(orch, args, actor):
    bind = str(args.get("bind") or "").strip()
    port = int(args.get("port") or 30312)
    advertise = str(args.get("advertise") or "").strip()
    if bind:
        overlay.check_bind(bind)
    if not (1024 <= port <= 65535):
        raise CommandError("port must be between 1024 and 65535")
    net = {"bind": bind, "port": port, "advertise": advertise}
    orch.store.set_setting("network", net)
    orch.store.audit(actor, "network.set", "", net)
    restart = getattr(orch, "restart_endpoint", None)
    status = restart() if restart else {}
    return {"network": net, "endpoint": status}


@handler("overlayInit")
def _overlay_init(orch, args, actor):
    import ipaddress
    subnet = str(args.get("subnet") or overlay.DEFAULT_SUBNET)
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        raise CommandError("subnet must be a CIDR like 10.77.0.0/24")
    if not net.is_private:
        raise CommandError("the overlay subnet must be private")
    master = overlay.wg_keypair()
    config = {"subnet": str(net), "private_key": master["private_key"], "public_key": master["public_key"],
              "endpoint": str(args.get("endpoint") or ""), "listen_port": int(args.get("listen_port") or overlay.DEFAULT_WG_PORT)}
    if args.get("hub_endpoint"):
        hub = overlay.wg_keypair()
        config.update(hub_endpoint=str(args["hub_endpoint"]), hub_private_key=hub["private_key"],
                      hub_public_key=hub["public_key"])
    orch.store.set_setting("overlay", config)
    orch.store.audit(actor, "overlay.init", str(net), {"hub": bool(args.get("hub_endpoint"))})
    return {"overlay": _overlay_public(config), "master_address": overlay.master_address(str(net))}


def _overlay_public(config: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in config.items() if "private" not in k}


@handler("overlayAddPeer")
def _overlay_peer(orch, args, actor):
    config = orch.store.get_setting("overlay")
    if not config:
        raise CommandError("initialise the overlay first")
    name = str(_arg(args, "name"))
    if not selectors.LABEL_KEY.match(name):
        raise CommandError("peer names use letters, digits, '-', '_' and '.'")
    used = [p["address"] for p in orch.store.overlay_peers()]
    address = overlay.allocate_address(config["subnet"], used)
    keys = overlay.wg_keypair()
    orch.store.put_overlay_peer(name, address, keys["public_key"], node_id=str(args.get("node_id") or ""))
    peer = {"name": name, "address": address, "public_key": keys["public_key"], "role": "node"}
    orch.store.audit(actor, "overlay.peer", name, {"address": address})
    # The node's private key is shown once, inside its config, and not stored on the master.
    return {"peer": peer, "node_config": overlay.render_node(config, peer, keys["private_key"])}


@handler("installStarterPack")
def _starter(orch, args, actor):
    only = args.get("only")
    return {"installed": orch.install_starter_pack(force=bool(args.get("force")),
                                                   only=[str(x) for x in only] if isinstance(only, list) else None)}


@handler("validateNodePolicy")
def _validate_node_policy(orch, args, actor):
    """Read-only: the policy editor's live check (errors, what changes, warnings)."""
    from . import node_policy
    node = _node(orch, args)
    return node_policy.validate(str(args.get("text") or ""), node_policy.status(orch.store, node)["text"])


@handler("setNodePolicy")
def _set_node_policy(orch, args, actor):
    """Replace a node's policy.yaml from the master. Operator only (never in AI_COMMANDS), audited;
    the node accepts it only when its policy in force allows remote edits."""
    from . import node_policy
    try:
        return node_policy.set_policy(orch, node_id=str(_arg(args, "node_id")), text=str(_arg(args, "text")), actor=actor)
    except node_policy.PolicyError as exc:
        raise CommandError(str(exc)) from exc


@handler("cancelNodePolicy")
def _cancel_node_policy(orch, args, actor):
    from . import node_policy
    try:
        return node_policy.cancel(orch, node_id=str(_arg(args, "node_id")), actor=actor)
    except node_policy.PolicyError as exc:
        raise CommandError(str(exc)) from exc


@handler("nodeMaintenance")
def _node_maintenance(orch, args, actor):
    """The node page's Maintenance panel: propose a built-in maintenance run (operator only;
    the access policy and each node's own policy still decide, exactly like any run)."""
    from . import maintenance
    ids = args.get("node_ids")
    if not isinstance(ids, list):
        ids = [str(_arg(args, "node_id"))]
    try:
        run = maintenance.propose(orch, actor=actor, action=str(_arg(args, "action")), node_ids=[str(i) for i in ids],
                                  params=args.get("params") or {}, check_mode=bool(args.get("check_mode")))
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    return {"run": run.to_dict()}


@handler("startTestNode")
def _start_test_node(orch, args, actor):
    from .container_nodes import start_test_node
    try:
        return start_test_node(orch, image=str(args.get("image") or ""), name=str(args.get("name") or ""))
    except RuntimeError as exc:
        raise CommandRejected(str(exc))


@handler("stopTestNode")
def _stop_test_node(orch, args, actor):
    from .container_nodes import stop_test_node
    try:
        return stop_test_node(orch, str(_arg(args, "node_id")))
    except RuntimeError as exc:
        raise CommandRejected(str(exc))


@handler("importBundle")
def _import_bundle(orch, args, actor):
    from .bundles import BundleError, import_bundle
    try:
        return import_bundle(orch, path=str(args.get("path") or ""), data=args.get("data"), actor=actor,
                             apply=bool(args.get("apply", True)))
    except BundleError as exc:
        raise CommandError(str(exc))
