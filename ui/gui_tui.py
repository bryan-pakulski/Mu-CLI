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

    context_index: int = 0
    feature_records: list[dict] = field(default_factory=list)
    feature_index: int = 0
    selected_feature: dict | None = None

    item_index: int = 0
    detail_offset: int = 0
    search_mode: bool = False
    search_query: str = ""
    status_message: str = "ready"


def _discover_sessions(session_root: str) -> list[str]:
    if not os.path.isdir(session_root):
        return []
    out = []
    for name in sorted(os.listdir(session_root)):
        if os.path.isfile(os.path.join(session_root, name, "session.json")):
            if "_subagent_" in name:
                # Hide ephemeral sub-agent child sessions from top-level session list.
                continue
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


def _bar(value: int, max_value: int, width: int = 18) -> str:
    if max_value <= 0:
        return "░" * width
    filled = max(0, min(width, int(round((value / max_value) * width))))
    return "█" * filled + "░" * (width - filled)


def _matches_filter(*values: str, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = " | ".join(str(v or "") for v in values).lower()
    return q in hay


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
            f"[bold bright_magenta]μCLI Workspace Console[/bold bright_magenta] • [bright_cyan]{datetime.now().strftime('%H:%M:%S')}[/bright_cyan]\n"
            f"path: [bright_green]{' > '.join(breadcrumb)}[/bright_green]\n"
            "controls: j/k navigate • Enter or l select • h back • / filter • q quit"
        ),
        border_style="bright_magenta",
        expand=True,
    )


def _taskbar(state: GuiState) -> Panel:
    mode = "[bold yellow]FILTER[/bold yellow]" if state.search_mode else "[bold green]NAV[/bold green]"
    query = state.search_query if state.search_query else "none"
    return Panel(
        Text.from_markup(
            f"mode: {mode}   filter: [cyan]{query}[/cyan]   status: [white]{state.status_message}[/white]"
        ),
        border_style="bright_cyan",
        expand=True,
    )


def _sessions_view(session_root: str, state: GuiState) -> Panel:
    all_names = _discover_sessions(session_root)
    state.session_names = [name for name in all_names if _matches_filter(name, query=state.search_query)]
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
        return Panel("No sessions found for active filter.", title="Sessions", border_style="yellow")
    return Panel(table, border_style="bright_green")


def _session_context_items(payload: dict) -> list[dict]:
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    feature_count = len(_feature_records(payload))
    usage = _tool_usage_counts(payload)
    total_tool_calls = sum(count for _, count in usage)
    research_calls = 0
    for msg in history:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_call":
                tool_name = str(part.get("tool_name", "") or "").lower()
                if any(token in tool_name for token in ("research", "search", "web", "citation")):
                    research_calls += 1
    subagents = payload.get("subagents", []) if isinstance(payload.get("subagents"), list) else []
    return [
        {"id": "chat", "label": "Chat Timeline", "count": len(history)},
        {"id": "research", "label": "Research Engine", "count": research_calls},
        {"id": "tools", "label": "Tool Heatmap", "count": total_tool_calls},
        {"id": "features", "label": "Feature Workstreams", "count": feature_count},
        {"id": "subagents", "label": "Sub-Agent Workers", "count": len(subagents)},
        {"id": "variables", "label": "Runtime Variables", "count": len(payload.get("variables", {}) if isinstance(payload.get("variables"), dict) else {})},
        {"id": "memory", "label": "Task Memory", "count": len(payload.get("task_memory", {}) if isinstance(payload.get("task_memory"), dict) else {})},
        {"id": "turn_context", "label": "Current Turn Context", "count": len(history)},
        {"id": "buffers", "label": "Summaries & Buffers", "count": len(payload.get("collation_buffer", {}).get("entries", []) if isinstance(payload.get("collation_buffer"), dict) else [])},
        {"id": "layers", "label": "Context Layers", "count": len(payload.get("context_layers", []) if isinstance(payload.get("context_layers"), list) else [])},
    ]


