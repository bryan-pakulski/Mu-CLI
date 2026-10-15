"""Keyboard-driven job dashboard state machine (planos TUI, in Python).

``JobsApp`` owns navigation, overlays (help, command palette), inline modes
(prompt, confirm, push form) and key handling. It is UI-toolkit agnostic:
``render(width, height)`` returns lines and ``handle_key(name)`` applies a
key, so the whole experience is unit-testable without a TTY. ``run.py`` wraps
it in a prompt_toolkit full-screen application.

Actions come only from the core VM ``actions`` list; disabled actions flash
their reason and every mutation goes through ``commands.dispatch``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import content
from .data import DispatchError, JobsData
from .theme import state_style

# Same tab ids and names as the web and Android job detail.
TABS = ("session", "plan", "diff", "review", "checks", "diagnostics", "costs")
TAB_LABEL = {"session": "Session", "plan": "Plan", "diff": "Diff", "review": "Review", "checks": "Checks",
             "diagnostics": "Diagnostics", "costs": "Cost & audit"}
# Keys owned by tab navigation; never routed to actions.
NAV_KEYS = {"j", "k", "g", "G", "n", "N", "v", "f", " ", "space", "/", "[", "]", "{", "}", "l"}
# Setup's tabs (route name -> label); Doctor lives inside Setup on every surface.
SETUP_TABS = {"setup": "Checklist", "doctor": "Doctor"}
DEFAULT_TAB = {"plan_review": "plan", "review_done": "review", "checking": "checks", "failed": "diagnostics",
               "pushed": "review"}
SAMPLES = 60

GLOBAL_KEYS: Sequence[Tuple[str, str]] = (
    (": / ctrl+k", "command palette (every action and screen)"),
    ("g s", "settings: job run rules"),
    ("g a", "agents: Claude Code / Codex setup"),
    ("g u", "setup: guided checklist (Docker, image, agents, sandbox) · tab switches to Doctor"),
    ("g d", "setup › doctor tab: check everything jobs need"),
    ("?", "this help"),
    ("esc / q", "back (q on the dashboard quits)"),
    ("j/k ↑/↓", "move · PgUp/PgDn, g/G"),
    ("ctrl+c", "quit"),
)


def fuzzy(q: str, text: str) -> float:
    """Fuzzy subsequence score (higher is better), -1 when ``q`` doesn't match."""
    if not q:
        return 0.0
    t, s = text.lower(), q.lower()
    direct = t.find(s)
    if direct >= 0:
        return 1000 - direct * 2 - len(t) / 100
    ti = 0
    score = 0.0
    run = 0
    for ch in s:
        at = t.find(ch, ti)
        if at < 0:
            return -1
        run = run + 1 if at == ti else 0
        score += 10 + run * 5 - min(9, at - ti)
        if at == 0 or t[at - 1] in " ›:/._-":
            score += 8
        ti = at + 1
    return score


def rank_palette(items: Sequence[Dict[str, Any]], q: str) -> List[Dict[str, Any]]:
    scored = []
    for i, it in enumerate(items):
        s = fuzzy(q, f"{it.get('group', '')} {it.get('label', '')} {it.get('key', '')}")
        if s >= 0:
            scored.append((0 if it.get("enabled", True) else 1, -s, i, it))
    scored.sort(key=lambda x: x[:3])
    return [x[3] for x in scored]


