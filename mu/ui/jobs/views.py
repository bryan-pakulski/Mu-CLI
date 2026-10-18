"""Screens for the terminal job dashboard: AppShell, dashboard, job, activity.

``render(app, width, height)`` → list of lines (see ``primitives``). Pure:
reads only the ``JobsApp`` state, never the store.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import content
from .data import TOOLCHAIN_TONE, toolchain_line
from .app import GLOBAL_KEYS, SETUP_TABS, TAB_LABEL, TABS, JobsApp, activity_rows, rank_palette
from .primitives import (Column, Line, add_style, age, clean, cwidth, duration, fit, fit_line, hjoin, key_hints,
                         line_width, mini_stepper, money, nbytes, pad, panel, progress_text, rule, sparkline,
                         status_icon, stepper, table, tabs, wrap_text)
from .theme import STATUS, state_style

LOGO = "mucli jobs"


# ---------------------------------------------------------------- shell

def _animate(app: JobsApp) -> bool:
    """Is anything on screen in an active state (spinner needs the tick)?"""
    route = app.route
    if route["name"] == "job":
        vm = app.jobs.get(route["id"]) or {}
        return state_style(vm.get("state", ""))["status"] == "active"
    if route["name"] == "setup":
        return bool(app.setup_checking or (app.setup_job or {}).get("running"))
    if route["name"] == "doctor":
        return bool(app.doctor_running)
    return bool((app.home_vm or {}).get("running"))


def chips(status: Dict[str, Any]) -> Line:
    out: Line = []
    agents = int(status.get("agents") or 0)
    checks = int(status.get("checks") or 0)
    needs = int(status.get("needs_you") or 0)
    if agents:
        out.append(("running", f"● {agents} agent{'s' if agents != 1 else ''}  "))
    if checks:
        out.append(("running", f"● {checks} check{'s' if checks != 1 else ''}  "))
    if needs:
        out.append(("warn bold", f"◆ {needs} need{'' if needs != 1 else 's'} you  "))
    engine = str(status.get("engine") or "")
    tone = {"up": "ok", "down": "danger"}.get(engine, "muted")
    if engine:
        out.append((tone, "⬢ docker" + ("" if engine in ("up", "down") else f" {engine}") + " "))
    return out


def crumb(app: JobsApp, route: Dict[str, Any]) -> str:
    if route["name"] == "job":
        vm = app.jobs.get(route["id"]) or {}
        return vm.get("key") or route["id"][:8]
    return route["name"]


def top_bar(app: JobsApp, width: int) -> Line:
    right = chips(app.status)
    rw = line_width(right)
    crumbs = " › ".join(crumb(app, r) for r in app.stack[1:])
    left: Line = [("accent bold", f" {LOGO}")]
    if crumbs:
        left.append(("muted", fit(f"  › {crumbs}", max(0, width - cwidth(LOGO) - rw - 3))))
    else:
        # Jobs mode is one plane across sessions: say which slice is on screen.
        scope = app.session_label(app.session_filter)
        left.append(("muted" if app.session_filter is None else "accent",
                     fit(f"  · {scope}", max(0, width - cwidth(LOGO) - rw - 3))))
    lw = line_width(left)
    if lw + rw >= width:
        return fit_line(left + [("", " ")] + right, width)
    return left + [("", " " * (width - lw - rw))] + right


def render(app: JobsApp, width: int, height: int) -> List[Line]:
    width = max(40, width)
    height = max(10, height)
    tick = app.tick if _animate(app) else None
    body_h = height - 3
    lines: List[Line] = [top_bar(app, width)]
    if app.overlay == "palette":
        body = palette_overlay(app, width, body_h)
    elif app.overlay == "help":
        body = help_overlay(app, width, body_h)
    elif app.overlay == "agents":
        body = agents_overlay(app, width, body_h)
    elif app.route["name"] == "home":
        body = dashboard(app, width, body_h, tick)
    elif app.route["name"] == "activity":
        body = activity_screen(app, width, body_h, tick)
    elif app.route["name"] == "settings":
        body = settings_screen(app, width, body_h)
    elif app.route["name"] in SETUP_TABS:
        body = [setup_tabs(app, width)] + (setup_screen if app.route["name"] == "setup" else doctor_screen)(
            app, width, body_h - 1, tick)
    else:
        body = job_screen(app, width, body_h, tick)
    body = (body + [[] for _ in range(body_h)])[:body_h]
    lines.extend(fit_line(l, width) for l in body)
    fl = app.current_flash()
    lines.append(fit_line([(fl[1], " " + fl[0])] if fl else [("", "")], width))
    lines.append(key_hints(app.hints(), width) if not app.mode else mode_hints(app, width))
    return lines


def mode_hints(app: JobsApp, width: int) -> Line:
    kind = (app.mode or {}).get("kind")
    hints = {"confirm": [("y", "confirm"), ("any key", "cancel")],
             "prompt": [("enter", "send"), ("ctrl+e", "$EDITOR"), ("ctrl+u", "clear"), ("esc", "cancel")],
             "push": [("enter", "push"), ("tab/↑↓", "field"), ("space", "toggle draft"), ("ctrl+e", "$EDITOR"),
                      ("esc", "cancel")]}.get(kind, [])
    return key_hints(hints, width, pinned=())


# ---------------------------------------------------------------- dashboard

def _step_label(row: Dict[str, Any]) -> str:
    for s in row.get("steps") or []:
        if s.get("status") in ("active", "waiting", "failed"):
            return str(s.get("label") or "").lower()
    return ""


def _reason(row: Dict[str, Any]) -> str:
    state = row.get("state", "")
    if state == "needs_input":
        if (row.get("limit") or {}).get("summary"):
            return clean(row["limit"]["summary"])  # "Limit reached: cost $100.03 / $100.00"
        detail = clean(row.get("attention_detail") or "").split("\n")[0]
        return detail or "needs input"
    if state == "failed":
        return f"failed in {row.get('failed_from') or '?'}"
    if state == "review_done" and row.get("verdict"):
        return f"review done · {str(row['verdict']).replace('_', ' ')}"
    return state_style(state)["label"]


def _row_keys(row: Dict[str, Any]) -> str:
    acts = [a for a in JobsApp.visible_actions(row.get("actions") or []) if a.get("enabled") and a.get("shortcut")]
    acts.sort(key=lambda a: not a.get("primary"))
    seen: List[str] = []
    for a in acts:
        if a["shortcut"] not in seen:
            seen.append(a["shortcut"])
    return "  ".join(seen[:5])


def _key(row: Dict[str, Any]) -> str:
    return str(row.get("key") or row.get("id", "")[:8])


def _session(row: Dict[str, Any]) -> str:
    return clean(row.get("origin_session") or "")


def needs_row(row: Dict[str, Any], width: int, selected: bool) -> Line:
    keys = _row_keys(row)
    st = state_style(row.get("state", ""))
    left_w = max(4, width - cwidth(keys) - 5)
    sess = _session(row)
    text = f"{pad(_key(row), 12)} {_reason(row)}" + (f" · {fit(sess, 16)}" if sess else "")
    line: Line = [("accent", "›" if selected else " "), status_icon(st["status"]), ("", " "),
                  ("bold", pad(text, left_w)), ("", " "), ("accent", keys)]
    line = fit_line(line, width)
    return add_style(line, "inverse") if selected else line


def running_row(app: JobsApp, row: Dict[str, Any], width: int, tick: Optional[int]) -> Line:
    vm = app.home_vm or {}
    now = app.clock()
    step = next((s for s in row.get("steps") or [] if s.get("status") == "active"), {})
    secs = float(step.get("elapsed") or 0) + max(0.0, now - float(vm.get("fetched_at") or now))
    head = f"{pad(_key(row), 12)} {pad(_step_label(row) or row.get('state', ''), 10)} {pad(duration(secs), 6, 'right')}"
    line: Line = [("", " "), status_icon("active", tick), ("", " " + head)]
    tail_w = max(0, width - cwidth(head) - 4)
    prog = app.progress.get(row["id"])
    if prog:
        total = prog["passed"] + prog["failed"]
        bar_w = min(12, max(4, tail_w - 14))
        line += [("", " "), ("danger" if prog["failed"] else "ok", progress_text(prog["passed"] / total if total else 0, bar_w)),
                 ("ok", f" {prog['passed']} ok"), ("danger" if prog["failed"] else "muted", f" {prog['failed']} ✗")]
    else:
        last = clean(row.get("last") or "").split("\n")[0]
        extra = f"{money(row.get('cost_usd')) + '  ' if row.get('cost_usd') else ''}{'› ' + last if last else ''}"
        line.append(("muted", " " + fit(extra, tail_w)))
    return fit_line(line, width)


def _title(row: Dict[str, Any]) -> str:
    """Title without a leading ``KEY:`` (the key has its own column)."""
    title = clean(row.get("title") or "")
    key = str(row.get("key") or "")
    if key and title.startswith(key):
        title = title[len(key):].lstrip(" :·-") or title
    return title


def job_columns(app: JobsApp, tick: Optional[int], width: int = 120) -> List[Column]:
    now = app.clock()
    wide = width >= 100

    def pipe(r):
        active = state_style(r.get("state", ""))["status"] == "active"
        out = mini_stepper(r.get("steps") or [], tick if active else None)
        return out + [("muted", "  " + pad(_step_label(r)[:9], 9))] if wide else out

    def state(r):
        st = state_style(r.get("state", ""))
        return [(st["token"], fit(f"{st['icon']} {st['label']}", 17 if wide else 15))]

    return [
        Column("key", "KEY", lambda r: ("bold", _key(r)), width=12 if wide else 10),
        Column("pipe", "PIPELINE", pipe, width=22 if wide else 11),
        Column("state", "STATE", state, width=17 if wide else 15),
        Column("cost", "COST", lambda r: ("" if r.get("cost_usd") else "muted", money(r.get("cost_usd"))), width=7, align="right"),
        Column("session", "SESSION", lambda r: ("muted", fit(_session(r) or "-", 14 if wide else 10)),
               width=14 if wide else 10),
        Column("age", "AGE", lambda r: ("muted", age(r.get("updated_at"), now)), width=4, align="right", hide_below=90),
        Column("summary", "SUMMARY", _title, flex=1, min=10),
    ]


def dashboard(app: JobsApp, width: int, height: int, tick: Optional[int]) -> List[Line]:
    vm = app.home_vm
    if vm is None:
        return [[("muted", " loading jobs…")]] + ([[("danger", " " + app.error)]] if app.error else [])
    needs = app.filtered("needs_you")
    running = app.filtered("running")
    jobs = app.filtered("jobs")
    narrow = width < 100
    top_rows = max(len(needs), len(running), 1)
    top_h = min(6 if narrow else 9, top_rows + 2)
    stacked = top_h * 2 if narrow else top_h
    mp = mode_panel(app, width)  # the new-job prompts
    hint: List[Line] = ([[("warn", " ◆ "), ("", fit("Jobs setup isn't finished: press g u (or : Setup) to check Docker, "
                                                     "the runtime image, agents and the sandbox", width - 4))]]
                        if app.setup_hint else [])
    jobs_h = max(4, height - stacked - len(mp) - len(hint))

    def needs_panel(w: int) -> List[Line]:
        inner = w - 2
        cur = app.cursors["needs"]
        focused = app.region == "needs"
        start = max(0, cur - (top_h - 3))
        body = [needs_row(r, inner, focused and i + start == cur) for i, r in enumerate(needs[start:start + top_h - 2])]
        if not needs:
            body = [[("muted", " nothing waiting on you")]]
        return panel(body, w, top_h, title="Needs you", badge=str(len(needs)) if needs else "", badge_tone="warn",
                     focused=focused)

    def running_panel(w: int) -> List[Line]:
        body = [running_row(app, r, w - 2, tick) for r in running[:top_h - 2]]
        if not running:
            body = [[("muted", " idle")]]
        return panel(body, w, top_h, title="Running now", badge=str(len(running)) if running else "",
                     badge_tone="running")

    out: List[Line] = []
    if narrow:
        out += needs_panel(width) + running_panel(width)
    else:
        left = int(width * 0.45)
        out += hjoin([(needs_panel(left), left), (running_panel(width - left), width - left)])
    focused = app.region == "jobs"
    body = table(job_columns(app, tick, width - 2), jobs, width - 2, jobs_h - 2, cursor=app.cursors["jobs"] if focused else -1,
                 empty=("no jobs yet: /job create <title> or /job jira pull KEY" if app.session_filter is None
                        else f"no jobs in {app.session_label(app.session_filter)} (s: next session)"))
    title = "Jobs" if app.session_filter is None else f"Jobs · {app.session_label(app.session_filter)}"
    out += panel(body, width, jobs_h, title=title, badge=str(len(jobs)), focused=focused)
    return out + hint + mp


# ---------------------------------------------------------------- job view

def _status_line(app: JobsApp, vm: Dict[str, Any], tick: Optional[int]) -> Optional[Line]:
    state = vm.get("state", "")
    detail = clean(vm.get("attention_detail") or "").split("\n")[0]
    if state == "failed":
        return [("danger", f" ✗ failed in {vm.get('failed_from') or '?'}: {detail}"), ("muted", " · y retry · 6 errors")]
    if state == "needs_input" and vm.get("limit"):
        return [("warn", f" ◆ {detail}"), ("muted", " · L approve more · cancel from the action bar")]
    if state == "needs_input":
        return [("warn", f" ◆ {detail or 'the agent needs input'}")]
    if state == "plan_review":
        plan = vm.get("plan") or {}
        return [("warn", f" ◆ plan v{plan.get('version', '?')} awaiting approval"),
                ("muted", " · a approve · x request re-plan · e edit in $EDITOR")]
    if state == "review_done":
        review = vm.get("review") or {}
        verdict = str(review.get("verdict") or "").replace("_", " ")
        return [("warn", f" ◆ review done{': ' + verdict if verdict else ''}"), ("muted", " · p push · F fix findings · c re-run checks")]
    if state_style(state)["status"] == "active":
        return [("running", " "), status_icon("active", tick), ("running", f" {state_style(state)['label']}")]
    return None


def _approval_box(approval: Dict[str, Any], width: int) -> List[Line]:
    args = approval.get("tool_args") or {}
    desc = content._args_summary(args) if isinstance(args, dict) else clean(str(args))
    body = [
        [("warn bold", f"◆ Approval needed: {approval.get('tool_name', 'tool')}")],
        [("", fit(desc, width - 4)), ("muted", "  ·  a approve · d deny (optional reason)")],
    ]
    if approval.get("can_approve") is False:
        body[1] = [("danger", "preview failed: this request can only be denied"), ("muted", "  ·  d deny")]
    return panel(body, width, 4, tone="warn", pad_x=1)


def _environment_approval_box(approval: Dict[str, Any], width: int) -> List[Line]:
    rows = content.environment_approval_lines(approval)
    title = ("◆ Build environment needs approval" if approval.get("blocking", True) else
             "◆ Repository egress awaits approval (stages run without it)" if approval.get("kind") == "egress" else
             "◆ Repository environment skipped (approve for future jobs)")
    body: List[Line] = [[("warn bold", title), ("muted", "  ·  A approve · X reject (optional reason)")]]
    body += [[("", fit(r, width - 4))] for r in rows]
    return panel(body, width, len(body) + 2, tone="warn", pad_x=1)


def _job_tabs(vm: Dict[str, Any], files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    review = vm.get("review") or {}
    checks = vm.get("checks") or {}
    errors = content.error_entries(vm)
    for t in TABS:
        spec: Dict[str, Any] = {"id": t, "label": TAB_LABEL[t]}
        if t == "diff" and files:
            spec["count"] = f"+{sum(f['added'] for f in files)} −{sum(f['removed'] for f in files)}"
        elif t == "review" and review:
            findings = review.get("findings") or []
            spec["count"] = len(findings)
            if any(f.get("severity") in ("blocker", "major") and f.get("disposition", "open") == "open" for f in findings):
                spec["tone"] = "danger"
        elif t == "checks":
            if vm.get("state") == "checking":
                spec["icon"] = "active"
            elif checks.get("runs"):
                spec["icon"] = content.run_status(checks["runs"][-1])
        elif t == "diagnostics" and (errors or vm.get("state") == "failed"):
            spec["count"] = len(errors) or 1
            spec["tone"] = "danger"
        elif t == "session" and (vm.get("session") or {}).get("live"):
            spec["icon"] = "active"
        out.append(spec)
    return out


def sidebar(app: JobsApp, vm: Dict[str, Any], width: int, height: int, tick: Optional[int]) -> List[Line]:
    checks = vm.get("checks") or {}
    runs = checks.get("runs") or []
    approval = (vm.get("session") or {}).get("approval")
    costs = (vm.get("costs") or {}).get("by_stage") or {}
    ap_h = 3 if not approval else 4
    cost_h = max(3, min(6, len(costs) + 2))
    checks_h = max(4, height - ap_h - cost_h)
    inner = width - 4
    body: List[Line] = []
    if vm.get("state") == "checking":
        prog = content.live_progress(checks.get("live") or [])
        body.append([status_icon("active", tick), ("", " running")])
        if prog:
            total = prog["passed"] + prog["failed"]
            body.append([("", "  "), ("danger" if prog["failed"] else "ok", progress_text(prog["passed"] / total if total else 0, min(10, inner - 14))),
                         ("ok", f" {prog['passed']} ok"), ("danger" if prog["failed"] else "muted", f" · {prog['failed']} ✗")])
    for r in reversed(runs[-max(1, (checks_h - 2) // 2):]):
        st = content.run_status(r)
        n = content.run_counts(r)
        dur = duration((r.get("duration_ms") or 0) / 1000.0) if r.get("duration_ms") else ""
        body.append([content._status_seg(st), ("", " " + pad(f"run {r.get('run')} {r.get('trigger') or ''}".strip(), inner - 9)),
                     ("muted", pad(dur, 7, "right"))])
        body.append([("ok", f"  {n['passed']} ok"), ("muted", " · "), ("danger" if n["failed"] else "muted", f"{n['failed']} ✗")])
    if not body:
        body = [[("muted", "no runs yet")]]
    out = panel(body, width, checks_h, title="Checks", badge=("●" if vm.get("state") == "checking" else str(len(runs)) if runs else ""),
                badge_tone="running" if vm.get("state") == "checking" else "muted", pad_x=1)
    if approval:
        ab = [[("warn", f"◆ {approval.get('tool_name', 'tool')}")],
              [("muted", fit(content._args_summary(approval.get("tool_args") or {}) if isinstance(approval.get("tool_args"), dict) else "", inner))]]
        out += panel(ab, width, ap_h, title="Approvals", badge="1", badge_tone="warn", tone="warn", pad_x=1)
    else:
        out += panel([[("muted", "none")]], width, ap_h, title="Approvals", pad_x=1)
    cb = [[("", pad(s, max(4, inner - 8))), ("muted", pad(money(v), 8, "right"))] for s, v in costs.items()]
    out += panel(cb or [[("muted", "no cost yet")]], width, cost_h, title="Cost",
                 badge=money((vm.get("costs") or {}).get("total_usd", vm.get("cost_usd"))), pad_x=1)
    return out


def _scrolled(lines: List[Line], height: int, top: int) -> Tuple[List[Line], int]:
    max_top = max(0, len(lines) - height)
    top = max(0, min(top, max_top))
    return lines[top:top + height], top


def session_view(app: JobsApp, vm: Dict[str, Any], width: int, height: int) -> List[Line]:
    st = app.job_state(vm["id"])
    items = content.session_items(vm.get("session"))
    if not items:
        msg = "provisioning…" if vm.get("state") == "provisioning" else "no session yet"
        return [[("muted", msg)]]
    cur = st.item if st.item >= 0 else len(items) - 1
    lines: List[Line] = []
    start_of: Dict[int, int] = {}
    for i, it in enumerate(items):
        start_of[i] = len(lines)
        lines.extend(content.session_item_lines(it, width, expanded=st.expand_all or i in st.expanded, selected=i == cur))
    sel_start = start_of.get(cur, 0)
    sel_end = start_of.get(cur + 1, len(lines))
    top = st.tops.get("session", 0)
    if st.item < 0:
        top = max(0, len(lines) - height)
    elif sel_start < top:
        top = sel_start
    elif sel_end > top + height:
        top = max(sel_start, sel_end - height) if sel_end - sel_start <= height else sel_start
    view, top = _scrolled(lines, height, top)
    st.tops["session"] = top
    return view


def diff_view(app: JobsApp, vm: Dict[str, Any], width: int, height: int) -> List[Line]:
    st = app.job_state(vm["id"])
    patch, live = content.job_patch(vm)
    files = content.parse_patch(patch)
    live_label = content.live_diff_label(live)
    if not files:
        return [[("running", live_label + " · no changes yet")]] if live_label else [[("muted", "no diff yet")]]
    st.diff_file = max(0, min(st.diff_file, len(files) - 1))
    viewed = app.viewed_paths(vm["id"])
    f = files[st.diff_file]
    pins: Dict[int, str] = {}
    from .theme import SEVERITY
    for fd in (vm.get("review") or {}).get("findings") or []:
        if fd.get("file") == f["path"] and isinstance(fd.get("line_start"), int):
            pins[fd["line_start"]] = SEVERITY.get(str(fd.get("severity")), ("warn", 9))[0]
    head: Line = ([("running", live_label)] if live_label else [("muted", f"{len(viewed)}/{len(files)} viewed")])
    if width >= 96:
        list_w = min(40, max(28, width // 3))
        hunk_w = width - list_w - 1
        side = width >= 156
        hunks, _ = content.diff_hunk_lines(f, hunk_w, side_by_side=side, pins=pins)
        view, st.diff_top = _scrolled(hunks, height, st.diff_top)
        file_rows = [head] + content.diff_file_rows(files, viewed, list_w, st.diff_file, True)
        start = max(0, st.diff_file + 1 - (height - 1)) if st.diff_file + 1 >= height else 0
        file_view = file_rows[:1] + file_rows[1 + start:1 + start + height - 1]
        sep = [[("muted", "│")] for _ in range(height)]
        return hjoin([(file_view, list_w), (sep, 1), (view, hunk_w)])
    list_h = min(len(files) + 1, max(3, height // 3))
    file_rows = [head] + content.diff_file_rows(files, viewed, width, st.diff_file, True)
    start = max(0, st.diff_file - (list_h - 2))
    file_view = file_rows[:1] + file_rows[1 + start:1 + start + list_h - 1]
    hunks, _ = content.diff_hunk_lines(f, width, pins=pins)
    rest = height - len(file_view) - 1
    view, st.diff_top = _scrolled(hunks, max(1, rest), st.diff_top)
    return file_view + [rule(width)] + view


def review_view(app: JobsApp, vm: Dict[str, Any], width: int, height: int) -> List[Line]:
    st = app.job_state(vm["id"])
    lines, anchors = content.review_lines(vm.get("review"), width, st.finding, sorted(st.marked))
    top = st.tops.get("review", 0)
    if st.finding in anchors:
        a = anchors[st.finding]
        if a < top or a + 4 > top + height:
            top = max(0, a - 2)
    view, st.tops["review"] = _scrolled(lines, height, top)
    return view


def tab_body(app: JobsApp, vm: Dict[str, Any], tab: str, width: int, height: int, tick: Optional[int]) -> List[Line]:
    st = app.job_state(vm["id"])
    if tab == "session":
        return session_view(app, vm, width, height)
    if tab == "diff":
        return diff_view(app, vm, width, height)
    if tab == "review":
        return review_view(app, vm, width, height)
    if tab == "checks":
        runs = (vm.get("checks") or {}).get("runs") or []
        cur = st.run if st.run >= 0 else len(runs) - 1
        lines = content.checks_lines(vm.get("checks"), vm.get("state", ""), width, cur, app.clock(), tick)
    elif tab == "plan":
        lines = content.plan_lines(vm.get("plan"), width)
    elif tab == "diagnostics":
        lines = content.error_lines(vm, width)
    else:
        lines = content.log_lines(vm, width, now=app.clock())
    view, st.tops[tab] = _scrolled(lines, height, st.tops.get(tab, 0))
    return view


def mode_panel(app: JobsApp, width: int) -> List[Line]:
    m = app.mode
    if not m:
        return []
    if m["kind"] == "confirm":
        return [[(m.get("tone") or "warn", f" ◆ {m['text']} "), ("accent bold", "(y/N)")]]
    if m["kind"] == "prompt":
        text = "•" * len(m["text"]) if m.get("secret") else m["text"].replace("\n", " ⏎ ")
        room = width - 6
        shown = text[-room:] if cwidth(text) > room else text
        body = [[("accent bold", "› "), ("", shown), ("inverse", " ")]]
        label = m["label"] + ("" if not m.get("optional") else "")
        return panel(body, width, 3, title=label, badge="" if "optional" in label.lower() else ("optional" if m.get("optional") else "required"),
                     focused=True, pad_x=1)
    if m["kind"] == "push":
        p = m.get("preview") or {}
        fields = m["fields"]
        inner = width - 4
        rows: List[Line] = []
        target = f"{p.get('branch') or '?'}{' → ' + p['base'] if p.get('base') else ''}"
        rows.append([("muted", fit(f"{target} · {p.get('mode') or 'publish'}{' · ' + p['remote'] if p.get('remote') else ''}", inner))])

        def label(name: str, text: str) -> Line:
            on = fields[m["field"]] == name
            return [("accent bold" if on else "muted", ("› " if on else "  ") + pad(text, 9))]
        title_line = label("title", "Title") + [("bold", m["title"])]
        if fields[m["field"]] == "title":
            title_line.append(("inverse", " "))
        rows.append(title_line)
        body_lines = m["body"].split("\n") if m["body"] else []
        rows.append(label("body", "Body") + [("muted", f"{len(body_lines)} line{'s' if len(body_lines) != 1 else ''} · enter/ctrl+e edits in $EDITOR")])
        for l in body_lines[:3]:
            rows.append([("muted", "             │ " + l)])
        rows.append(label("draft", "Draft") + [("accent", "[x]" if m["draft"] else "[ ]"),
                                               ("", " open as draft" if m["draft"] else " ready for review")])
        if "override" in fields:
            ov = label("override", "Override") + [("", m["override"])]
            if fields[m["field"]] == "override":
                ov.append(("inverse", " "))
            ov.append(("danger", "  required: verdict is do_not_merge"))
            rows.append(ov)
        if p.get("reason"):
            rows.append([("danger", f"✗ {p['reason']}")])
        if p.get("fallback"):
            rows.append([("muted", "push preview unavailable: using the job title; the backend fills the PR body")])
        return panel(rows, width, len(rows) + 2, title=f"Push {p.get('verdict') and '· verdict ' + str(p['verdict']).replace('_', ' ') or ''}".strip(),
                     focused=True, pad_x=1)
    return []


def job_screen(app: JobsApp, width: int, height: int, tick: Optional[int]) -> List[Line]:
    job_id = app.route["id"]
    vm = app.jobs.get(job_id)
    if vm is None:
        return [[("muted", f" loading {job_id[:10]}…")]]
    st = app.job_state(job_id)
    tab = st.tab or "session"
    key = vm.get("key") or job_id[:8]
    title = clean(vm.get("title") or "")
    if key and title.startswith(key):
        title = title[len(key):].lstrip(" :·-")
    parts = [key, title] + [p for p in (vm.get("branch"), _repo(vm)) if p]
    sty = state_style(vm.get("state", ""))
    badge = f" {sty['icon']} {sty['label']} "
    right: Line = [(sty["token"] + " inverse bold", badge), ("muted", f" {money(vm.get('cost_usd'))} ")]
    head_w = width - line_width(right) - 1
    lines: List[Line] = [[("bold", fit(" " + " · ".join(parts), head_w)),
                          ("", " " * max(0, head_w - cwidth(fit(' ' + ' · '.join(parts), head_w))) + " ")] + right]
    lines.append([("", " ")] + stepper(vm.get("pipeline") or vm.get("steps") or [], width - 2, tick=tick,
                                       now=app.clock(), fetched_at=vm.get("fetched_at")))
    status = _status_line(app, vm, tick)
    if status:
        lines.append(status)
    agents = content.agents_line(vm)
    if agents:
        lines.append([("muted", fit(" " + agents, width))])
    budget = content.budget_line(vm)
    if budget:
        tone = "danger" if (vm.get("pr") or {}).get("state") == "closed" else "muted"
        lines.append([(tone, fit(" " + budget, width))])
    envs = content.environments_line(vm)
    if envs:
        failed = "· failed" in envs
        lines.append([("danger" if failed else "muted", fit(" " + envs + "  (E refresh · K keep)", width))])
    lines.append(rule(width))
    approval = (vm.get("session") or {}).get("approval")
    if approval:
        lines += _approval_box(approval, width)
    env_approval = vm.get("environment_approval")
    if env_approval:
        lines += _environment_approval_box(env_approval, width)
    setup_warning = vm.get("setup_warning")
    if setup_warning:
        # Jobs run unattended: a failed template job setup is a warning, never a park.
        text = str(setup_warning.get("message") or f"`{setup_warning.get('command', '')}` failed")
        lines.append([("warn bold", " ? Job setup failed; continued without it  "), ("muted", fit(text, max(10, width - 44)))])
    files = content.parse_patch(content.job_patch(vm)[0])
    lines.append(tabs(_job_tabs(vm, files), tab, width, tick))
    mp = mode_panel(app, width)
    body_h = max(4, height - len(lines) - len(mp))
    side_w = 0 if (width < 100 or tab == "diff") else (40 if width >= 160 else 30)
    main_w = width - (side_w + 1 if side_w else 0)
    inner_w = main_w - 4
    content_lines = tab_body(app, vm, tab, inner_w, body_h - 2, tick)
    main = panel(content_lines, main_w, body_h, focused=not app.mode, pad_x=1)
    if side_w:
        side = sidebar(app, vm, side_w, body_h, tick)
        lines += hjoin([(main, main_w), (side, side_w)], gap=1)
    else:
        lines += main
    # The inline mode panel (prompt / confirm / push form) always stays visible.
    return lines[:max(0, height - len(mp))] + mp


def _repo(vm: Dict[str, Any]) -> str:
    repo = str(vm.get("repository") or "")
    return repo.rstrip("/").rsplit("/", 1)[-1] if repo else ""


# ---------------------------------------------------------------- activity

def activity_screen(app: JobsApp, width: int, height: int, tick: Optional[int]) -> List[Line]:
    vm = app.activity_vm
    if vm is None:
        return [[("muted", " loading activity…")]]
    rows = activity_rows(vm)
    cur = min(app.cursors["activity"], max(0, len(rows) - 1))
    list_h = max(6, int(height * 0.6))
    detail_h = max(3, height - list_h)
    spark_w = 12 if width >= 140 else (8 if width >= 100 else 0)
    now = app.clock()
    body: List[Line] = []
    for i, r in enumerate(rows):
        sel = i == cur
        line: Line = [("accent", "›" if sel else " ")]
        if r["kind"] == "job":
            j = r["job"]
            sty = state_style(j.get("state", ""))
            line += [("bold", f"{j.get('key') or str(j.get('id', ''))[:8]}  "), (sty["token"], f"{sty['icon']} {sty['label']}"),
                     ("muted", f"  {clean(j.get('title') or '')}")]
        elif r["kind"] == "header":
            line += [("muted bold", r["text"])]
        elif r["kind"] == "container":
            c = r["c"]
            running = str(c.get("status") or "").lower().startswith(("running", "up"))
            hist = app.samples.get(str(c.get("name") or c.get("id") or ""), {"cpu": [], "mem": []})
            cpu = c.get("cpu_percent")
            mem = c.get("mem_bytes")
            line += [("", "    "), ("running" if running else "muted", "●" if running else "○"),
                     ("", " " + pad(str(c.get("name") or c.get("id") or "?"), 36 if width >= 120 else 26))]
            if spark_w:
                mem_known = [m for m in hist["mem"] if m is not None]
                line += [("muted", " cpu "), ("running", sparkline(hist["cpu"], spark_w, 100.0)),
                         ("", pad(f"{cpu:.0f}%" if isinstance(cpu, (int, float)) else "—", 5, "right")),
                         ("muted", "  mem "), ("accent", sparkline(hist["mem"], spark_w, float(c.get("mem_limit") or 0) or (max(mem_known) if mem_known else None))),
                         ("", " " + (nbytes(mem) if mem is not None else "—"))]
            else:
                line += [("", f" {cpu:.0f}%" if isinstance(cpu, (int, float)) else " —"), ("", f" {nbytes(mem) if mem is not None else '—'}")]
            role = str(c.get("role") or "")
            if role in ("engine", "broker"):
                line += [("accent", f"  {role}{' ' + str(c['binding']) if c.get('binding') else ''}")]
            if c.get("gpus"):
                line += [("warn", f"  gpus {c['gpus']}")]
            line += [("muted", f"  {c.get('image') or ''}")]
        else:
            j = r["job"]
            since = j.get("since")
            el = duration(now - float(since)) if since else ""
            kind = r["kind"]
            icon = status_icon("waiting") if kind == "waiting" else status_icon("active", tick)
            line += [("", "  "), icon, ("", f" {pad(kind, 9)} {pad(str(j.get('stage') or ''), 10)} {pad(el, 7, 'right')} "),
                     ("muted", money(j.get("cost_usd"))), ("muted", f"  › {clean(j.get('last') or '')}" if j.get("last") else "")]
        line = fit_line(line, width - 2)
        body.append(add_style(line, "inverse") if sel else line)
    if not rows:
        engine = vm.get("engine")
        body = [[("muted", " nothing running" + ("" if engine == "up" else f" (docker {engine or 'unavailable'})"))]]
    counts = vm.get("counts") or {}
    badge = f"{counts.get('agents', 0)} agents · {counts.get('checks', 0)} checks · {counts.get('containers', 0)} containers"
    start = max(0, min(cur - (list_h - 3) // 2, max(0, len(body) - (list_h - 2))))
    out = panel(body, width, list_h, title="Activity", badge=badge, focused=True, top=start)
    detail: List[Line] = []
    title = "Details"
    if rows:
        r = rows[cur]
        if r["kind"] == "container":
            c = r["c"]
            title = f"{c.get('name')} · {c.get('image') or ''}"
            started = c.get("started_at")
            detail.append([("muted", f"status {c.get('status') or '?'} · stage {c.get('stage') or '?'}"
                                      f"{' · ' + str(c['role']) if c.get('role') else ''}"
                                      f"{' · env ' + str(c['binding']) if c.get('binding') else ''}"
                                      f"{' · gpus ' + str(c['gpus']) if c.get('gpus') else ''}"
                                      f"{' · up ' + duration(now - float(started)) if started else ''}"
                                      f" · mem {nbytes(c.get('mem_bytes'))}/{nbytes(c.get('mem_limit'))}")])
        elif r.get("job_id"):
            j = next((x for x in vm.get("jobs") or [] if x.get("id") == r["job_id"]), {})
            title = f"{j.get('key') or str(r['job_id'])[:8]} · {state_style(j.get('state', ''))['label']}"
            logs = app.activity_logs.get(r["job_id"])
            if logs and logs.get("text"):
                for l in clean(logs["text"]).split("\n")[-(detail_h - 2):]:
                    detail.append([("muted", l)])
            else:
                detail.append([("muted", "enter opens the job · l loads the worker log tail")])
                if j.get("last"):
                    detail.append([("", clean(j["last"]))])
    if vm.get("fallback"):
        detail.append([("muted", "container stats unavailable (activity VM not provided by this backend)")])
    if vm.get("error"):
        detail.append([("danger", f"docker: {vm['error']}")])
    out += panel(detail[-(detail_h - 2):] or [[("muted", "select a row")]], width, detail_h, title=fit(title, width - 10), pad_x=1)
    return out


# ---------------------------------------------------------------- settings

def settings_screen(app: JobsApp, width: int, height: int) -> List[Line]:
    """Every job run rule grouped by section; the selected rule shows its hint and default."""
    vm = app.settings_vm
    if vm is None:
        return [[("muted", " loading settings…")]]
    fields = app.settings_fields()
    cur = min(app.cursors["settings"], max(0, len(fields) - 1))
    inner = width - 4
    label_w = min(32, max(12, inner // 3))
    body: List[Line] = []
    sel_row = 0
    i = 0
    for section in vm.get("sections") or []:
        if body:
            body.append([])
        body.append([("accent bold", section.get("title") or section.get("id") or "")])
        if section.get("hint"):
            body.append([("muted", section["hint"])])
        for n, f in enumerate(section.get("fields") or []):
            if section.get("toolchains") and n == section.get("toolchains_at"):
                body.append([("bold", "Toolchains")])
                if section.get("toolchains_hint"):
                    body.append([("muted", section["toolchains_hint"])])
            sel = i == cur
            editing = bool(app.mode and app.mode.get("setting") == f["path"])
            marker: Line = [("accent", " set") if f.get("set") else ("muted", " default")]
            row: Line = [("accent bold" if sel else "", "› " if sel else "  "), ("bold" if sel else "", pad(f.get("label", ""), label_w)),
                         ("", " "), ("warn" if editing else ("" if f.get("set") else "muted"), fit(str(f.get("display", "")), max(4, inner - label_w - 12)))]
            row += [("", "  ")] + marker
            row = fit_line(row, inner)
            if sel:
                sel_row = len(body)
                row = add_style(row, "inverse")
            body.append(row)
            if sel:
                meta = f["path"] + ("" if not f.get("set") else f" · default {f.get('default_display')}")
                body.append([("muted", "    " + fit(meta, inner - 4))])
                for l in wrap_text(clean(f.get("hint") or ""), inner - 4) if f.get("hint") else []:
                    body.append([("muted", "    " + l)])
            i += 1
        if section.get("id") == "toolchains" or section.get("toolchains"):
            body += _toolchain_lines(app.toolchains, inner)
    if not fields:
        body = [[("danger" if vm.get("error") else "muted", f" {vm.get('error') or 'no settings available'}")]]
    mp = mode_panel(app, width)
    list_h = max(4, height - len(mp))
    room = list_h - 2
    top = max(0, min(sel_row - room // 3, max(0, len(body) - room)))
    overridden = sum(1 for f in fields if f.get("set"))
    badge = f"{overridden} changed · {len(fields)} rules" if fields else ""
    out = panel(body, width, list_h, title="Settings · job run rules", badge=badge, focused=not app.mode, top=top, pad_x=1)
    return out + mp


def _toolchain_lines(toolchains: List[Dict[str, Any]], inner: int) -> List[Line]:
    """Derived image status per saved toolchain row (+ the error / last log line while not built)."""
    if not toolchains:
        return [[("muted", "  images: save a row to build its toolchain image")]]
    out: List[Line] = [[("muted", "  images")]]
    for t in toolchains:
        status = str(t.get("status") or "missing")
        out.append(fit_line([("", "    "), (TOOLCHAIN_TONE.get(status, "muted"), toolchain_line(t))], inner))
        detail = str(t.get("error") or "").strip()
        if not detail and status == "building":
            detail = next((l for l in reversed(str(t.get("log_tail") or "").splitlines()) if l.strip()), "")
        if detail:
            out.append([("danger" if status == "failed" else "muted", "      " + fit(clean(detail), max(4, inner - 6)))])
    return out


# ---------------------------------------------------------------- setup / doctor

SETUP_TONE = {"ok": "ok", "todo": "warn", "warn": "warn", "skipped": "muted", "optional": "muted"}
DOCTOR_TONE = {"ok": "ok", "warn": "warn", "fail": "danger"}


def setup_tabs(app: JobsApp, width: int) -> Line:
    """`Setup  Checklist  Doctor` — Doctor is a tab of Setup (tab switches)."""
    specs: List[Dict[str, Any]] = [{"id": name, "label": label} for name, label in SETUP_TABS.items()]
    checks = app.doctor_checks or []
    failed = sum(1 for c in checks if c.get("status") == "fail")
    if failed:
        specs[1].update(count=failed, tone="danger")
    return fit_line([("accent bold", " Setup ")] + tabs(specs, app.route["name"], width - 8), width)


def setup_screen(app: JobsApp, width: int, height: int, tick: Optional[int]) -> List[Line]:
    """Guided setup: the step checklist (left) and the selected step's why/state/actions/log (right)."""
    from mu.commands.job import STEP_ICONS
    vm = app.setup_vm
    mp = mode_panel(app, width)
    body_h = max(6, height - len(mp))
    if vm is None:
        return [[("", " "), status_icon("active", tick), ("muted", " checking your machine…")]] + mp
    steps = app.setup_steps()
    cur = min(app.setup_cursor, max(0, len(steps) - 1))
    left_w = min(46, max(28, int(width * 0.38)))
    right_w = width - left_w
    rows: List[Line] = []
    for i, st in enumerate(steps):
        checking = st.get("checking")
        icon: Any = status_icon("active", tick) if checking else (SETUP_TONE.get(st.get("state"), "muted"),
                                                                    STEP_ICONS.get(st.get("state") or "", "?"))
        row: Line = [("", " "), icon, ("bold" if i == cur else "",
                                       f" {st.get('title', '')}{' (optional)' if st.get('optional') else ''}")]
        row = fit_line(row, left_w - 2)
        rows.append(add_style(row, "inverse") if i == cur and app.setup_focus == "steps" else row)
    rows.append([])
    if vm.get("can_finish"):
        rows.append([("ok bold", " All set: press F to finish")])
    elif vm.get("blocking"):
        for l in wrap_text("To do: " + ", ".join(vm["blocking"]), left_w - 4):
            rows.append([("muted", " " + l)])
    elif app.setup_checking:
        rows.append([("muted", " checking…")])
    badge = f"{vm.get('done', 0)}/{vm.get('total', len(steps))} done" + (" · checking…" if app.setup_checking else "")
    left = panel(rows, left_w, body_h, title="Setup", badge=badge, badge_tone="ok" if vm.get("can_finish") else "muted",
                 focused=app.setup_focus == "steps")
    step = app.selected_step() or {}
    inner = right_w - 4
    detail: List[Line] = []
    for l in wrap_text(clean(step.get("why") or ""), inner):
        detail.append([("muted", l)])
    state = step.get("state") or ""
    tone = SETUP_TONE.get(state, "muted")
    for i, l in enumerate(wrap_text(clean(step.get("detail") or ""), inner - 2)):
        detail.append([(tone, (STEP_ICONS.get(state, "…") + " ") if i == 0 else "  "), ("", l)])
    actions = list(step.get("actions") or [])
    if actions:
        detail.append([])
    for i, a in enumerate(actions):
        sel = app.setup_focus == "actions" and i == app.setup_action_cursor
        suffix = {"terminal": "  (terminal)", "job": "  (runs with a live log)", "nav": "  (opens settings)"}.get(a.get("kind"), "")
        line: Line = [("accent bold" if sel else "muted", "› " if sel else "  "), ("bold" if sel else "", a.get("label", "")),
                      ("muted", suffix)]
        line = fit_line(line, inner)
        detail.append(add_style(line, "inverse") if sel else line)
        if sel and a.get("hint"):
            for l in wrap_text(clean(a["hint"]), inner - 4):
                detail.append([("muted", "    " + l)])
    if app.setup_terminal and app.setup_terminal[0] == step.get("id"):
        detail += [[], [("accent bold", "Run in another terminal:")], [("", "  $ " + app.setup_terminal[2])],
                   [("muted", "then press enter on the action again to re-check")]]
    job = app.setup_job
    if job and (job.get("step") == step.get("id") or job.get("running")):
        state_icon: Any = status_icon("active", tick) if job.get("running") else \
            (("ok", "✓") if job.get("ok") else ("danger", "✗"))
        detail += [[], [state_icon, ("bold", f" {job.get('title', '')}"),
                        ("muted", " running…" if job.get("running") else "")]]
        room = max(3, body_h - 2 - len(detail))
        for l in (job.get("lines") or [])[-room:]:
            detail.append([("muted", fit(clean(l), inner))])
    if vm.get("error"):
        detail.append([("danger", fit(clean(str(vm["error"])), inner))])
    right = panel(detail[-(body_h - 2):] if len(detail) > body_h - 2 else detail, right_w, body_h,
                  title=fit(str(step.get("title") or "Step"), right_w - 12),
                  badge="optional" if step.get("optional") else "", badge_tone=tone,
                  focused=app.setup_focus == "actions", pad_x=1)
    return hjoin([(left, left_w), (right, right_w)]) + mp


