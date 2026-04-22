"""Realtime read-only session watcher TUI."""

from __future__ import annotations

import json
import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DETAIL_TABS = ["overview", "chat", "features", "memory", "variables", "layers"]


@dataclass
class WatchState:
    selected_index: int = 0
    tab_index: int = 0
    in_session_view: bool = False
    detail_offset: int = 0
    detail_cursor: int = 0
    sort_key: str = "name"
    running_only: bool = False
    search_mode: bool = False
    search_query: str = ""
    should_exit: bool = False


def _truncate(value: str, max_len: int = 72) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _fmt_ts(epoch: float, *, include_date: bool = False) -> str:
    now = _local_now()
    label = now.tzname() or "local"
    fmt = f"%Y-%m-%d %H:%M:%S {label}" if include_date else f"%H:%M:%S {label}"
    return datetime.fromtimestamp(float(epoch or 0), tz=now.tzinfo).strftime(fmt)


def _extract_last_activity(history: list[dict]) -> str:
    if not isinstance(history, list) or not history:
        return "idle"
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type", ""))
            if ptype == "tool_call":
                return f"{role}: tool_call({part.get('tool_name', 'tool')})"
            if ptype == "tool_result":
                return f"{role}: tool_result({part.get('tool_name', 'tool')})"
            if ptype == "text" and str(part.get("text", "")).strip():
                return f"{role}: {_truncate(part.get('text', ''), 84)}"
    return "idle"


def _derive_feature_name(feature_state: dict | None) -> str:
    if not isinstance(feature_state, dict):
        return "-"
    plan = feature_state.get("feature_plan", {})
    if isinstance(plan, dict):
        value = str(plan.get("feature_name", "")).strip()
        if value:
            return _truncate(value, 42)
    for key in ("feature_name", "feature_id"):
        value = str(feature_state.get(key, "")).strip()
        if value:
            return _truncate(value, 42)
    return "-"


def _derive_running(payload: dict, updated_at: float, now: float) -> tuple[bool, str]:
    age = now - float(updated_at or 0)
    if age <= 45:
        return True, "recent_write"
    variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
    if isinstance(variables, dict) and bool(variables.get("loop_active", False)):
        return True, "loop_active"
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    if isinstance(feature_state, dict):
        status = str(feature_state.get("status", "")).lower()
        if status in {"running", "in_progress", "awaiting_input", "awaiting_approval", "blocked"}:
            return True, f"feature:{status}"
    history = payload.get("history", []) if isinstance(payload, dict) else []
    if isinstance(history, list) and history:
        last = history[-1] if isinstance(history[-1], dict) else {}
        if str(last.get("role", "")) == "assistant":
            parts = last.get("parts", [])
            if isinstance(parts, list) and any(
                isinstance(part, dict) and part.get("type") == "tool_call" for part in parts
            ):
                return True, "assistant_tool_call"
    return False, "idle"