def _session_contexts_view(payload: dict, state: GuiState) -> Panel:
    items = [item for item in _session_context_items(payload) if _matches_filter(item["label"], query=state.search_query)]
    if not items:
        return Panel("No context layers match active filter.", title="Session Stack", border_style="yellow")
    state.context_index = max(0, min(state.context_index, max(0, len(items) - 1)))
    table = Table(title="Session Stack", expand=True)
    table.add_column(" ", width=2)
    table.add_column("Layer")
    table.add_column("Count", justify="right")
    for i, item in enumerate(items):
        table.add_row("▶" if i == state.context_index else " ", item["label"], str(item["count"]))
    return Panel(table, border_style="bright_cyan")


def _features_view(payload: dict, state: GuiState) -> Panel:
    records = _feature_records(payload)
    state.feature_records = [
        item
        for item in records
        if _matches_filter(item.get("feature_id", ""), item.get("feature_name", ""), item.get("status", ""), query=state.search_query)
    ]
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
        return Panel("No feature records for this session/filter.", title="Features", border_style="yellow")

    status_counts = {"done": 0, "active": 0, "blocked": 0, "todo": 0}
    for feature in state.feature_records:
        normalized = normalize_task_status(str(feature.get("status", "")))
        if normalized in {STATUS_COMPLETED, STATUS_ARCHIVED}:
            status_counts["done"] += 1
        elif normalized == STATUS_IN_PROGRESS:
            status_counts["active"] += 1
        elif normalized == STATUS_BLOCKED:
            status_counts["blocked"] += 1
        else:
            status_counts["todo"] += 1
    total = max(1, len(state.feature_records))
    viz = Panel(
        "\n".join(
            [
                f"done    {status_counts['done']:>3} {_bar(status_counts['done'], total)}",
                f"active  {status_counts['active']:>3} {_bar(status_counts['active'], total)}",
                f"blocked {status_counts['blocked']:>3} {_bar(status_counts['blocked'], total)}",
                f"todo    {status_counts['todo']:>3} {_bar(status_counts['todo'], total)}",
            ]
        ),
        title="Feature Visualisation",
        border_style="bright_magenta",
    )
    return Panel(Columns([table, viz], expand=True), border_style="bright_blue")


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


def _history_panel(plan: FeaturePlan, payload: dict, offset: int = 0) -> Panel:
    sub_counts = payload.get("subagent_counts", {}) if isinstance(payload.get("subagent_counts"), dict) else {}
    running = int(sub_counts.get("running", 0) or 0)
    queued = int(sub_counts.get("queued", 0) or 0)
    completed = int(sub_counts.get("completed", 0) or 0)
    lines = [
        f"SubAgents active: running={running} queued={queued} completed={completed}",
        "",
        "Feature events:",
    ]
    for event in plan.event_log[-25:]:
        ts = datetime.fromtimestamp(float(getattr(event, "created_at", 0) or 0)).strftime("%H:%M:%S")
        lines.append(f"  [{ts}] {getattr(event, 'kind', '-')} {getattr(event, 'entity', '-')}/{getattr(event, 'entity_id', '-')}")

    lines += ["", "Conversation (recent):"]
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    history_window = history[max(0, len(history) - 200):]
    start = max(0, min(offset, max(0, len(history_window) - 20)))
    for msg in history_window[start:start + 20]:
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

    timeline = payload.get("subagent_timeline", []) if isinstance(payload.get("subagent_timeline"), list) else []
    if timeline:
        lines += ["", "Sub-agent events (recent):"]
        for event in timeline[-12:]:
            if not isinstance(event, dict):
                continue
            ts = datetime.fromtimestamp(float(event.get("ts", 0) or 0)).strftime("%H:%M:%S")
            lines.append(f"  [{ts}] {event.get('worker_id', '-')} {event.get('kind', 'event')}")

    lines += ["", f"window offset: {start} / {max(0, len(history_window) - 20)}"]
    return Panel("\n".join(lines), title="History Browser", border_style="bright_blue")