def doctor_screen(app: JobsApp, width: int, height: int, tick: Optional[int]) -> List[Line]:
    """Every check a job run depends on: ✓ ok · ! warn · ✗ fail (D adds the sandbox self-test)."""
    from mu.commands.job import DOCTOR_ICONS
    checks = app.doctor_checks
    inner = width - 4
    body: List[Line] = []
    if checks is None:
        body = [[("", " "), status_icon("active", tick), ("muted", " running checks…")]]
        badge, tone = "running checks…", "muted"
    else:
        name_w = min(22, max([8] + [cwidth(str(c.get("name") or "")) for c in checks]) + 1)
        for i, c in enumerate(checks):
            status = str(c.get("status") or "")
            line: Line = [(DOCTOR_TONE.get(status, "muted"), f" {DOCTOR_ICONS.get(status, '?')} "),
                          ("bold", pad(str(c.get("name") or ""), name_w)),
                          ("danger" if status == "fail" else "warn" if status == "warn" else "muted",
                           " " + clean(str(c.get("detail") or "")))]
            line = fit_line(line, inner)
            body.append(add_style(line, "inverse") if i == app.doctor_cursor else line)
        failed = sum(1 for c in checks if c.get("status") == "fail")
        warned = sum(1 for c in checks if c.get("status") == "warn")
        badge = f"{len(checks) - failed - warned}/{len(checks)} ok" + (f" · {failed} failing" if failed else "") + \
            (f" · {warned} warnings" if warned else "")
        tone = "danger" if failed else "warn" if warned else "ok"
        if failed:
            body += [[], [("muted", " g u opens Setup's checklist (or tab): each step offers the actions that fix it.")]]
        sel = checks[min(app.doctor_cursor, len(checks) - 1)] if checks else None
        if sel and cwidth(str(sel.get("detail") or "")) > inner - name_w - 4:
            body.append([])
            for l in wrap_text(clean(str(sel.get("detail") or "")), inner - 2):
                body.append([("muted", " " + l)])
    if app.doctor_running:
        body.insert(0, [("", " "), status_icon("active", tick),
                        ("muted", " running the sandbox self-test (real containers)…" if app.doctor_running == "deep"
                         else " re-running checks…")])
    return panel(body, width, height, title="Doctor", badge=badge, badge_tone=tone, focused=True)


