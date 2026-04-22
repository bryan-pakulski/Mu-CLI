"""MuCLI Watch — realtime analytics TUI (read-only)."""

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

DETAIL_TABS = ["dashboard", "sessions", "features", "memory", "variables", "layers", "events"]


@dataclass
class WatchState:
    selected_index: int = 0
    tab_index: int = 0
    in_detail: bool = False
    detail_offset: int = 0
    detail_cursor: int = 0
    sort_key: str = "newest"
    running_only: bool = False
    search_mode: bool = False
    search_query: str = ""
    should_exit: bool = False


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _fmt_ts(epoch: float, *, include_date: bool = False) -> str:
    now = _now_local()
    tz = now.tzname() or "local"
    fmt = f"%Y-%m-%d %H:%M:%S {tz}" if include_date else f"%H:%M:%S {tz}"
    return datetime.fromtimestamp(float(epoch or 0), tz=now.tzinfo).strftime(fmt)


def _truncate(value: str, max_len: int = 80) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _spark(values: list[int]) -> str:
    ticks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return ticks[0] * len(values)
    out = []
    for v in values:
        idx = int((v - lo) / max(1, hi - lo) * (len(ticks) - 1))
        out.append(ticks[max(0, min(idx, len(ticks) - 1))])
    return "".join(out)


def _bar(value: int, maximum: int, width: int = 14) -> str:
    maximum = max(1, int(maximum or 1))
    value = max(0, int(value or 0))
    filled = min(width, int(round((value / maximum) * width)))
    return "█" * filled + "░" * (width - filled)


def _extract_last_activity(history: list[dict]) -> str:
    if not isinstance(history, list) or not history:
        return "idle"
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown") or "unknown")
        parts = msg.get("parts", [])
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type", ""))
            if ptype == "tool_call":
                return f"{role}: tool_call({part.get('tool_name', 'tool')})"
            if ptype == "tool_result":
                return f"{role}: tool_result({part.get('tool_name', 'tool')})"
            if ptype == "text":
                txt = str(part.get("text", "")).strip()
                if txt:
                    return f"{role}: {_truncate(txt, 96)}"
    return "idle"


def _derive_active(payload: dict, updated_at: float, now: float) -> tuple[bool, str]:
    age = now - float(updated_at or 0)
    if age <= 45:
        return True, "recent_write"
    variables = payload.get("variables", {}) if isinstance(payload, dict) else {}
    if isinstance(variables, dict) and variables.get("loop_active") is True:
        return True, "loop_active"
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    if isinstance(feature_state, dict):
        status = str(feature_state.get("status", "")).lower()
        if status in {"running", "in_progress", "awaiting_input", "blocked"}:
            return True, f"feature:{status}"
    return False, "idle"


def _derive_feature_name(feature_state: dict | None) -> str:
    if not isinstance(feature_state, dict):
        return "-"
    plan = feature_state.get("feature_plan", {})
    if isinstance(plan, dict):
        val = str(plan.get("feature_name", "")).strip()
        if val:
            return _truncate(val, 44)
    for key in ("feature_name", "feature_id"):
        val = str(feature_state.get(key, "")).strip()
        if val:
            return _truncate(val, 44)
    return "-"


def _derive_layers(payload: dict, history: list[dict]) -> list[dict]:
    folder = payload.get("folder_context", {}) if isinstance(payload, dict) else {}
    summary = str(payload.get("conversation_summary", "") or "")
    scratch = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}
    feature = payload.get("feature_state", {}) if isinstance(payload, dict) else {}
    tool_parts = []
    for m in history[-20:]:
        for p in m.get("parts", []) if isinstance(m, dict) else []:
            if isinstance(p, dict) and p.get("type") in ("tool_call", "tool_result"):
                tool_parts.append(p)
    current_turn = json.dumps(history[-1], default=str) if history else ""
    l3 = json.dumps(
        {
            "feature_state": feature if isinstance(feature, dict) else {},
            "scratchpad_entries": len(scratch.get("entries", []))
            if isinstance(scratch, dict)
            else 0,
        },
        default=str,
    )
    return [
        {"layer": "L1", "name": "Workspace map", "size": len(folder.get("folders", [])) + len(folder.get("files", [])) if isinstance(folder, dict) else 0},
        {"layer": "L2", "name": "Conversation summary", "size": len(summary)},
        {"layer": "L3", "name": "Active goal", "size": len(l3)},
        {"layer": "L4", "name": "Recent tool activity", "size": len(json.dumps(tool_parts, default=str))},
        {"layer": "L5", "name": "Current turn", "size": len(current_turn)},
    ]


