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
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

RUNNING_THRESHOLD_SECONDS = 8.0
DETAIL_TABS = ["chat", "memory", "layers", "metadata", "features"]


@dataclass
class WatchState:
    selected_index: int = 0
    tab_index: int = 0
    detail_offset: int = 0
    should_exit: bool = False


def _truncate(value: str, limit: int = 72) -> str:
    raw = str(value or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _fmt_time(epoch: float, *, with_date: bool = False) -> str:
    fmt = "%Y-%m-%d %H:%M:%S UTC" if with_date else "%H:%M:%S"
    return datetime.fromtimestamp(float(epoch or 0), tz=timezone.utc).strftime(fmt)


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
        running = (now - updated_at) <= RUNNING_THRESHOLD_SECONDS

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


def _render_detail(snapshot: dict, state: WatchState) -> Panel:
    active_tab = DETAIL_TABS[state.tab_index % len(DETAIL_TABS)]
    lines = _detail_lines(snapshot, active_tab)
    start = max(0, min(state.detail_offset, max(0, len(lines) - 1)))
    window = lines[start : start + 22]
    subtitle = (
        f"Tab: {active_tab} | lines {start + 1}-{start + len(window)}"
        if lines
        else f"Tab: {active_tab}"
    )
    body = "\n".join(window) if window else "(empty)"
    return Panel(body, title=f"Session Detail — {snapshot.get('name', '-')}", subtitle=subtitle)


def _handle_key(state: WatchState, key: str, total_sessions: int) -> WatchState:
    if key in ("q", "\x03"):
        state.should_exit = True
        return state
    if key in ("\x1b[A", "k"):
        state.selected_index = max(0, state.selected_index - 1)
        state.detail_offset = 0
        return state
    if key in ("\x1b[B", "j"):
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
        state.detail_offset += 8
        return state
    if key in ("p", "\x1b[5~"):
        state.detail_offset = max(0, state.detail_offset - 8)
        return state
    return state


def _render_watch(session_root: str, refresh_seconds: float, state: WatchState) -> Group:
    snapshots = load_session_snapshots(session_root)
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

    table = Table(title="Session Activity", expand=True)
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
        status = "[green]running[/green]" if item.get("running") else "[dim]idle[/dim]"
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
    header = Panel(
        Text.from_markup(
            f"[bold]μCLI Watch[/bold] • read-only realtime monitor\n"
            f"Sessions: [cyan]{len(snapshots)}[/cyan] • "
            f"Refresh: [cyan]{refresh_seconds:.1f}s[/cyan] • Now: [cyan]{now_text}[/cyan]\n"
            f"Keys: ↑/↓ or j/k session • ←/→ or h/l tabs • n/p scroll • q quit\n"
            f"Tabs: {tabs}"
        ),
        border_style="cyan",
    )
    footer = Text(f"Session path: {session_root}", style="dim")
    return Group(header, table, _render_detail(current, state), footer)


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

    with _KeyReader() as keys, Live(
        _render_watch(session_root, refresh_seconds, state),
        refresh_per_second=8,
        screen=True,
    ) as live:
        while not state.should_exit:
            key = keys.read_key(timeout=0.05)
            if key:
                snapshots = load_session_snapshots(session_root)
                state = _handle_key(state, key, len(snapshots))
                if state.should_exit:
                    break
            live.update(_render_watch(session_root, refresh_seconds, state))
            time.sleep(refresh_seconds)
