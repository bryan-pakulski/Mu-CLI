"""Read-only realtime session watcher for Mu-CLI."""

from __future__ import annotations

import json
import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

RUNNING_THRESHOLD_SECONDS = 8.0
DETAIL_TABS = ["board", "chat", "memory", "layers", "metadata", "variables", "features"]


@dataclass
class WatchState:
    selected_index: int = 0
    tab_index: int = 0
    detail_offset: int = 0
    in_session_view: bool = False
    search_mode: bool = False
    search_query: str = ""
    detail_cursor: int = 0
    sort_key: str = "name"
    running_only: bool = False
    help_overlay: bool = False
    expand_focused: bool = False
    focused_offset: int = 0
    should_exit: bool = False


def _truncate(value: str, limit: int = 72) -> str:
    raw = str(value or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _fmt_time(epoch: float, *, with_date: bool = False) -> str:
    fmt = "%Y-%m-%d %H:%M:%S UTC" if with_date else "%H:%M:%S"
    return datetime.fromtimestamp(float(epoch or 0), tz=timezone.utc).strftime(fmt)


def _sparkline(values: list[int]) -> str:
    ticks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    low = min(values)
    high = max(values)
    if high == low:
        return ticks[0] * len(values)
    out = []
    for val in values:
        idx = int((val - low) / max(1, (high - low)) * (len(ticks) - 1))
        out.append(ticks[max(0, min(idx, len(ticks) - 1))])
    return "".join(out)


def _status_style(status: str) -> str:
    mapping = {
        "running": "bold green",
        "idle": "dim",
        "in_progress": "yellow",
        "blocked": "bold red",
        "completed": "green",
        "not_started": "dim",
    }
    return mapping.get(str(status or ""), "white")


def _is_session_active(payload: dict, updated_at: float, now: float) -> bool:
    if (now - float(updated_at or 0)) <= 45.0:
        return True

    variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
    if isinstance(variables, dict) and bool(variables.get("loop_active", False)):
        return True

    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    if isinstance(feature_state, dict):
        active_statuses = {
            "running",
            "in_progress",
            "awaiting_input",
            "awaiting_approval",
            "blocked",
        }
        status = str(feature_state.get("status", "") or "").strip().lower()
        if status in active_statuses:
            return True

    history = payload.get("history", []) if isinstance(payload, dict) else []
    if isinstance(history, list) and history:
        # If the latest assistant message still contains a tool_call, treat as active.
        last = history[-1] if isinstance(history[-1], dict) else {}
        if str(last.get("role", "") or "").strip() == "assistant":
            parts = last.get("parts", [])
            if isinstance(parts, list) and any(
                isinstance(part, dict) and part.get("type") == "tool_call"
                for part in parts
            ):
                return True
    return False


def _extract_last_activity(history: list[dict]) -> str:
    if not isinstance(history, list) or not history:
        return "idle"

    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "").strip() or "unknown"
        parts = message.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "") or "").strip()
            if part_type == "tool_call":
                tool_name = str(part.get("tool_name", "") or "").strip() or "tool"
                return f"{role}: tool_call({tool_name})"
            if part_type == "tool_result":
                tool_name = str(part.get("tool_name", "") or "").strip() or "tool"
                return f"{role}: tool_result({tool_name})"
            if part_type == "text":
                text = str(part.get("text", "") or "").strip()
                if text:
                    return f"{role}: {_truncate(text, 80)}"
    return "idle"


def _extract_feature_label(feature_state: dict | None) -> str:
    if not isinstance(feature_state, dict):
        return "-"
    plan = feature_state.get("feature_plan")
    feature_name = ""
    if isinstance(plan, dict):
        feature_name = str(plan.get("feature_name", "") or "").strip()
    if not feature_name:
        feature_name = str(feature_state.get("feature_name", "") or "").strip()
    if not feature_name:
        feature_name = str(feature_state.get("feature_id", "") or "").strip()
    if not feature_name:
        return "-"
    return _truncate(feature_name, 42)


