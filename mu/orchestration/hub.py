"""The node-facing hub (plan §6.2): heartbeats, dispatch queue, cancels, results, blobs.

Stateless between calls: the dispatch queue is the ``executions`` table, so a master restart
loses nothing (queued work is handed out on the next poll, in-flight work keeps reporting).

* ``poll`` records the heartbeat (facts, policy summary, agent version) and ``claim``s queued
  executions for the node, building a fresh signed envelope for each dispatch attempt (new
  nonce, start-by deadline). Secrets are resolved at that moment and travel beside the
  envelope; the stored copy never contains them.
* ``events`` accepts only events for the calling node's own executions. Results are redacted
  again on the master (``redact_secrets`` + the declared secrets as literals) and outputs are
  re-validated against the tool's schema before anything is stored or shown to the AI.
* ``blob`` serves a file only if it belongs to an execution currently dispatched to that node.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import enroll
from .models import Execution, Node
from .node import schema as jschema
from .node.canonical import digest
from .node.redact import redact as node_redact
from .secrets import SecretsLocked
from .signing import new_nonce
from .states import EXEC_IN_FLIGHT, EXEC_TERMINAL, check_exec

START_BY_S = 600.0
MAX_LOG_BYTES = 512 * 1024
MAX_LIVE_LOG_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_PER_POLL = 4
RESULT_STATES = ("succeeded", "failed", "timed_out", "cancelled", "refused")


def master_redact(text: str, literals: List[str]) -> Tuple[str, int]:
    from mu.security.secret_paths import redact_secrets

    text, n1 = node_redact(text or "", literals)
    text, n2 = redact_secrets(text)
    return text, n1 + n2


def _redact_value(value: Any, literals: List[str]) -> Tuple[Any, int]:
    if isinstance(value, str):
        return master_redact(value, literals)
    if isinstance(value, list):
        total, out = 0, []
        for item in value:
            v, n = _redact_value(item, literals)
            out.append(v)
            total += n
        return out, total
    if isinstance(value, dict):
        total, out = 0, {}
        for key, item in value.items():
            v, n = _redact_value(item, literals)
            out[key] = v
            total += n
        return out, total
    return value, 0


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class Hub:
    def __init__(self, orch):
        self.orch = orch
        self.store = orch.store

    # ------------------------------------------------------------ auth
    def authenticate(self, node_id: str, credential: str) -> Optional[Node]:
        return enroll.authenticate(self.store, node_id, credential)

    # ------------------------------------------------------------ heartbeat
    def heartbeat(self, node: Node, body: Mapping[str, Any], address: str = "") -> Dict[str, Any]:
        fields: Dict[str, Any] = {"last_seen": self.store.now()}
        if body.get("agent_version"):
            fields["agent_version"] = str(body["agent_version"])[:32]
        if isinstance(body.get("policy"), dict):
            fields["policy"] = body["policy"]
        facts_changed = False
        if isinstance(body.get("facts"), dict):
            fields["facts"] = body["facts"]
            fields["facts_at"] = self.store.now()
            facts_changed = True
        if address:
            fields["address"] = address
        was_offline = node.last_seen is None or self.store.now() - float(node.last_seen) > 90
        # The first poll after acceptance proves the node is alive (the setup wizard waits for it).
        first_since_accept = bool(node.accepted_at) and float(node.last_seen or 0) < float(node.accepted_at)
        self.store.update_node(node.id, **fields)
        if facts_changed or was_offline or first_since_accept or "policy" in fields and fields["policy"] != node.policy:
            self.store.change("node", node.id, ["state", "facts"])
        if facts_changed:
            self.orch.on_facts(node.id, body["facts"], node.facts)
        reply: Dict[str, Any] = {"status": node.status}
        if node.status == "active":
            rotated = enroll.maybe_rotate(self.store, node, rotate_after_s=self.orch.rotate_after_s)
            if rotated:
                reply["rotate_credential"] = rotated
        if node.facts_at is None and "facts" not in body:
            reply["collect_facts"] = True
        # A policy the operator edited on the master: delivered until the node reports it in force.
        from . import node_policy
        node_policy.on_heartbeat(self.store, self.store.get_node(node.id) or node, body, reply)
        return reply

    # ------------------------------------------------------------ claim
    def claim(self, node: Node, running: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        store = self.store
        now = store.now()
        dispatches: List[Dict[str, Any]] = []
        cancels: List[Dict[str, Any]] = []
        with store.tx() as conn:
            # Cancels: anything the node says is running that the master wants stopped or has
            # already written off (timed out, cancelled, unreachable).
            for exec_id in running[:64]:
                ex = store.get_execution(str(exec_id), conn)
                if ex is None or ex.node_id != node.id:
                    continue
                if ex.cancel_requested or ex.state in EXEC_TERMINAL:
                    cancels.append(self.orch.keys.sign_cancel({
                        "kind": "cancel", "exec_id": ex.id, "node_id": node.id, "nonce": new_nonce(),
                        "issued_at": now, "deadline": now + 300}))
            if node.status != "active" or self.orch.dispatch_paused():
                return [], cancels
            room = max(0, MAX_PER_POLL - len(running))
            if not room:
                return [], cancels
            rows = conn.execute(
                "SELECT e.id FROM executions e JOIN runs r ON r.id = e.run_id WHERE e.node_id=? AND e.state='queued'"
                " AND (r.state='running' OR (r.state='rolling_back' AND e.rollback_of != ''))"
                " ORDER BY e.created_at LIMIT ?", (node.id, room)).fetchall()
            for row in rows:
                ex = store.get_execution(row["id"], conn)
                try:
                    envelope, secrets_ = self._envelope(conn, node, ex, now)
                except (ValueError, KeyError, SecretsLocked) as exc:
                    target = "cancelled" if isinstance(exc, SecretsLocked) else "failed"
                    check_exec(ex.state, target)
                    store.update_execution(ex.id, conn, state=target,
                                           finished_at=now, reason=f"cannot dispatch: {exc}")
                    store.audit("system", "execution.dispatch_failed", ex.id, {"reason": str(exc)}, conn=conn)
                    store.change("run", ex.run_id, ["executions"], conn=conn)
                    continue
                check_exec(ex.state, "dispatched")
                stored = dict(envelope)
                store.update_execution(ex.id, conn, state="dispatched", dispatched_at=now,
                                       attempts=ex.attempts + 1, nonce=envelope["nonce"], envelope=stored)
                store.audit("system", "execution.dispatch", ex.id,
                            {"run": ex.run_id, "node": node.id, "tool": f"{ex.tool_id}@{ex.version}",
                             "params_digest": digest(ex.params), "check_mode": ex.check_mode,
                             "attempt": ex.attempts + 1}, conn=conn)
                store.change("run", ex.run_id, ["executions"], conn=conn)
                if secrets_:
                    envelope = dict(envelope, secrets=secrets_)
                dispatches.append(envelope)
        return dispatches, cancels

    def _envelope(self, conn, node: Node, ex: Execution, now: float) -> Tuple[Dict[str, Any], Dict[str, str]]:
        store = self.store
        run = store.get_run(ex.run_id, conn)
        if run is not None and run.kind == "agent_update":
            return self._update_order(node, ex, now), {}
        tv = store.get_version(ex.tool_id, ex.version)
        if tv is None:
            raise ValueError(f"tool {ex.tool_id}@{ex.version} no longer exists")
        step = ((run.plan or {}).get("steps") or [{}])[ex.step] if run and not ex.rollback_of else {}
        test = bool(step.get("test"))
        if run is not None and run.kind == "runbook_test" and not ex.rollback_of:
            rb = store.get_runbook_version(run.target_id, run.target_version)
            if rb is None or rb.digest != ((run.plan or {}).get("target") or {}).get("digest"):
                raise ValueError("the draft runbook changed after this test was approved")
            if not (node.canary or node.labels.get("canary") == "true"):
                raise ValueError("test runs are only dispatched to canary nodes")
        if test:
            if tv.manifest_digest != step.get("manifest_digest") or tv.blobs != step.get("blobs"):
                raise ValueError("the draft changed after this test was approved")
            if not (node.canary or node.labels.get("canary") == "true"):
                raise ValueError("test versions are only dispatched to canary nodes")
            version_sig = self.orch.keys.sign_version(tv.tool_id, tv.version, tv.manifest_digest, tv.blobs, test=True)
        else:
            if tv.status != "published" or not tv.signature:
                raise ValueError(f"{tv.ref} is {tv.status}, not a signed published version")
            if not self.orch.keys.verify_version(tv.tool_id, tv.version, tv.manifest_digest, tv.blobs, tv.signature):
                raise ValueError(f"{tv.ref} signature does not verify with the current signing key")
            version_sig = tv.signature
        secret_names = list(tv.manifest.get("secrets") or [])
        secrets_ = self.orch.secrets.resolve(secret_names) if secret_names else {}
        envelope = {
            "kind": "dispatch", "exec_id": ex.id, "run_id": ex.run_id, "node_id": node.id,
            "tool_id": tv.tool_id, "version": tv.version, "test": test, "manifest": tv.manifest,
            "manifest_digest": tv.manifest_digest, "blobs": dict(tv.blobs), "version_sig": version_sig,
            "params": ex.params, "check_mode": bool(ex.check_mode), "issued_at": now,
            "deadline": now + START_BY_S, "nonce": new_nonce(), "secret_names": secret_names,
        }
        return self.orch.keys.sign_dispatch(envelope), secrets_

    def _update_order(self, node: Node, ex: Execution, now: float) -> Dict[str, Any]:
        """The signed agent-update order; the agent the master serves must still be the one approved."""
        from .zipapp import build_zipapp
        data, sha = build_zipapp()
        if sha != (ex.params or {}).get("sha256"):
            raise ValueError("the master's node agent changed since this update was approved; start a new update")
        order = {"kind": "agent_update", "exec_id": ex.id, "run_id": ex.run_id, "node_id": node.id,
                 "version": str(ex.params.get("version")), "sha256": sha, "size": len(data), "issued_at": now,
                 "deadline": now + START_BY_S, "nonce": new_nonce()}
        return self.orch.keys.sign_update(order)

    # ------------------------------------------------------------ events
    def events(self, node: Node, events: List[Mapping[str, Any]]) -> Dict[str, Any]:
        accepted = 0
        for event in events[:200]:
            if not isinstance(event, Mapping):
                continue
            exec_id = str(event.get("exec_id") or "")
            ex = self.store.get_execution(exec_id)
            if ex is None or ex.node_id != node.id:
                continue  # never let one node write another node's results
            kind = event.get("type")
            if kind == "running":
                self._running(ex)
            elif kind == "output":
                self._output(ex, str(event.get("text") or ""))
            elif kind == "result" and isinstance(event.get("result"), Mapping):
                self._result(node, ex, event["result"])
            else:
                continue
            accepted += 1
        return {"accepted": accepted}

    def _running(self, ex: Execution) -> None:
        if ex.state != "dispatched":
            return
        with self.store.tx() as conn:
            self.store.update_execution(ex.id, conn, state="running", started_at=self.store.now())
            self.store.change("run", ex.run_id, ["executions"], conn=conn)

    def _literals(self, ex: Execution) -> List[str]:
        tv = self.store.get_version(ex.tool_id, ex.version)
        names = list((tv.manifest.get("secrets") or []) if tv else [])
        return self.orch.secrets.literals_for(names) if names else []

    def _output(self, ex: Execution, text: str) -> None:
        if ex.state not in EXEC_IN_FLIGHT or not text:
            return
        clean, _ = master_redact(text, self._literals(ex))
        log = (ex.log or "") + clean
        if len(log.encode()) > MAX_LIVE_LOG_BYTES:
            log = "[earlier output trimmed]\n" + log.encode()[-MAX_LIVE_LOG_BYTES:].decode(errors="ignore")
        with self.store.tx() as conn:
            self.store.update_execution(ex.id, conn, log=log, state="running" if ex.state == "dispatched" else ex.state,
                                        started_at=ex.started_at or self.store.now())
            self.store.change("run", ex.run_id, ["executions"], conn=conn)

    def _result(self, node: Node, ex: Execution, result: Mapping[str, Any]) -> None:
        store = self.store
        state = str(result.get("state") or "failed")
        if state not in RESULT_STATES:
            state = "failed"
        if ex.state in EXEC_TERMINAL or ex.state == "queued":
            store.audit(f"node:{node.id}", "execution.late_result", ex.id,
                        {"state": state, "master_state": ex.state})
            return
        literals = self._literals(ex)
        log, n_log = master_redact(str(result.get("log") or ""), literals)
        if len(log.encode()) > MAX_LOG_BYTES:
            log = log.encode()[:MAX_LOG_BYTES].decode(errors="ignore") + "\n[output truncated]\n"
        reason, n_reason = master_redact(str(result.get("reason") or "")[:2000], literals)
        output, n_out = _redact_value(result.get("output"), literals)
        output_errors = [str(e)[:300] for e in (result.get("output_errors") or [])][:20]
        if output is not None and len(json.dumps(output, default=str)) > MAX_OUTPUT_BYTES:
            output, output_errors = None, ["output larger than 1 MiB was dropped"]
        tv = store.get_version(ex.tool_id, ex.version)
        outputs_schema = (tv.manifest.get("outputs") if tv else None) or {}
        if output is not None and outputs_schema:
            master_errors = jschema.validate(output, outputs_schema, "output")[:20]
            if master_errors and not output_errors:
                output_errors = master_errors
        if state == "succeeded" and output_errors:
            state, reason = "failed", reason or "output does not match the declared schema"
        exit_code = result.get("exit_code")
        try:
            exit_code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_code = None
        try:
            duration = float(result.get("duration_s")) if result.get("duration_s") is not None else None
        except (TypeError, ValueError):
            duration = None
        with store.tx() as conn:
            current = store.get_execution(ex.id, conn)
            if current is None or current.state in EXEC_TERMINAL:
                return
            check_exec(current.state, state)
            store.update_execution(ex.id, conn, state=state, finished_at=store.now(), exit_code=exit_code,
                                   output=output, output_errors=output_errors, log=log,
                                   log_truncated=bool(result.get("log_truncated")), reason=reason,
                                   duration_s=duration, output_digest=digest(output) if output is not None else "",
                                   redactions=_int(result.get("redactions")) + n_log + n_reason + n_out,
                                   started_at=current.started_at or current.dispatched_at)
            store.audit(f"node:{node.id}", "execution.result", ex.id,
                        {"run": ex.run_id, "state": state, "exit_code": exit_code,
                         "output_digest": digest(output) if output is not None else "", "reason": reason[:300]},
                        conn=conn)
            store.change("run", ex.run_id, ["executions", "state"], conn=conn)
        self.orch.wake()

    # ------------------------------------------------------------ blobs
    def blob(self, node: Node, sha: str) -> Optional[bytes]:
        sha = str(sha or "").replace("sha256:", "")
        for ex in self.store.list_executions(node_id=node.id, states=EXEC_IN_FLIGHT):
            if sha in (ex.envelope.get("blobs") or {}).values():
                return self.store.get_blob(sha)
        return None
