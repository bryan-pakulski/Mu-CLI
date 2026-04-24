"""Terminal GUI mode for MuCLI (`--gui`)."""

from __future__ import annotations

import json
import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from datetime import datetime

from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.feature_mode import (
    STATUS_ARCHIVED,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_PENDING,
    FeaturePlan,
    load_feature_plan,
    normalize_task_status,
)

MODE_TABS = ["default", "debug", "feature", "research", "git"]


@dataclass
class GuiState:
    tab_index: int = 2
    selected_bucket: int = 0
    selected_card: int = 0
    should_exit: bool = False
    focus: str = "sessions"
    session_index: int = 0
    pinned_session: str | None = None
    session_names: list[str] = field(default_factory=list)
    detail_open: bool = False
    detail_offset: int = 0
    history_open: bool = False
    history_offset: int = 0


def _discover_sessions(session_root: str) -> list[str]:
    if not os.path.isdir(session_root):
        return []
    names = []
    for name in sorted(os.listdir(session_root)):
        if os.path.isfile(os.path.join(session_root, name, "session.json")):
            names.append(name)
    return names


def _load_session_payload(session_root: str, session_name: str) -> dict:
    path = os.path.join(session_root, session_name, "session.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_feature_plan_for_session_name(session_root: str, session_name: str) -> FeaturePlan | None:
    payload = _load_session_payload(session_root, session_name)
    feature_state = payload.get("feature_state", {}) if isinstance(payload.get("feature_state"), dict) else {}
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not metadata_path:
        return None
    try:
        return load_feature_plan(metadata_path)
    except Exception:
        return None


def _spark(values: list[int]) -> str:
    ticks = "▁▂▃▄▅▆▇█"
    if not values:
        return "—"
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return ticks[0] * len(values)
    out = []
    for v in values:
        idx = int((v - lo) / max(1, hi - lo) * (len(ticks) - 1))
        out.append(ticks[max(0, min(idx, len(ticks) - 1))])
    return "".join(out)


def _bar(value: int, maximum: int, width: int = 18) -> str:
    maximum = max(1, int(maximum or 1))
    value = max(0, int(value or 0))
    filled = min(width, int(round((value / maximum) * width)))
    return "█" * filled + "░" * (width - filled)


def _bucket_tasks(plan: FeaturePlan) -> dict[str, list]:
    buckets = {"Backlog": [], "In Progress": [], "Done": []}
    for task in plan.tasks:
        status = normalize_task_status(task.status)
        if status in {STATUS_NOT_STARTED, STATUS_PENDING}:
            buckets["Backlog"].append(task)
        elif status == STATUS_BLOCKED:
            buckets["Backlog"].append(task)
        elif status == STATUS_IN_PROGRESS:
            buckets["In Progress"].append(task)
        elif status in {STATUS_COMPLETED, STATUS_ARCHIVED}:
            buckets["Done"].append(task)
        else:
            buckets["Backlog"].append(task)
    return buckets


def _task_panel(task, selected: bool = False) -> Panel:
    lines = [
        f"[bold]{task.id}[/bold] — {task.title}",
        f"phase: {task.phase_id if task.phase_id is not None else '-'}",
        f"status: {normalize_task_status(task.status)}",
    ]
    if task.blocked_reason:
        lines.append(f"blocked: {task.blocked_reason}")
    if task.exit_criteria:
        lines.append(f"criteria: {len(task.exit_criteria)}")
    return Panel("\n".join(lines), border_style="bright_green" if selected else "blue", padding=(0, 1))


def _selected_task(plan: FeaturePlan, state: GuiState):
    buckets = _bucket_tasks(plan)
    names = list(buckets.keys())
    if not names:
        return None
    lane = max(0, min(state.selected_bucket, len(names) - 1))
    cards = buckets[names[lane]]
    if not cards:
        return None
    card = max(0, min(state.selected_card, len(cards) - 1))
    return cards[card]


def _feature_board(plan: FeaturePlan, state: GuiState) -> Group:
    buckets = _bucket_tasks(plan)
    names = list(buckets.keys())
    state.selected_bucket = max(0, min(state.selected_bucket, len(names) - 1))
    cards = buckets[names[state.selected_bucket]]
    if cards:
        state.selected_card = max(0, min(state.selected_card, len(cards) - 1))
    else:
        state.selected_card = 0

    cols = []
    for b_idx, name in enumerate(names):
        lane_cards = buckets[name]
        panels = []
        for c_idx, task in enumerate(lane_cards[:10]):
            sel = (
                state.focus == "board" and not state.detail_open and not state.history_open
                and b_idx == state.selected_bucket and c_idx == state.selected_card
            )
            panels.append(_task_panel(task, selected=sel))
        if not panels:
            panels = [Panel("(empty)", border_style="dim")]
        border = "green" if (state.focus == "board" and b_idx == state.selected_bucket) else "cyan"
        cols.append(Panel(Group(*panels), title=f"{name} ({len(lane_cards)} Tasks)", border_style=border))
    return Group(Columns(cols, expand=True))


def _task_detail_panel(plan: FeaturePlan, state: GuiState, session_name: str) -> Panel:
    task = _selected_task(plan, state)
    if not task:
        return Panel("(No card selected in this lane.)", title="Card Detail", border_style="yellow")

    lines = [
        f"session: {session_name}",
        f"feature: {plan.feature_name} ({plan.feature_id})",
        "",
        f"task_id: {task.id}",
        f"title: {task.title}",
        f"phase_id: {task.phase_id}",
        f"status: {normalize_task_status(task.status)}",
        "",
        "objectives:",
    ]
    lines.extend([f"  - {x}" for x in (task.objectives or [])] or ["  - (none)"])
    lines += ["", "action_points:"]
    lines.extend([f"  - {x}" for x in (task.action_points or [])] or ["  - (none)"])
    lines += ["", "exit_criteria:"]
    lines.extend([f"  - {x}" for x in (task.exit_criteria or [])] or ["  - (none)"])
    lines += ["", "verified_exit_criteria:"]
    lines.extend([f"  - {x}" for x in (task.verified_exit_criteria or [])] or ["  - (none)"])
    lines += ["", f"blocked_reason: {task.blocked_reason or '-'}", f"notes: {task.notes or '-'}"]

    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 24]
    return Panel("\n".join(window) if window else "(empty)", title=f"Card Detail • Task {task.id}", subtitle="j/k scroll • b/Esc back", border_style="magenta")