def _build_layers(payload: dict, history: list[dict]) -> list[dict]:
    folder_context = payload.get("folder_context", {}) if isinstance(payload, dict) else {}
    summary_text = str(payload.get("conversation_summary", "") or "")
    scratchpad = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    tool_parts = []
    for message in history[-20:]:
        for part in (message.get("parts", []) if isinstance(message, dict) else []):
            if isinstance(part, dict) and part.get("type") in ("tool_call", "tool_result"):
                tool_parts.append(part)
    tool_blob = json.dumps(tool_parts, default=str)
    current_turn = json.dumps(history[-1], default=str) if history else ""
    l3_goal = json.dumps(
        {
            "feature_state": feature_state if isinstance(feature_state, dict) else {},
            "scratchpad_entries": len(
                (scratchpad.get("entries", []) if isinstance(scratchpad, dict) else [])
            ),
        },
        default=str,
    )
    return [
        {
            "layer": "L1",
            "name": "Workspace map",
            "current": len(folder_context.get("folders", []))
            + len(folder_context.get("files", []))
            if isinstance(folder_context, dict)
            else 0,
            "description": "Tracked folders/files currently attached to session context.",
        },
        {
            "layer": "L2",
            "name": "Conversation summary",
            "current": len(summary_text),
            "description": "Long-horizon summary text currently stored.",
        },
        {
            "layer": "L3",
            "name": "Active goal",
            "current": len(l3_goal),
            "description": "Feature progress + scratchpad signal for current goal.",
        },
        {
            "layer": "L4",
            "name": "Recent tool activity",
            "current": len(tool_blob),
            "description": "Compressed recent tool call/result data.",
        },
        {
            "layer": "L5",
            "name": "Current turn",
            "current": len(current_turn),
            "description": "Most recent request/response payload.",
        },
    ]


