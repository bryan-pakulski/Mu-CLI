"""Orchestration page and API (documentation/plans/orchestration.md §8).

``GET /orchestration`` serves the page; ``/api/orchestration/*`` serves the web and mobile
clients. Reads are plain GETs. Every mutation is ``POST /api/orchestration/cmd`` (plus import
and the Studio session helper), goes through ``mu.orchestration.commands.dispatch`` as the
``operator`` and requires the CSRF header (``mu.gui.csrf``). Live updates stream from
``GET /api/orchestration/events``: the store's change log bridged into a ``ChangeBus`` with the
``node``, ``tool`` and ``run`` scopes.

The node endpoint is *not* here: nodes talk to a separate TLS listener bound to a private
address (``mu.orchestration.server``); the GUI stays on loopback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from mu.gui import csrf
from mu.jobs.core.changes import ChangeBus
from mu.orchestration import vm
from mu.orchestration.commands import CommandError, CommandRejected, dispatch
from mu.orchestration.service import Orchestrator, default_orchestrator
from mu.orchestration.store import orchestration_home

router = APIRouter()
_BUS = ChangeBus(window_s=0.25)
_CURSOR = {"seq": -1}


def orch_for(request: Request) -> Orchestrator:
    """The GUI's master. First use starts the scheduler (and the node endpoint if configured)."""
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        start_services(request.app)
        orch = request.app.state.orchestrator
    return orch


def _bridge(orch: Orchestrator) -> None:
    """Mirror new rows of the store's change log into the in-process ChangeBus."""
    if _CURSOR["seq"] < 0:
        _CURSOR["seq"] = orch.store.last_change_seq()
        return
    for change in orch.store.changes_since(_CURSOR["seq"]):
        _CURSOR["seq"] = change["seq"]
        scope = change["scope"] if change["scope"] in ("node", "tool", "run") else "run"
        _BUS.emit(scope, change["id"], change["parts"] or ["state"])


# --------------------------------------------------------------------- page

@router.get("/orchestration", response_class=HTMLResponse)
async def orchestration_page(request: Request):
    csrf.check_host(request)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "orchestration.html", {
        "nav_active": "orchestration", "csrf_token": csrf.issue(),
    })


@router.get("/api/orchestration/csrf")
async def csrf_token(request: Request):
    """A fresh token for non-page clients (mobile). Unreadable cross-origin (no CORS)."""
    csrf.check_host(request)
    return {"token": csrf.issue(), "header": "X-MuCLI-CSRF"}


# --------------------------------------------------------------------- reads

@router.get("/api/orchestration/overview")
def overview(request: Request):
    return vm.overview(orch_for(request))


@router.get("/api/orchestration/attention")
def attention(request: Request):
    return orch_for(request).attention()


@router.get("/api/orchestration/badge")
def badge(request: Request):
    """Attention counts for badges on every page (sidebar, phone tab). Never starts
    orchestration: on a machine that hasn't used it this answers ``used: false``."""
    from mu.orchestration.attention import describe, peek
    orch = getattr(request.app.state, "orchestrator", None)
    att = orch.attention() if orch is not None else peek()
    used = att is not None
    att = att or {"approvals": 0, "paused_runs": 0, "reviews": 0, "pending_nodes": 0, "offline_nodes": 0,
                  "dispatch_paused": False, "total": 0}
    return dict(att, used=used, summary=describe(att))


@router.get("/api/orchestration/nodes")
def list_nodes(request: Request):
    orch = orch_for(request)
    from mu.orchestration import enroll, container_nodes
    nodes = [vm.node_vm(orch.store, n) for n in orch.store.list_nodes()]
    from mu.orchestration.node import AGENT_VERSION
    open_update = orch.store.list_runs(states=["proposed", "approved", "running", "paused"], kind="agent_update",
                                       limit=1)
    agents = {"current": AGENT_VERSION, "auto": bool(orch.agent_update_settings().get("auto")),
              "outdated": sum(1 for n in nodes if n["agent"]["outdated"] and n["status"] != "revoked"),
              "updatable": sum(1 for n in nodes if n["agent"]["updatable"]),
              "open_run": open_update[0].id if open_update else ""}
    return {"nodes": nodes, "agents": agents,
            "groups": orch.store.list_groups(), "tokens": enroll.list_tokens(orch.store),
            "test_nodes": container_nodes.statuses(), "endpoint": _endpoint_status(request),
            "test_node_ids": list((orch.store.get_setting("test_nodes", {}) or {}).keys())}