def _derive_layers(payload: dict, history: list[dict]) -> list[dict]:
    folder_context = payload.get("folder_context", {}) if isinstance(payload, dict) else {}
    conversation_summary = str(payload.get("conversation_summary", "") or "")
    scratch = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    tool_parts = []
    for msg in history[-20:]:
        for part in msg.get("parts", []) if isinstance(msg, dict) else []:
            if isinstance(part, dict) and part.get("type") in ("tool_call", "tool_result"):
                tool_parts.append(part)
    current_turn = json.dumps(history[-1], default=str) if history else ""
    l3 = json.dumps(
        {
            "feature_state": feature_state if isinstance(feature_state, dict) else {},
            "scratchpad_entries": len(scratch.get("entries", []))
            if isinstance(scratch, dict)
            else 0,
        },
        default=str,
    )
    return [
        {"layer": "L1", "name": "Workspace map", "size": len(folder_context.get("folders", [])) + len(folder_context.get("files", [])) if isinstance(folder_context, dict) else 0},
        {"layer": "L2", "name": "Conversation summary", "size": len(conversation_summary)},
        {"layer": "L3", "name": "Active goal", "size": len(l3)},
        {"layer": "L4", "name": "Recent tool activity", "size": len(json.dumps(tool_parts, default=str))},
        {"layer": "L5", "name": "Current turn", "size": len(current_turn)},
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
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

        payload = raw if isinstance(raw, dict) else {"history": raw if isinstance(raw, list) else []}
        history = payload.get("history", []) if isinstance(payload, dict) else []
        updated_at = float(os.path.getmtime(session_path))
        running, running_reason = _derive_running(payload, updated_at, now)
        feature_state = payload.get("feature_state")
        token_counts = payload.get("token_counts", {}) if isinstance(payload, dict) else {}
        provider_config = payload.get("provider_config", {}) if isinstance(payload, dict) else {}
        snapshots.append(
            {
                "name": name,
                "path": session_path,
                "updated_at": updated_at,
                "running": running,
                "running_reason": running_reason,
                "history_length": len(history) if isinstance(history, list) else 0,
                "agent_mode": str(payload.get("variables", {}).get("agent_mode", "default")),
                "feature": _derive_feature_name(feature_state),
                "feature_status": str(feature_state.get("status", "-")) if isinstance(feature_state, dict) else "-",
                "tokens": int(token_counts.get("total", 0) or 0),
                "provider": str(provider_config.get("provider", "-") or "-"),
                "model": str(provider_config.get("model", "-") or "-"),
                "activity": _extract_last_activity(history if isinstance(history, list) else []),
                "layers": _derive_layers(payload, history if isinstance(history, list) else []),
                "payload": payload,
            }
        )
    return snapshots


def _detail_lines(snapshot: dict, tab: str) -> list[str]:
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    history = payload.get("history", []) if isinstance(payload, dict) else []
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    if tab == "overview":
        return [
            f"session: {snapshot.get('name')}",
            f"updated: {_fmt_ts(snapshot.get('updated_at', 0), include_date=True)}",
            f"state: {'active' if snapshot.get('running') else 'idle'} ({snapshot.get('running_reason', '-')})",
            f"mode: {snapshot.get('agent_mode', '-')}",
            f"provider/model: {snapshot.get('provider', '-')}/{snapshot.get('model', '-')}",
            f"history turns: {snapshot.get('history_length', 0)}",
            f"tokens: {snapshot.get('tokens', 0)}",
            f"last activity: {snapshot.get('activity', '-')}",
        ]
    if tab == "chat":
        lines = [f"history entries: {len(history) if isinstance(history, list) else 0}"]
        for idx, msg in enumerate(history if isinstance(history, list) else []):
            role = str(msg.get("role", "unknown"))
            snippet = "no parts"
            parts = msg.get("parts", [])
            if isinstance(parts, list) and parts and isinstance(parts[0], dict):
                part = parts[0]
                if part.get("type") == "text":
                    snippet = _truncate(part.get("text", ""), 120)
                else:
                    snippet = _truncate(json.dumps(part, default=str), 120)
            lines.append(f"{idx:>4} | {role:<10} | {snippet}")
        return lines
    if tab == "features":
        lines = ["feature state:"]
        if not isinstance(feature_state, dict):
            lines.append("  (none)")
            return lines
        for key in ("feature_id", "feature_name", "status", "directory", "metadata_path"):
            if key in feature_state:
                lines.append(f"  {key}: {_truncate(json.dumps(feature_state.get(key), default=str), 130)}")
        phases = ((feature_state.get("feature_plan") or {}).get("phases", [])) if isinstance(feature_state.get("feature_plan"), dict) else []
        if isinstance(phases, list):
            lines.append(f"  phases: {len(phases)}")
        return lines
    if tab == "memory":
        lines = ["task memory:"]
        task_memory = payload.get("task_memory", {}) if isinstance(payload, dict) else {}
        for entry in task_memory.get("entries", []) if isinstance(task_memory, dict) else []:
            if isinstance(entry, dict):
                lines.append(f"  #{entry.get('id', '?')} {_truncate(entry.get('content', ''), 120)}")
        lines.append("")
        lines.append("scratchpad:")
        scratch = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}
        for entry in scratch.get("entries", []) if isinstance(scratch, dict) else []:
            if isinstance(entry, dict):
                lines.append(f"  #{entry.get('id', '?')} {_truncate(entry.get('content', ''), 120)}")
        if len(lines) == 3:
            lines.append("  (none)")
        return lines
    if tab == "variables":
        lines = ["variables:"]
        variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
        if not isinstance(variables, dict) or not variables:
            return lines + ["  (none)"]
        for key in sorted(variables.keys()):
            lines.append(f"  {key}: {json.dumps(variables.get(key), default=str)}")
        return lines
    # layers
    lines = ["context layers:"]
    for layer in snapshot.get("layers", []):
        lines.append(f"  {layer.get('layer')}: {layer.get('name')} size={layer.get('size')}")
    return lines