def _chat_panel(payload: dict, offset: int = 0) -> Panel:
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    sub_counts = payload.get("subagent_counts", {}) if isinstance(payload.get("subagent_counts"), dict) else {}
    lines = [
        f"turns: {len(history)}",
        f"subagents: running={int(sub_counts.get('running', 0) or 0)} queued={int(sub_counts.get('queued', 0) or 0)} completed={int(sub_counts.get('completed', 0) or 0)}",
        "tip: / filter, j/k scroll, includes tool calls/results",
        "",
    ]
    history_window = history[max(0, len(history) - 300):]
    start = max(0, min(offset, max(0, len(history_window) - 30)))
    for idx, msg in enumerate(history_window[start:start + 30], start=max(0, len(history) - len(history_window)) + start + 1):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        snippets: list[str] = []
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            ptype = str(part.get("type", ""))
            if ptype == "text":
                text = str(part.get("text", "")).strip().replace("\n", " ")
                if text:
                    snippets.append(text)
            elif ptype == "tool_call":
                snippets.append(f"tool_call:{part.get('tool_name', 'tool')}")
            elif ptype == "tool_result":
                snippets.append(f"tool_result:{part.get('tool_name', 'tool')}")
        snippet = " | ".join(snippets) or "(non-text)"
        if len(snippet) > 180:
            snippet = snippet[:179] + "…"
        lines.append(f"{idx:>4} {role:<10} {snippet}")
    timeline = payload.get("subagent_timeline", []) if isinstance(payload.get("subagent_timeline"), list) else []
    if timeline:
        lines += ["", "sub-agent activity:"]
        for event in timeline[-8:]:
            if not isinstance(event, dict):
                continue
            ts = datetime.fromtimestamp(float(event.get("ts", 0) or 0)).strftime("%H:%M:%S")
            lines.append(f"  [{ts}] {event.get('worker_id', '-')} {event.get('kind', 'event')}")
    lines += ["", f"window offset: {start} / {max(0, len(history_window) - 30)}"]
    return Panel("\n".join(lines), title="Chat Timeline", border_style="green")