@router.get("/api/orchestration/nodes/{node_id}")
def node_detail(node_id: str, request: Request):
    orch = orch_for(request)
    node = orch.store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "unknown node")
    return vm.node_vm(orch.store, node, detail=True)


def _has_canary(orch) -> bool:
    return any(n.canary or n.labels.get("canary") == "true" for n in orch.store.list_nodes(statuses=["active"]))


@router.get("/api/orchestration/tools")
def list_tools(request: Request, all: bool = Query(True)):
    orch = orch_for(request)
    statuses = None if all else ["published", "deprecated"]
    has_canary = _has_canary(orch)
    return {"tools": [vm.tool_vm(orch.store, tv, has_canary=has_canary)
                      for tv in orch.store.list_versions(statuses=statuses)],
            "runbooks": [vm.runbook_vm(orch.store, rb, has_canary=has_canary)
                         for rb in orch.store.list_runbook_versions()]}


@router.get("/api/orchestration/catalog/options")
def catalog_options(request: Request, purpose: str = Query("run")):
    """Compact options for the tool/runbook pickers (no full tool details)."""
    try:
        return vm.catalog_options(orch_for(request).store, purpose)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/api/orchestration/labels")
def labels(request: Request):
    """Labels on non-revoked nodes with counts, groups and nodes, for the selector builder."""
    return vm.label_index(orch_for(request).store)


@router.get("/api/orchestration/network/interfaces")
def network_interfaces(request: Request):
    """Every local address with the node endpoint's classification (public ones can't be bound)."""
    from mu.orchestration import overlay
    csrf.check_host(request)
    orch = orch_for(request)
    return {"interfaces": overlay.interfaces(), "network": orch.network(), "endpoint": _endpoint_status(request)}


@router.get("/api/orchestration/tools/{tool_id}/{version}")
def tool_detail(tool_id: str, version: int, request: Request):
    orch = orch_for(request)
    tv = orch.store.get_version(tool_id, version)
    if tv is None:
        raise HTTPException(404, "unknown tool version")
    has_canary = any(n.canary or n.labels.get("canary") == "true" for n in orch.store.list_nodes(statuses=["active"]))
    return vm.tool_detail(orch.store, tv, has_canary=has_canary)


@router.get("/api/orchestration/runbooks/{runbook_id}/{version}")
def runbook_detail(runbook_id: str, version: int, request: Request):
    orch = orch_for(request)
    rb = orch.store.get_runbook_version(runbook_id, version)
    if rb is None:
        raise HTTPException(404, "unknown runbook version")
    return vm.runbook_vm(orch.store, rb, has_canary=_has_canary(orch), detail=True)


@router.get("/api/orchestration/runs")
def list_runs(request: Request, state: str = "", limit: int = Query(100, ge=1, le=500)):
    orch = orch_for(request)
    states = [s for s in state.split(",") if s] or None
    return {"runs": [vm.run_brief(r) for r in orch.store.list_runs(states=states, limit=limit)]}


@router.get("/api/orchestration/runs/{run_id}")
def run_detail(run_id: str, request: Request):
    orch = orch_for(request)
    run = orch.store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    return vm.run_detail(orch.store, run)


@router.get("/api/orchestration/executions/{exec_id}")
def execution_detail(exec_id: str, request: Request):
    orch = orch_for(request)
    ex = orch.store.get_execution(exec_id)
    if ex is None:
        raise HTTPException(404, "unknown execution")
    return vm.execution_vm(ex)


@router.get("/api/orchestration/approvals")
def approvals(request: Request):
    orch = orch_for(request)
    store = orch.store
    return {
        "runs": [vm.run_detail(store, r) for r in store.list_runs(states=["proposed"], limit=100)],
        "paused": [vm.run_detail(store, r) for r in store.list_runs(states=["paused"], limit=100)],
        "reviews": [vm.tool_vm(store, tv) for tv in store.list_versions(statuses=["in_review"])],
        "runbook_reviews": [vm.runbook_vm(store, rb) for rb in store.list_runbook_versions(statuses=["in_review"])],
        "nodes": [vm.node_vm(store, n) for n in store.list_nodes(statuses=["pending"])],
    }