def load_session_snapshots(session_root: str) -> list[dict]:
    if not os.path.isdir(session_root):
        return []
    now = time.time()
    snapshots = []
    for name in os.listdir(session_root):
        path = os.path.join(session_root, name, "session.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue

        payload = data if isinstance(data, dict) else {"history": data if isinstance(data, list) else []}
        history = payload.get("history", []) if isinstance(payload, dict) else []
        updated_at = float(os.path.getmtime(path))
        active, reason = _derive_active(payload, updated_at, now)
        feature_state = payload.get("feature_state")
        token_counts = payload.get("token_counts", {}) if isinstance(payload, dict) else {}
        provider_cfg = payload.get("provider_config", {}) if isinstance(payload, dict) else {}
        snapshots.append(
            {
                "name": name,
                "path": path,
                "updated_at": updated_at,
                "active": active,
                "active_reason": reason,
                "agent_mode": str(payload.get("variables", {}).get("agent_mode", "default")),
                "feature": _derive_feature_name(feature_state),
                "feature_status": str(feature_state.get("status", "-")) if isinstance(feature_state, dict) else "-",
                "history_length": len(history) if isinstance(history, list) else 0,
                "tokens": int(token_counts.get("total", 0) or 0),
                "provider": str(provider_cfg.get("provider", "-") or "-"),
                "model": str(provider_cfg.get("model", "-") or "-"),
                "activity": _extract_last_activity(history if isinstance(history, list) else []),
                "layers": _derive_layers(payload, history if isinstance(history, list) else []),
                "payload": payload,
            }
        )
    return snapshots


def _sort_sessions(rows: list[dict], sort_key: str) -> list[dict]:
    if sort_key == "tokens":
        return sorted(rows, key=lambda r: int(r.get("tokens", 0) or 0), reverse=True)
    if sort_key == "name":
        return sorted(rows, key=lambda r: str(r.get("name", "")).lower())
    # newest default
    return sorted(rows, key=lambda r: float(r.get("updated_at", 0) or 0), reverse=True)


def _apply_search(rows: list[dict], query: str) -> list[dict]:
    q = str(query or "").strip().lower()
    if not q:
        return rows
    filtered = []
    for row in rows:
        haystack = " ".join(
            [
                str(row.get("name", "")),
                str(row.get("feature", "")),
                str(row.get("activity", "")),
                str(row.get("agent_mode", "")),
                str(row.get("feature_status", "")),
            ]
        ).lower()
        if q in haystack:
            filtered.append(row)
    return filtered


def _detail_lines(snapshot: dict, tab: str) -> list[str]:
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    history = payload.get("history", []) if isinstance(payload, dict) else []
    feature_state = payload.get("feature_state", {}) if isinstance(payload, dict) else {}

    if tab == "dashboard":
        return [
            f"session: {snapshot.get('name')}",
            f"updated: {_fmt_ts(snapshot.get('updated_at', 0), include_date=True)}",
            f"state: {'active' if snapshot.get('active') else 'idle'} ({snapshot.get('active_reason')})",
            f"mode: {snapshot.get('agent_mode')}",
            f"provider/model: {snapshot.get('provider')}/{snapshot.get('model')}",
            f"turns: {snapshot.get('history_length')}",
            f"tokens: {snapshot.get('tokens')}",
            f"activity: {snapshot.get('activity')}",
        ]
    if tab == "sessions":
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
        lines = ["feature:"]
        if not isinstance(feature_state, dict):
            return lines + ["  (none)"]
        for k in ("feature_id", "feature_name", "status", "directory", "metadata_path"):
            if k in feature_state:
                lines.append(f"  {k}: {_truncate(json.dumps(feature_state.get(k), default=str), 140)}")
        plan = feature_state.get("feature_plan", {})
        phases = plan.get("phases", []) if isinstance(plan, dict) else []
        lines.append(f"  phases: {len(phases) if isinstance(phases, list) else 0}")
        return lines
    if tab == "memory":
        lines = ["task memory:"]
        tm = payload.get("task_memory", {}) if isinstance(payload, dict) else {}
        for entry in tm.get("entries", []) if isinstance(tm, dict) else []:
            if isinstance(entry, dict):
                lines.append(f"  #{entry.get('id', '?')} {_truncate(entry.get('content', ''), 120)}")
        lines.append("")
        lines.append("scratchpad:")
        sc = payload.get("turn_scratchpad", {}) if isinstance(payload, dict) else {}
        for entry in sc.get("entries", []) if isinstance(sc, dict) else []:
            if isinstance(entry, dict):
                lines.append(f"  #{entry.get('id', '?')} {_truncate(entry.get('content', ''), 120)}")
        if len(lines) == 3:
            lines.append("  (none)")
        return lines
    if tab == "variables":
        lines = ["variables:"]
        vars_dict = payload.get("variables", {}) if isinstance(payload, dict) else {}
        if not isinstance(vars_dict, dict) or not vars_dict:
            return lines + ["  (none)"]
        for key in sorted(vars_dict.keys()):
            lines.append(f"  {key}: {json.dumps(vars_dict.get(key), default=str)}")
        return lines
    # layers/events
    if tab == "layers":
        lines = ["layers:"]
        for layer in snapshot.get("layers", []):
            lines.append(f"  {layer.get('layer')}: {layer.get('name')} size={layer.get('size')}")
        return lines
    # events tab
    lines = ["events (recent roles):"]
    for idx, msg in enumerate(history[-30:] if isinstance(history, list) else []):
        if isinstance(msg, dict):
            lines.append(f"  {idx:>2}: role={msg.get('role', 'unknown')}")
    if len(lines) == 1:
        lines.append("  (none)")
    return lines


def _render_detail(snapshot: dict, state: WatchState) -> Panel:
    tab = DETAIL_TABS[state.tab_index % len(DETAIL_TABS)]
    lines = _detail_lines(snapshot, tab)
    if state.search_query:
        q = state.search_query.lower()
        lines = [ln for ln in lines if q in ln.lower()] or [f"No matches for '{state.search_query}'"]

    state.detail_cursor = max(0, min(state.detail_cursor, max(0, len(lines) - 1)))
    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 24]
    rendered = [
        f"{'▶' if (start + i) == state.detail_cursor else ' '} {start + i:>4} {line}"
        for i, line in enumerate(window)
    ]
    return Panel(
        "\n".join(rendered) if rendered else "(empty)",
        title=f"{snapshot.get('name', '-')}: {tab}",
        subtitle=f"lines {start + 1}-{start + len(window)}",
        border_style="cyan",
    )


