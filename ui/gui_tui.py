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
    tab_index: int = 2  # feature
    selected_bucket: int = 0
    selected_card: int = 0
    should_exit: bool = False
    focus: str = "sessions"  # sessions | board
    session_index: int = 0
    pinned_session: str | None = None
    session_names: list[str] = field(default_factory=list)
    detail_open: bool = False
    detail_offset: int = 0


# ---------------- Session discovery ----------------

def _discover_sessions(session_root: str) -> list[str]:
    if not os.path.isdir(session_root):
        return []
    names = []
    for name in sorted(os.listdir(session_root)):
        session_path = os.path.join(session_root, name, "session.json")
        if os.path.isfile(session_path):
            names.append(name)
    return names


def _load_feature_plan_for_session_name(session_root: str, session_name: str) -> FeaturePlan | None:
    session_path = os.path.join(session_root, session_name, "session.json")
    if not os.path.isfile(session_path):
        return None
    try:
        with open(session_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    feature_state = payload.get("feature_state", {}) if isinstance(payload.get("feature_state"), dict) else {}
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not metadata_path:
        return None

    try:
        return load_feature_plan(metadata_path)
    except Exception:
        return None


# ---------------- Board rendering ----------------

def _bucket_tasks(plan: FeaturePlan) -> dict[str, list]:
    buckets = {
        "Backlog": [],
        "Selected for Development": [],
        "In Progress": [],
        "Done": [],
    }
    for task in plan.tasks:
        status = normalize_task_status(task.status)
        if status in {STATUS_NOT_STARTED, STATUS_PENDING}:
            buckets["Backlog"].append(task)
        elif status == STATUS_BLOCKED:
            buckets["Selected for Development"].append(task)
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
    return Panel(
        "\n".join(lines),
        border_style="bright_green" if selected else "blue",
        padding=(0, 1),
    )


def _feature_board(plan: FeaturePlan, state: GuiState) -> Group:
    buckets = _bucket_tasks(plan)
    bucket_names = list(buckets.keys())
    state.selected_bucket = max(0, min(state.selected_bucket, len(bucket_names) - 1))
    current_bucket_name = bucket_names[state.selected_bucket]
    current_cards = buckets[current_bucket_name]
    if current_cards:
        state.selected_card = max(0, min(state.selected_card, len(current_cards) - 1))
    else:
        state.selected_card = 0

    columns = []
    for b_idx, name in enumerate(bucket_names):
        cards = buckets[name]
        card_panels = []
        for c_idx, task in enumerate(cards[:10]):
            is_selected = (
                state.focus == "board"
                and b_idx == state.selected_bucket
                and c_idx == state.selected_card
            )
            card_panels.append(_task_panel(task, selected=is_selected))

        if not card_panels:
            card_panels = [Panel("(empty)", border_style="dim")]

        title = f"{name} ({len(cards)} Tasks)"
        border = "green" if (state.focus == "board" and b_idx == state.selected_bucket) else "cyan"
        columns.append(Panel(Group(*card_panels), title=title, border_style=border))

    return Group(Columns(columns, expand=True))


def _selected_task(plan: FeaturePlan, state: GuiState):
    buckets = _bucket_tasks(plan)
    bucket_names = list(buckets.keys())
    if not bucket_names:
        return None
    b_idx = max(0, min(state.selected_bucket, len(bucket_names) - 1))
    cards = buckets[bucket_names[b_idx]]
    if not cards:
        return None
    c_idx = max(0, min(state.selected_card, len(cards) - 1))
    return cards[c_idx]


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
    lines.extend([f"  - {item}" for item in (task.objectives or [])] or ["  - (none)"])
    lines.append("")
    lines.append("action_points:")
    lines.extend([f"  - {item}" for item in (task.action_points or [])] or ["  - (none)"])
    lines.append("")
    lines.append("exit_criteria:")
    lines.extend([f"  - {item}" for item in (task.exit_criteria or [])] or ["  - (none)"])
    lines.append("")
    lines.append("verified_exit_criteria:")
    lines.extend([f"  - {item}" for item in (task.verified_exit_criteria or [])] or ["  - (none)"])
    lines.append("")
    lines.append(f"blocked_reason: {task.blocked_reason or '-'}")
    lines.append(f"notes: {task.notes or '-'}")

    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 24]
    body = "\n".join(window) if window else "(empty)"
    return Panel(
        body,
        title=f"Card Detail • Task {task.id}",
        subtitle="Enter=open detail • j/k scroll • b/Esc back",
        border_style="magenta",
    )


