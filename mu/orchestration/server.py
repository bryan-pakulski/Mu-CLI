"""The node endpoint (plan §6.0, §6.2): a separate TLS listener on a private address only.

It is not part of the GUI app: the GUI stays on loopback, nodes reach this listener over a LAN,
VPN, WireGuard overlay or SSH tunnel. ``NodeEndpoint.start`` refuses public and any-address
binds (``overlay.check_bind``). Routes::

    POST /api/orchestration/node/enroll     one-time join token -> credential (rate limited)
    POST /api/orchestration/node/poll       heartbeat + long-poll for dispatches and cancels
    POST /api/orchestration/node/events     acks, output chunks, results
    GET  /api/orchestration/node/blob/{sha} a file of a tool dispatched to this node
    GET  /api/orchestration/node/agent.pyz  the node agent zipapp
    GET  /api/orchestration/node/install.sh the installer (pins the zipapp digest and TLS fingerprint)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import enroll
from .overlay import BindRefused, check_bind

_logger = logging.getLogger(__name__)
POLL_MAX_S = 25.0
MAX_BODY = 8 * 1024 * 1024


class RateLimiter:
    """Sliding-window limit per key (client address / node id)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = int(limit), float(window_s)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def create_node_app(orch) -> FastAPI:
    app = FastAPI(title="mucli-orchestration-node", docs_url=None, redoc_url=None, openapi_url=None)
    enroll_limit = RateLimiter(10, 60.0)
    poll_limit = RateLimiter(120, 60.0)
    auth_fail_limit = RateLimiter(20, 60.0)

    async def _json(request: Request) -> Dict[str, Any]:
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_BODY:
            raise HTTPException(413, "request too large")
        body = await request.body()
        if len(body) > MAX_BODY:
            raise HTTPException(413, "request too large")
        try:
            import json
            data = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "expected an object")
        return data

    def _client(request: Request) -> str:
        return str(getattr(request.client, "host", "") or "")

    def _auth(request: Request):
        client = _client(request)
        node = orch.hub.authenticate(request.headers.get("x-mucli-node", ""),
                                     request.headers.get("x-mucli-node-token", ""))
        if node is None:
            if not auth_fail_limit.allow(client):
                raise HTTPException(429, "too many failed authentications")
            raise HTTPException(401, "unknown or revoked node credential")
        return node

    @app.post("/api/orchestration/node/enroll")
    async def node_enroll(request: Request):
        client = _client(request)
        if not enroll_limit.allow(client):
            raise HTTPException(429, "too many enrolment attempts")
        data = await _json(request)
        try:
            reply = enroll.enroll(orch.store, orch.keys, token=str(data.get("token") or ""),
                                  name=str(data.get("name") or ""), facts=data.get("facts") or {},
                                  policy=data.get("policy") or {}, agent_version=str(data.get("agent_version") or ""),
                                  address=client)
        except enroll.EnrollError as exc:
            orch.store.audit("system", "node.enroll_refused", client, {"reason": str(exc)})
            raise HTTPException(403, str(exc))
        orch._attention({"kind": "orchestration_node", "node_id": reply["node_id"], "status": "pending"})
        return reply

    @app.post("/api/orchestration/node/poll")
    async def node_poll(request: Request):
        node = _auth(request)
        if not poll_limit.allow(node.id):
            raise HTTPException(429, "polling too fast")
        data = await _json(request)
        orch.metrics["polls"] += 1
        reply = await asyncio.to_thread(orch.hub.heartbeat, node, data, _client(request))
        running = [str(x) for x in (data.get("running") or [])][:64]
        try:
            wait_s = max(0.0, min(float(data.get("wait_s", POLL_MAX_S)), POLL_MAX_S))
        except (TypeError, ValueError):
            wait_s = POLL_MAX_S
        deadline = time.monotonic() + wait_s
        while True:
            node = orch.store.get_node(node.id) or node
            dispatches, cancels = await asyncio.to_thread(orch.hub.claim, node, running)
            if dispatches or cancels or reply.get("rotate_credential") or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        orch.metrics["dispatches"] += len(dispatches)
        reply.update(dispatches=dispatches, cancels=cancels, status=node.status, server_time=time.time())
        return JSONResponse(reply)

    @app.post("/api/orchestration/node/events")
    async def node_events(request: Request):
        node = _auth(request)
        data = await _json(request)
        events = data.get("events") or []
        if not isinstance(events, list):
            raise HTTPException(400, "events must be a list")
        orch.metrics["events"] += len(events)
        return await asyncio.to_thread(orch.hub.events, node, events)

    @app.get("/api/orchestration/node/blob/{sha}")
    async def node_blob(sha: str, request: Request):
        node = _auth(request)
        data = await asyncio.to_thread(orch.hub.blob, node, sha)
        if data is None:
            raise HTTPException(404, "no such file for this node")
        return Response(content=data, media_type="application/octet-stream")

    @app.get("/api/orchestration/node/agent.pyz")
    async def agent_pyz():
        from .zipapp import build_zipapp
        data, _sha = build_zipapp()
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename=mucli-node.pyz"})

    @app.get("/api/orchestration/node/install.sh")
    async def install_sh():
        from .zipapp import install_script
        return PlainTextResponse(install_script(orch))

    return app


class NodeEndpoint:
    """Runs the node app under uvicorn with TLS in a background thread."""

    def __init__(self, orch):
        self.orch = orch
        self.server = None
        self.thread: Optional[threading.Thread] = None
        self.error = ""
        self.bind = ""
        self.port = 0
        self.kind = ""
        self.fingerprint = ""

    def status(self) -> Dict[str, Any]:
        return {"running": bool(self.thread and self.thread.is_alive() and self.server and self.server.started),
                "bind": self.bind, "port": self.port, "kind": self.kind, "error": self.error,
                "fingerprint": ("SHA256:" + self.fingerprint) if self.fingerprint else "",
                "url": self.orch.master_url()}

    def start(self, host: str, port: int, *, tls: bool = True, allow_public: Optional[bool] = None) -> Dict[str, Any]:
        import uvicorn

        self.stop()
        self.error = ""
        try:
            check = check_bind(host, allow_public=allow_public)
        except BindRefused as exc:
            self.error = str(exc)
            raise
        self.bind, self.port, self.kind = host, int(port), check.kind
        kwargs: Dict[str, Any] = {}
        if tls:
            identity = self.orch.keys.tls_identity([host, self.orch.network().get("advertise") or ""])
            self.fingerprint = identity.fingerprint
            kwargs.update(ssl_certfile=identity.cert_path, ssl_keyfile=identity.key_path)
        config = uvicorn.Config(create_node_app(self.orch), host=host, port=int(port), log_level="warning",
                                access_log=False, loop="asyncio", ws="none", timeout_keep_alive=30, **kwargs)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, name="orchestration-node-endpoint", daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not self.server.started and self.thread.is_alive():
            time.sleep(0.05)
        if not self.server.started:
            self.error = self.error or f"node endpoint failed to start on {host}:{port}"
        return self.status()

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # noqa: BLE001 - surfaced through status()
            self.error = f"node endpoint stopped: {exc}"
            _logger.exception("orchestration node endpoint crashed")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None