@router.get("/api/orchestration/audit")
def audit(request: Request, after: int = 0, before: int = 0, limit: int = Query(200, ge=1, le=2000),
          actor: str = "", action: str = "", subject: str = ""):
    orch = orch_for(request)
    if before or not after:
        entries = orch.store.audit_recent(limit=5000 if (actor or action or subject) else limit)
        if before:
            entries = [e for e in entries if e["seq"] < before]
        if actor:
            entries = [e for e in entries if actor.replace("*", "") in e["actor"]]
        if action:
            entries = [e for e in entries if action.replace("*", "") in e["action"]]
        if subject:
            entries = [e for e in entries if subject in e["subject"]]
        entries = entries[:limit]
    else:
        entries = orch.store.audit_entries(after=after, limit=limit, actor=actor, action=action, subject=subject)
    return {"entries": entries}


@router.get("/api/orchestration/audit/verify")
def audit_verify(request: Request):
    from mu.orchestration.audit import verify_store
    return verify_store(orch_for(request).store).to_dict()


def _endpoint_status(request: Request) -> Dict[str, Any]:
    endpoint = getattr(request.app.state, "orchestration_endpoint", None)
    orch = orch_for(request)
    if endpoint is None:
        net = orch.network()
        return {"running": False, "bind": net.get("bind"), "port": net.get("port"), "kind": "", "error": "",
                "fingerprint": "", "url": orch.master_url()}
    return endpoint.status()


@router.get("/api/orchestration/settings")
def settings(request: Request):
    from mu.orchestration import access as access_mod
    from mu.orchestration.manifest import to_yaml
    from mu.orchestration.runs import effective_rules
    orch = orch_for(request)
    store = orch.store
    version, _rules = store.access_rules()
    rules = effective_rules(store)
    overlay_cfg = store.get_setting("overlay") or {}
    return {
        "network": orch.network(), "endpoint": _endpoint_status(request),
        "access_policy": {"version": version, "rules": rules, "yaml": to_yaml({"rules": rules}),
                          "default": version == 0, "history": store.access_history()[:20],
                          "default_rules": access_mod.DEFAULT_RULES},
        "keys": orch.keys.describe(), "trusted_keys": store.trusted_keys(),
        "secrets": {"names": orch.secrets.names(), "locked": orch.secrets.locked,
                    "passphrase": orch.secrets.passphrase_protected},
        "defaults": orch.defaults(), "dispatch_paused": orch.dispatch_paused(),
        "overlay": {k: v for k, v in overlay_cfg.items() if "private" not in k},
        "overlay_peers": store.overlay_peers(), "schedules": store.list_schedules(),
        "groups": store.list_groups(), "metrics": orch.metrics,
        "agent_updates": dict(orch.agent_update_settings(), current=_agent_version()),
    }


def _agent_version() -> str:
    from mu.orchestration.node import AGENT_VERSION
    return AGENT_VERSION


@router.get("/api/orchestration/overlay/{which}")
def overlay_config(which: str, request: Request):
    from mu.orchestration import overlay
    csrf.check_host(request)
    orch = orch_for(request)
    config = orch.store.get_setting("overlay")
    if not config:
        raise HTTPException(404, "overlay not initialised")
    peers = orch.store.overlay_peers()
    if which == "master":
        text = overlay.render_master(config, peers)
    elif which == "hub":
        if not config.get("hub_endpoint"):
            raise HTTPException(404, "no hub configured")
        text = overlay.render_hub(config, peers)
    else:
        raise HTTPException(404, "unknown config")
    return Response(text, media_type="text/plain")


# --------------------------------------------------------------------- mutations

class _Refused(Exception):
    def __init__(self, status: int, detail: str, data: Any = None):
        self.status, self.detail, self.data = status, detail, data


