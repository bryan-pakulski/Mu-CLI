"""Functional terminal GUI mode for MuCLI (`--gui`)."""

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


@dataclass
class GuiState:
    screen: str = "sessions"
    should_exit: bool = False
    confirm_quit: bool = False
    confirm_index: int = 0  # 0 cancel, 1 quit

    session_names: list[str] = field(default_factory=list)
    session_index: int = 0
    selected_session: str | None = None

    feature_records: list[dict] = field(default_factory=list)
    feature_index: int = 0
    selected_feature: dict | None = None

    item_index: int = 0
    detail_offset: int = 0


def _discover_sessions(session_root: str) -> list[str]:
    if not os.path.isdir(session_root):
        return []
    out = []
    for name in sorted(os.listdir(session_root)):
        if os.path.isfile(os.path.join(session_root, name, "session.json")):
            out.append(name)
    return out


def _load_session_payload(session_root: str, session_name: str) -> dict:
    path = os.path.join(session_root, session_name, "session.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _feature_records(payload: dict) -> list[dict]:
    registry = payload.get("feature_registry", {}) if isinstance(payload.get("feature_registry"), dict) else {}
    records = [v for v in registry.values() if isinstance(v, dict)]
    records.sort(key=lambda item: float(item.get("updated_at", 0) or 0), reverse=True)
    return records


def _load_feature_plan_from_record(record: dict | None) -> FeaturePlan | None:
    if not isinstance(record, dict):
        return None
    metadata_path = str(record.get("metadata_path", "") or "").strip()
    if not metadata_path:
        return None
    try:
        return load_feature_plan(metadata_path)
    except Exception:
        return None


def _tool_usage_counts(payload: dict) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        parts = msg.get("parts", [])
        for part in parts if isinstance(parts, list) else []:
            if isinstance(part, dict) and part.get("type") == "tool_call":
                tool = str(part.get("tool_name", "tool") or "tool")
                counts[tool] = counts.get(tool, 0) + 1
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _status_color(status: str) -> str:
    normalized = normalize_task_status(status)
    if normalized in {STATUS_COMPLETED, STATUS_ARCHIVED}:
        return "green"
    if normalized == STATUS_IN_PROGRESS:
        return "yellow"
    if normalized == STATUS_BLOCKED:
        return "red"
    return "cyan"


def _feature_items(plan: FeaturePlan) -> list[dict]:
    items = [
        {"kind": "view", "id": "overview", "label": "Overview"},
        {"kind": "view", "id": "heatmap", "label": "Tool Heatmap"},
        {"kind": "view", "id": "history", "label": "History Browser"},
    ]
    for task in plan.tasks:
        items.append({"kind": "task", "task": task})
    return items


def _header(state: GuiState) -> Panel:
    breadcrumb = ["Sessions"]
    if state.selected_session:
        breadcrumb.append(state.selected_session)
    if isinstance(state.selected_feature, dict):
        breadcrumb.append(str(state.selected_feature.get("feature_id", "feature")))
    breadcrumb.append(state.screen)

    return Panel(
        Text.from_markup(
            f"[bold cyan]μCLI Functional GUI[/bold cyan] • [green]{datetime.now().strftime('%H:%M:%S')}[/green]\n"
            f"path: [magenta]{' > '.join(breadcrumb)}[/magenta]\n"
            "controls: ↑/↓ navigate • Enter select • Esc back • q quit"
        ),
        border_style="cyan",
    )


def _sessions_view(session_root: str, state: GuiState) -> Panel:
    state.session_names = _discover_sessions(session_root)
    state.session_index = max(0, min(state.session_index, max(0, len(state.session_names) - 1)))
    table = Table(title="Sessions", expand=True)
    table.add_column(" ", width=2)
    table.add_column("Session")
    table.add_column("Features", justify="right")
    table.add_column("Turns", justify="right")

    for i, name in enumerate(state.session_names):
        payload = _load_session_payload(session_root, name)
        features = _feature_records(payload)
        turns = len(payload.get("history", [])) if isinstance(payload.get("history"), list) else 0
        table.add_row("▶" if i == state.session_index else " ", name, str(len(features)), str(turns))

    if not state.session_names:
        return Panel("No sessions found.", title="Sessions", border_style="yellow")
    return Panel(table, border_style="green")


def _features_view(payload: dict, state: GuiState) -> Panel:
    state.feature_records = _feature_records(payload)
    state.feature_index = max(0, min(state.feature_index, max(0, len(state.feature_records) - 1)))

    table = Table(title="Features (including archived)", expand=True)
    table.add_column(" ", width=2)
    table.add_column("Feature ID")
    table.add_column("Status")
    table.add_column("Name")

    for i, feature in enumerate(state.feature_records):
        status = str(feature.get("status", "unknown"))
        style = _status_color(status)
        table.add_row(
            "▶" if i == state.feature_index else " ",
            str(feature.get("feature_id", "-")),
            f"[{style}]{status}[/{style}]",
            str(feature.get("feature_name", "-")),
        )

    if not state.feature_records:
        return Panel("No feature records for this session.", title="Features", border_style="yellow")
    return Panel(table, border_style="blue")


def _overview_panel(plan: FeaturePlan, payload: dict) -> Panel:
    total = len(plan.tasks)
    done = sum(1 for t in plan.tasks if normalize_task_status(t.status) in {STATUS_COMPLETED, STATUS_ARCHIVED})
    blocked = sum(1 for t in plan.tasks if normalize_task_status(t.status) == STATUS_BLOCKED)
    active = sum(1 for t in plan.tasks if normalize_task_status(t.status) == STATUS_IN_PROGRESS)
    tokens = int((payload.get("token_counts", {}) or {}).get("total", 0) or 0)
    lines = [
        f"feature: {plan.feature_name} ({plan.feature_id})",
        f"review_status: {plan.review_status}",
        f"tasks: {total} | done: {done} | active: {active} | blocked: {blocked}",
        f"events: {len(plan.event_log)} | tokens: {tokens:,}",
        "",
        "Recent events:",
    ]
    for event in plan.event_log[-12:]:
        ts = datetime.fromtimestamp(float(getattr(event, "created_at", 0) or 0)).strftime("%H:%M:%S")
        lines.append(f"  [{ts}] {getattr(event, 'kind', '-')} #{getattr(event, 'entity_id', '-')}")
    return Panel("\n".join(lines), title="Overview", border_style="green")


def _heatmap_panel(payload: dict) -> Panel:
    usage = _tool_usage_counts(payload)
    if not usage:
        return Panel("No tool calls in history.", title="Tool Heatmap", border_style="yellow")
    max_count = max(count for _, count in usage)
    lines = []
    for name, count in usage[:28]:
        width = 24
        filled = int(round((count / max_count) * width))
        bar = "█" * filled + "░" * (width - filled)
        lines.append(f"{name:<22} {count:>4} {bar}")
    return Panel("\n".join(lines), title="Tool Heatmap", border_style="magenta")


def _history_panel(plan: FeaturePlan, payload: dict) -> Panel:
    lines = ["Feature events:"]
    for event in plan.event_log[-25:]:
        ts = datetime.fromtimestamp(float(getattr(event, "created_at", 0) or 0)).strftime("%H:%M:%S")
        lines.append(f"  [{ts}] {getattr(event, 'kind', '-')} {getattr(event, 'entity', '-')}/{getattr(event, 'entity_id', '-')}")

    lines += ["", "Conversation (recent):"]
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    for msg in history[-20:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        snippet = ""
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if isinstance(part, dict) and part.get("type") == "text":
                snippet = str(part.get("text", "")).replace("\n", " ").strip()
                break
        if len(snippet) > 90:
            snippet = snippet[:89] + "…"
        lines.append(f"  {role:<9} | {snippet or '(non-text event)'}")

    return Panel("\n".join(lines), title="History Browser", border_style="bright_blue")


def _task_detail_panel(task, state: GuiState) -> Panel:
    lines = [
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
    lines += ["", f"blocked_reason: {task.blocked_reason or '-'}", f"notes: {task.notes or '-'}"]

    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 26]
    return Panel("\n".join(window), title=f"Task {task.id}", subtitle="↑/↓ scroll • Esc back", border_style="yellow")


def _items_view(plan: FeaturePlan, payload: dict, state: GuiState) -> Group:
    items = _feature_items(plan)
    state.item_index = max(0, min(state.item_index, len(items) - 1))

    table = Table(title="Feature Context", expand=True)
    table.add_column(" ", width=2)
    table.add_column("Type")
    table.add_column("Label")
    table.add_column("Status")

    for i, item in enumerate(items):
        if item["kind"] == "view":
            table.add_row("▶" if i == state.item_index else " ", "view", item["label"], "-")
        else:
            task = item["task"]
            st = normalize_task_status(task.status)
            table.add_row(
                "▶" if i == state.item_index else " ",
                "task",
                f"#{task.id} {task.title}",
                f"[{_status_color(st)}]{st}[/{_status_color(st)}]",
            )

    selected = items[state.item_index]
    if selected["kind"] == "view":
        if selected["id"] == "overview":
            preview = _overview_panel(plan, payload)
        elif selected["id"] == "heatmap":
            preview = _heatmap_panel(payload)
        else:
            preview = _history_panel(plan, payload)
    else:
        task = selected["task"]
        preview = Panel(
            f"Task #{task.id}\n{task.title}\n\nstatus: {normalize_task_status(task.status)}\nphase: {task.phase_id}",
            title="Task Preview",
            border_style="green",
        )

    return Group(Columns([Panel(table, border_style="cyan"), preview], expand=True, equal=False))


def _confirm_modal(state: GuiState) -> Panel:
    yes = "[bold red]QUIT[/bold red]" if state.confirm_index == 1 else "QUIT"
    no = "[bold green]CANCEL[/bold green]" if state.confirm_index == 0 else "CANCEL"
    return Panel(
        f"Exit GUI?\n\n{no}    {yes}\n\nUse ↑/↓ then Enter",
        title="Confirm Exit",
        border_style="red",
    )


def _render_gui(session_root: str, state: GuiState) -> Group:
    header = _header(state)

    if state.screen == "sessions":
        body = _sessions_view(session_root, state)
    else:
        session_name = state.selected_session or ""
        payload = _load_session_payload(session_root, session_name) if session_name else {}
        if state.screen == "features":
            body = _features_view(payload, state)
        else:
            plan = _load_feature_plan_from_record(state.selected_feature)
            if not plan:
                body = Panel("Selected feature metadata cannot be loaded.", border_style="red")
            elif state.screen == "items":
                body = _items_view(plan, payload, state)
            elif state.screen == "task_detail":
                task_item = _feature_items(plan)[state.item_index]
                body = _task_detail_panel(task_item["task"], state)
            elif state.screen == "overview":
                body = _overview_panel(plan, payload)
            elif state.screen == "heatmap":
                body = _heatmap_panel(payload)
            else:
                body = _history_panel(plan, payload)

    if state.confirm_quit:
        body = Group(body, _confirm_modal(state))

    return Group(header, body)


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
        ready, _, _ = select.select([sys.stdin], [], [], 0.01)
        while ready:
            seq += sys.stdin.read(1)
            ready, _, _ = select.select([sys.stdin], [], [], 0.01)
        return seq


def _handle_key(state: GuiState, key: str, session_root: str) -> GuiState:
    up_keys = {"\x1b[A", "\x1bOA"}
    down_keys = {"\x1b[B", "\x1bOB"}

    if key in {"q", "Q"} and not state.confirm_quit:
        state.confirm_quit = True
        state.confirm_index = 0
        return state

    if state.confirm_quit:
        if key in {"\x1b", "b"}:
            state.confirm_quit = False
            return state
        if key in up_keys | down_keys:
            state.confirm_index = 1 - state.confirm_index
            return state
        if key in {"\n", "\r"}:
            if state.confirm_index == 1:
                state.should_exit = True
            else:
                state.confirm_quit = False
            return state
        return state

    if key in {"\x1b", "b"}:
        if state.screen == "task_detail":
            state.screen = "items"
            state.detail_offset = 0
        elif state.screen in {"overview", "heatmap", "history"}:
            state.screen = "items"
        elif state.screen == "items":
            state.screen = "features"
        elif state.screen == "features":
            state.screen = "sessions"
            state.selected_feature = None
        return state

    if state.screen == "sessions":
        state.session_names = _discover_sessions(session_root)
        if key in down_keys:
            state.session_index = min(max(0, len(state.session_names) - 1), state.session_index + 1)
        elif key in up_keys:
            state.session_index = max(0, state.session_index - 1)
        elif key in {"\n", "\r"} and state.session_names:
            state.selected_session = state.session_names[state.session_index]
            state.screen = "features"
            state.feature_index = 0
        return state

    if state.screen == "features":
        payload = _load_session_payload(session_root, state.selected_session or "")
        state.feature_records = _feature_records(payload)
        if key in down_keys:
            state.feature_index = min(max(0, len(state.feature_records) - 1), state.feature_index + 1)
        elif key in up_keys:
            state.feature_index = max(0, state.feature_index - 1)
        elif key in {"\n", "\r"} and state.feature_records:
            state.selected_feature = state.feature_records[state.feature_index]
            state.screen = "items"
            state.item_index = 0
        return state

    if state.screen == "items":
        plan = _load_feature_plan_from_record(state.selected_feature)
        if not plan:
            return state
        items = _feature_items(plan)
        if key in down_keys:
            state.item_index = min(len(items) - 1, state.item_index + 1)
        elif key in up_keys:
            state.item_index = max(0, state.item_index - 1)
        elif key in {"\n", "\r"}:
            sel = items[state.item_index]
            if sel["kind"] == "task":
                state.screen = "task_detail"
                state.detail_offset = 0
            else:
                state.screen = sel["id"]
        return state

    if state.screen == "task_detail":
        if key in down_keys:
            state.detail_offset += 1
        elif key in up_keys:
            state.detail_offset = max(0, state.detail_offset - 1)
        return state

    # overview/heatmap/history only need Esc/back
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
                state = _handle_key(state, key, session_root)
                live.update(_render_gui(session_root, state))