def _research_panel(payload: dict) -> Panel:
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    lines = ["Research activity:", ""]
    hits = 0
    for msg in history[-60:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        parts = msg.get("parts", []) if isinstance(msg.get("parts"), list) else []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_call":
                tool = str(part.get("tool_name", "") or "")
                if any(token in tool.lower() for token in ("research", "search", "web", "citation")):
                    hits += 1
                    lines.append(f"{role:<10} tool_call  {tool}")
            elif part.get("type") == "tool_result":
                payload_text = str(part.get("content", "") or "")
                if "citation" in payload_text.lower() or "source" in payload_text.lower():
                    hits += 1
                    lines.append(f"{role:<10} tool_result citation/source payload")
    if hits == 0:
        lines.append("No explicit research/citation tool activity found in recent turns.")
    return Panel("\n".join(lines[:40]), title="Research Engine", border_style="magenta")


def _subagents_panel(payload: dict) -> Panel:
    workers = payload.get("subagents", []) if isinstance(payload.get("subagents"), list) else []
    if not workers:
        return Panel("No sub-agent workers recorded.", title="Sub-Agent Workers", border_style="yellow")
    timeline = payload.get("subagent_timeline", []) if isinstance(payload.get("subagent_timeline"), list) else []
    lines = [f"workers: {len(workers)}", "controls: use /api/tool cancel_sub_agents | retry_sub_agents", ""]
    for worker in workers[:30]:
        if not isinstance(worker, dict):
            continue
        wid = str(worker.get("worker_id", "-"))
        status = str(worker.get("status", "unknown"))
        title = str(worker.get("title", ""))
        elapsed = "-"
        st = worker.get("started_at")
        en = worker.get("ended_at")
        if isinstance(st, (int, float)):
            end_ts = en if isinstance(en, (int, float)) else time.time()
            elapsed = f"{max(0, int(end_ts - st))}s"
        summary = str(worker.get("summary", "") or "")
        if len(summary) > 60:
            summary = summary[:59] + "…"
        lines.append(f"[{status:<9}] {wid} {elapsed} {title}")
        if summary:
            lines.append(f"  ↳ {summary}")
    if timeline:
        lines += ["", "recent events:"]
        for event in timeline[-8:]:
            if not isinstance(event, dict):
                continue
            ts = datetime.fromtimestamp(float(event.get("ts", 0) or 0)).strftime("%H:%M:%S")
            lines.append(f"  [{ts}] {event.get('worker_id', '-')} {event.get('kind', 'event')}")
    return Panel("\n".join(lines), title="Sub-Agent Workers", border_style="magenta")


def _variables_panel(payload: dict) -> Panel:
    variables = payload.get("variables", {}) if isinstance(payload.get("variables"), dict) else {}
    if not variables:
        return Panel("No runtime variables persisted in this session.", title="Runtime Variables", border_style="yellow")
    table = Table(expand=True)
    table.add_column("Variable")
    table.add_column("Value")
    for key, value in list(sorted(variables.items(), key=lambda item: str(item[0]).lower()))[:40]:
        rendered = str(value)
        if len(rendered) > 100:
            rendered = rendered[:99] + "…"
        table.add_row(str(key), rendered)
    return Panel(table, title="Runtime Variables", border_style="cyan")


def _memory_panel(payload: dict, offset: int = 0) -> Panel:
    memory = payload.get("task_memory", {}) if isinstance(payload.get("task_memory"), dict) else {}
    scratch = payload.get("turn_scratchpad", {}) if isinstance(payload.get("turn_scratchpad"), dict) else {}
    lines = [
        f"task_memory keys: {len(memory)}",
        f"turn_scratchpad keys: {len(scratch)}",
        "",
        "task_memory preview:",
    ]
    memory_items = list(memory.items())
    start = max(0, min(offset, max(0, len(memory_items) - 15)))
    for key, value in memory_items[start:start + 15]:
        text = str(value).replace("\n", " ")
        if len(text) > 90:
            text = text[:89] + "…"
        lines.append(f"- {key}: {text}")
    if not memory:
        lines.append("- (empty)")
    lines += ["", f"window offset: {start} / {max(0, len(memory_items) - 15)}"]
    return Panel("\n".join(lines), title="Task Memory", border_style="yellow")


def _turn_context_panel(payload: dict) -> Panel:
    history = payload.get("history", []) if isinstance(payload.get("history"), list) else []
    recent = history[-6:]
    lines = ["Recent turn context:"]
    for msg in recent:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown"))
        snippet = ""
        for part in msg.get("parts", []) if isinstance(msg.get("parts"), list) else []:
            if isinstance(part, dict) and part.get("type") == "text":
                snippet = str(part.get("text", "")).replace("\n", " ").strip()
                if snippet:
                    break
        if len(snippet) > 140:
            snippet = snippet[:139] + "…"
        lines.append(f"{role:<10} {snippet or '(non-text event)'}")
    return Panel("\n".join(lines), title="Current Turn Context", border_style="cyan")


def _buffers_panel(payload: dict) -> Panel:
    summary = str(payload.get("conversation_summary", "") or "")
    summary_anchor = int(payload.get("summary_anchor", 0) or 0)
    collation = payload.get("collation_buffer", {}) if isinstance(payload.get("collation_buffer"), dict) else {}
    entries = collation.get("entries", []) if isinstance(collation.get("entries"), list) else []
    lines = [
        f"summary_anchor: {summary_anchor}",
        f"summary_chars: {len(summary)}",
        f"collation_pending: {len(entries)}",
        "",
        "summary preview:",
        (summary[:900] + "…") if len(summary) > 900 else (summary or "(empty)"),
    ]
    return Panel("\n".join(lines), title="Summaries & Buffers", border_style="yellow")


def _context_layers_panel(payload: dict) -> Panel:
    layers = payload.get("context_layers", []) if isinstance(payload.get("context_layers"), list) else []
    if not layers:
        return Panel("No context_layers in payload snapshot.", title="Context Layers", border_style="yellow")
    lines = []
    for layer in layers[:20]:
        if not isinstance(layer, dict):
            continue
        lines.append(f"{layer.get('layer', '-')}: {layer.get('name', '-')}")
        lines.append(f"  chars={layer.get('char_count', 0)} tokens~={layer.get('token_estimate', 0)}")
    return Panel("\n".join(lines), title="Context Layers", border_style="magenta")


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
    raw_items = _feature_items(plan)
    items = []
    for item in raw_items:
        if item["kind"] == "view":
            if _matches_filter(item["label"], query=state.search_query):
                items.append(item)
        else:
            task = item["task"]
            if _matches_filter(task.title, str(task.id), normalize_task_status(task.status), query=state.search_query):
                items.append(item)
    if not items:
        return Group(Panel("No feature items match active filter.", border_style="yellow"))
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
        f"Exit GUI?\n\n{no}    {yes}\n\nUse j/k then Enter",
        title="Confirm Exit",
        border_style="bright_red",
    )