def _render_detail(snapshot: dict, state: WatchState) -> Panel:
    tab = DETAIL_TABS[state.tab_index % len(DETAIL_TABS)]
    lines = _detail_lines(snapshot, tab)
    if state.search_query:
        q = state.search_query.lower()
        lines = [line for line in lines if q in line.lower()] or [f"No matches for '{state.search_query}'."]
    state.detail_cursor = max(0, min(state.detail_cursor, max(0, len(lines) - 1)))
    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 22]
    out = []
    for idx, line in enumerate(window, start=start):
        out.append(f"{'▶' if idx == state.detail_cursor else ' '} {idx:>4} {line}")
    return Panel(
        "\n".join(out) if out else "(empty)",
        title=f"{snapshot.get('name', '-')}: {tab}",
        subtitle=f"lines {start + 1}-{start + len(window)}",
        border_style="cyan",
    )


def _handle_key(state: WatchState, key: str, total_sessions: int) -> WatchState:
    if state.search_mode:
        if key in ("\n", "\r"):
            state.search_mode = False
            state.detail_offset = 0
            state.detail_cursor = 0
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
    if key in ("/",):
        state.search_mode = True
        state.search_query = ""
        return state
    if key in ("c",):
        state.search_query = ""
        return state
    if key in ("\n", "\r"):
        state.in_session_view = True
        state.detail_offset = 0
        state.detail_cursor = 0
        return state
    if key in ("\x1b", "b"):
        state.in_session_view = False
        return state
    if key in ("s",):
        order = ["name", "updated", "tokens"]
        idx = order.index(state.sort_key) if state.sort_key in order else 0
        state.sort_key = order[(idx + 1) % len(order)]
        return state
    if key in ("r",):
        state.running_only = not state.running_only
        state.selected_index = 0
        return state
    if key in ("\x1b[A", "k"):
        if state.in_session_view:
            state.detail_cursor = max(0, state.detail_cursor - 1)
            state.detail_offset = min(state.detail_offset, state.detail_cursor)
        else:
            state.selected_index = max(0, state.selected_index - 1)
        return state
    if key in ("\x1b[B", "j"):
        if state.in_session_view:
            state.detail_cursor += 1
            if state.detail_cursor >= state.detail_offset + 22:
                state.detail_offset += 1
        else:
            state.selected_index = min(max(0, total_sessions - 1), state.selected_index + 1)
        return state
    if key in ("\x1b[C", "l"):
        state.tab_index = (state.tab_index + 1) % len(DETAIL_TABS)
        state.detail_offset = 0
        state.detail_cursor = 0
        return state
    if key in ("\x1b[D", "h"):
        state.tab_index = (state.tab_index - 1) % len(DETAIL_TABS)
        state.detail_offset = 0
        state.detail_cursor = 0
        return state
    if key in ("n", "\x1b[6~"):
        state.detail_offset += 8
        state.detail_cursor = max(state.detail_cursor, state.detail_offset)
        return state
    if key in ("p", "\x1b[5~"):
        state.detail_offset = max(0, state.detail_offset - 8)
        state.detail_cursor = max(0, min(state.detail_cursor, state.detail_offset + 21))
        return state
    return state


def _sort_snapshots(snapshots: list[dict], sort_key: str) -> list[dict]:
    if sort_key == "updated":
        return sorted(snapshots, key=lambda s: float(s.get("updated_at", 0) or 0), reverse=True)
    if sort_key == "tokens":
        return sorted(snapshots, key=lambda s: int(s.get("tokens", 0) or 0), reverse=True)
    return sorted(snapshots, key=lambda s: str(s.get("name", "")).lower())


