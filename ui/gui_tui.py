"""Terminal GUI mode for MuCLI (`--gui`)."""

from __future__ import annotations

import select
import sys
import termios
import time
import tty
from dataclasses import dataclass

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


def _load_feature_plan_for_session(session) -> FeaturePlan | None:
    feature_state = session.session_manager.get_feature_state() or {}
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if metadata_path:
        try:
            return load_feature_plan(metadata_path)
        except Exception:
            return None
    return None


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
            is_selected = b_idx == state.selected_bucket and c_idx == state.selected_card
            card_panels.append(_task_panel(task, selected=is_selected))

        if not card_panels:
            card_panels = [Panel("(empty)", border_style="dim")]

        title = f"{name} ({len(cards)} Tasks)"
        border = "green" if b_idx == state.selected_bucket else "cyan"
        columns.append(Panel(Group(*card_panels), title=title, border_style=border))

    header = Panel(
        Text.from_markup(
            f"[bold green]Feature Board[/bold green] • {plan.feature_name} ([cyan]{plan.feature_id}[/cyan])\n"
            "keys: ←/→ switch mode • h/l switch lane • j/k move card • q quit"
        ),
        border_style="green",
    )
    return Group(header, Columns(columns, expand=True))


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


def _render_gui(session, state: GuiState) -> Group:
    tabs = _mode_tabs(state)
    active_mode = MODE_TABS[state.tab_index]

    if active_mode != "feature":
        placeholder = Panel(
            f"{active_mode} mode tab is not implemented yet.\nSwitch to FEATURE tab.",
            border_style="yellow",
            title=f"{active_mode.title()} Mode",
        )
        return Group(tabs, placeholder)

    plan = _load_feature_plan_for_session(session)
    if not plan:
        return Group(
            tabs,
            Panel(
                "No active feature plan found.\nUse /feature new or /feature load first.",
                border_style="yellow",
                title="Feature Mode",
            ),
        )

    return Group(tabs, _feature_board(plan, state))


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
    if key in {"\x1b[C", "l"}:  # right
        state.tab_index = (state.tab_index + 1) % len(MODE_TABS)
        state.selected_bucket = 0
        state.selected_card = 0
        return state
    if key in {"\x1b[D", "h"}:  # left
        state.tab_index = (state.tab_index - 1) % len(MODE_TABS)
        state.selected_bucket = 0
        state.selected_card = 0
        return state
    if key in {"\x1b[B", "j"}:  # down
        state.selected_card += 1
        return state
    if key in {"\x1b[A", "k"}:  # up
        state.selected_card = max(0, state.selected_card - 1)
        return state
    if key in {"\t"}:  # next bucket
        state.selected_bucket = (state.selected_bucket + 1) % 4
        state.selected_card = 0
        return state
    return state


def run_gui_mode(session, refresh_seconds: float = 1.0) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.0))
    state = GuiState()

    with _KeyReader() as reader, Live(
        _render_gui(session, state), refresh_per_second=8, screen=True
    ) as live:
        next_refresh = 0.0
        while not state.should_exit:
            now = time.time()
            if now >= next_refresh:
                live.update(_render_gui(session, state))
                next_refresh = now + refresh_seconds
            key = reader.read_key(timeout=0.05)
            if key:
                state = _handle_key(state, key)
                live.update(_render_gui(session, state))