def _render_gui(session_root: str, state: GuiState) -> Group:
    header = _header(state)

    if state.screen == "sessions":
        body = _sessions_view(session_root, state)
    else:
        session_name = state.selected_session or ""
        payload = _load_session_payload(session_root, session_name) if session_name else {}
        if state.screen == "contexts":
            body = _session_contexts_view(payload, state)
        elif state.screen == "features":
            body = _features_view(payload, state)
        elif state.screen == "chat":
            body = _chat_panel(payload, offset=state.detail_offset)
        elif state.screen == "research":
            body = _research_panel(payload)
        elif state.screen == "tools":
            body = _heatmap_panel(payload)
        elif state.screen == "subagents":
            body = _subagents_panel(payload)
        elif state.screen == "variables":
            body = _variables_panel(payload)
        elif state.screen == "memory":
            body = _memory_panel(payload, offset=state.detail_offset)
        elif state.screen == "turn_context":
            body = _turn_context_panel(payload)
        elif state.screen == "buffers":
            body = _buffers_panel(payload)
        elif state.screen == "layers":
            body = _context_layers_panel(payload)
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
                body = _history_panel(plan, payload, offset=state.detail_offset)

    if state.confirm_quit:
        body = Group(body, _confirm_modal(state))

    footer = _taskbar(state)
    return Group(header, body, footer)


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
        # Arrow keys and modified arrows arrive as a short escape sequence.
        # Wait slightly longer for the first follow-up byte to avoid treating
        # a split arrow sequence as a raw "Esc" back action.
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            return seq
        seq += sys.stdin.read(1)
        ready, _, _ = select.select([sys.stdin], [], [], 0.01)
        while ready:
            seq += sys.stdin.read(1)
            ready, _, _ = select.select([sys.stdin], [], [], 0.01)
        return seq


def _is_up_key(key: str) -> bool:
    return key in {"k", "K"}


def _is_down_key(key: str) -> bool:
    return key in {"j", "J"}