def _render_watch(session_root: str, refresh_seconds: float, state: WatchState, snapshots: list[dict] | None = None) -> Group:
    snapshots = snapshots if isinstance(snapshots, list) else load_session_snapshots(session_root)
    snapshots = _sort_snapshots(
        [s for s in snapshots if (s.get("running") or not state.running_only)],
        state.sort_key,
    )
    now = _local_now()
    now_text = now.strftime(f"%Y-%m-%d %H:%M:%S {now.tzname() or 'local'}")

    if not snapshots:
        return Group(
            Panel(
                "No sessions found.\nStart MuCLI normally to create session state.",
                title="μCLI Watch",
                border_style="yellow",
            ),
            Text(f"path: {session_root}"),
        )

    state.selected_index = min(max(0, state.selected_index), len(snapshots) - 1)
    selected = snapshots[state.selected_index]

    table = Table(title="Live Session Reporting", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column(" ")
    table.add_column("Session", style="bold cyan")
    table.add_column("State")
    table.add_column("Reason", style="dim")
    table.add_column("Mode", style="magenta")
    table.add_column("Feature", style="yellow")
    table.add_column("Turns", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Last Activity")
    table.add_column("Updated")
    for i, row in enumerate(snapshots):
        table.add_row(
            "▶" if i == state.selected_index else " ",
            str(row.get("name", "-")),
            "[green]active[/green]" if row.get("running") else "[dim]idle[/dim]",
            str(row.get("running_reason", "-")),
            str(row.get("agent_mode", "-")),
            str(row.get("feature", "-")),
            str(row.get("history_length", 0)),
            f"{int(row.get('tokens', 0)):,}",
            _truncate(row.get("activity", "-"), 72),
            _fmt_ts(row.get("updated_at", 0)),
        )

    running = sum(1 for s in snapshots if s.get("running"))
    blocked = sum(1 for s in snapshots if str(s.get("feature_status", "")).lower() == "blocked")
    cards = Columns(
        [
            Panel(f"[bold]{len(snapshots)}[/bold]\nSessions", border_style="cyan"),
            Panel(f"[bold green]{running}[/bold green]\nActive", border_style="green"),
            Panel(f"[bold red]{blocked}[/bold red]\nBlocked features", border_style="red"),
            Panel(f"[bold magenta]{sum(int(s.get('tokens', 0) or 0) for s in snapshots):,}[/bold magenta]\nTotal tokens", border_style="magenta"),
        ],
        expand=True,
    )

    tabs = " | ".join(
        f"[bold cyan]{tab}[/bold cyan]" if idx == state.tab_index else tab
        for idx, tab in enumerate(DETAIL_TABS)
    )
    header = Panel(
        Text.from_markup(
            f"[bold cyan]μCLI Watch[/bold cyan] · live reporting\n"
            f"now: [cyan]{now_text}[/cyan] · refresh: [cyan]{refresh_seconds:.1f}s[/cyan] · sort: [cyan]{state.sort_key}[/cyan] · running-only: [cyan]{state.running_only}[/cyan]\n"
            "keys: j/k select · Enter open · b/Esc back · h/l tabs · / search · c clear · s sort · r running filter · q quit\n"
            f"tabs: {tabs}"
        ),
        border_style="cyan",
    )
    footer = Text(f"path: {session_root}", style="dim")
    if state.in_session_view:
        return Group(header, cards, _render_detail(selected, state), footer)
    return Group(header, cards, table, footer)


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


def run_watch_mode(session_root: str, refresh_seconds: float = 1.5) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.5))
    state = WatchState()
    snapshots = load_session_snapshots(session_root)
    next_refresh = 0.0

    with _KeyReader() as reader, Live(
        _render_watch(session_root, refresh_seconds, state, snapshots),
        refresh_per_second=8,
        screen=True,
    ) as live:
        while not state.should_exit:
            now = time.time()
            if now >= next_refresh:
                snapshots = load_session_snapshots(session_root)
                next_refresh = now + refresh_seconds
            key = reader.read_key(timeout=0.05)
            if key:
                state = _handle_key(state, key, len(snapshots))
                if state.should_exit:
                    break
            live.update(_render_watch(session_root, refresh_seconds, state, snapshots))
