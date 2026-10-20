"""The orchestration master: one object wiring store, keys, secrets, hub and the scheduler loop.

The GUI process owns one ``Orchestrator`` (``default_orchestrator()``), starts its scheduler
loop and, when a private bind address is configured, the node endpoint. CLI processes and AI
tool handlers open the same store; the database is the source of truth, so anything they
create is picked up by the running master. A lease row ensures only one process schedules.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import catalog, runs, schedules
from .hub import Hub
from .models import Run
from .secrets import SecretStore, SecretsLocked
from .signing import KeyRing
from .store import OrchestrationStore, new_id, orchestration_home

_logger = logging.getLogger(__name__)

LEASE_S = 15.0
TICK_S = 1.0
AGENT_UPDATE_CHECK_S = 300.0          # how often the scheduler looks for out-of-date agents
AGENT_UPDATE_RETRY_S = 24 * 3600.0    # don't re-propose a version within a day of the last proposal
DEFAULT_NETWORK = {"bind": "", "port": 30312, "advertise": ""}


class Orchestrator:
    def __init__(self, root: Optional[str] = None, *, clock: Callable[[], float] = time.time,
                 notify: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.root = os.path.abspath(os.path.expanduser(root or orchestration_home()))
        self.store = OrchestrationStore(self.root, clock=clock)
        self.keys = KeyRing(self.root)
        self.secrets = SecretStore(self.store, self.keys.dir)
        self.hub = Hub(self)
        self.notify = notify
        self.clock = clock
        self.rotate_after_s = 30 * 86400.0
        self.scheduler_config: Dict[str, float] = {}
        self._owner = f"{socket.gethostname()}:{os.getpid()}:{id(self)}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.metrics: Dict[str, float] = {"ticks": 0, "polls": 0, "events": 0, "dispatches": 0, "tick_errors": 0}
        # Raw join tokens of this process's setup wizard, kept only until the token is used or
        # expires so the wizard can hand out a pre-filled install.sh (the store keeps hashes only).
        self._setup_tokens: Dict[str, Dict[str, Any]] = {}
        self._setup_lock = threading.Lock()
        self._agent_check_at = 0.0

    # ------------------------------------------------------------ settings
    def dispatch_paused(self) -> bool:
        return bool(self.store.get_setting("dispatch_paused", False))

    def set_dispatch_paused(self, paused: bool, *, actor: str, reason: str = "") -> None:
        with self.store.tx() as conn:
            self.store.set_setting("dispatch_paused", bool(paused), conn=conn)
            self.store.audit(actor, "dispatch.pause" if paused else "dispatch.resume", "", {"reason": reason},
                             conn=conn)
            self.store.change("run", "", ["state"], conn=conn)
        self._attention({"kind": "dispatch", "paused": bool(paused)})

    def network(self) -> Dict[str, Any]:
        return dict(DEFAULT_NETWORK, **(self.store.get_setting("network", {}) or {}))

    def master_url(self) -> str:
        net = self.network()
        host = net.get("advertise") or net.get("bind") or ""
        if not host:
            return ""
        host = f"[{host}]" if ":" in host else host
        return f"https://{host}:{int(net.get('port') or 30312)}"

    def defaults(self) -> Dict[str, Any]:
        return dict({"strategy": {"canary": 1, "batch": 10, "failure_threshold": 10, "pause_between_s": 0}},
                    **(self.store.get_setting("defaults", {}) or {}))

    # ------------------------------------------------------------ attention
    def _attention(self, event: Dict[str, Any]) -> None:
        if self.notify:
            try:
                self.notify(dict(event, kind=event.get("kind", "orchestration")))
            except Exception:  # noqa: BLE001 - notification is best effort
                pass

    def attention(self) -> Dict[str, Any]:
        from .attention import counts
        return counts(self.store)

    # ------------------------------------------------------------ runs
    def secrets_ready(self, names) -> Optional[str]:
        try:
            missing = [n for n in names if self.secrets.get(n) is None]
        except SecretsLocked as exc:
            return str(exc)
        if missing:
            return "missing secret(s): " + ", ".join(missing)
        return None

    def create_run(self, *, actor: str, kind: str, target: str, params=None, selector=None, strategy=None,
                   check_mode: bool = False, title: str = "", schedule_id: str = "") -> Run:
        """Propose a run; start it at once when the access policy says ``allow``."""
        run = runs.propose(self.store, actor=actor, kind=kind, target=target, params=params, selector=selector,
                           strategy=strategy, check_mode=check_mode, title=title, schedule_id=schedule_id)
        return self.start_if_allowed(run)

    def start_if_allowed(self, run: Run) -> Run:
        """Approve a fresh proposal on the access policy's behalf when its decision is ``allow``
        (for tests only an explicit ``run: test`` rule can say that); otherwise ask the operator."""
        decision = run.decision or {}
        if run.state == "proposed" and decision.get("level") == "allow":
            try:
                run = runs.approve(self.store, run.id, actor="policy", auto_rule=str(decision.get("rule") or "default"),
                                   secrets_ready=self.secrets_ready)
            except runs.RunError as exc:
                self.store.audit("system", "run.auto_approve_failed", run.id, {"reason": str(exc)})
        if run.state == "proposed":
            self._attention({"kind": "orchestration_approval", "run_id": run.id, "title": run.title})
        self.wake()
        return run

    # ------------------------------------------------------------ agent updates
    def agent_update_settings(self) -> Dict[str, Any]:
        return dict({"auto": False}, **(self.store.get_setting("agent_updates", {}) or {}))

    def outdated_nodes(self) -> List[Any]:
        """Active nodes whose agent this master can update now (see ``runs.agent_update_block``)."""
        return [n for n in self.store.list_nodes(statuses=["active"]) if runs.agent_update_block(n) is None]

    def propose_agent_update(self, *, actor: str, node_ids: Optional[List[str]] = None, strategy=None,
                             title: str = "") -> Run:
        """An ordinary run of kind ``agent_update``: planned (with every node it can't update and
        why), decided by the access policy, canary first, batched, audited."""
        from .node import AGENT_VERSION
        ids = list(node_ids or [n.id for n in self.outdated_nodes()])
        if not ids:
            raise runs.RunError(f"every node that can update itself already runs agent {AGENT_VERSION}")
        return self.create_run(actor=actor, kind="agent_update", target=f"{runs.AGENT_TOOL}@0",
                               selector={"nodes": ids}, strategy=strategy or {},
                               title=title or f"Update node agents to {AGENT_VERSION}")

    def auto_update_agents(self, now: float) -> Optional[Run]:
        """With automatic updates on, propose one update job for the out-of-date nodes (whether it
        starts without you is the access policy's decision, like any run)."""
        settings = self.agent_update_settings()
        if not settings.get("auto"):
            return None
        from .node import AGENT_VERSION
        open_runs = self.store.list_runs(states=["proposed", "approved", "running", "paused"], kind="agent_update",
                                         limit=5)
        if open_runs or not self.outdated_nodes():
            return None
        last = settings.get("last") or {}
        if last.get("version") == AGENT_VERSION and now - float(last.get("at") or 0) < AGENT_UPDATE_RETRY_S:
            return None
        self.store.set_setting("agent_updates", dict(settings, last={"version": AGENT_VERSION, "at": now}))
        try:
            run = self.propose_agent_update(actor="schedule:agent-updates")
        except runs.RunError as exc:
            self.store.audit("schedule:agent-updates", "agent_update.skipped", "", {"reason": str(exc)})
            return None
        return run

    # ------------------------------------------------------------ node setup
    def remember_setup_token(self, token: Mapping[str, Any], *, name: str = "") -> None:
        with self._setup_lock:
            self._purge_setup_tokens()
            if token.get("token_id"):
                self._setup_tokens[str(token["token_id"])] = {"token": str(token["token"]), "name": name,
                                                              "expires_at": float(token["expires_at"])}

    def setup_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """The raw token for ``token_id`` while it is unused and unexpired, else None."""
        with self._setup_lock:
            self._purge_setup_tokens()
            entry = self._setup_tokens.get(str(token_id))
            return dict(entry) if entry else None

    def _purge_setup_tokens(self) -> None:
        from . import enroll
        now = self.store.now()
        for token_id in list(self._setup_tokens):
            entry = self._setup_tokens[token_id]
            status = enroll.token_status(self.store, token_id)
            if entry["expires_at"] < now or status is None or status.get("used_at"):
                del self._setup_tokens[token_id]

    def approve_run(self, run_id: str, *, actor: str, typed: str = "") -> Run:
        run = runs.approve(self.store, run_id, actor=actor, typed=typed, secrets_ready=self.secrets_ready)
        self.wake()
        return run

    # ------------------------------------------------------------ facts / triggers
    def on_facts(self, node_id: str, new: Mapping[str, Any], old: Mapping[str, Any]) -> None:
        node = self.store.get_node(node_id)
        if node is None or node.status != "active":
            return
        from . import selectors
        for item in self.store.list_schedules():
            if item["kind"] != "trigger" or not item["enabled"]:
                continue
            target = item["target"] or {}
            node_sel = str((target.get("selector") or {}).get("labels") or "")
            try:
                if node_sel and not selectors.matches(node_sel, node.labels):
                    continue
            except selectors.SelectorError:
                continue
            state = dict(item.get("state") or {})
            per_node = dict(state.get(node_id) or {})
            hit = schedules.condition(item["spec"], new, old)
            was = bool(per_node.get("active"))
            now = self.store.now()
            cooldown = float(item["spec"].get("cooldown_s") or schedules.DEFAULT_COOLDOWN_S)
            fire = hit and (not was or item["spec"].get("op") == "changed") and \
                now - float(per_node.get("fired_at") or 0) >= cooldown
            per_node["active"] = hit
            if fire:
                per_node["fired_at"] = now
            state[node_id] = per_node
            item = dict(item, state=state, last_fired_at=now if fire else item.get("last_fired_at"))
            self.store.put_schedule(item)
            if fire:
                self._fire(item, selector={"nodes": [node_id]},
                           reason=f"{item['spec'].get('metric')} {item['spec'].get('op')} "
                                  f"{item['spec'].get('value', '')} on {node.name}")

    def _fire(self, item: Mapping[str, Any], *, selector: Optional[Mapping[str, Any]] = None, reason: str = "") -> None:
        target = item.get("target") or {}
        actor = f"schedule:{item['id']}"
        try:
            run = self.create_run(actor=actor, kind=target.get("kind", "tool"), target=str(target.get("target")),
                                  params=target.get("params") or {},
                                  selector=selector or target.get("selector") or {},
                                  strategy=target.get("strategy") or {}, check_mode=bool(target.get("check_mode")),
                                  title=f"{item['name']}: {reason}" if reason else str(item["name"]),
                                  schedule_id=str(item["id"]))
            self.store.audit(actor, "schedule.fire", item["id"], {"run": run.id, "reason": reason})
        except runs.RunError as exc:
            self.store.audit(actor, "schedule.fire_failed", item["id"], {"error": str(exc)[:500]})

    def put_schedule(self, item: Mapping[str, Any], *, actor: str) -> Dict[str, Any]:
        item = dict(item)
        item.setdefault("id", new_id("s", 4))
        item.setdefault("enabled", True)
        problems = schedules.validate(item)
        if problems:
            raise schedules.ScheduleError("; ".join(problems))
        existing = self.store.get_schedule(item["id"]) or {}
        item.setdefault("state", existing.get("state") or {})
        item["next_at"] = schedules.next_fire(item, self.store.now())
        self.store.put_schedule(item)
        self.store.audit(actor, "schedule.put", item["id"], {"name": item["name"], "kind": item["kind"],
                                                            "spec": item["spec"], "target": item["target"]})
        self.store.change("run", "", ["schedules"])
        return self.store.get_schedule(item["id"])

    def _due_schedules(self, now: float) -> None:
        for item in self.store.list_schedules():
            if not item["enabled"] or item["kind"] == "trigger" or not item.get("next_at"):
                continue
            if float(item["next_at"]) <= now:
                item = dict(item, last_fired_at=now, next_at=schedules.next_fire(item, now))
                self.store.put_schedule(item)
                self._fire(item)

    # ------------------------------------------------------------ scheduler loop
    def _acquire_lease(self, now: float) -> bool:
        with self.store.tx() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='scheduler_lease'").fetchone()
            import json
            lease = json.loads(row["value"]) if row else {}
            if lease.get("owner") not in (None, self._owner) and float(lease.get("until") or 0) > now:
                return False
            self.store.set_setting("scheduler_lease", {"owner": self._owner, "until": now + LEASE_S}, conn=conn)
        return True

    def tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        """One scheduler pass. Safe to call from tests without the background thread."""
        now = self.store.now() if now is None else now
        if not self._acquire_lease(now):
            return {"leader": False}
        runs.expire_proposals(self.store, now)
        paused = self.dispatch_paused()
        applied = 0
        for run in self.store.list_runs(states=["approved", "running", "rolling_back"], limit=1000):
            try:
                applied += len(runs.apply_tick(self.store, run.id, now=now, dispatch_paused=paused,
                                               config=self.scheduler_config, on_attention=self._attention))
            except Exception:  # noqa: BLE001 - one bad run must not stop the others
                self.metrics["tick_errors"] += 1
                _logger.exception("orchestration: tick failed for run %s", run.id)
        try:
            self._due_schedules(now)
        except Exception:  # noqa: BLE001
            _logger.exception("orchestration: schedule evaluation failed")
        if now - self._agent_check_at >= AGENT_UPDATE_CHECK_S:
            self._agent_check_at = now
            try:
                self.auto_update_agents(now)
            except Exception:  # noqa: BLE001
                _logger.exception("orchestration: agent update check failed")
        self.metrics["ticks"] += 1
        return {"leader": True, "applied": applied}

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Nodes reconnect over the next minute or two: until then they show as connecting.
        self.store.set_setting("master_started_at", self.store.now())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="orchestration-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            with self.store.tx() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key='scheduler_lease'").fetchone()
                import json
                if row and json.loads(row["value"]).get("owner") == self._owner:
                    conn.execute("DELETE FROM settings WHERE key='scheduler_lease'")
        except Exception:  # noqa: BLE001
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                self.metrics["tick_errors"] += 1
                _logger.exception("orchestration: scheduler tick failed")
            self._wake.wait(TICK_S)
            self._wake.clear()

    # ------------------------------------------------------------ starter pack
    def install_starter_pack(self, *, force: bool = False, only: Optional[List[str]] = None) -> List[str]:
        """Sign and publish the built-in tools that aren't published yet (all of them with
        ``force``). Safe to repeat: a master that installed an older pack gains the new tools
        (e.g. the maintenance tools) without touching the ones it has. ``only`` limits it to ids."""
        from .starter import load_starter_tools
        installed = []
        for manifest, files in load_starter_tools():
            if only is not None and manifest["id"] not in only:
                continue
            if self.store.latest_version(manifest["id"], published_only=True) and not force:
                continue
            existing = [v for v in self.store.list_versions(manifest["id"]) if v.status in ("draft", "testing", "in_review")]
            for v in existing:
                catalog.delete_draft(self.store, v.tool_id, v.version, actor="system")
            tv = catalog.create_draft(self.store, actor="system", manifest=manifest, files=files, origin="starter",
                                      intent="Built-in starter pack")
            catalog.submit_for_review(self.store, tv.tool_id, tv.version, actor="system",
                                      headline="Built-in starter tool" if manifest.get("effect") == "read_only"
                                      else f"Built-in {manifest.get('effect')} maintenance tool")
            catalog.publish(self.store, self.keys, tv.tool_id, tv.version, actor="system")
            installed.append(tv.ref)
        self.store.set_setting("starter_installed", True)
        return installed


_DEFAULT: Optional[Orchestrator] = None
_DEFAULT_LOCK = threading.Lock()


def default_orchestrator(**kwargs) -> Orchestrator:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None or _DEFAULT.root != os.path.abspath(os.path.expanduser(orchestration_home())):
            _DEFAULT = Orchestrator(**kwargs)
        elif kwargs.get("notify") is not None:
            _DEFAULT.notify = kwargs["notify"]
        return _DEFAULT