def _run_command(orch: Orchestrator, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return dispatch(orch, payload, actor="operator").to_dict()
    except CommandRejected as exc:
        raise _Refused(409, str(exc), getattr(exc, "data", None)) from exc
    except CommandError as exc:
        raise _Refused(400, str(exc)) from exc


async def _command_response(orch: Orchestrator, payload: Dict[str, Any]):
    """The command result, or ``{detail, data?}`` with 400/409 (a 409 save conflict carries the
    current draft in ``data``)."""
    try:
        return await asyncio.to_thread(_run_command, orch, payload)
    except _Refused as exc:
        body: Dict[str, Any] = {"detail": exc.detail}
        if exc.data is not None:
            body["data"] = exc.data
        return JSONResponse(body, status_code=exc.status)


@router.post("/api/orchestration/cmd", dependencies=[Depends(csrf.check_request)])
async def command(request: Request, payload: Dict[str, Any]):
    return await _command_response(orch_for(request), payload)


@router.post("/api/orchestration/plan", dependencies=[Depends(csrf.check_request)])
async def plan(request: Request, payload: Dict[str, Any]):
    return await _command_response(orch_for(request), {"type": "planRun", "args": payload})


@router.get("/api/orchestration/export")
def export(request: Request, drafts: bool = False, inventory: bool = False):
    csrf.check_host(request)
    from mu.orchestration.bundles import export_bundle
    orch = orch_for(request)
    with tempfile.TemporaryDirectory() as tmp:
        name = time.strftime("mucli-orch-%Y%m%d-%H%M%S.mucli-orch.tar.zst")
        out = export_bundle(orch, os.path.join(tmp, name), include_drafts=drafts, include_inventory=inventory)
        with open(out["path"], "rb") as fh:
            data = fh.read()
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename={os.path.basename(out['path'])}"})


@router.post("/api/orchestration/import", dependencies=[Depends(csrf.check_request)])
async def import_bundle(request: Request, file: UploadFile = File(...), apply: bool = False,
                        policies: bool = False):
    from mu.orchestration.bundles import BundleError, import_bundle as do_import
    data = await file.read()
    orch = orch_for(request)
    try:
        result = await asyncio.to_thread(do_import, orch, data=data, actor="operator", apply=apply,
                                         apply_policies=policies)
    except BundleError as exc:
        raise HTTPException(400, str(exc))
    return result


@router.post("/api/orchestration/studio/session", dependencies=[Depends(csrf.check_request)])
async def studio_session(request: Request, payload: Dict[str, Any]):
    """Open (creating if needed) the discovery chat for a draft: an ``orchestration`` session."""
    from mu.gui.routers import sessions as sessions_router
    from mu.orchestration.manifest import parse_ref
    orch = orch_for(request)
    ref = str(payload.get("ref") or "")
    parsed = parse_ref(ref) if ref else None
    tv = orch.store.get_version(*parsed) if parsed else None
    if ref and tv is None:
        raise HTTPException(404, "unknown draft")
    name = str(payload.get("session_name") or (tv.session_name if tv else "") or "").strip()
    if not name:
        base = (tv.tool_id if tv else "tool-studio").replace(".", "-")
        name = f"orch-{base}-{int(time.time()) % 100000}"
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not provider or not model:
        current = request.app.state.session_by_name(None)
        if current is not None and current.provider is not None:
            provider = provider or current.provider.name
            model = model or current.provider.model_name
        else:
            args = request.app.state.args
            provider = provider or str(getattr(args, "provider", "") or "")
            model = model or str(getattr(args, "model", "") or "")
    if sessions_router._read_session_data(name) is None:
        if not provider or not model:
            raise HTTPException(400, "choose a provider and model for the discovery chat")
        await sessions_router.create_session(request, {"name": name, "provider": provider, "model": model,
                                                       "session_type": "orchestration", "activate": True})
    else:
        await sessions_router.load_session(name, request, payload={})
    if tv is not None and tv.session_name != name and tv.status in ("draft", "testing"):
        await asyncio.to_thread(dispatch, orch, {"type": "updateDraft",
                                                 "args": {"ref": tv.ref, "session_name": name}}, actor="operator")
    return {"session_name": name, "url": "/"}


# --------------------------------------------------------------------- node setup wizard

def _setup_state(orch: Orchestrator, token_id: str) -> Dict[str, Any]:
    from mu.orchestration import enroll, zipapp
    status = enroll.token_status(orch.store, token_id)
    if status is None:
        raise HTTPException(404, "unknown join token")
    held = orch.setup_token(token_id)
    url = orch.master_url()
    _pyz, pyz_sha = zipapp.build_zipapp()
    out: Dict[str, Any] = dict(status, master_url=url, available=bool(held and url),
                               name=(held or {}).get("name", ""), pyz_sha256=pyz_sha,
                               fingerprint=orch.keys.tls_identity([orch.network().get("bind") or "",
                                                                   orch.network().get("advertise") or ""]
                                                                  ).display_fingerprint)
    if held and url:
        script = zipapp.install_script(orch, token=held["token"], name=held["name"]).encode("utf-8")
        out.update(install_sha256=hashlib.sha256(script).hexdigest(),
                   command=zipapp.install_command(orch, held["token"], held["name"]),
                   manual=zipapp.manual_commands(orch, held["token"], held["name"]))
    node = orch.store.get_node(status["node_id"]) if status.get("node_id") else None
    out["node"] = vm.node_vm(orch.store, node, detail=True) if node else None
    out["verify_run_id"] = ""
    if node is not None:
        out["name"] = out["name"] or node.name
        checks = [e for e in orch.store.audit_entries(action="node.verify", subject=node.id, limit=1000)
                  if e["subject"] == node.id]
        if checks:
            out["verify_run_id"] = str((checks[-1].get("payload") or {}).get("run") or "")
    return out


@router.get("/api/orchestration/setup/{token_id}", dependencies=[Depends(csrf.check_request)])
def setup_state(token_id: str, request: Request):
    """The wizard's resumable state. Carries the live token (in the install command) while it is
    unused, so like the downloads it needs the CSRF header."""
    return _setup_state(orch_for(request), token_id)


@router.get("/api/orchestration/setup/{token_id}/{name}", dependencies=[Depends(csrf.check_request)])
def setup_download(token_id: str, name: str, request: Request):
    """``agent.pyz`` or a pre-filled ``install.sh`` (token, master URL and fingerprint embedded)."""
    from mu.orchestration import zipapp
    orch = orch_for(request)
    if name not in ("agent.pyz", "install.sh"):
        raise HTTPException(404, "unknown download")
    held = orch.setup_token(token_id)
    if not held:
        raise HTTPException(410, "this join token was used, expired or was created in another session; "
                                 "create a new one")
    if not orch.master_url():
        raise HTTPException(409, "the node endpoint isn't configured yet")
    if name == "agent.pyz":
        data, sha = zipapp.build_zipapp()
        media, filename = "application/octet-stream", "mucli-node.pyz"
    else:
        data = zipapp.install_script(orch, token=held["token"], name=held["name"]).encode("utf-8")
        sha, media, filename = hashlib.sha256(data).hexdigest(), "text/x-shellscript", "install.sh"
    orch.store.audit("operator", "setup.download", token_id, {"file": filename, "sha256": sha})
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename={filename}",
                                                     "X-Content-SHA256": sha, "Cache-Control": "no-store"})