# ---------------------------------------------------------------- overlays

def help_overlay(app: JobsApp, width: int, height: int) -> List[Line]:
    rows: List[Any] = ["This screen"] + list(app.hints()) + ["", "Everywhere"] + list(GLOBAL_KEYS)
    if app.route["name"] == "job":
        rows += ["", "Job actions"]
        vm = app.jobs.get(app.route["id"]) or {}
        rows += [(a["shortcut"], a["label"] + ("" if a.get("enabled") else f"  — {a.get('reason')}"))
                 for a in JobsApp.visible_actions(vm.get("actions", [])) if a.get("shortcut")]
    kw = min(16, max([6] + [cwidth(r[0]) for r in rows if isinstance(r, tuple)]))
    body: List[Line] = []
    for r in rows:
        if isinstance(r, str):
            body.append([("muted bold", " " + r)])
        else:
            body.append([("", " "), ("accent bold", pad(r[0], kw)), ("", "  " + r[1])])
    return panel(body, width, height, title="Keys", badge="? or esc to close", focused=True)


def agents_overlay(app: JobsApp, width: int, height: int) -> List[Line]:
    """Claude Code / Codex setup and readiness (same text as `/job agents`)."""
    from mu.commands.job import agents_text
    data = app.agents_vm or {}
    body: List[Line] = []
    for row in agents_text(data).splitlines():
        tone = "danger" if row.lstrip().startswith("✗") else "warn" if row.lstrip().startswith("!") else \
            "bold" if row and not row.startswith(" ") else ""
        body.append([(tone, fit(" " + clean(row), width - 4))])
    if data.get("error"):
        body.append([("danger", fit(" " + clean(str(data["error"])), width - 4))])
    return panel(body, width, height, title="Agents", badge="r refresh · esc to close", focused=True)