def _handle_key(state: WatchState, key: str, total_sessions: int) -> WatchState:
    if state.search_mode:
        if key in ("\n", "\r"):
            state.search_mode = False
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
    if key in ("s",):
        order = ["newest", "name", "tokens"]
        idx = order.index(state.sort_key) if state.sort_key in order else 0
        state.sort_key = order[(idx + 1) % len(order)]
        return state
    if key in ("r",):
        state.running_only = not state.running_only
        state.selected_index = 0
        return state
    if key in ("\n", "\r"):
        state.in_detail = True
        state.detail_offset = 0
        state.detail_cursor = 0
        return state
    if key in ("b", "\x1b"):
        state.in_detail = False
        return state
    if key in ("\x1b[A", "k"):
        if state.in_detail:
            state.detail_cursor = max(0, state.detail_cursor - 1)
            state.detail_offset = min(state.detail_offset, state.detail_cursor)
        else:
            state.selected_index = max(0, state.selected_index - 1)
        return state
    if key in ("\x1b[B", "j"):
        if state.in_detail:
            state.detail_cursor += 1
            if state.detail_cursor >= state.detail_offset + 24:
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
        state.detail_cursor = max(0, min(state.detail_cursor, state.detail_offset + 23))
        return state
    return state


def _analytics_panels(rows: list[dict]) -> Columns:
    active = sum(1 for r in rows if r.get("active"))
    blocked = sum(1 for r in rows if str(r.get("feature_status", "")).lower() == "blocked")
    total_tokens = sum(int(r.get("tokens", 0) or 0) for r in rows)
    updated_values = [int(r.get("updated_at", 0) or 0) for r in rows[:16]]
    token_values = [int(r.get("tokens", 0) or 0) for r in rows[:8]]
    token_chart = " ".join(
        f"{_truncate(r.get('name', '-'), 10)} {_bar(int(r.get('tokens', 0) or 0), max(token_values) if token_values else 1, 10)}"
        for r in rows[:4]
    ) or "no token data"
    return Columns(
        [
            Panel(f"[bold]{len(rows)}[/bold]\nSessions", border_style="cyan"),
            Panel(f"[bold green]{active}[/bold green]\nActive", border_style="green"),
            Panel(f"[bold red]{blocked}[/bold red]\nBlocked", border_style="red"),
            Panel(f"[bold magenta]{total_tokens:,}[/bold magenta]\nTokens", border_style="magenta"),
            Panel(f"[bold white]{_spark(updated_values) or '—'}[/bold white]\nUpdate Pulse", border_style="blue"),
            Panel(token_chart, title="Token Bars", border_style="yellow"),
        ],
        expand=True,
    )