class JobViewState:
    """Per-job view state (tab, cursors, folds, marks)."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.tab = ""
        self.tops: Dict[str, int] = {}
        self.item = -1          # session item cursor (-1 = follow the end)
        self.expanded: Set[int] = set()
        self.expand_all = False
        self.finding = 0
        self.marked: Set[str] = set()
        self.diff_file = 0
        self.diff_top = 0
        self.run = -1           # checks run cursor (-1 = latest)
        self.last_state = ""
        self.local_viewed: Dict[str, bool] = {}


class JobsApp:
    def __init__(self, data: Optional[JobsData] = None, *, clock: Callable[[], float] = time.time,
                 start_job: Optional[str] = None):
        self.data = data or JobsData()
        self.clock = clock
        self.stack: List[Dict[str, Any]] = [{"name": "home"}]
        self.home_vm: Optional[Dict[str, Any]] = None
        self.status: Dict[str, Any] = {}
        self.activity_vm: Optional[Dict[str, Any]] = None
        self.samples: Dict[str, Dict[str, List[Optional[float]]]] = {}
        self.activity_logs: Dict[str, Dict[str, Any]] = {}
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.job_states: Dict[str, JobViewState] = {}
        self.progress: Dict[str, Dict[str, int]] = {}
        self.flash_msg: Optional[Tuple[str, str, float]] = None
        self.overlay: Optional[str] = None
        self.palette_q = ""
        self.palette_cursor = 0
        self.mode: Optional[Dict[str, Any]] = None
        self.region = "needs"
        self.cursors = {"needs": 0, "jobs": 0, "activity": 0, "settings": 0}
        # Jobs span every session; ``None`` shows all, otherwise one origin session.
        self.session_filter: Optional[str] = None
        self.settings_vm: Optional[Dict[str, Any]] = None
        self.toolchains: List[Dict[str, Any]] = []
        self.agents_vm: Optional[Dict[str, Any]] = None
        # Setup / Doctor (planos-style): checklist VM, cursors, the running setup job's live log.
        self.setup_vm: Optional[Dict[str, Any]] = None
        self.setup_focus = "steps"          # steps | actions
        self.setup_cursor = 0
        self.setup_action_cursor = 0
        self.setup_checking = False
        self.setup_job: Optional[Dict[str, Any]] = None   # {step, title, running, ok, lines}
        self.setup_terminal: Optional[Tuple[str, str, str]] = None  # (step, action, command) shown to run
        self.doctor_checks: Optional[List[Dict[str, Any]]] = None
        self.doctor_running = ""            # "" | "quick" | "deep"
        self.doctor_cursor = 0
        self.setup_hint = False
        # Runs slow work (docker checks, builds, self-tests) off the UI loop; tests swap in a
        # synchronous runner. ``on_change`` repaints from the background thread.
        self.run_background: Callable[[Callable[[], None]], None] = self._thread
        self._pending_g = False
        self.tick = 0
        self.exit_requested = False
        self.error: str = ""
        self.editor: Callable[[str, str, Callable[[Optional[str]], None]], None] = self._no_editor
        self.on_change: Callable[[], None] = lambda: None
        if start_job:
            self.open_job(start_job)
        needs = getattr(self.data, "needs_setup", None)
        try:
            self.setup_hint = bool(needs()) if callable(needs) else False
        except Exception:  # noqa: BLE001 - a hint only
            self.setup_hint = False

    @staticmethod
    def _thread(work: Callable[[], None]) -> None:
        threading.Thread(target=work, name="mucli-jobs-tui-setup", daemon=True).start()

    # ------------------------------------------------------------ data
    def refresh(self, *, home: bool = True, job: bool = True, activity: Optional[bool] = None) -> None:
        try:
            if home or self.home_vm is None:
                self.refresh_home()
            route = self.route
            if job and route["name"] == "job":
                self.refresh_job(route["id"])
            if activity or (activity is None and route["name"] == "activity"):
                self.refresh_activity()
            if route["name"] == "settings":
                self.refresh_settings()
            self.error = ""
        except Exception as exc:  # noqa: BLE001 - the UI must survive store hiccups
            self.error = str(exc)
            self.flash(f"refresh failed: {exc}", "danger")

    def refresh_home(self) -> None:
        self.home_vm = self.data.home()
        self.status = self.data.status(self.home_vm)
        progress = {}
        for row in self.home_vm.get("running", []):
            if row.get("state") == "checking":
                try:
                    checks = self.data.job(row["id"], parts=["checks"]).get("checks") or {}
                    p = content.live_progress(checks.get("live") or [])
                    if p:
                        progress[row["id"]] = p
                except Exception:  # noqa: BLE001 - progress is cosmetic
                    pass
            elif not row.get("last"):
                try:
                    events = (self.data.job(row["id"], parts=["costs"]).get("costs") or {}).get("events") or []
                    row["last"] = last_activity(events)
                except Exception:  # noqa: BLE001 - the last line is cosmetic
                    pass
        self.progress = progress
        if self.session_filter is not None and self.session_filter not in self.session_names():
            self.session_filter = None
        if self.region == "needs" and not self.filtered("needs_you"):
            self.region = "jobs"
        self._clamp_home()

    def refresh_job(self, job_id: str, parts: Optional[Sequence[str]] = None) -> None:
        try:
            vm = self.data.job(job_id, parts=parts)
        except KeyError:
            self.flash(f"job {job_id[:10]} no longer exists", "warn")
            if self.route.get("id") == job_id:
                self.pop()
            return
        if parts and job_id in self.jobs:
            merged = dict(self.jobs[job_id])
            merged.update(vm)
            vm = merged
        self.jobs[job_id] = vm
        st = self.job_state(job_id)
        state = str(vm.get("state") or "")
        if not st.tab or (state != st.last_state and st.last_state):
            st.tab = DEFAULT_TAB.get(state, st.tab or "session")
        st.last_state = state

    def refresh_activity(self) -> None:
        vm = self.data.activity(self.home_vm)
        self.activity_vm = vm
        seen = set()
        for c in self._containers(vm):
            name = str(c.get("name") or c.get("id") or "")
            seen.add(name)
            hist = self.samples.setdefault(name, {"cpu": [], "mem": []})
            hist["cpu"] = (hist["cpu"] + [c.get("cpu_percent")])[-SAMPLES:]
            hist["mem"] = (hist["mem"] + [c.get("mem_bytes")])[-SAMPLES:]
        for name in list(self.samples):
            if name not in seen:
                del self.samples[name]

    @staticmethod
    def _containers(vm: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for j in (vm or {}).get("jobs") or []:
            out.extend(j.get("containers") or [])
        out.extend((vm or {}).get("orphans") or [])
        return out

    def apply_changes(self, changes: Optional[Sequence[Dict[str, Any]]]) -> bool:
        """Refetch exactly what changed; ``None`` (no change hook) → full refresh."""
        if changes is None:
            self.refresh()
            return True
        if not changes:
            return False
        route = self.route
        home = any(c.get("scope") in ("home", "board") for c in changes)
        job_parts: Set[str] = set()
        for c in changes:
            if c.get("scope") == "job" and route["name"] == "job" and c.get("id") == route.get("id"):
                job_parts |= set(c.get("parts") or [])
            if c.get("scope") == "job":
                home = True
        if home:
            self.refresh_home()
        if job_parts:
            self.refresh_job(route["id"], parts=sorted(job_parts | {"state"}))
        return bool(home or job_parts)

    # ------------------------------------------------------------ navigation
    @property
    def route(self) -> Dict[str, Any]:
        return self.stack[-1]

    def push(self, route: Dict[str, Any]) -> None:
        self.stack.append(route)

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.mode = None

    def open_job(self, job_id: str) -> None:
        try:
            job_id = self.data.resolve(job_id)
        except (KeyError, ValueError) as exc:
            self.flash(f"job not found: {exc}", "danger")
            return
        self.push({"name": "job", "id": job_id})
        self.refresh_job(job_id)

    def open_activity(self) -> None:
        self.push({"name": "activity"})
        self.refresh_activity()

    def open_settings(self) -> None:
        if self.route["name"] != "settings":
            self.push({"name": "settings"})
        self.refresh_settings()

    def refresh_settings(self) -> None:
        try:
            self.settings_vm = self.data.settings()
        except Exception as exc:  # noqa: BLE001 - never break the view
            self.settings_vm = {"sections": [], "path": "", "error": str(exc)}
        try:
            self.toolchains = list(self.data.toolchains()) if hasattr(self.data, "toolchains") else []
        except Exception:  # noqa: BLE001 - image status is informational
            self.toolchains = []
        n = len(self.settings_fields())
        self.cursors["settings"] = max(0, min(self.cursors["settings"], n - 1)) if n else 0

    def settings_fields(self) -> List[Dict[str, Any]]:
        """Every rule field in schema order (the settings cursor indexes this)."""
        return [f for sec in (self.settings_vm or {}).get("sections") or [] for f in sec.get("fields") or []]

    def selected_setting(self) -> Optional[Dict[str, Any]]:
        fields = self.settings_fields()
        if not fields:
            return None
        return fields[max(0, min(self.cursors["settings"], len(fields) - 1))]

    def save_setting(self, field: Dict[str, Any], text: Optional[str]) -> bool:
        """Save typed text for a rule (``None`` resets); on error reopen the prompt with the text."""
        try:
            self.data.set_rule(field["path"], text, field=field)
        except DispatchError as exc:
            msg = str(exc)
            self.flash(msg if msg.startswith(field["label"]) else f"{field['label']}: {msg}", "danger")
            if text is not None and field.get("kind") != "bool":
                self._edit_setting(field, initial=text)
            return False
        self.refresh_settings()
        now = next((f for f in self.settings_fields() if f["path"] == field["path"]), field)
        if text is None:
            self.flash(f"✓ {field['label']} reset to default ({now.get('display')})", "ok")
        else:
            self.flash(f"✓ {field['label']} = {now.get('display')}", "ok")
        return True

    @staticmethod
    def _setting_format(field: Dict[str, Any]) -> str:
        kind = field.get("kind")
        if kind == "select":
            return "one of: " + ", ".join(opt or "default" for opt, _ in field.get("options") or [])
        if kind == "list":
            return "comma-separated"
        if kind == "suites":
            return 'JSON: [{"name": …, "command": …, "junit": …}] · ctrl+e $EDITOR'
        if kind == "rows":
            keys = ", ".join(f'"{c["key"]}": …' for c in field.get("columns") or [])
            return f"JSON: [{{{keys}}}] · ctrl+e $EDITOR"
        if kind in ("int", "float"):
            rng = [f"{field[k]:,}" for k in ("min", "max") if k in field]
            unit = f" {field['unit']}" if field.get("unit") else ""
            opt = " · blank = unset" if field.get("optional") else ""
            return (f"{'whole number' if kind == 'int' else 'number'}{unit}" + (f" {'–'.join(rng)}" if len(rng) == 2 else "") + opt)
        if kind == "bool":
            return "on/off"
        return "text"

    def _edit_setting(self, field: Dict[str, Any], *, initial: Optional[str] = None) -> None:
        if field.get("kind") == "bool":
            return self.toggle_setting(field)
        label = f"{field['label']} ({self._setting_format(field)})"
        text = field.get("edit_text", "") if initial is None else initial
        optional = bool(field.get("optional")) or field.get("kind") in ("list", "suites", "rows", "select")
        self._prompt(label, lambda t: self.save_setting(field, t), optional=optional, initial=text)
        self.mode["setting"] = field["path"]

    def build_toolchains(self) -> None:
        """Start background builds for every saved toolchain image that is not built (or building)."""
        todo = [t for t in self.toolchains if t.get("status") not in ("built", "building")]
        if not self.toolchains:
            return self.flash("save a toolchain row first", "muted")
        if not todo:
            return self.flash("every toolchain image is already built or building", "muted")
        try:
            for t in todo:
                self.data.build_toolchain(t["base_image"])
        except DispatchError as exc:
            self.flash(str(exc), "danger")
        else:
            self.flash(f"building {len(todo)} toolchain image{'s' if len(todo) != 1 else ''}… (r refreshes)", "ok")
        self.refresh_settings()

    # ------------------------------------------------------------ setup / doctor
    def _setup_tab(self, name: str) -> None:
        """Setup has two tabs, Checklist (route ``setup``) and Doctor (route ``doctor``):
        switching tabs replaces the screen instead of stacking another one."""
        if self.route["name"] == name:
            return
        if self.route["name"] in SETUP_TABS:
            self.stack[-1] = {"name": name}
        else:
            self.push({"name": name})

    def open_setup(self) -> None:
        self._setup_tab("setup")
        self.setup_focus = "steps"
        self.recheck_setup()

    def recheck_setup(self, *, recheck: bool = True) -> None:
        """Re-check every step off the UI loop (each check talks to Docker, git, the CLIs…)."""
        self.setup_checking = True

        def work() -> None:
            try:
                vm = self.data.setup(recheck=recheck)
            except Exception as exc:  # noqa: BLE001 - never break the view
                vm = dict(self.setup_vm or {"steps": []}, error=str(exc))
            self.setup_vm = vm
            self.setup_checking = False
            if vm.get("completed_at"):
                self.setup_hint = False
            self._clamp_setup()
            self.on_change()
        self.run_background(work)

    def setup_steps(self) -> List[Dict[str, Any]]:
        return list((self.setup_vm or {}).get("steps") or [])

    def _clamp_setup(self) -> None:
        steps = self.setup_steps()
        self.setup_cursor = max(0, min(self.setup_cursor, len(steps) - 1)) if steps else 0
        acts = (self.selected_step() or {}).get("actions") or []
        self.setup_action_cursor = max(0, min(self.setup_action_cursor, len(acts) - 1)) if acts else 0

    def selected_step(self) -> Optional[Dict[str, Any]]:
        steps = self.setup_steps()
        return steps[min(self.setup_cursor, len(steps) - 1)] if steps else None

    def run_setup_action(self, step: Dict[str, Any], action: Dict[str, Any], inputs: Optional[List[str]] = None) -> None:
        """Enter on a setup action: run / prompts / job / terminal / nav (with ``confirm`` first)."""
        inputs = list(inputs or [])
        kind = action.get("kind")
        if action.get("confirm") and not inputs:
            def confirmed() -> None:
                self._run_setup_action(step, action, inputs)
            self.mode = {"kind": "confirm", "text": str(action["confirm"]), "run": confirmed, "tone": "warn"}
            return
        if kind == "nav":
            return self.open_settings()
        if kind == "terminal":
            # Never spawn an interactive login inside the full-screen dashboard: show the command,
            # the second enter re-checks after the user ran it in another terminal.
            key = (step["id"], action["id"], str(action.get("command") or ""))
            if self.setup_terminal != key:
                self.setup_terminal = key
                return self.flash(f"run `{key[2]}` in another terminal, then press enter again to re-check", "accent")
            self.setup_terminal = None
        self._run_setup_action(step, action, inputs)

    def _run_setup_action(self, step: Dict[str, Any], action: Dict[str, Any], inputs: List[str]) -> None:
        prompts = list(action.get("prompts") or [])
        if action.get("kind") == "prompts" and len(inputs) < len(prompts):
            prompt = prompts[len(inputs)]
            label = f"{action['label']} · {prompt.get('label', '')}"

            def answered(text: str) -> None:
                self._run_setup_action(step, action, inputs + [text])
            self._prompt(label, answered, optional=bool(prompt.get("secret")) or bool(prompt.get("initial")),
                         initial="" if prompt.get("secret") else str(prompt.get("initial") or ""),
                         secret=bool(prompt.get("secret")))
            self.mode["setup"] = step["id"]
            return
        is_job = action.get("kind") == "job"
        if is_job:
            if self.setup_job and self.setup_job.get("running"):
                return self.flash(f"{self.setup_job['title']} is still running", "warn")
            self.setup_job = {"step": step["id"], "title": action["label"], "running": True, "ok": None, "lines": []}
        else:
            self.flash(f"{action['label']}…", "muted")

        def on_line(line: str) -> None:
            if self.setup_job is not None:
                lines = self.setup_job["lines"]
                lines.append(str(line)[:500])
                del lines[:-400]
            self.on_change()

        def work() -> None:
            ok, message = True, ""
            try:
                result = self.data.setup_action(step["id"], action["id"], inputs, on_line=on_line if is_job else None)
                vm = result.get("setup") if isinstance(result.get("setup"), dict) else None
                job = (vm or {}).get("job") if is_job else None
                if is_job:
                    after = self.data.setup()
                    job = after.get("job") or job or {}
                    ok = bool(job.get("ok"))
                    vm = after
                message = str(result.get("message") or "")
                self.setup_vm = vm or self.data.setup()
            except DispatchError as exc:
                ok, message = False, str(exc)
            except Exception as exc:  # noqa: BLE001 - report, never crash the dashboard
                ok, message = False, str(exc)
            if is_job and self.setup_job is not None:
                self.setup_job.update(running=False, ok=ok)
                self.flash(f"{'✓' if ok else '✗'} {action['label']}" + ("" if ok or not message else f": {message}"),
                           "ok" if ok else "danger")
            else:
                self.flash(f"✓ {message or action['label']}" if ok else f"{action['label']}: {message}",
                           "ok" if ok else "danger")
            self._clamp_setup()
            self.on_change()
        self.run_background(work)

    def finish_setup(self) -> None:
        if not (self.setup_vm or {}).get("can_finish"):
            todo = ", ".join((self.setup_vm or {}).get("blocking") or []) or "checking…"
            return self.flash(f"still to do: {todo}", "warn")
        try:
            self.setup_vm = self.data.setup_finish() or self.setup_vm
        except DispatchError as exc:
            return self.flash(str(exc), "danger")
        self.setup_hint = False
        self.flash("✓ setup complete", "ok")

    def open_doctor(self) -> None:
        self._setup_tab("doctor")
        self.run_doctor(deep=False)

    def run_doctor(self, *, deep: bool) -> None:
        if self.doctor_running:
            return self.flash("the doctor is already running", "muted")
        self.doctor_running = "deep" if deep else "quick"

        def work() -> None:
            try:
                self.doctor_checks = self.data.doctor(deep=deep)
            except Exception as exc:  # noqa: BLE001
                self.doctor_checks = [{"name": "doctor", "status": "fail", "detail": str(exc)}]
            self.doctor_running = ""
            failed = sum(1 for c in self.doctor_checks if c.get("status") == "fail")
            self.flash(f"doctor: {failed} failing" if failed else "doctor: all required checks pass",
                       "danger" if failed else "ok")
            self.on_change()
        self.run_background(work)

    # ------------------------------------------------------------ agents / new job
    def open_agents(self) -> None:
        self.agents_vm = self.data.agents()
        self.overlay = "agents"

    @staticmethod
    def default_agent_spec(agents: Optional[Dict[str, Any]]) -> str:
        defaults = dict((agents or {}).get("defaults") or {})
        return ",".join(f"{s}={defaults.get(s) or 'mucli'}" for s in ("plan", "implement", "review"))

    def new_job(self) -> None:
        """Prompt for a title, then which agent works on each stage (mucli / claude / codex)."""
        if getattr(self.data, "creator", None) is None:
            return self.flash("open the dashboard from a session (/jobs) to create jobs", "warn")

        def titled(title: str) -> None:
            title = title.strip()
            if not title:
                return self.flash("a job needs a title", "warn")
            agents = self.data.agents()
            ready = [f"{(agents.get(a) or {}).get('label', a)} {'ready' if (agents.get(a) or {}).get('ready') else 'not set up'}"
                     for a in ("claude", "codex")]
            label = ("Agent per stage: mucli|claude|codex[:model] · " + " · ".join(ready))

            def agented(spec: str) -> None:
                self.create_job(title, spec.strip())
            self._prompt(label, agented, optional=True, initial=self.default_agent_spec(agents))
            self.mode["new_job"] = "agents"
        self._prompt("New job title", titled, optional=False)
        self.mode["new_job"] = "title"

    def create_job(self, title: str, spec: str) -> bool:
        try:
            if spec:
                from mu.jobs.core.vm import parse_agent_spec
                parse_agent_spec(spec)  # validate before queuing
            job_id = self.data.create_job(title, spec)
        except (DispatchError, ValueError) as exc:
            self.flash(f"new job: {exc}", "danger")
            return False
        self.flash(f"✓ queued {title}", "ok")
        self.refresh(job=False)
        if job_id:
            self.open_job(job_id)
        return True

    def toggle_setting(self, field: Dict[str, Any]) -> None:
        self.save_setting(field, "off" if field.get("value") else "on")

    def job_state(self, job_id: str) -> JobViewState:
        if job_id not in self.job_states:
            self.job_states[job_id] = JobViewState(job_id)
        return self.job_states[job_id]

    def flash(self, text: str, tone: str = "warn") -> None:
        first = str(text or "").strip().split("\n")[0][:400]
        self.flash_msg = (first, tone, self.clock())

    def current_flash(self) -> Optional[Tuple[str, str]]:
        if not self.flash_msg:
            return None
        text, tone, at = self.flash_msg
        if self.clock() - at > 6:
            return None
        return text, tone

    # ------------------------------------------------------------ home helpers
    def session_names(self) -> List[str]:
        """Origin sessions present on the board (``""`` = jobs with no recorded session)."""
        vm = self.home_vm or {}
        seen: List[str] = []
        for key in ("needs_you", "running", "jobs"):
            for row in vm.get(key) or []:
                name = str(row.get("origin_session") or "")
                if name not in seen:
                    seen.append(name)
        return sorted(seen, key=lambda n: (n == "", n.lower()))

    def filtered(self, key: str) -> List[Dict[str, Any]]:
        """``home_vm[key]`` restricted to the session filter."""
        rows = list((self.home_vm or {}).get(key) or [])
        if self.session_filter is None:
            return rows
        return [r for r in rows if str(r.get("origin_session") or "") == self.session_filter]

    @staticmethod
    def session_label(name: Optional[str]) -> str:
        if name is None:
            return "all sessions"
        return f"session {name}" if name else "no session"

    def set_session_filter(self, name: Optional[str]) -> None:
        self.session_filter = name
        self.cursors["needs"] = self.cursors["jobs"] = 0
        if self.region == "needs" and not self.filtered("needs_you"):
            self.region = "jobs"
        self._clamp_home()

    def cycle_session_filter(self) -> None:
        """``s`` on the dashboard: all sessions → each session → all sessions."""
        options: List[Optional[str]] = [None] + list(self.session_names())
        try:
            nxt = options[(options.index(self.session_filter) + 1) % len(options)]
        except ValueError:
            nxt = None
        self.set_session_filter(nxt)
        self.flash(f"showing {self.session_label(nxt)}", "muted")

    def home_rows(self, region: str) -> List[Dict[str, Any]]:
        return self.filtered("needs_you" if region == "needs" else "jobs")

    def _clamp_home(self) -> None:
        for region in ("needs", "jobs"):
            n = len(self.home_rows(region))
            self.cursors[region] = max(0, min(self.cursors[region], n - 1)) if n else 0

    def selected_home_row(self) -> Optional[Dict[str, Any]]:
        rows = self.home_rows(self.region)
        if not rows:
            return None
        return rows[max(0, min(self.cursors[self.region], len(rows) - 1))]

    # ------------------------------------------------------------ actions
    @staticmethod
    def visible_actions(actions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [a for a in actions if not a.get("hidden")]

    def actions_for_key(self, actions: Sequence[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
        matches = [a for a in self.visible_actions(actions) if a.get("shortcut") == key]
        if not matches:
            return None
        return next((a for a in matches if a.get("enabled")), matches[0])

    # Build-environment commands are kept out of action bars but keep their keys (E refresh, K keep).
    PANEL_ACTIONS = ("refreshEnvironments", "keepEnvironments")

    def panel_action_for_key(self, actions: Sequence[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
        return next((a for a in actions if a.get("id") in self.PANEL_ACTIONS and a.get("shortcut") == key), None)

    # Environment approval box keys (job view only, while a request is pending).
    ENV_KEYS = {"A": "approveEnvironment", "X": "rejectEnvironment"}

    def environment_action_for_key(self, vm: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
        if not vm.get("environment_approval") or key not in self.ENV_KEYS:
            return None
        return next((a for a in vm.get("actions", []) if a.get("id") == self.ENV_KEYS[key]), None)

    # Live diff: refetch the diff part this often while the Diff tab of an active job is open.
    LIVE_DIFF_INTERVAL = 5.0

    def maybe_refresh_live_diff(self, now: Optional[float] = None) -> bool:
        route = self.route
        if route.get("name") != "job":
            return False
        job_id = route["id"]
        vm = self.jobs.get(job_id)
        if not vm or (self.job_state(job_id).tab or "session") != "diff":
            return False
        active = state_style(vm.get("state", ""))["status"] == "active" or vm.get("state") == "needs_input" \
            or bool((vm.get("diff") or {}).get("live"))
        if not active:
            return False
        now = time.monotonic() if now is None else now
        last = getattr(self, "_live_diff_at", {}).get(job_id, 0.0)
        if last and now - last < self.LIVE_DIFF_INTERVAL:
            return False
        self._live_diff_at = {**getattr(self, "_live_diff_at", {}), job_id: now}
        self.refresh_job(job_id, parts=["diff"])
        return True

    def trigger(self, action: Dict[str, Any], job: Dict[str, Any], *, from_home: bool = False) -> None:
        if action.get("hidden") and action.get("id") not in self.PANEL_ACTIONS:
            return
        if not action.get("enabled"):
            self.flash(f"{action.get('label')}: {action.get('reason') or 'not available now'}", "warn")
            return
        aid = action["id"]
        if from_home and aid in ("reprompt", "savePlanDraft"):
            self.open_job(job["id"])
            jvm = self.jobs.get(job["id"])
            if jvm:
                full = next((a for a in jvm.get("actions", []) if a.get("id") == aid), action)
                self.trigger(full, jvm)
            return

        def go() -> None:
            self._go(action, job)

        if action.get("confirm"):
            self.mode = {"kind": "confirm", "text": str(action["confirm"]), "run": go, "tone": action.get("tone") or "warn"}
        else:
            go()

    def _go(self, action: Dict[str, Any], job: Dict[str, Any]) -> None:
        aid = action["id"]
        args = {k: v for k, v in (action.get("args") or {}).items() if k != "requires_override"}
        job_id = job["id"]
        if aid == "savePlanDraft":
            return self._edit_plan(job_id)
        if action.get("dialog") == "push" or aid == "push":
            return self._open_push(job_id)
        if aid == "reprompt":
            ids = self._reprompt_targets(job_id)
            if not ids:
                return self.flash("select a finding first (review tab: j/k, space marks several)", "warn")
            label = f"fix {len(ids)} finding{'s' if len(ids) != 1 else ''} ({', '.join(ids)}): extra guidance (optional)"

            def submit_fix(text: str) -> None:
                self.job_state(job_id).marked.clear()
                self.run_command(job_id, "reprompt", {"finding_ids": ids, "guidance": text})
            return self._prompt(label, submit_fix, optional=True, multiline=True)
        spec = action.get("input")
        if spec:
            # ``input.arg`` names the command arg (e.g. raiseLimit's ``amount``); ``input.value`` prefills it.
            arg = str(spec.get("arg") or "")

            def submit(text: str) -> None:
                self.run_command(job_id, aid, self._input_args(aid, args, text, arg=arg))
            label = str(spec.get("label") or action.get("label"))
            if spec.get("hint"):
                label = f"{label} — {spec['hint']}"
            return self._prompt(label, submit, optional=bool(spec.get("optional")),
                                multiline=bool(spec.get("multiline")), initial=str(spec.get("value") or ""))
        if aid == "delete":
            if self.run_command(job_id, aid, args):
                if self.route.get("id") == job_id:
                    self.pop()
                self.jobs.pop(job_id, None)
            return
        self.run_command(job_id, aid, args)

    @staticmethod
    def _input_args(aid: str, args: Dict[str, Any], text: str, *, arg: str = "") -> Dict[str, Any]:
        out = dict(args)
        text = text.strip()
        if arg:
            out[arg] = text
        elif aid == "rejectPlan":
            out["feedback"] = text
        elif aid == "denyTool":
            if text:
                out["message"] = text[:2000]
        elif aid in ("resume", "restartStage"):
            out["guidance"] = text
        elif aid == "message":
            out["text"] = text
        elif aid == "discard":
            out["reason"] = text or "discarded"
        elif aid == "rejectEnvironment":
            if text:
                out["reason"] = text[:500]
        else:
            out.setdefault("guidance", text)
        return out

    def _reprompt_targets(self, job_id: str) -> List[str]:
        st = self.job_state(job_id)
        review = (self.jobs.get(job_id) or {}).get("review") or {}
        findings = review.get("findings") or []
        if st.marked:
            return [str(f.get("id")) for f in findings if str(f.get("id")) in st.marked]
        if 0 <= st.finding < len(findings):
            return [str(findings[st.finding].get("id"))]
        return []

    def run_command(self, job_id: str, kind: str, args: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self.data.dispatch(job_id, kind, args or {})
        except DispatchError as exc:
            self.flash(f"{kind}: {exc}", "danger")
            return False
        self.flash(f"✓ {kind} sent", "ok")
        try:
            self.refresh_home()
            if self.route["name"] == "job" and self.route.get("id") == job_id:
                self.refresh_job(job_id)
        except Exception as exc:  # noqa: BLE001
            self.flash(f"✓ {kind} sent (refresh failed: {exc})", "warn")
        return True

    def _prompt(self, label: str, submit: Callable[[str], None], *, optional: bool, multiline: bool = False,
                initial: str = "", secret: bool = False) -> None:
        self.mode = {"kind": "prompt", "label": label, "text": initial, "optional": optional,
                     "multiline": multiline, "submit": submit, "secret": secret}

    def _edit_plan(self, job_id: str) -> None:
        vm = self.jobs.get(job_id) or self.data.job(job_id, parts=["plan"])
        plan = vm.get("plan") or {}
        text = plan.get("draft") or plan.get("markdown") or ""
        version = plan.get("latest_version") or plan.get("version")

        def done(edited: Optional[str]) -> None:
            if edited is None:
                return self.flash("plan edit cancelled", "muted")
            if edited.strip() == text.strip():
                return self.flash("plan unchanged", "muted")
            args: Dict[str, Any] = {"plan": edited}
            if version:
                args["version"] = version
            self.run_command(job_id, "savePlanDraft", args)
        self.editor(text, ".md", done)

    def _open_push(self, job_id: str) -> None:
        job = self.jobs.get(job_id) or self.data.job(job_id, parts=["state"])
        preview = self.data.push_preview(job)
        fields = ["title", "body", "draft"] + (["override"] if preview.get("requires_override") else [])
        self.mode = {"kind": "push", "job_id": job_id, "preview": preview, "title": str(preview.get("title") or ""),
                     "body": str(preview.get("body") or ""), "draft": bool(preview.get("draft", True)),
                     "override": "", "fields": fields, "field": 0}

    def _submit_push(self) -> None:
        m = self.mode or {}
        if "override" in m["fields"] and not m["override"].strip():
            m["field"] = m["fields"].index("override")
            return self.flash("verdict is do_not_merge: an override reason is required", "warn")
        args: Dict[str, Any] = {"pr_title": m["title"].strip()[:200], "pr_body": m["body"][:20000], "draft": bool(m["draft"])}
        if m["override"].strip():
            args["override_reason"] = m["override"].strip()
        self.mode = None
        self.run_command(m["job_id"], "push", args)

    def _no_editor(self, text: str, suffix: str, done: Callable[[Optional[str]], None]) -> None:
        self.flash("no editor available ($EDITOR)", "warn")
        done(None)

    def mark_viewed(self, job_id: str, path: str) -> None:
        vm = self.jobs.get(job_id) or {}
        st = self.job_state(job_id)
        viewed = set((vm.get("diff") or {}).get("viewed") or [])
        now_viewed = not st.local_viewed.get(path, path in viewed)
        st.local_viewed[path] = now_viewed
        action = next((a for a in vm.get("actions", []) if a.get("id") == "markViewed"), None)
        if action is None:
            return self.flash(f"{'✓ viewed' if now_viewed else 'unviewed'} {path} (this session only)", "muted")
        if not action.get("enabled"):
            return self.flash(f"viewed marks: {action.get('reason') or 'not available'}", "warn")
        try:
            self.data.dispatch(job_id, "markViewed", {"path": path, "viewed": now_viewed})
        except DispatchError as exc:
            return self.flash(f"markViewed: {exc}", "danger")
        try:
            self.refresh_job(job_id, parts=["diff"])
        except Exception:  # noqa: BLE001
            pass
        st.local_viewed.pop(path, None)

    def viewed_paths(self, job_id: str) -> List[str]:
        vm = self.jobs.get(job_id) or {}
        st = self.job_state(job_id)
        viewed = set((vm.get("diff") or {}).get("viewed") or [])
        for path, on in st.local_viewed.items():
            if on:
                viewed.add(path)
            else:
                viewed.discard(path)
        return sorted(viewed)

    # ------------------------------------------------------------ hints / palette
    def hints(self) -> List[Tuple[str, str]]:
        route = self.route
        if self.mode:
            return []
        if route["name"] == "home":
            row = self.selected_home_row()
            acts: List[Tuple[str, str]] = []
            if row:
                enabled = [a for a in self.visible_actions(row.get("actions", [])) if a.get("enabled") and a.get("shortcut")]
                enabled.sort(key=lambda a: not a.get("primary"))
                acts = [(a["shortcut"], a["label"]) for a in enabled[:3]]
            out = [("enter", "open")] + acts
            if self.filtered("needs_you"):
                out.append(("tab", "jobs" if self.region == "needs" else "needs you"))
            out.append(("s", self.session_label(self.session_filter)))
            return out + [("n", "new job"), ("v", "activity"), ("r", "refresh"), ("q", "quit")]
        if route["name"] == "activity":
            return [("enter", "open job"), ("l", "log tail"), ("K", "remove container"), ("r", "refresh"), ("esc", "back")]
        if route["name"] == "setup":
            if self.setup_focus == "actions":
                return [("enter", "run"), ("j/k", "action"), ("esc", "steps")]
            out = [("enter", "actions"), ("j/k", "step"), ("r", "check all again")]
            if (self.setup_vm or {}).get("can_finish"):
                out.append(("F", "finish"))
            return out + [("tab", "doctor"), ("esc", "back")]
        if route["name"] == "doctor":
            return [("r", "rerun"), ("D", "deep: sandbox self-test"), ("tab", "checklist"), ("esc", "back")]
        if route["name"] == "settings":
            field = self.selected_setting() or {}
            out = [("enter", "toggle" if field.get("kind") == "bool" else "edit")]
            if field.get("set"):
                out.append(("ctrl+r", "reset to default"))
            if field.get("path") == "sandbox.toolchains" and self.toolchains:
                out.append(("b", "build images"))
            return out + [("j/k", "move"), ("r", "refresh"), ("esc", "back")]
        vm = self.jobs.get(route["id"]) or {}
        st = self.job_state(route["id"])
        enabled = [a for a in self.visible_actions(vm.get("actions", [])) if a.get("enabled")]
        out = [(a["shortcut"], a["label"]) for a in enabled if a.get("primary") and a.get("shortcut")]
        out += [(a["shortcut"], a["label"]) for a in enabled
                if not a.get("primary") and a.get("shortcut") and a["id"] in ("message", "reprompt", "recheck", "retry")]
        tab = st.tab or "session"
        if tab == "session":
            out += [("enter", "expand"), ("f", "fold all" if st.expand_all else "expand all")]
        elif tab == "diff":
            out += [("n/N", "file"), ("v", "viewed")]
        elif tab == "review":
            out += [("enter", "jump to diff"), ("space", "mark")]
        elif tab == "checks":
            out += [("j/k", "run")]
        return out + [("1-7", "tabs"), ("esc", "back")]

    def palette_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        route = self.route
        if route["name"] == "job":
            vm = self.jobs.get(route["id"]) or {}
            key = vm.get("key") or route["id"][:8]
            panel = [a for a in vm.get("actions", []) if a.get("id") in self.PANEL_ACTIONS]
            for a in self.visible_actions(vm.get("actions", [])) + panel:
                items.append({"group": key, "label": a.get("label", a["id"]), "key": a.get("shortcut") or "",
                              "enabled": bool(a.get("enabled")), "reason": a.get("reason") or "",
                              "run": (lambda a=a, vm=vm: self.trigger(a, vm))})
            for i, t in enumerate(TABS):
                items.append({"group": "tab", "label": TAB_LABEL[t], "key": str(i + 1),
                              "run": (lambda t=t: setattr(self.job_state(route["id"]), "tab", t))})
        elif route["name"] == "home":
            row = self.selected_home_row()
            if row:
                key = row.get("key") or row["id"][:8]
                for a in self.visible_actions(row.get("actions", [])):
                    items.append({"group": key, "label": a.get("label", a["id"]), "key": a.get("shortcut") or "",
                                  "enabled": bool(a.get("enabled")), "reason": a.get("reason") or "",
                                  "run": (lambda a=a, row=row: self.trigger(a, row, from_home=True))})
        if route["name"] == "setup":
            for st in self.setup_steps():
                for a in st.get("actions") or []:
                    items.append({"group": f"setup › {st.get('title')}", "label": a.get("label", a["id"]),
                                  "run": (lambda st=st, a=a: self.run_setup_action(st, a))})
        items += [
            {"group": "go", "label": "Home (dashboard)", "run": self.go_home},
            {"group": "go", "label": "Activity: agents, checks, containers", "key": "v", "run": self.open_activity},
            {"group": "go", "label": "Settings: job run rules", "key": "g s", "run": self.open_settings},
            {"group": "go", "label": "Agents: Claude Code / Codex setup and status", "key": "g a", "run": self.open_agents},
            {"group": "go", "label": "Setup: guided checklist and doctor (Docker, image, agents, sandbox)",
             "key": "g u", "run": self.open_setup},
            {"group": "setup", "label": "Doctor: check everything jobs need (Setup tab)", "key": "g d",
             "run": self.open_doctor},
            {"group": "setup", "label": "Doctor (deep): run the sandbox self-test too", "key": "D",
             "run": (lambda: (self.open_doctor() if self.route["name"] != "doctor" else None,
                              self.run_doctor(deep=True)))},
            {"group": "job", "label": "New job (pick the agent per stage)", "key": "n",
             "run": (lambda: (self.go_home(), self.new_job()))},
            {"group": "go", "label": "Refresh", "key": "r", "run": lambda: self.refresh(activity=True)},
        ]
        for name in [None] + list(self.session_names()):
            items.append({"group": "session", "label": f"Show {self.session_label(name)}", "key": "s",
                          "run": (lambda n=name: (self.go_home(), self.set_session_filter(n)))})
        for row in (self.home_vm or {}).get("jobs", []):
            items.append({"group": "job", "label": f"{row.get('key') or row['id'][:8]}  {row.get('title', '')}",
                          "run": (lambda jid=row["id"]: (self.go_home(), self.open_job(jid)))})
        items.append({"group": "app", "label": "Quit", "key": "ctrl+c", "run": self.quit})
        return items

    def go_home(self) -> None:
        self.stack = [{"name": "home"}]
        self.mode = None

    def quit(self) -> None:
        self.exit_requested = True

    # ------------------------------------------------------------ keys
    def handle_key(self, key: str) -> None:
        if key == "c-c":
            if self.mode:
                self.mode = None
                return
            return self.quit()
        if self.mode:
            return self._mode_key(key)
        if self.overlay == "help":
            if key in ("escape", "?", "q", "enter"):
                self.overlay = None
            return
        if self.overlay == "palette":
            return self._palette_key(key)
        if self.overlay == "agents":
            if key in ("escape", "q", "enter", "?"):
                self.overlay = None
            elif key == "r":
                self.open_agents()
            return
        if key in (":", "c-k"):
            self.overlay, self.palette_q, self.palette_cursor = "palette", "", 0
            return
        if key == "?":
            self.overlay = "help"
            return
        chord, self._pending_g = self._pending_g, key == "g"
        if chord and key == "s":
            return self.open_settings()
        if chord and key == "a":
            return self.open_agents()
        if chord and key == "u":
            return self.open_setup()
        if chord and key == "d":
            return self.open_doctor()
        name = self.route["name"]
        if name == "home":
            return self._home_key(key)
        if name == "setup":
            return self._setup_key(key)
        if name == "doctor":
            return self._doctor_key(key)
        if name == "settings":
            return self._settings_key(key)
        if name == "activity":
            return self._activity_key(key)
        return self._job_key(key)

    def _palette_key(self, key: str) -> None:
        ranked = rank_palette(self.palette_items(), self.palette_q)
        if key == "escape":
            self.overlay = None
        elif key in ("up", "c-p"):
            self.palette_cursor = max(0, self.palette_cursor - 1)
        elif key in ("down", "c-n"):
            self.palette_cursor = min(max(0, len(ranked) - 1), self.palette_cursor + 1)
        elif key == "enter":
            if not ranked:
                return
            it = ranked[min(self.palette_cursor, len(ranked) - 1)]
            if it.get("enabled") is False:
                self.flash(f"{it['label']}: {it.get('reason') or 'not available'}", "warn")
                return
            self.overlay = None
            it["run"]()
        elif key == "backspace":
            self.palette_q = self.palette_q[:-1]
            self.palette_cursor = 0
        elif key == "c-u":
            self.palette_q = ""
            self.palette_cursor = 0
        elif len(key) == 1 or key == "space":
            self.palette_q += " " if key == "space" else key
            self.palette_cursor = 0

    def _mode_key(self, key: str) -> None:
        m = self.mode or {}
        kind = m.get("kind")
        if kind == "confirm":
            self.mode = None
            if key in ("y", "Y"):
                m["run"]()
            else:
                self.flash("cancelled", "muted")
            return
        if kind == "prompt":
            if key == "escape":
                self.mode = None
                self.flash("cancelled", "muted")
            elif key == "enter":
                text = m["text"]
                if not text.strip() and not m.get("optional"):
                    self.flash("this needs some text (esc cancels)", "warn")
                    return
                self.mode = None
                m["submit"](text)
            elif key == "backspace":
                m["text"] = m["text"][:-1]
            elif key == "c-u":
                m["text"] = ""
            elif key == "c-e":
                if m.get("secret"):
                    return self.flash("secrets are typed or pasted here, not edited in $EDITOR", "muted")

                def done(edited: Optional[str]) -> None:
                    if edited is not None and self.mode is m:
                        m["text"] = edited.rstrip("\n")
                self.editor(m["text"], ".md", done)
            elif key == "space":
                m["text"] += " "
            elif len(key) == 1:
                m["text"] += key
            return
        if kind == "push":
            fields = m["fields"]
            field = fields[m["field"]]
            if key == "escape":
                self.mode = None
                self.flash("push cancelled", "muted")
            elif key in ("tab", "down"):
                m["field"] = (m["field"] + 1) % len(fields)
            elif key in ("s-tab", "up"):
                m["field"] = (m["field"] - 1) % len(fields)
            elif key == "c-e" or (key == "enter" and field == "body"):
                target = field if field in ("title", "body", "override") else "body"

                def done(edited: Optional[str]) -> None:
                    if edited is not None and self.mode is m:
                        m[target] = edited.strip() if target != "body" else edited.rstrip("\n")
                self.editor(m[target], ".md", done)
            elif key in ("enter", "c-s"):
                self._submit_push()
            elif field == "draft":
                if key in ("space", " ", "x"):
                    m["draft"] = not m["draft"]
            elif field in ("title", "override"):
                if key == "backspace":
                    m[field] = m[field][:-1]
                elif key == "c-u":
                    m[field] = ""
                elif key == "space":
                    m[field] += " "
                elif len(key) == 1:
                    m[field] += key
            return

    def _home_key(self, key: str) -> None:
        if key in ("q", "escape"):
            return self.quit()
        if key == "tab" or key == "s-tab":
            if self.filtered("needs_you"):
                self.region = "jobs" if self.region == "needs" else "needs"
            return
        rows = self.home_rows(self.region)
        cur = self.cursors[self.region]
        if key in ("down", "j"):
            if cur + 1 < len(rows):
                self.cursors[self.region] = cur + 1
            elif self.region == "needs":
                self.region = "jobs"
                self.cursors["jobs"] = 0
            return
        if key in ("up", "k"):
            if cur > 0:
                self.cursors[self.region] = cur - 1
            elif self.region == "jobs" and self.filtered("needs_you"):
                self.region = "needs"
            return
        if key in ("g", "home"):
            self.cursors[self.region] = 0
            return
        if key in ("G", "end"):
            self.cursors[self.region] = max(0, len(rows) - 1)
            return
        if key == "pagedown":
            self.cursors[self.region] = min(max(0, len(rows) - 1), cur + 10)
            return
        if key == "pageup":
            self.cursors[self.region] = max(0, cur - 10)
            return
        if key == "enter":
            row = self.selected_home_row()
            if row:
                self.open_job(row["id"])
            return
        if key == "v":
            return self.open_activity()
        if key == "n":
            return self.new_job()
        if key == "s":
            return self.cycle_session_filter()
        if key == "r":
            self.refresh()
            return self.flash("refreshed", "muted")
        row = self.selected_home_row()
        if row:
            action = self.actions_for_key(row.get("actions", []), key)
            if action:
                self.trigger(action, row, from_home=True)

    def _setup_key(self, key: str) -> None:
        steps = self.setup_steps()
        step = self.selected_step()
        acts = list((step or {}).get("actions") or [])
        if self.setup_focus == "actions":
            if key in ("escape", "q", "left", "h"):
                self.setup_focus = "steps"
            elif key in ("down", "j"):
                self.setup_action_cursor = min(max(0, len(acts) - 1), self.setup_action_cursor + 1)
            elif key in ("up", "k"):
                self.setup_action_cursor = max(0, self.setup_action_cursor - 1)
            elif key == "enter" and acts and step:
                self.run_setup_action(step, acts[min(self.setup_action_cursor, len(acts) - 1)])
            return
        if key in ("escape", "q"):
            return self.pop()
        if key in ("tab", "s-tab"):
            return self.open_doctor()
        if key in ("down", "j"):
            self.setup_cursor = min(max(0, len(steps) - 1), self.setup_cursor + 1)
            self.setup_action_cursor = 0
            self.setup_terminal = None
        elif key in ("up", "k"):
            self.setup_cursor = max(0, self.setup_cursor - 1)
            self.setup_action_cursor = 0
            self.setup_terminal = None
        elif key in ("enter", "right", "l"):
            if acts:
                self.setup_focus = "actions"
            else:
                self.flash("nothing to do for this step", "muted")
        elif key == "r":
            self.recheck_setup()
            self.flash("checking every step again…", "muted")
        elif key == "F":
            self.finish_setup()

    def _doctor_key(self, key: str) -> None:
        checks = self.doctor_checks or []
        if key in ("escape", "q"):
            return self.pop()
        if key in ("tab", "s-tab"):
            return self.open_setup()
        if key in ("down", "j"):
            self.doctor_cursor = min(max(0, len(checks) - 1), self.doctor_cursor + 1)
        elif key in ("up", "k"):
            self.doctor_cursor = max(0, self.doctor_cursor - 1)
        elif key == "r":
            self.run_doctor(deep=False)
        elif key == "D":
            self.run_doctor(deep=True)

    def _activity_key(self, key: str) -> None:
        rows = activity_rows(self.activity_vm)
        cur = self.cursors["activity"]
        if key in ("escape", "q"):
            return self.pop()
        if key in ("down", "j"):
            self.cursors["activity"] = min(max(0, len(rows) - 1), cur + 1)
        elif key in ("up", "k"):
            self.cursors["activity"] = max(0, cur - 1)
        elif key == "r":
            self.refresh_activity()
            self.flash("refreshed", "muted")
        elif key == "enter" and rows:
            row = rows[min(cur, len(rows) - 1)]
            if row.get("job_id"):
                self.open_job(row["job_id"])
            else:
                self.flash("not attributed to an active job", "warn")
        elif key == "K" and rows:
            row = rows[min(cur, len(rows) - 1)]
            if row["kind"] != "container":
                return self.flash("select a container to remove", "warn")
            name = str(row["c"].get("name") or "")

            def remove() -> None:
                try:
                    self.data.remove_container(name)
                except DispatchError as exc:
                    return self.flash(f"remove {name}: {exc}", "danger")
                self.flash(f"✓ removed {name}", "ok")
                self.refresh_activity()
            self.mode = {"kind": "confirm", "text": f"Remove container {name}? (docker rm -f)", "run": remove,
                         "tone": "danger"}
        elif key == "l" and rows:
            row = rows[min(cur, len(rows) - 1)]
            if row.get("job_id"):
                self.activity_logs[row["job_id"]] = self.data.activity_logs(row["job_id"])

    def _settings_key(self, key: str) -> None:
        fields = self.settings_fields()
        cur = self.cursors["settings"]
        last = max(0, len(fields) - 1)
        if key in ("escape", "q"):
            return self.pop()
        if key in ("down", "j"):
            self.cursors["settings"] = min(last, cur + 1)
        elif key in ("up", "k"):
            self.cursors["settings"] = max(0, cur - 1)
        elif key in ("g", "home"):
            self.cursors["settings"] = 0
        elif key in ("G", "end"):
            self.cursors["settings"] = last
        elif key == "pagedown":
            self.cursors["settings"] = min(last, cur + 10)
        elif key == "pageup":
            self.cursors["settings"] = max(0, cur - 10)
        elif key == "r":
            self.refresh_settings()
            self.flash("refreshed", "muted")
        elif key in ("enter", "e", "space", " ") and fields:
            field = self.selected_setting()
            if key in ("space", " ") and field.get("kind") != "bool":
                return
            self._edit_setting(field)
        elif key == "b" and fields and self.selected_setting().get("path") == "sandbox.toolchains":
            self.build_toolchains()
        elif key == "c-r" and fields:
            field = self.selected_setting()
            if not field.get("set"):
                return self.flash(f"{field['label']} already uses the default", "muted")
            self.save_setting(field, None)

    def _job_key(self, key: str) -> None:
        job_id = self.route["id"]
        vm = self.jobs.get(job_id)
        st = self.job_state(job_id)
        if key in ("escape", "q"):
            return self.pop()
        if key in ("tab", "s-tab"):
            i = TABS.index(st.tab) if st.tab in TABS else 0
            st.tab = TABS[(i + (1 if key == "tab" else -1)) % len(TABS)]
            return
        if len(key) == 1 and key in "1234567":
            st.tab = TABS[int(key) - 1]
            return
        if vm is None:
            return
        tab = st.tab or "session"
        if self._tab_key(tab, key, vm, st):
            return
        if key in NAV_KEYS:
            return
        env = self.environment_action_for_key(vm, key)
        if env:
            return self.trigger(env, vm)
        action = self.actions_for_key(vm.get("actions", []), key) or self.panel_action_for_key(vm.get("actions", []), key)
        if action:
            self.trigger(action, vm)

    def _scroll(self, st: JobViewState, tab: str, key: str, page: int = 10) -> bool:
        top = st.tops.get(tab, 0)
        delta = {"down": 1, "j": 1, "up": -1, "k": -1, "pagedown": page, "pageup": -page}.get(key)
        if delta is not None:
            st.tops[tab] = max(0, top + delta)
            return True
        if key in ("g", "home"):
            st.tops[tab] = 0
            return True
        if key in ("G", "end"):
            st.tops[tab] = 10 ** 9
            return True
        return False

    def _tab_key(self, tab: str, key: str, vm: Dict[str, Any], st: JobViewState) -> bool:
        if tab == "session":
            items = content.session_items(vm.get("session"))
            n = len(items)
            cur = st.item if st.item >= 0 else n - 1
            if key in ("down", "j"):
                st.item = -1 if cur + 1 >= n - 1 else cur + 1
                return True
            if key in ("up", "k"):
                st.item = max(0, cur - 1)
                return True
            if key in ("g", "home"):
                st.item = 0
                return True
            if key in ("G", "end"):
                st.item = -1
                return True
            if key == "pagedown":
                st.item = -1 if cur + 10 >= n - 1 else cur + 10
                return True
            if key == "pageup":
                st.item = max(0, cur - 10)
                return True
            if key == "enter":
                if 0 <= cur < n:
                    if cur in st.expanded:
                        st.expanded.discard(cur)
                    else:
                        st.expanded.add(cur)
                return True
            if key == "f":
                st.expand_all = not st.expand_all
                return True
            return False
        if tab == "diff":
            files = content.parse_patch(content.job_patch(vm)[0])
            if key in ("n", "J", "]"):
                st.diff_file = min(max(0, len(files) - 1), st.diff_file + 1)
                st.diff_top = 0
                return True
            if key in ("N", "K", "["):
                st.diff_file = max(0, st.diff_file - 1)
                st.diff_top = 0
                return True
            if key == "v" and files and content.job_patch(vm)[1]:
                self.flash("viewed marks apply to the checkpointed diff (this one is live)", "muted")
                return True
            if key == "v" and files:
                self.mark_viewed(vm["id"], files[min(st.diff_file, len(files) - 1)]["path"])
                return True
            delta = {"down": 1, "j": 1, "up": -1, "k": -1, "pagedown": 15, "pageup": -15}.get(key)
            if delta is not None:
                st.diff_top = max(0, st.diff_top + delta)
                return True
            if key in ("g", "home"):
                st.diff_top = 0
                return True
            if key in ("G", "end"):
                st.diff_top = 10 ** 9
                return True
            return False
        if tab == "review":
            review = vm.get("review") or {}
            order = content.review_order(review)
            if not order:
                return self._scroll(st, tab, key)
            at = order.index(st.finding) if st.finding in order else 0
            if key in ("down", "j"):
                st.finding = order[min(len(order) - 1, at + 1)]
                return True
            if key in ("up", "k"):
                st.finding = order[max(0, at - 1)]
                return True
            if key in ("space", " "):
                fid = str((review.get("findings") or [])[st.finding].get("id"))
                if fid in st.marked:
                    st.marked.discard(fid)
                else:
                    st.marked.add(fid)
                return True
            if key == "enter":
                self.jump_to_finding(vm, st)
                return True
            return False
        if tab == "checks":
            runs = (vm.get("checks") or {}).get("runs") or []
            cur = st.run if st.run >= 0 else len(runs) - 1
            if key in ("down", "j") and runs:
                st.run = max(0, cur - 1)  # newest first on screen
                return True
            if key in ("up", "k") and runs:
                st.run = min(len(runs) - 1, cur + 1)
                return True
            if key in ("pagedown", "pageup", "g", "G", "home", "end"):
                return self._scroll(st, tab, key)
            return False
        return self._scroll(st, tab, key)

    def jump_to_finding(self, vm: Dict[str, Any], st: JobViewState) -> None:
        findings = (vm.get("review") or {}).get("findings") or []
        if not (0 <= st.finding < len(findings)):
            return self.flash("no finding selected", "warn")
        f = findings[st.finding]
        files = content.parse_patch(content.job_patch(vm)[0])
        idx = next((i for i, df in enumerate(files) if df["path"] == f.get("file")), None)
        if idx is None:
            return self.flash(f"{f.get('file') or 'finding'} is not part of the diff", "warn")
        st.diff_file = idx
        _, rows = content.diff_hunk_lines(files[idx], 200)
        line = f.get("line_start")
        target = 0
        if isinstance(line, int) and rows:
            nearest = min(rows, key=lambda n: abs(n - line))
            target = max(0, rows[nearest] - 3)
        st.diff_top = target
        st.tab = "diff"


def activity_rows(vm: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Job → agent/checks → containers as one flat tree (orphans last)."""
    rows: List[Dict[str, Any]] = []
    for j in (vm or {}).get("jobs") or []:
        rows.append({"kind": "job", "job_id": j.get("id"), "job": j})
        rows.append({"kind": j.get("kind") or "agent", "job_id": j.get("id"), "job": j})
        for c in j.get("containers") or []:
            rows.append({"kind": "container", "job_id": j.get("id"), "c": c})
    orphans = (vm or {}).get("orphans") or []
    if orphans:
        rows.append({"kind": "header", "job_id": None, "text": "(not attributed to an active job)"})
        for c in orphans:
            rows.append({"kind": "container", "job_id": None, "c": c})
    return rows


def last_activity(events: Sequence[Dict[str, Any]]) -> str:
    """Latest agent line (status, tool call or message) from durable events."""
    for ev in reversed(list(events)):
        if ev.get("event_type") != "sandbox_event":
            continue
        payload = ev.get("payload") or {}
        inner = payload.get("payload") or {}
        kind = payload.get("event")
        if kind == "runtime_status" and inner.get("text"):
            return str(inner["text"])[:200]
        if kind == "tool_call_ui" and inner.get("tool_name"):
            return f"● {inner['tool_name']}"
        if kind == "agent_message" and inner.get("text"):
            return str(inner["text"]).strip().split("\n")[0][:200]
    return ""