def load_session_snapshots(session_root: str) -> list[dict]:
    snapshots: list[dict] = []
    if not os.path.isdir(session_root):
        return snapshots

    now = time.time()
    for name in sorted(os.listdir(session_root)):
        session_path = os.path.join(session_root, name, "session.json")
        if not os.path.isfile(session_path):
            continue

        try:
            with open(session_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

        if isinstance(data, list):
            payload = {"history": data}
        elif isinstance(data, dict):
            payload = data
        else:
            payload = {"history": []}

        history = payload.get("history", [])
        variables = payload.get("variables", {})
        feature_state = payload.get("feature_state")
        token_counts = payload.get("token_counts", {})
        provider_config = payload.get("provider_config", {})
        updated_at = float(os.path.getmtime(session_path))
        running = _is_session_active(payload, updated_at, now)

        snapshots.append(
            {
                "name": name,
                "path": session_path,
                "updated_at": updated_at,
                "running": running,
                "running_label": "running" if running else "idle",
                "history_length": len(history) if isinstance(history, list) else 0,
                "agent_mode": str(variables.get("agent_mode", "default") or "default"),
                "feature": _extract_feature_label(feature_state),
                "feature_status": (
                    str(feature_state.get("status", "-") or "-")
                    if isinstance(feature_state, dict)
                    else "-"
                ),
                "tokens": int(token_counts.get("total", 0) or 0),
                "model": str(provider_config.get("model", "-") or "-"),
                "provider": str(provider_config.get("provider", "-") or "-"),
                "activity": _extract_last_activity(history if isinstance(history, list) else []),
                "payload": payload,
                "layers": _build_layers(payload, history if isinstance(history, list) else []),
            }
        )

    snapshots.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return snapshots


def _detail_lines(snapshot: dict, tab: str) -> list[str]:
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    history = payload.get("history", []) if isinstance(payload, dict) else []
    feature = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    memory = payload.get("task_memory", {}) if isinstance(payload, dict) else {}
    scratch = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}

    if tab == "chat":
        lines = [f"History entries: {len(history) if isinstance(history, list) else 0}"]
        if isinstance(history, list):
            for idx, msg in enumerate(history):
                role = str(msg.get("role", "unknown"))
                parts = msg.get("parts", [])
                summary = "no parts"
                if isinstance(parts, list) and parts:
                    first = parts[0]
                    if isinstance(first, dict):
                        part_type = str(first.get("type", "unknown"))
                        if part_type == "text":
                            summary = _truncate(str(first.get("text", "")), 100)
                        else:
                            summary = _truncate(json.dumps(first, default=str), 100)
                lines.append(f"{idx:>4} | {role:<9} | {summary}")
        return lines

    if tab == "memory":
        lines = ["Task memory entries:"]
        entries = memory.get("entries", []) if isinstance(memory, dict) else []
        if not entries:
            lines.append("  (none)")
        for entry in entries:
            if isinstance(entry, dict):
                tags = ",".join(entry.get("tags", []) or [])
                lines.append(
                    f"  #{entry.get('id', '?')} [{tags or '-'}] "
                    f"{_truncate(entry.get('content', ''), 110)}"
                )

        lines.append("")
        lines.append("Scratchpad entries:")
        scratch_entries = scratch.get("entries", []) if isinstance(scratch, dict) else []
        if not scratch_entries:
            lines.append("  (none)")
        for entry in scratch_entries:
            if isinstance(entry, dict):
                lines.append(
                    f"  #{entry.get('id', '?')} {_truncate(entry.get('content', ''), 110)}"
                )
        return lines

    if tab == "layers":
        lines = ["Context layers (L1→L5):"]
        for layer in snapshot.get("layers", []):
            lines.append(
                f"  {layer.get('layer')}: {layer.get('name')} | size={layer.get('current', 0)}"
            )
            lines.append(f"      {layer.get('description', '')}")
        return lines

    if tab == "features":
        lines = ["Feature state:"]
        if not isinstance(feature, dict):
            lines.append("  (none)")
            return lines
        for key in (
            "feature_id",
            "feature_name",
            "status",
            "directory",
            "metadata_path",
            "next_phase",
            "next_task",
            "updated_at",
        ):
            if key in feature:
                lines.append(f"  {key}: {_truncate(json.dumps(feature.get(key), default=str), 120)}")
        plan = feature.get("feature_plan", {})
        if isinstance(plan, dict):
            lines.append("")
            lines.append("  feature_plan:")
            for k in ("feature_name", "approved", "review_status"):
                if k in plan:
                    lines.append(f"    {k}: {_truncate(str(plan.get(k)), 120)}")
            phases = plan.get("phases", [])
            if isinstance(phases, list):
                lines.append(f"    phases: {len(phases)}")
        return lines

    if tab == "variables":
        lines = ["Session variables:"]
        variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
        if not isinstance(variables, dict) or not variables:
            lines.append("  (none)")
            return lines
        for key in sorted(variables.keys()):
            value = variables.get(key)
            rendered = json.dumps(value, default=str)
            lines.append(f"  {key}: {rendered}")
        return lines

    # metadata tab
    lines = ["Session metadata:"]
    variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
    folder_context = payload.get("folder_context", {}) if isinstance(payload, dict) else {}
    lines.extend(
        [
            f"  session: {snapshot.get('name', '-')}",
            f"  path: {snapshot.get('path', '-')}",
            f"  updated_at: {_fmt_time(snapshot.get('updated_at', 0), with_date=True)}",
            f"  status: {snapshot.get('running_label', 'idle')}",
            f"  provider/model: {snapshot.get('provider', '-')}/{snapshot.get('model', '-')}",
            f"  turns: {snapshot.get('history_length', 0)}",
            f"  tokens: {snapshot.get('tokens', 0)}",
            f"  mode: {variables.get('agent_mode', 'default') if isinstance(variables, dict) else 'default'}",
            f"  yolo: {variables.get('yolo', False) if isinstance(variables, dict) else False}",
            f"  workspace folders: {len(folder_context.get('folders', [])) if isinstance(folder_context, dict) else 0}",
            f"  workspace files: {len(folder_context.get('files', [])) if isinstance(folder_context, dict) else 0}",
        ]
    )
    return lines


def _build_feature_board(snapshot: dict):
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    feature = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    plan = feature.get("feature_plan", {}) if isinstance(feature, dict) else {}
    tasks = plan.get("phases", []) if isinstance(plan, dict) else []

    table = Table(title="Feature Board (Jira-style)", expand=True, row_styles=["", "dim"])
    statuses = ["not_started", "in_progress", "blocked", "completed"]
    for status in statuses:
        table.add_column(status, style=_status_style(status))

    by_status = {status: [] for status in statuses}
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status", "not_started") or "not_started")
        key = status if status in by_status else "not_started"
        verified = len(task.get("verified_exit_criteria", []) or [])
        total_exit = len(task.get("exit_criteria", []) or [])
        percent = int((verified / max(1, total_exit)) * 100)
        bar_count = min(10, int(round(percent / 10)))
        meter = "█" * bar_count + "░" * (10 - bar_count)
        card = (
            f"#{task.get('number', task.get('id', '?'))} {task.get('title', 'untitled')}\n"
            f"{meter} {percent:>3}% ({verified}/{max(total_exit, 1)})"
        )
        by_status[key].append(card)

    rows = max(1, max((len(items) for items in by_status.values()), default=1))
    for idx in range(rows):
        table.add_row(*[by_status[s][idx] if idx < len(by_status[s]) else "" for s in statuses])
    return table