def _mode_tabs(state: GuiState) -> Table:
    table = Table.grid(expand=True)
    for _ in MODE_TABS:
        table.add_column(justify="center")
    cells = []
    for i, mode in enumerate(MODE_TABS):
        if i == state.tab_index:
            cells.append(f"[bold black on bright_cyan] {mode.upper()} [/bold black on bright_cyan]")
        else:
            cells.append(f"[dim]{mode.upper()}[/dim]")
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

    return Panel(
        "\n".join(lines),
        title="Sessions",
        border_style="green" if state.focus == "sessions" else "cyan",
    )


def _render_gui(session_root: str, state: GuiState) -> Group:
    state.session_names = _discover_sessions(session_root)
    tabs = _mode_tabs(state)
    active_mode = MODE_TABS[state.tab_index]

    header = Panel(
        Text.from_markup(
            "[bold cyan]μCLI GUI[/bold cyan] • multi-session\n"
            "keys: ←/→ mode • Tab switch focus • j/k move • Enter pin/open session or open card detail • b back • q quit"
        ),
        border_style="cyan",
    )

    if active_mode != "feature":
        return Group(
            tabs,
            header,
            Panel(
                f"{active_mode} mode tab is not implemented yet.\nSwitch to FEATURE tab.",
                border_style="yellow",
                title=f"{active_mode.title()} Mode",
            ),
        )

    if not state.session_names:
        return Group(tabs, header, _sessions_panel(state), Panel("No sessions available.", border_style="yellow"))

    state.session_index = max(0, min(state.session_index, len(state.session_names) - 1))
    selected_name = state.pinned_session or state.session_names[state.session_index]
    plan = _load_feature_plan_for_session_name(session_root, selected_name)

    right: Panel | Group
    if plan:
        if state.detail_open:
            right = Group(
                Panel(
                    f"[bold green]{plan.feature_name}[/bold green] ([cyan]{plan.feature_id}[/cyan]) • session: [magenta]{selected_name}[/magenta]",
                    border_style="green",
                ),
                _task_detail_panel(plan, state, selected_name),
            )
        else:
            right = Group(
                Panel(
                    f"[bold green]{plan.feature_name}[/bold green] ([cyan]{plan.feature_id}[/cyan]) • session: [magenta]{selected_name}[/magenta]",
                    border_style="green",
                ),
                _feature_board(plan, state),
            )
    else:
        right = Panel(
            f"Session '{selected_name}' has no active feature plan metadata.",
            border_style="yellow",
            title="Feature Mode",
        )

    layout = Columns([_sessions_panel(state), right], expand=True, equal=False)
    return Group(tabs, header, layout)


# ---------------- Input handling ----------------

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
    if key in {"\x1b[C"}:  # right mode
        state.tab_index = (state.tab_index + 1) % len(MODE_TABS)
        return state
    if key in {"\x1b[D"}:  # left mode
        state.tab_index = (state.tab_index - 1) % len(MODE_TABS)
        return state
    if key == "\t":
        state.detail_open = False
        state.detail_offset = 0
        state.focus = "board" if state.focus == "sessions" else "sessions"
        return state
    if key in {"b", "\x1b"}:
        if state.detail_open:
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
        elif state.focus == "board":
            state.detail_open = True
            state.detail_offset = 0
        return state

    if state.focus == "sessions":
        if key in {"\x1b[B", "j"}:
            state.session_index += 1
            return state
        if key in {"\x1b[A", "k"}:
            state.session_index = max(0, state.session_index - 1)
            return state
    else:
        if state.detail_open:
            if key in {"\x1b[B", "j"}:
                state.detail_offset += 1
                return state
            if key in {"\x1b[A", "k"}:
                state.detail_offset = max(0, state.detail_offset - 1)
                return state
            return state
        if key in {"h"}:
            state.selected_bucket = max(0, state.selected_bucket - 1)
            state.selected_card = 0
            return state
        if key in {"l"}:
            state.selected_bucket = min(3, state.selected_bucket + 1)
            state.selected_card = 0
            return state
        if key in {"\x1b[B", "j"}:
            state.selected_card += 1
            return state
        if key in {"\x1b[A", "k"}:
            state.selected_card = max(0, state.selected_card - 1)
            return state

    return state


def run_gui_mode(session_root: str, refresh_seconds: float = 1.0) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.0))
    state = GuiState()

    with _KeyReader() as reader, Live(
        _render_gui(session_root, state), refresh_per_second=8, screen=True
    ) as live:
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