# --------------------------------------------------------------------- live updates

@router.get("/api/orchestration/events")
async def events(request: Request, after: int = Query(0, ge=0), once: bool = False):
    """SSE: ``data: {scope, id, parts, seq}``; clients refetch only what changed."""
    orch = orch_for(request)

    async def gen():
        await asyncio.to_thread(_bridge, orch)
        seq = min(after or _BUS.seq, _BUS.seq)
        yield f"event: hello\ndata: {json.dumps({'seq': seq, 'attention': orch.attention()})}\n\n"
        heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            await asyncio.to_thread(_bridge, orch)
            changes = await asyncio.to_thread(_BUS.wait, seq, 0.5)
            if not changes:
                if time.monotonic() - heartbeat >= 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                if once:
                    return
                continue
            for change in changes:
                seq = max(seq, change.seq)
                yield f"id: {seq}\ndata: {json.dumps(change.to_dict())}\n\n"
            if once:
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------- lifecycle

def start_services(app, *, restart: bool = False) -> Dict[str, Any]:
    """Start the scheduler and, when a private bind address is configured, the node endpoint."""
    from mu.orchestration.server import NodeEndpoint
    from mu.orchestration.overlay import BindRefused
    orch = getattr(app.state, "orchestrator", None)
    if orch is None:
        bus = getattr(app.state, "bus", None)

        def _notify(event: Dict[str, Any]) -> None:
            # Session-agnostic: every surface learns orchestration needs the operator.
            if bus is not None:
                bus.publish_threadsafe(dict(event, kind="orchestration_attention", detail=event.get("kind")))

        orch = default_orchestrator(notify=_notify)
        app.state.orchestrator = orch
        orch.restart_endpoint = lambda: start_services(app, restart=True)
    orch.start()
    endpoint = getattr(app.state, "orchestration_endpoint", None)
    if endpoint is None:
        endpoint = NodeEndpoint(orch)
        app.state.orchestration_endpoint = endpoint
    net = orch.network()
    if restart or not endpoint.status()["running"]:
        endpoint.stop()
        if net.get("bind"):
            try:
                endpoint.start(str(net["bind"]), int(net.get("port") or 30312))
            except (BindRefused, OSError) as exc:
                endpoint.error = str(exc)
    return endpoint.status()


def start_if_used(app) -> None:
    """GUI startup: resume orchestration only where it has been used (no files otherwise)."""
    if os.path.exists(os.path.join(orchestration_home(), "orch.sqlite3")):
        start_services(app)


def stop_services(app) -> None:
    endpoint = getattr(app.state, "orchestration_endpoint", None)
    if endpoint is not None:
        endpoint.stop()
    orch = getattr(app.state, "orchestrator", None)
    if orch is not None:
        orch.stop()
