"""Read-only realtime session watcher for Mu-CLI."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _truncate(value: str, limit: int = 72) -> str:
    raw = str(value or "").strip().replace("\n", " ")
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


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


def load_session_snapshots(session_root: str) -> list[dict]:
    snapshots: list[dict] = []
    if not os.path.isdir(session_root):
        return snapshots

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
            history = data
            variables = {}
            feature_state = None
            token_counts = {}
            provider_config = {}
        else:
            history = data.get("history", []) if isinstance(data, dict) else []
            variables = data.get("variables", {}) if isinstance(data, dict) else {}
            feature_state = (
                data.get("feature_state") if isinstance(data, dict) else None
            )
            token_counts = data.get("token_counts", {}) if isinstance(data, dict) else {}
            provider_config = (
                data.get("provider_config", {}) if isinstance(data, dict) else {}
            )

        snapshots.append(
            {
                "name": name,
                "updated_at": os.path.getmtime(session_path),
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
                "activity": _extract_last_activity(history),
            }
        )

    snapshots.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return snapshots


def build_watch_renderable(session_root: str, refresh_seconds: float) -> Group:
    snapshots = load_session_snapshots(session_root)
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not snapshots:
        return Group(
            Panel(
                Text(
                    "No saved sessions yet.\n"
                    "Start Mu-CLI normally and create at least one session.",
                    justify="center",
                ),
                title="μCLI Watch",
                border_style="cyan",
            ),
            Text(f"Session path: {session_root}\nRefresh: {refresh_seconds:.1f}s"),
        )

    table = Table(title="Session Activity", expand=True)
    table.add_column("Session", style="bold cyan", no_wrap=True)
    table.add_column("Mode", style="magenta", no_wrap=True)
    table.add_column("Feature", style="yellow")
    table.add_column("Feature Status", style="yellow", no_wrap=True)
    table.add_column("Turns", justify="right", style="green", no_wrap=True)
    table.add_column("Tokens", justify="right", style="green", no_wrap=True)
    table.add_column("Provider/Model", style="blue")
    table.add_column("Latest Activity", style="white")
    table.add_column("Updated (UTC)", style="dim", no_wrap=True)

    for item in snapshots:
        updated = datetime.fromtimestamp(
            float(item.get("updated_at", 0) or 0), tz=timezone.utc
        ).strftime("%H:%M:%S")
        table.add_row(
            str(item.get("name", "-")),
            str(item.get("agent_mode", "default")),
            str(item.get("feature", "-")),
            str(item.get("feature_status", "-")),
            str(item.get("history_length", 0)),
            f"{int(item.get('tokens', 0)):,}",
            f"{item.get('provider', '-')}/{item.get('model', '-')}",
            _truncate(str(item.get("activity", "-")), 84),
            updated,
        )

    header = Panel(
        Text.from_markup(
            f"[bold]μCLI Watch[/bold]  •  read-only realtime monitor\n"
            f"Sessions: [cyan]{len(snapshots)}[/cyan]  •  "
            f"Refresh: [cyan]{refresh_seconds:.1f}s[/cyan]  •  "
            f"Now: [cyan]{now_text}[/cyan]\n"
            "Press Ctrl+C to exit."
        ),
        border_style="cyan",
    )
    footer = Text(f"Session path: {session_root}", style="dim")
    return Group(header, table, footer)


def run_watch_mode(session_root: str, refresh_seconds: float = 1.5) -> None:
    refresh_seconds = max(0.2, float(refresh_seconds or 1.5))
    with Live(
        build_watch_renderable(session_root, refresh_seconds),
        refresh_per_second=max(2, int(1 / refresh_seconds) if refresh_seconds < 1 else 4),
        screen=True,
    ) as live:
        try:
            while True:
                live.update(build_watch_renderable(session_root, refresh_seconds))
                time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            return