def _render_detail(snapshot: dict, state: WatchState) -> Panel:
    active_tab = DETAIL_TABS[state.tab_index % len(DETAIL_TABS)]
    if active_tab == "board":
        return Panel(_build_feature_board(snapshot), title=f"Session Detail — {snapshot.get('name', '-')}", subtitle="Tab: board")

    lines = _detail_lines(snapshot, active_tab)
    if state.search_query:
        query = state.search_query.lower()
        lines = [line for line in lines if query in line.lower()]
        if not lines:
            lines = [f"No matches for '{state.search_query}'."]
    if lines:
        state.detail_cursor = max(0, min(state.detail_cursor, len(lines) - 1))
    else:
        state.detail_cursor = 0
    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 18]
    search_suffix = f" | search: {state.search_query}" if state.search_query else ""
    subtitle = (
        f"Tab: {active_tab} | lines {start + 1}-{start + len(window)}{search_suffix}"
        if lines
        else f"Tab: {active_tab}{search_suffix}"
    )
    rendered_lines = []
    for idx, line in enumerate(window, start=start):
        prefix = "▶" if idx == state.detail_cursor else " "
        rendered_lines.append(f"{prefix} {idx:>4} {line}")
    selected = lines[state.detail_cursor] if lines else "(empty)"
    if state.expand_focused:
        expanded_text = str(selected)
        start_idx = max(0, state.focused_offset)
        expanded_window = expanded_text[start_idx : start_idx + 1200]
        selected_panel = Panel(
            expanded_window or "(empty)",
            title=f"Focused Item (expanded, offset {start_idx})",
            border_style="magenta",
        )
    else:
        selected_panel = Panel(
            _truncate(selected, 500),
            title="Focused Item",
            border_style="magenta",
        )
    body = Group(
        Panel("\n".join(rendered_lines) if rendered_lines else "(empty)", title="Entries", border_style="cyan"),
        selected_panel,
    )
    return Panel(body, title=f"Session Detail — {snapshot.get('name', '-')}", subtitle=subtitle, box=box.ROUNDED)


def _handle_key(state: WatchState, key: str, total_sessions: int) -> WatchState:
    if state.search_mode:
        if key in ("\r", "\n"):
            state.search_mode = False
            state.detail_offset = 0
            return state
        if key in ("\x1b",):
            state.search_mode = False
            state.search_query = ""
            return state
        if key in ("\x7f", "\b"):
            state.search_query = state.search_query[:-1]
            return state
        if key and len(key) == 1 and key.isprintable():
            state.search_query += key
        return state

    if key in ("q", "\x03"):
        state.should_exit = True
        return state
    if key in ("?",):
        state.help_overlay = not state.help_overlay
        return state
    if key in ("e",):
        state.expand_focused = not state.expand_focused
        state.focused_offset = 0
        return state
    if key in ("s",):
        ordering = ["updated", "tokens", "name"]
        idx = ordering.index(state.sort_key) if state.sort_key in ordering else 0
        state.sort_key = ordering[(idx + 1) % len(ordering)]
        return state
    if key in ("r",):
        state.running_only = not state.running_only
        state.selected_index = 0
        state.detail_offset = 0
        return state
    if key in ("/",):
        state.search_mode = True
        state.search_query = ""
        return state
    if key in ("c",):
        state.search_query = ""
        state.search_mode = False
        return state
    if key in ("\r", "\n"):
        state.in_session_view = True
        state.detail_offset = 0
        state.detail_cursor = 0
        return state
    if key in ("\x1b", "b"):
        state.in_session_view = False
        state.detail_offset = 0
        return state
    if key in ("\x1b[A", "k"):
        if state.in_session_view:
            state.detail_cursor = max(0, state.detail_cursor - 1)
            state.detail_offset = max(0, min(state.detail_offset, state.detail_cursor))
        else:
            state.selected_index = max(0, state.selected_index - 1)
            state.detail_offset = 0
        return state
    if key in ("\x1b[B", "j"):
        if state.in_session_view:
            state.detail_cursor += 1
            if state.detail_cursor >= state.detail_offset + 18:
                state.detail_offset += 1
        else:
            state.selected_index = min(max(0, total_sessions - 1), state.selected_index + 1)
            state.detail_offset = 0
        return state
    if key in ("\x1b[C", "l"):
        state.tab_index = (state.tab_index + 1) % len(DETAIL_TABS)
        state.detail_offset = 0
        return state
    if key in ("\x1b[D", "h"):
        state.tab_index = (state.tab_index - 1) % len(DETAIL_TABS)
        state.detail_offset = 0
        return state
    if key in ("n", "\x1b[6~"):
        if state.expand_focused:
            state.focused_offset += 300
        else:
            state.detail_offset += 8
            state.detail_cursor = max(state.detail_cursor, state.detail_offset)
        return state
    if key in ("p", "\x1b[5~"):
        if state.expand_focused:
            state.focused_offset = max(0, state.focused_offset - 300)
        else:
            state.detail_offset = max(0, state.detail_offset - 8)
            state.detail_cursor = max(0, min(state.detail_cursor, state.detail_offset + 17))
        return state
    return state