def _render_watch(session_root: str, refresh_seconds: float, state: WatchState, snapshots: list[dict] | None = None) -> Group:
    snapshots = snapshots if isinstance(snapshots, list) else load_session_snapshots(session_root)
    rows = _sort_sessions(snapshots, state.sort_key)
    if state.running_only:
        rows = [r for r in rows if r.get("active")]
    rows = _apply_search(rows, state.search_query)
    now = _now_local()
    now_text = now.strftime(f"%Y-%m-%d %H:%M:%S {now.tzname() or 'local'}")

    if not rows:
        return Group(
            Panel("No matching sessions. Try clearing filters/search.", title="μCLI Watch", border_style="yellow"),
            Text(f"path: {session_root}", style="dim"),
        )

    state.selected_index = min(max(0, state.selected_index), len(rows) - 1)
    selected = rows[state.selected_index]
    table = Table(title="μCLI Live Sessions (newest-first default)", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column(" ")
    table.add_column("Session", style="bold cyan")
    table.add_column("State")
    table.add_column("Reason", style="dim")
    table.add_column("Mode", style="magenta")
    table.add_column("Feature", style="yellow")
    table.add_column("Turns", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Activity")
    table.add_column("Updated")
    for i, row in enumerate(rows):
        table.add_row(
            "▶" if i == state.selected_index else " ",
            str(row.get("name", "-")),
            "[green]active[/green]" if row.get("active") else "[dim]idle[/dim]",
            str(row.get("active_reason", "-")),
            str(row.get("agent_mode", "-")),
            str(row.get("feature", "-")),
            str(row.get("history_length", 0)),
            f"{int(row.get('tokens', 0)):,}",
            _truncate(row.get("activity", "-"), 70),
            _fmt_ts(row.get("updated_at", 0)),
        )

    tabs = " | ".join(
        f"[bold cyan]{tab}[/bold cyan]" if idx == state.tab_index else tab
        for idx, tab in enumerate(DETAIL_TABS)
    )
    header = Panel(
        Text.from_markup(
            f"[bold cyan]μCLI Watch[/bold cyan] · analytics\n"
            f"now: [cyan]{now_text}[/cyan] · refresh: [cyan]{refresh_seconds:.1f}s[/cyan] · sort: [cyan]{state.sort_key}[/cyan] · running-only: [cyan]{state.running_only}[/cyan] · search: [cyan]{state.search_query or '-'}[/cyan]\n"
            "keys: j/k move · Enter detail · b/Esc back · h/l tabs · / search · c clear · s sort · r running-filter · q quit\n"
            f"tabs: {tabs}"
        ),
        border_style="cyan",
    )
    footer = Text(f"path: {session_root}", style="dim")
    analytics = _analytics_panels(rows)
    if state.in_detail:
        return Group(header, analytics, _render_detail(selected, state), footer)
    return Group(header, analytics, table, footer)


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