def _stats_widgets(plan: FeaturePlan, payload: dict) -> Columns:
    total = len(plan.tasks)
    done = sum(1 for t in plan.tasks if normalize_task_status(t.status) in {STATUS_COMPLETED, STATUS_ARCHIVED})
    blocked = sum(1 for t in plan.tasks if normalize_task_status(t.status) == STATUS_BLOCKED)
    in_progress = sum(1 for t in plan.tasks if normalize_task_status(t.status) == STATUS_IN_PROGRESS)
    progress = int((done / max(1, total)) * 100)

    token_counts = payload.get("token_counts", {}) if isinstance(payload, dict) else {}
    total_tokens = int(token_counts.get("total", 0) or 0)
    history_len = len(payload.get("history", [])) if isinstance(payload.get("history"), list) else 0
    transitions = []
    running = 0
    for event in plan.event_log[-24:]:
        if getattr(event, "kind", "") == "status_transition":
            running += 1
        transitions.append(running)

    return Columns([
        Panel(f"[bold cyan]{total}[/bold cyan]\nTasks", border_style="cyan"),
        Panel(f"[bold green]{done}[/bold green]\nDone ({progress}%)\n{_bar(done, total)}", border_style="green"),
        Panel(f"[bold yellow]{in_progress}[/bold yellow]\nIn Progress", border_style="yellow"),
        Panel(f"[bold red]{blocked}[/bold red]\nBlocked", border_style="red"),
        Panel(f"[bold magenta]{total_tokens:,}[/bold magenta]\nTokens", border_style="magenta"),
        Panel(f"[bold white]{history_len}[/bold white]\nTurns", border_style="blue"),
        Panel(f"[bold white]{_spark(transitions)}[/bold white]\nTransition Pulse", border_style="bright_blue"),
    ], expand=True)