def _render_watch(
    session_root: str,
    refresh_seconds: float,
    state: WatchState,
    snapshots: list[dict] | None = None,
) -> Group:
    snapshots = snapshots if isinstance(snapshots, list) else load_session_snapshots(session_root)
    if state.running_only:
        snapshots = [item for item in snapshots if item.get("running")]
    if state.sort_key == "updated":
        snapshots = sorted(
            snapshots,
            key=lambda item: float(item.get("updated_at", 0) or 0),
            reverse=True,
        )
    elif state.sort_key == "tokens":
        snapshots = sorted(snapshots, key=lambda item: int(item.get("tokens", 0) or 0), reverse=True)
    else:
        snapshots = sorted(
            snapshots, key=lambda item: str(item.get("name", "")).lower()
        )
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not snapshots:
        return Group(
            Panel(
                Text(
                    "No saved sessions yet.\nStart Mu-CLI normally and create a session.",
                    justify="center",
                ),
                title="μCLI Watch",
                border_style="cyan",
            ),
            Text(f"Session path: {session_root}\nRefresh: {refresh_seconds:.1f}s"),
        )

    state.selected_index = min(state.selected_index, len(snapshots) - 1)
    current = snapshots[state.selected_index]
    state.detail_offset = max(0, state.detail_offset)

    table = Table(title="Session Activity", expand=True, box=box.SIMPLE_HEAVY, row_styles=["", "dim"])
    table.add_column(" ", no_wrap=True, width=2)
    table.add_column("Session", style="bold cyan", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Mode", style="magenta", no_wrap=True)
    table.add_column("Feature", style="yellow")
    table.add_column("Turns", justify="right", style="green", no_wrap=True)
    table.add_column("Tokens", justify="right", style="green", no_wrap=True)
    table.add_column("Provider/Model", style="blue")
    table.add_column("Latest Activity", style="white")
    table.add_column("Updated", style="dim", no_wrap=True)

    for idx, item in enumerate(snapshots):
        pointer = "▶" if idx == state.selected_index else " "
        status_label = "running" if item.get("running") else "idle"
        status = f"[{_status_style(status_label)}]{status_label}[/{_status_style(status_label)}]"
        table.add_row(
            pointer,
            str(item.get("name", "-")),
            status,
            str(item.get("agent_mode", "default")),
            str(item.get("feature", "-")),
            str(item.get("history_length", 0)),
            f"{int(item.get('tokens', 0)):,}",
            f"{item.get('provider', '-')}/{item.get('model', '-')}",
            _truncate(str(item.get("activity", "-")), 68),
            _fmt_time(float(item.get("updated_at", 0) or 0)),
        )

    tabs = " | ".join(
        f"[bold cyan]{name}[/bold cyan]" if i == state.tab_index else name
        for i, name in enumerate(DETAIL_TABS)
    )
    running_count = sum(1 for item in snapshots if item.get("running"))
    features_count = sum(1 for item in snapshots if item.get("feature") not in ("", "-"))
    token_total = sum(int(item.get("tokens", 0) or 0) for item in snapshots)
    spark = _sparkline([int(item.get("updated_at", 0) or 0) for item in snapshots[-12:]])
    stat_cards = Columns(
        [
            Panel(
                f"[bold cyan]{len(snapshots)}[/bold cyan]\nSessions",
                border_style="cyan",
                padding=(0, 2),
            ),
            Panel(
                f"[bold green]{running_count}[/bold green]\nRunning",
                border_style="green",
                padding=(0, 2),
            ),
            Panel(
                f"[bold yellow]{features_count}[/bold yellow]\nWith Features",
                border_style="yellow",
                padding=(0, 2),
            ),
            Panel(
                f"[bold magenta]{token_total:,}[/bold magenta]\nTotal Tokens",
                border_style="magenta",
                padding=(0, 2),
            ),
            Panel(
                f"[bold white]{spark or '—'}[/bold white]\nUpdate Pulse",
                border_style="blue",
                padding=(0, 2),
            ),
        ],
        expand=True,
    )
    mode_text = "session view" if state.in_session_view else "board list"
    search_text = (
        f"\nSearch: [yellow]{state.search_query}[/yellow]"
        if state.search_query
        else ""
    )
    if state.search_mode:
        search_text = f"\nSearch mode: [yellow]{state.search_query}[/yellow]_"
    header = Panel(
        Text.from_markup(
            f"[bold cyan]μCLI Watch[/bold cyan] ✨ [dim]read-only realtime command center[/dim]\n"
            f"Sessions: [cyan]{len(snapshots)}[/cyan] • "
            f"Refresh: [cyan]{refresh_seconds:.1f}s[/cyan] • Now: [cyan]{now_text}[/cyan]\n"
            f"Mode: [cyan]{mode_text}[/cyan] • Enter open session • Esc/b back • sort:{state.sort_key} • running-only:{state.running_only} • expand:{state.expand_focused}\n"
            f"Keys: ↑/↓ or j/k navigate • ←/→ or h/l tabs • n/p page (or focused scroll) • e expand focus • / find • c clear-find • s sort • r running-filter • ? help • q quit\n"
            f"Tabs: {tabs}{search_text}"
        ),
        border_style="bright_cyan",
        box=box.DOUBLE,
    )
    footer = Text(f"Session path: {session_root}", style="dim")
    if state.help_overlay:
        help_panel = Panel(
            "[bold]μCLI Watch Controls[/bold]\n"
            "• Enter: open selected session detail\n"
            "• Esc/b: back to session list\n"
            "• j/k or arrows: move selection/cursor\n"
            "• h/l or arrows: switch tabs\n"
            "• n/p: page detail list\n"
            "• / then type then Enter: search/filter active tab\n"
            "• c: clear search\n"
            "• e: expand/collapse focused item full content\n"
            "• s: cycle sorting (updated/tokens/name)\n"
            "• r: toggle running-only filter\n"
            "• ?: toggle this help\n"
            "• q: quit",
            border_style="yellow",
            box=box.DOUBLE_EDGE,
            title="Help Overlay",
        )
        if state.in_session_view:
            return Group(header, stat_cards, _render_detail(current, state), help_panel, footer)
        return Group(header, stat_cards, table, help_panel, footer)
    if state.in_session_view:
        return Group(header, stat_cards, _render_detail(current, state), footer)
    return Group(header, stat_cards, table, footer)


class _KeyReader:
    def __enter__(self):
        self.enabled = sys.stdin.isatty()
        self.fd = None
        self.old_settings = None
        if self.enabled:
            self.fd = sys.stdin.fileno()
            self.old_settings = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.enabled and self.fd is not None and self.old_settings is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)

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


def run_watch_mode(session_root: str, refresh_seconds: float = 1.5) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.5))
    state = WatchState()
    snapshots = load_session_snapshots(session_root)
    next_snapshot_refresh = 0.0

    with _KeyReader() as keys, Live(
        _render_watch(session_root, refresh_seconds, state, snapshots),
        refresh_per_second=8,
        screen=True,
    ) as live:
        while not state.should_exit:
            now = time.time()
            if now >= next_snapshot_refresh:
                snapshots = load_session_snapshots(session_root)
                next_snapshot_refresh = now + refresh_seconds

            key = keys.read_key(timeout=0.05)
            if key:
                state = _handle_key(state, key, len(snapshots))
                if state.should_exit:
                    break
                live.update(_render_watch(session_root, refresh_seconds, state, snapshots))
                continue
            live.update(_render_watch(session_root, refresh_seconds, state, snapshots))
