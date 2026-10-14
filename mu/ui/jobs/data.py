"""In-process data source for the terminal job dashboard.

Reads ONLY the shared core view models (``mu.jobs.core.vm``) and mutates ONLY
through ``mu.jobs.core.commands.dispatch`` — availability is never decided
here. Optional CONTRACT additions (activity VM, push preview, status VM,
``markViewed``) are looked up lazily and degrade gracefully when the backend
does not provide them yet.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

# Action presentation fields (CONTRACT §7). Used only when the backend's
# ``JobAction.to_dict`` does not provide them yet; never affects ``enabled``.
_TONES = {"approvePlan": "ok", "rejectPlan": "danger", "approveTool": "warn", "denyTool": "danger",
          "retry": "warn", "restartStage": "warn", "resume": "warn", "push": "ok",
          "discard": "danger", "delete": "danger"}
_CONFIRMS = {"discard": "Discard this job? Running work is cancelled.",
             "delete": "Delete this job? The record, telemetry, evidence and managed worktree are removed; "
                       "the Git branch is preserved.",
             "restartStage": "Restart the current stage?"}
_INPUTS = {"rejectPlan": {"label": "What should the planner change?", "optional": False, "multiline": True},
           "denyTool": {"label": "Reason for the agent (optional)", "optional": True, "multiline": False},
           "resume": {"label": "Guidance for the agent (optional)", "optional": True, "multiline": True},
           "reprompt": {"label": "Extra guidance (optional)", "optional": True, "multiline": True},
           "message": {"label": "Message the agent", "optional": False, "multiline": True}}
_HIDDEN = {"markFindings", "markViewed"}


def normalize_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """Fill CONTRACT §7 presentation fields the backend may not send yet."""
    a = dict(action)
    aid = a.get("id", "")
    if "tone" not in a:
        a["tone"] = _TONES.get(aid, "")
    if "confirm" not in a:
        a["confirm"] = _CONFIRMS.get(aid)
    if "input" not in a:
        a["input"] = _INPUTS.get(aid)
    if "dialog" not in a:
        a["dialog"] = "push" if aid == "push" else None
    if "hidden" not in a:
        a["hidden"] = aid in _HIDDEN
    return a


# ------------------------------------------------------------ "rows" rules + toolchains
#
# Shared by the jobs TUI Settings screen and `/job settings` / `/job toolchains`.

def rows_display(field: Dict[str, Any], value: Any) -> str:
    """``/src/app · rust:1.88; /src/web · node:22`` (column values in schema order)."""
    rows = [r for r in (value or []) if isinstance(r, dict)]
    if not rows:
        return "none"
    keys = [c["key"] for c in field.get("columns") or []]
    return "; ".join(" · ".join(str(r.get(k)) for k in (keys or sorted(r)) if r.get(k) not in (None, "")) or "(empty)"
                     for r in rows)


def rows_example(field: Dict[str, Any]) -> str:
    cols = field.get("columns") or []
    return json.dumps([{c["key"]: c.get("placeholder") or c.get("label") or c["key"] for c in cols}])


def parse_rows_text(field: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    """JSON text -> list of row objects (a single object is accepted; blank = no rows)."""
    raw = str(text or "").strip()
    if not raw:
        return []
    label = field.get("label") or field.get("path") or "Rows"
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be JSON, e.g. {rows_example(field)} ({exc})") from None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise ValueError(f"{label} must be a JSON list of objects, e.g. {rows_example(field)}")
    return value


def present_rows_field(field: Dict[str, Any]) -> Dict[str, Any]:
    """Readable ``display``/``default_display`` and JSON ``edit_text`` for a ``rows`` field."""
    if field.get("kind") != "rows":
        return field
    return {**field, "display": rows_display(field, field.get("value")),
            "default_display": rows_display(field, field.get("default")),
            "edit_text": json.dumps(field.get("value") or [])}


TOOLCHAIN_GLYPH = {"built": "✓", "building": "●", "failed": "✗", "missing": "○"}
TOOLCHAIN_TONE = {"built": "ok", "building": "accent", "failed": "danger", "missing": "muted"}


def toolchain_label(status: str) -> str:
    return "not built" if status == "missing" else (status or "unknown")


def toolchain_line(item: Dict[str, Any]) -> str:
    status = str(item.get("status") or "missing")
    return (f"{TOOLCHAIN_GLYPH.get(status, '○')} {toolchain_label(status):<9}  {item.get('repository', '')}"
            f" ← {item.get('base_image', '')}")



def fold_environment_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One "Environments" section: Toolchains rules fold into Build environments.

    The toolchain rows are another build-environment source, so the dashboard
    shows them (and their image status, ``toolchains: True``) at the end of the
    Build environments section instead of a screen of their own."""
    by_id = {str(sec.get("id") or ""): sec for sec in sections}
    tools, envs = by_id.get("toolchains"), by_id.get("build_environments")
    if tools is None or envs is None:
        return sections
    merged = {**envs, "title": "Environments", "toolchains": True, "toolchains_at": len(envs.get("fields") or []),
              "fields": list(envs.get("fields") or []) + list(tools.get("fields") or []),
              "hint": envs.get("hint") or "", "toolchains_hint": tools.get("hint") or ""}
    return [merged if sec is envs else sec for sec in sections if sec is not tools]