def _history_browser(plan: FeaturePlan, payload: dict, state: GuiState) -> Panel:
    lines = ["Feature Event Log:"]
    for event in plan.event_log[-60:]:
        ts = datetime.fromtimestamp(float(getattr(event, "created_at", 0) or 0)).strftime("%H:%M:%S")
        lines.append(f"  [{ts}] {getattr(event, 'kind', '-')}: {getattr(event, 'entity', '-')}#{getattr(event, 'entity_id', '-')}")

    lines += ["", "All Features (including archived):"]
    registry = payload.get("feature_registry", {}) if isinstance(payload.get("feature_registry"), dict) else {}
    feature_records = sorted(
        [value for value in registry.values() if isinstance(value, dict)],
        key=lambda item: float(item.get("updated_at", 0) or 0),
        reverse=True,
    )
    if feature_records:
        for feature in feature_records[:40]:
            fid = str(feature.get("feature_id", "-"))
            name = str(feature.get("feature_name", fid))
            status = str(feature.get("status", "unknown"))
            lines.append(f"  {fid:<28} | {status:<12} | {name}")
    else:
        lines.append("  (none)")

    lines += ["", "Conversation History (recent):"]
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    for msg in history[-30:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        snippet = ""
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if isinstance(part, dict) and part.get("type") == "text":
                snippet = str(part.get("text", "")).strip().replace("\n", " ")
                break
        if len(snippet) > 120:
            snippet = snippet[:119] + "…"
        lines.append(f"  {role:<10} | {snippet or '(non-text event)'}")

    start = max(0, min(state.history_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 26]
    return Panel("\n".join(window) if window else "(empty)", title="History Browser", subtitle="H close • j/k scroll", border_style="bright_magenta")


def _mode_tabs(state: GuiState) -> Table:
    table = Table.grid(expand=True)
    for _ in MODE_TABS:
        table.add_column(justify="center")
    cells = []
    for i, mode in enumerate(MODE_TABS):
        cells.append(f"[bold black on bright_cyan] {mode.upper()} [/bold black on bright_cyan]" if i == state.tab_index else f"[dim]{mode.upper()}[/dim]")
    table.add_row(*cells)
    return table


def _sessions_panel(state: GuiState) -> Panel:
    state.session_index = max(0, min(state.session_index, max(0, len(state.session_names) - 1)))
    lines = []
    for i, name in enumerate(state.session_names[:40]):
        marker = "▶" if i == state.session_index else " "
        pin = "📌" if state.pinned_session == name else " "
        style = "bold green" if (state.focus == "sessions" and i == state.session_index) else "white"
        lines.append(f"[{style}]{marker} {pin} {name}[/{style}]")
    if not lines:
        lines = ["(no sessions found)"]
    return Panel("\n".join(lines), title="Sessions", border_style="green" if state.focus == "sessions" else "cyan")


def _render_gui(session_root: str, state: GuiState) -> Group:
    state.session_names = _discover_sessions(session_root)
    tabs = _mode_tabs(state)
    active_mode = MODE_TABS[state.tab_index]

    header = Panel(
        Text.from_markup(
            "[bold cyan]μCLI GUI[/bold cyan] • multi-session\n"
            "keys: ←/→ mode • Tab focus • j/k move • Enter pin/open or card detail • H history browser • b back • q quit"
        ),
        border_style="cyan",
    )

    if active_mode != "feature":
        return Group(tabs, header, Panel(f"{active_mode} mode tab is not implemented yet.\nSwitch to FEATURE tab.", border_style="yellow", title=f"{active_mode.title()} Mode"))

    if not state.session_names:
        return Group(tabs, header, _sessions_panel(state), Panel("No sessions available.", border_style="yellow"))

    state.session_index = max(0, min(state.session_index, len(state.session_names) - 1))
    selected_name = state.pinned_session or state.session_names[state.session_index]
    payload = _load_session_payload(session_root, selected_name)
    plan = _load_feature_plan_for_session_name(session_root, selected_name)

    if not plan:
        right: Panel | Group = Panel(f"Session '{selected_name}' has no active feature plan metadata.", border_style="yellow", title="Feature Mode")
    else:
        top = Panel(f"[bold green]{plan.feature_name}[/bold green] ([cyan]{plan.feature_id}[/cyan]) • session: [magenta]{selected_name}[/magenta]", border_style="green")
        widgets = _stats_widgets(plan, payload)
        if state.history_open:
            right = Group(top, widgets, _history_browser(plan, payload, state))
        elif state.detail_open:
            right = Group(top, widgets, _task_detail_panel(plan, state, selected_name))
        else:
            right = Group(top, widgets, _feature_board(plan, state))

    return Group(tabs, header, Columns([_sessions_panel(state), right], expand=True, equal=False))


class _KeyReader:
    def __enter__(self):
        self.enabled = sys.stdin.isatty()
        self.fd = None
        self.old = None
        if self.enabled:
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled and self.fd is not None and self.old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def read_key(self, timeout: float = 0.0) -> str | None:
        if not self.enabled:
            return None
        ready, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout))
        if not ready:
            return None
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        seq = ch
        ready, _, _ = select.select([sys.stdin], [], [], 0.0001)
        while ready:
            seq += sys.stdin.read(1)
            ready, _, _ = select.select([sys.stdin], [], [], 0.0001)
        return seq


