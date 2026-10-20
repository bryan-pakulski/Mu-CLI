"""The node agent: enrol, long-poll the master, verify and run dispatches, report results.

Outbound only (plan §6.2): ``POST /api/orchestration/node/poll`` is held open by the master for
up to ~25 s and returns dispatches and cancels; it doubles as the heartbeat. Results, acks and
output chunks go to ``POST /api/orchestration/node/events``. Nothing listens on the node.
"""

from __future__ import annotations

import json
import os
import queue
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import subprocess

from . import AGENT_VERSION
from . import update as agent_update
from .collectors import collect_facts
from .envelope import b64d, verify_cancel, verify_update
from .executor import Executor, Keys, authorize
from .policy import NodePolicy, decide_update, load_policy_file
from .store import NodeStore
from .transport import AuthRejected, MasterClient, TransportError, fetch_certificate, \
    cert_fingerprint, normalize_fingerprint

DEFAULT_STATE_DIR = "/var/lib/mucli-node"
DEFAULT_ETC_DIR = "/etc/mucli-node"
FACTS_INTERVAL_S = 600.0
MAX_CONCURRENT = 4


def enroll(*, master: str, token: str, fingerprint: str, state_dir: str = DEFAULT_STATE_DIR,
           etc_dir: str = DEFAULT_ETC_DIR, name: str = "", insecure_http: bool = False,
           facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Exchange a one-time join token for a node credential. Returns the saved identity."""
    store = NodeStore(state_dir)
    cafile = os.path.join(store.root, "master.pem")
    pin = normalize_fingerprint(fingerprint)
    if master.startswith("https://"):
        pem, der = fetch_certificate(master)
        if cert_fingerprint(der) != pin:
            raise TransportError("the master's certificate does not match --fingerprint; refusing to enrol")
        with open(cafile, "w", encoding="utf-8") as fh:
            fh.write(pem)
    elif not insecure_http:
        raise TransportError("master URL must be https://")
    policy = load_policy_file(os.path.join(etc_dir, "policy.yaml"))
    client = MasterClient(master, cafile=cafile, fingerprint=pin)
    reply = client.post("/api/orchestration/node/enroll", {
        "token": token, "name": name or (facts or {}).get("hostname") or "",
        "facts": facts if facts is not None else collect_facts(),
        "policy": policy.summary(), "agent_version": AGENT_VERSION,
    })
    identity = {
        "node_id": reply["node_id"], "credential": reply["credential"], "master": master,
        "fingerprint": pin, "cafile": cafile if master.startswith("https://") else "",
        "signing_key": reply["signing_key"], "test_key": reply.get("test_key") or "",
        "enrolled_at": time.time(),
    }
    store.save_identity(identity)
    trust = {k: identity[k] for k in ("node_id", "signing_key", "test_key", "fingerprint", "master")}
    try:
        os.makedirs(etc_dir, exist_ok=True)
        with open(os.path.join(etc_dir, "master.json"), "w", encoding="utf-8") as fh:
            json.dump(trust, fh, indent=2, sort_keys=True)
    except OSError:
        pass  # Unprivileged enrolment (dev/test nodes): no root helper to configure.
    return identity


class NodeAgent:
    def __init__(self, *, state_dir: str = DEFAULT_STATE_DIR, etc_dir: str = DEFAULT_ETC_DIR,
                 sandbox: str = "systemd", helper: Optional[List[str]] = None,
                 poll_wait_s: float = 25.0, facts_fn: Callable[[], Dict[str, Any]] = collect_facts,
                 log: Callable[[str], None] = print):
        self.store = NodeStore(state_dir)
        self.etc_dir = etc_dir
        identity = self.store.load_identity()
        if not identity:
            raise RuntimeError(f"not enrolled (no identity in {self.store.root}); run `mucli-node enroll` first")
        self.identity = identity
        self.keys = Keys(b64d(identity["signing_key"]),
                         b64d(identity["test_key"]) if identity.get("test_key") else None)
        self.executor = Executor(self.store, sandbox=sandbox, helper=helper)
        self.sandbox = sandbox
        self.poll_wait_s = poll_wait_s
        self.facts_fn = facts_fn
        self.log = log
        self.client = self._client()
        self.replay = self.store.replay_cache()
        self._events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._running: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._facts_at = 0.0
        self._sender: Optional[threading.Thread] = None
        self._pending_update: Optional[Dict[str, Any]] = None
        self._policy_result: Optional[Dict[str, Any]] = None
        self._policy_nonce = ""
        self.updated = False          # set when this process exits so systemd starts the new agent
        self._report_update()

    def _client(self) -> MasterClient:
        return MasterClient(self.identity["master"], cafile=self.identity.get("cafile") or None,
                            fingerprint=self.identity.get("fingerprint", ""),
                            node_id=self.identity["node_id"], credential=self.identity["credential"])

    @property
    def node_id(self) -> str:
        return self.identity["node_id"]

    def policy(self) -> NodePolicy:
        return load_policy_file(os.path.join(self.etc_dir, "policy.yaml"))

    def policy_summary(self) -> Dict[str, Any]:
        summary = self.policy().summary()
        summary["sandbox"] = self.sandbox
        # The file itself, so the operator can edit it on the master (it is not secret).
        from .remote_policy import current_text, text_digest
        text = current_text(self.etc_dir)
        summary["text"] = text[:16384]
        summary["sha256"] = text_digest(text)
        from .policy import remote_policy_allowed
        summary["remote_policy"] = remote_policy_allowed(self.policy())
        return summary

    # ------------------------------------------------------------ main loop
    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        self._sender = threading.Thread(target=self._send_loop, name="mucli-node-events", daemon=True)
        self._sender.start()
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.poll_once()
                backoff = 1.0
            except AuthRejected as exc:
                self.log(f"mucli-node: {exc}; retrying in 60s")
                self._stop.wait(60.0)
            except TransportError as exc:
                self.log(f"mucli-node: {exc}; retrying in {backoff:.0f}s")
                self._stop.wait(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, 60.0)

    def poll_once(self, wait_s: Optional[float] = None) -> Dict[str, Any]:
        wait = self.poll_wait_s if wait_s is None else wait_s
        body: Dict[str, Any] = {"agent_version": AGENT_VERSION, "running": sorted(self._running),
                                "policy": self.policy_summary(), "wait_s": wait}
        if time.time() - self._facts_at > FACTS_INTERVAL_S:
            body["facts"] = self.facts_fn()
            self._facts_at = time.time()
        if self._policy_result is not None:
            body["policy_result"] = self._policy_result
        reply = self.client.post("/api/orchestration/node/poll", body, timeout=wait + 15)
        self._policy_result = None
        if reply.get("rotate_credential"):
            self.identity["credential"] = reply["rotate_credential"]
            self.store.save_identity(self.identity)
            self.client = self._client()
        if reply.get("collect_facts"):
            self._facts_at = 0.0
        for cancel in reply.get("cancels") or []:
            check = verify_cancel(cancel, node_id=self.node_id, signing_key=self.keys.signing)
            if check.ok:
                self.executor.cancel(str(cancel.get("exec_id")))
            else:
                self.log(f"mucli-node: ignored cancel: {check.reason}")
        if isinstance(reply.get("policy_update"), dict):
            self.handle_policy(reply["policy_update"])
        for envelope in reply.get("dispatches") or []:
            self.handle_dispatch(envelope)
        self._maybe_update()
        return reply

    # ------------------------------------------------------------ policy from the master
    def handle_policy(self, order: Dict[str, Any]) -> None:
        """Install a policy the operator edited on the master (checked here, then again by the
        root helper, which writes the root-owned file). The outcome rides on the next poll."""
        from .remote_policy import apply as apply_policy, check as check_policy
        nonce = str(order.get("nonce") or "")
        if not nonce or nonce == self._policy_nonce:
            return                      # already tried this order; the master is told once
        self._policy_nonce = nonce
        ok, reason = check_policy(order, etc_dir=self.etc_dir, node_id=self.node_id,
                                  signing_key=self.keys.signing, replay=None)
        if ok and self.sandbox == "systemd":
            folder = os.path.join(self.store.root, "policy")
            os.makedirs(folder, mode=0o700, exist_ok=True)
            spec = os.path.join(folder, "spec.json")
            with open(spec, "w", encoding="utf-8") as fh:
                json.dump({"policy": dict(order)}, fh)
            try:
                out = subprocess.run(self.executor.helper + [spec], capture_output=True, text=True, timeout=60)
                ok = out.returncode == 0
                lines = [ln for ln in (out.stdout + out.stderr).strip().splitlines() if ln.strip()]
                reason = "" if ok else (lines[-1] if lines else f"root helper exit {out.returncode}")[:500]
            except (OSError, subprocess.TimeoutExpired) as exc:
                ok, reason = False, f"the root helper didn't run: {exc}"
        elif ok:                        # no root helper (sandbox none): the agent owns its etc dir
            ok, reason = apply_policy(order, etc_dir=self.etc_dir, node_id=self.node_id,
                                      signing_key=self.keys.signing, replay=self.replay)
            self.store.save_replay_cache(self.replay)
            reason = "" if ok else reason
        self._policy_result = {"nonce": nonce, "sha256": str(order.get("sha256") or ""), "ok": ok, "reason": reason}
        self.log(f"mucli-node: policy from the master {'installed' if ok else 'refused: ' + reason}")

    # ------------------------------------------------------------ dispatch
    def handle_dispatch(self, envelope: Dict[str, Any]) -> None:
        exec_id = str(envelope.get("exec_id") or "")
        if envelope.get("kind") == "agent_update":
            self.handle_update(envelope)
            return
        if self._pending_update is not None:
            self._emit({"type": "result", "exec_id": exec_id, "result": {
                "exec_id": exec_id, "state": "refused", "reason": "the node is updating its agent; try again shortly"}})
            return
        secrets = envelope.get("secrets") or {}
        policy = self.policy()
        ok, reason, params = authorize(envelope, node_id=self.node_id, keys=self.keys, policy=policy,
                                       replay=self.replay, check_mode_passed=self.store.check_mode_passed())
        self.store.save_replay_cache(self.replay)
        if ok and len(self._running) >= MAX_CONCURRENT:
            ok, reason = False, f"node is busy ({MAX_CONCURRENT} executions running)"
        if not ok:
            self._emit({"type": "result", "exec_id": exec_id,
                        "result": {"exec_id": exec_id, "state": "refused", "reason": reason}})
            return
        try:
            self._fetch_blobs(envelope)
        except (TransportError, ValueError) as exc:
            self._emit({"type": "result", "exec_id": exec_id,
                        "result": {"exec_id": exec_id, "state": "failed", "reason": f"blob fetch failed: {exc}"}})
            return
        self._emit({"type": "running", "exec_id": exec_id})

        def _work():
            try:
                result = self.executor.execute(
                    envelope, params, secrets=secrets,
                    on_output=lambda chunk: self._emit({"type": "output", "exec_id": exec_id, "text": chunk}))
                self._emit({"type": "result", "exec_id": exec_id, "result": result.to_dict()})
            finally:
                with self._lock:
                    self._running.pop(exec_id, None)

        thread = threading.Thread(target=_work, name=f"exec-{exec_id}", daemon=True)
        with self._lock:
            self._running[exec_id] = thread
        thread.start()

    # ------------------------------------------------------------ agent update
    def _result(self, exec_id: str, state: str, reason: str = "", **extra: Any) -> None:
        self._emit({"type": "result", "exec_id": exec_id,
                    "result": dict({"exec_id": exec_id, "state": state, "reason": reason}, **extra)})

    def handle_update(self, order: Dict[str, Any]) -> None:
        """Check an update order now; install it once no execution is running."""
        exec_id = str(order.get("exec_id") or "")
        check = verify_update(order, node_id=self.node_id, signing_key=self.keys.signing, replay=self.replay)
        self.store.save_replay_cache(self.replay)
        if not check.ok:
            self._result(exec_id, "refused", check.reason)
            return
        decision = decide_update(self.policy(), now=time.time())
        if not decision.allowed:
            self._result(exec_id, "refused", decision.reason)
            return
        if self.sandbox != "systemd":
            self._result(exec_id, "refused", "this node runs without the root helper (sandbox none): "
                                            "replace its agent or image instead")
            return
        if str(order.get("version")) == AGENT_VERSION:
            self._result(exec_id, "succeeded", exit_code=0, output={"from": AGENT_VERSION, "to": AGENT_VERSION,
                                                                    "note": "already running this version"})
            return
        self._pending_update = order
        self._emit({"type": "running", "exec_id": exec_id})
        self._maybe_update()

    def _maybe_update(self) -> None:
        order = self._pending_update
        if order is None or self._running:
            return              # running executions finish first; no new ones start meanwhile
        self._pending_update = None
        exec_id = str(order.get("exec_id") or "")
        try:
            data = self.client.get("/api/orchestration/node/agent.pyz", raw=True, timeout=120)
        except TransportError as exc:
            self._result(exec_id, "failed", f"couldn't download the agent: {exc}")
            return
        spec = agent_update.stage(self.store.root, data, order)
        problem = agent_update.check_file(os.path.join(os.path.dirname(spec), "mucli-node.pyz"), str(order["sha256"]))
        if problem:
            self._result(exec_id, "failed", problem)
            return
        try:
            out = subprocess.run(self.executor.helper + [spec], capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._result(exec_id, "failed", f"the root helper didn't run: {exc}")
            return
        log = (out.stdout + out.stderr)[-4000:]
        if out.returncode != 0:
            lines = [ln for ln in log.strip().splitlines() if ln.strip()]
            self._result(exec_id, "failed", (lines[-1] if lines else f"root helper exit {out.returncode}")[:500],
                         exit_code=out.returncode, log=log)
            return
        agent_update.write_marker(self.store.root, order, AGENT_VERSION)
        self.log(f"mucli-node: installed agent {order.get('version')}; restarting")
        self.updated = True
        self._stop.set()

    def _report_update(self) -> None:
        """Started to finish an update: report it from the agent that now runs."""
        marker = agent_update.take_marker(self.store.root)
        if not marker or not marker.get("exec_id"):
            return
        exec_id = str(marker["exec_id"])
        try:
            if marker.get("to") == AGENT_VERSION:
                self._result(exec_id, "succeeded", exit_code=0,
                             output={"from": marker.get("from"), "to": AGENT_VERSION})
            else:
                self._result(exec_id, "failed",
                             f"the node still runs agent {AGENT_VERSION} after installing {marker.get('to')}")
        except TransportError:
            pass   # the result stays queued; the event sender delivers it once the master answers

    def _fetch_blobs(self, envelope: Dict[str, Any]) -> None:
        for name, sha in (envelope.get("blobs") or {}).items():
            sha = str(sha).replace("sha256:", "")
            if self.store.has_blob(sha):
                continue
            data = self.client.get(f"/api/orchestration/node/blob/{sha}", raw=True, timeout=60)
            self.store.put_blob(sha, data)

    # ------------------------------------------------------------ events
    def _emit(self, event: Dict[str, Any]) -> None:
        event.setdefault("at", time.time())
        self._events.put(event)
        if self._sender is None:  # synchronous mode (tests drive poll_once directly)
            self.flush_events()

    def flush_events(self, first: Optional[Dict[str, Any]] = None) -> int:
        batch: List[Dict[str, Any]] = [first] if first else []
        while True:
            try:
                batch.append(self._events.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return 0
        try:
            self.client.post("/api/orchestration/node/events", {"events": batch}, timeout=30)
        except TransportError:
            for event in batch:
                if event.get("type") != "output":  # results and acks are retried; chunks are not
                    self._events.put(event)
            raise
        return len(batch)

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._events.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.flush_events(first=event)
            except TransportError:
                self._stop.wait(2.0)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait for running executions to finish (tests)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                threads = list(self._running.values())
            if not threads:
                return True
            for thread in threads:
                thread.join(timeout=max(0.0, deadline - time.time()))
        return not self._running