class DispatchError(RuntimeError):
    """A rejected or malformed command (message is user-facing)."""


class JobsData:
    """Thin facade over ``mu.jobs.core`` (lazy imports, one lock)."""

    def __init__(self, service: Any = None, *, creator: Optional[Callable[[str, str], str]] = None):
        self._service = service
        # ``creator(title, agent_spec) -> job id``: set when the dashboard runs inside a session
        # (/jobs), which supplies the workspace and model defaults a new job needs.
        self.creator = creator
        self._lock = threading.RLock()
        self._cursor: Optional[int] = None
        self._bus = None

    # ------------------------------------------------------------ plumbing
    @property
    def service(self):
        if self._service is None:
            from mu.jobs.core.vm import get_default_job_service
            self._service = get_default_job_service()
        return self._service

    @staticmethod
    def _vm():
        from mu.jobs.core import vm
        return vm

    @staticmethod
    def _optional(*names: str) -> Optional[Callable[..., Any]]:
        try:
            from mu.jobs.core import vm
        except Exception:  # noqa: BLE001
            return None
        for name in names:
            fn = getattr(vm, name, None)
            if callable(fn):
                return fn
        return None

    # ------------------------------------------------------------ reads
    def home(self) -> Dict[str, Any]:
        with self._lock:
            data = self._vm().home_vm(self.service)
        data["fetched_at"] = time.time()
        for key in ("needs_you", "running", "jobs"):
            for row in data.get(key, []):
                row["actions"] = [normalize_action(a) for a in row.get("actions", [])]
        return data

    def job(self, job_id: str, parts: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        with self._lock:
            data = self._vm().job_vm(self.service, job_id, parts=list(parts) if parts else None)
        data["fetched_at"] = time.time()
        data["actions"] = [normalize_action(a) for a in data.get("actions", [])]
        return data

    def resolve(self, token: str) -> str:
        with self._lock:
            return self._vm().resolve_job(self.service, token).id

    def status(self, home: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fn = self._optional("activity_status_vm", "status_vm")
        if fn is not None:
            try:
                with self._lock:
                    out = dict(fn(self.service))
                return out
            except Exception:  # noqa: BLE001 - chips are cosmetic
                pass
        home = home or {}
        running = home.get("running", [])
        return {"agents": sum(1 for r in running if r.get("state") in ("planning", "implementing", "reviewing")),
                "checks": sum(1 for r in running if r.get("state") == "checking"),
                "needs_you": len(home.get("needs_you", [])), "engine": "unknown"}

    def activity(self, home: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fn = self._optional("activity_vm")
        if fn is not None:
            try:
                with self._lock:
                    return dict(fn(self.service))
            except Exception as exc:  # noqa: BLE001 - never break the view
                return {"engine": "down", "jobs": [], "orphans": [], "error": str(exc),
                        "counts": {"agents": 0, "checks": 0, "containers": 0, "needs_you": 0}}
        home = home or self.home()
        stage_of = {"provisioning": "plan", "planning": "plan", "implementing": "implement",
                    "checking": "checks", "reviewing": "review", "pushing": "push"}
        jobs = []
        for r in home.get("running", []):
            step = next((s for s in r.get("steps", []) if s.get("status") == "active"), {})
            kind = "checks" if r.get("state") == "checking" else "agent"
            jobs.append({"id": r["id"], "key": r.get("key") or r["id"][:8], "title": r.get("title", ""),
                         "state": r.get("state", ""), "stage": stage_of.get(r.get("state", ""), ""),
                         "worker_id": None, "heartbeat_at": None, "lease_expires_at": None,
                         "since": step.get("started_at"), "cost_usd": r.get("cost_usd", 0.0),
                         "kind": kind, "last": "", "containers": []})
        return {"generated_at": time.time(), "engine": "off", "jobs": jobs, "orphans": [],
                "counts": {"agents": sum(1 for j in jobs if j["kind"] == "agent"),
                           "checks": sum(1 for j in jobs if j["kind"] == "checks"),
                           "containers": 0, "needs_you": len(home.get("needs_you", []))},
                "fallback": True}

    def activity_logs(self, job_id: str, tail_bytes: int = 16384) -> Dict[str, Any]:
        fn = self._optional("activity_logs_vm", "activity_logs")
        if fn is None:
            return {"text": "", "truncated": False, "source": ""}
        try:
            with self._lock:
                return dict(fn(self.service, job_id, tail_bytes=tail_bytes))
        except Exception:  # noqa: BLE001
            return {"text": "", "truncated": False, "source": ""}

    def push_preview(self, job: Dict[str, Any]) -> Dict[str, Any]:
        fn = self._optional("push_preview_vm", "push_preview")
        if fn is not None:
            try:
                with self._lock:
                    return dict(fn(self.service, job["id"]))
            except Exception:  # noqa: BLE001 - fall back to a local default
                pass
        push = next((a for a in job.get("actions", []) if a.get("id") == "push"), {})
        return {"title": job.get("title", ""), "body": "", "branch": job.get("branch") or "",
                "base": "", "mode": "", "remote": "", "draft": True,
                "requires_override": bool((push.get("args") or {}).get("requires_override")),
                "verdict": job.get("verdict", ""), "head_sha": "", "reason": push.get("reason", ""),
                "fallback": True}

    def remove_container(self, name: str) -> Dict[str, Any]:
        fn = self._optional("remove_container")
        if fn is None:
            raise DispatchError("removing containers is not supported by this backend")
        try:
            with self._lock:
                return dict(fn(self.service, name) or {})
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc

    def settings(self) -> Dict[str, Any]:
        """Run-rule sections (``job_settings_vm``); empty when the backend has none."""
        fn = self._optional("job_settings_vm")
        if fn is None:
            return {"sections": [], "path": ""}
        with self._lock:
            out = dict(fn())
        out["sections"] = fold_environment_sections(
            [{**sec, "fields": [present_rows_field(f) for f in sec.get("fields") or []]}
             for sec in out.get("sections") or []])
        return out

    def toolchains(self) -> List[Dict[str, Any]]:
        """Settings → Toolchains rows with image status (``[]`` when the backend has none)."""
        fn = self._optional("toolchains_vm")
        if fn is None:
            return []
        with self._lock:
            return list((fn() or {}).get("toolchains") or [])

    def agents(self) -> Dict[str, Any]:
        """Agent harness status (mucli / Claude Code / Codex): readiness and per-stage defaults."""
        fn = self._optional("agents_status")
        if fn is None:
            return {"defaults": {}}
        try:
            return dict(fn() or {})
        except Exception as exc:  # noqa: BLE001 - informational
            return {"defaults": {}, "error": str(exc)}

    def setup(self, *, recheck: bool = False) -> Dict[str, Any]:
        """Guided setup checklist (``setup_vm``); empty when the backend has none."""
        fn = self._optional("setup_vm")
        if fn is None:
            return {"steps": [], "done": 0, "total": 0, "blocking": [], "can_finish": False}
        return dict(fn(recheck=recheck) or {})

    def doctor(self, *, deep: bool = False) -> List[Dict[str, Any]]:
        """Doctor checks ``[{name, status, detail}]`` (``doctor_vm``)."""
        fn = self._optional("doctor_vm")
        if fn is None:
            return []
        return list(fn(deep=deep) or [])

    def needs_setup(self) -> bool:
        fn = self._optional("needs_setup")
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - a hint only
            return False

    def setup_action(self, step: str, action: str, inputs: Optional[List[str]] = None, *,
                     on_line: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """Run one setup action; ``job`` actions block (call from a background thread)."""
        from mu.jobs.core import commands
        try:
            return dict(commands.setup_action(step, action, list(inputs or []), wait=True, on_line=on_line) or {})
        except (ValueError, RuntimeError, OSError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc

    def setup_finish(self) -> Dict[str, Any]:
        from mu.jobs.core import commands
        try:
            return dict(commands.setup_finish() or {})
        except (ValueError, RuntimeError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc

    # ------------------------------------------------------------ writes
    def create_job(self, title: str, agents: str = "") -> str:
        """Queue a new job in the dashboard's session; ``agents`` is a ``--agent`` spec."""
        if self.creator is None:
            raise DispatchError("open the dashboard from a session (/jobs) to create jobs")
        try:
            return str(self.creator(title, agents))
        except (ValueError, TypeError, RuntimeError, OSError, KeyError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc

    def set_rule(self, path: str, text: Optional[str], *, field: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Save one run rule from typed text (``None`` resets it); ``rows`` rules take JSON."""
        from mu.jobs.core import commands
        try:
            with self._lock:
                if text is not None and (field or {}).get("kind") == "rows":
                    saved = dict(commands.set_job_rule_value(path, parse_rows_text(field, text)) or {})
                else:
                    saved = dict(commands.set_job_rule(path, text) or {})
        except (ValueError, TypeError, OSError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc
        return present_rows_field({**(field or {}), **saved}) if (field or {}).get("kind") == "rows" else saved

    def build_toolchain(self, base_image: str) -> Dict[str, Any]:
        from mu.jobs.core import commands
        try:
            with self._lock:
                return dict(commands.build_toolchain(base_image) or {})
        except (ValueError, RuntimeError, OSError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc

    def dispatch(self, job_id: str, kind: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from mu.jobs.core import commands
        try:
            with self._lock:
                result = commands.dispatch(self.service, {"type": kind, "job_id": job_id, "args": dict(args or {})})
        except KeyError as exc:
            raise DispatchError(f"job not found: {exc}") from exc
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            raise DispatchError(str(exc) or exc.__class__.__name__) from exc
        if kind not in ("delete", "markViewed", "savePlanDraft"):
            try:
                commands.ensure_controller_daemon()
            except Exception:  # noqa: BLE001 - worker host is best effort here
                pass
        return result.to_dict() if hasattr(result, "to_dict") else dict(result or {})

    # ------------------------------------------------------------ changes
    def changes(self) -> Optional[List[Dict[str, Any]]]:
        """Durable change events since the last call (``None`` = no hook, poll)."""
        try:
            from mu.jobs.core.changes import ChangeBus, collect_durable_changes
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            store = self.service.store
            if self._cursor is None:
                cursor_fn = getattr(store, "event_cursor", None)
                if not callable(cursor_fn):
                    return None
                self._cursor = int(cursor_fn())
                self._bus = ChangeBus(window_s=0.0)
                return []
            self._cursor = collect_durable_changes(self.service, self._bus, self._cursor)
            return [c.to_dict() for c in self._bus.flush(force=True)]