def _handle_key(state: GuiState, key: str) -> GuiState:
    if key in {"q", "\x03"}:
        state.should_exit = True
        return state
    if key in {"\x1b[C"}:
        state.tab_index = (state.tab_index + 1) % len(MODE_TABS)
        return state
    if key in {"\x1b[D"}:
        state.tab_index = (state.tab_index - 1) % len(MODE_TABS)
        return state
    if key == "\t":
        state.detail_open = False
        state.detail_offset = 0
        state.history_open = False
        state.history_offset = 0
        state.focus = "board" if state.focus == "sessions" else "sessions"
        return state
    if key == "H" and state.focus == "board":
        state.history_open = not state.history_open
        state.history_offset = 0
        state.detail_open = False
        return state
    if key in {"b", "\x1b"}:
        if state.history_open:
            state.history_open = False
            state.history_offset = 0
        elif state.detail_open:
            state.detail_open = False
            state.detail_offset = 0
        else:
            state.focus = "sessions"
        return state
    if key in {"\n", "\r"}:
        if state.focus == "sessions" and state.session_names:
            state.pinned_session = state.session_names[state.session_index]
            state.focus = "board"
            state.detail_open = False
            state.detail_offset = 0
            state.history_open = False
            state.history_offset = 0
        elif state.focus == "board" and not state.history_open:
            state.detail_open = True
            state.detail_offset = 0
        return state

    if state.focus == "sessions":
        if key in {"\x1b[B", "j"}:
            state.session_index += 1
        elif key in {"\x1b[A", "k"}:
            state.session_index = max(0, state.session_index - 1)
        return state

    if state.history_open:
        if key in {"\x1b[B", "j"}:
            state.history_offset += 1
        elif key in {"\x1b[A", "k"}:
            state.history_offset = max(0, state.history_offset - 1)
        return state

    if state.detail_open:
        if key in {"\x1b[B", "j"}:
            state.detail_offset += 1
        elif key in {"\x1b[A", "k"}:
            state.detail_offset = max(0, state.detail_offset - 1)
        return state

    if key in {"h"}:
        state.selected_bucket = max(0, state.selected_bucket - 1)
        state.selected_card = 0
    elif key in {"l"}:
        state.selected_bucket = min(2, state.selected_bucket + 1)
        state.selected_card = 0
    elif key in {"\x1b[B", "j"}:
        state.selected_card += 1
    elif key in {"\x1b[A", "k"}:
        state.selected_card = max(0, state.selected_card - 1)
    return state


def run_gui_mode(session_root: str, refresh_seconds: float = 1.0) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.0))
    state = GuiState()

    with _KeyReader() as reader, Live(_render_gui(session_root, state), refresh_per_second=8, screen=True) as live:
        next_refresh = 0.0
        while not state.should_exit:
            now = time.time()
            if now >= next_refresh:
                live.update(_render_gui(session_root, state))
                next_refresh = now + refresh_seconds
            key = reader.read_key(timeout=0.05)
            if key:
                state = _handle_key(state, key)
                live.update(_render_gui(session_root, state))