def _handle_key(state: GuiState, key: str, session_root: str) -> GuiState:
    is_up = _is_up_key(key)
    is_down = _is_down_key(key)
    is_select = key in {"\n", "\r", "l", "L"}
    is_back = key in {"\x1b", "b", "B", "h", "H"}

    if key in {"q", "Q"} and not state.confirm_quit:
        state.confirm_quit = True
        state.confirm_index = 0
        return state

    if state.search_mode:
        if key in {"\n", "\r", "\x1b"}:
            state.search_mode = False
            state.status_message = "filter applied"
            return state
        if key in {"\x7f", "\b"}:
            state.search_query = state.search_query[:-1]
            state.status_message = "filter updated"
            return state
        if key == "\x15":
            state.search_query = ""
            state.status_message = "filter cleared"
            return state
        if len(key) == 1 and key.isprintable():
            state.search_query += key
            state.status_message = "filter updated"
            return state
        return state

    if state.confirm_quit:
        if is_back:
            state.confirm_quit = False
            return state
        if is_up or is_down:
            state.confirm_index = 1 - state.confirm_index
            return state
        if is_select:
            if state.confirm_index == 1:
                state.should_exit = True
            else:
                state.confirm_quit = False
            return state
        return state

    if is_back:
        if state.screen == "task_detail":
            state.screen = "items"
            state.detail_offset = 0
        elif state.screen in {"overview", "heatmap", "history"}:
            state.screen = "items"
        elif state.screen == "items":
            state.screen = "features"
        elif state.screen == "features":
            state.screen = "contexts"
            state.selected_feature = None
        elif state.screen in {"chat", "research", "tools", "subagents", "variables", "memory", "turn_context", "buffers", "layers"}:
            state.screen = "contexts"
            state.detail_offset = 0
        elif state.screen == "contexts":
            state.screen = "sessions"
            state.selected_feature = None
        return state

    if key == "/":
        state.search_mode = True
        state.status_message = "type to filter, Enter to apply, Esc to cancel"
        return state

    if state.screen == "sessions":
        state.session_names = [
            name
            for name in _discover_sessions(session_root)
            if _matches_filter(name, query=state.search_query)
        ]
        if not state.session_names:
            return state
        if is_down:
            state.session_index = min(max(0, len(state.session_names) - 1), state.session_index + 1)
        elif is_up:
            state.session_index = max(0, state.session_index - 1)
        elif is_select and state.session_names:
            state.selected_session = state.session_names[state.session_index]
            state.screen = "contexts"
            state.context_index = 0
            state.feature_index = 0
        return state

    if state.screen == "contexts":
        payload = _load_session_payload(session_root, state.selected_session or "")
        items = [item for item in _session_context_items(payload) if _matches_filter(item["label"], query=state.search_query)]
        if not items:
            return state
        if is_down:
            state.context_index = min(max(0, len(items) - 1), state.context_index + 1)
        elif is_up:
            state.context_index = max(0, state.context_index - 1)
        elif is_select and items:
            selected = items[state.context_index]
            state.screen = selected["id"]
            if selected["id"] == "features":
                state.feature_index = 0
        return state

    if state.screen == "subagents":
        if key in {"c", "C"}:
            state.status_message = "Use /api/tool cancel_sub_agents with selected worker IDs."
            return state
        if key in {"r", "R"}:
            state.status_message = "Use /api/tool retry_sub_agents with selected worker IDs."
            return state
    if state.screen in {"chat", "memory", "history"}:
        if is_down:
            state.detail_offset = min(5000, state.detail_offset + 1)
            return state
        if is_up:
            state.detail_offset = max(0, state.detail_offset - 1)
            return state

    if state.screen == "features":
        payload = _load_session_payload(session_root, state.selected_session or "")
        state.feature_records = [
            item
            for item in _feature_records(payload)
            if _matches_filter(item.get("feature_id", ""), item.get("feature_name", ""), item.get("status", ""), query=state.search_query)
        ]
        if not state.feature_records:
            return state
        if is_down:
            state.feature_index = min(max(0, len(state.feature_records) - 1), state.feature_index + 1)
        elif is_up:
            state.feature_index = max(0, state.feature_index - 1)
        elif is_select and state.feature_records:
            state.selected_feature = state.feature_records[state.feature_index]
            state.screen = "items"
            state.item_index = 0
        return state

    if state.screen == "items":
        plan = _load_feature_plan_from_record(state.selected_feature)
        if not plan:
            return state
        items = []
        for item in _feature_items(plan):
            if item["kind"] == "view":
                if _matches_filter(item["label"], query=state.search_query):
                    items.append(item)
            else:
                task = item["task"]
                if _matches_filter(task.title, str(task.id), normalize_task_status(task.status), query=state.search_query):
                    items.append(item)
        if not items:
            return state
        if is_down:
            state.item_index = min(len(items) - 1, state.item_index + 1)
        elif is_up:
            state.item_index = max(0, state.item_index - 1)
        elif is_select:
            sel = items[state.item_index]
            if sel["kind"] == "task":
                state.screen = "task_detail"
                state.detail_offset = 0
            else:
                state.screen = sel["id"]
        return state

    if state.screen == "task_detail":
        if is_down:
            state.detail_offset += 1
        elif is_up:
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