def palette_overlay(app: JobsApp, width: int, height: int) -> List[Line]:
    ranked = rank_palette(app.palette_items(), app.palette_q)
    inner = width - 4
    list_h = max(1, height - 4)
    cur = min(app.palette_cursor, max(0, len(ranked) - 1))
    start = min(max(0, cur - list_h // 2), max(0, len(ranked) - list_h))
    body: List[Line] = [[("accent bold", " › "), ("", app.palette_q),
                         ("inverse", " "), ("muted", "" if app.palette_q else " type to search actions and screens")],
                        [("muted", " " + "─" * max(0, inner))]]
    for i, it in enumerate(ranked[start:start + list_h]):
        sel = start + i == cur
        off = it.get("enabled") is False
        right = (it.get("reason") or "not available") if off else (it.get("key") or "")
        left_w = max(4, inner - cwidth(right) - 3)
        label = f"{it.get('group') + ' › ' if it.get('group') else ''}{it.get('label')}"
        line: Line = [("accent", " › " if sel else "   "), ("muted" if off else "", pad(label, left_w)),
                      ("muted" if off else "accent", fit(right, inner - left_w))]
        body.append(add_style(fit_line(line, width - 2), "inverse") if sel else line)
    if not ranked:
        body.append([("muted", "   no matches")])
    return panel(body, width, height, title="Commands", badge=str(len(ranked)), focused=True)
