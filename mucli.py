#!/usr/bin/env python

import argparse
import json
import os
import re
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.text import Text
from rich.table import Table
from rich import box

# Import from our new modular structure
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from utils.helpers import safe_markup
from utils.logger import logger
from providers.openai import OpenAIProvider
from mu.session.session import SessionManager, Session, derive_feature_state_status
from mu.feature.engine import (
    load_feature_plan,
    refresh_and_persist_feature_plan,
    save_feature_plan,
    summarize_feature_plan,
)
from mu.tools._dispatcher import execute_tool
from mu.ui.rich_ui import RichUI
from utils.config import AGENT_MODE_METADATA

console = Console()


def refresh_memory_hud(session, ui, *, force=False):
    if force and ui and hasattr(ui, "show_memory_monitor"):
        ui.show_memory_monitor(session)


def print_mode_overview(session):
    current_mode = str(session.variables.get("agent_mode", "default"))
    table = Table(title="Available Agent Modes", box=box.SIMPLE_HEAVY)
    table.add_column("Mode", style="cyan", no_wrap=True)
    table.add_column("Current", style="yellow", justify="center")
    table.add_column("Description", style="white")
    table.add_column("Docs", style="magenta")

    for mode_name, metadata in AGENT_MODE_METADATA.items():
        table.add_row(
            mode_name,
            "*" if mode_name == current_mode else "",
            metadata.get("description", ""),
            metadata.get("documentation", ""),
        )

    console.print(table)
    console.print(f"[dim]Current mode: {safe_markup(current_mode)}[/dim]")


def _research_tool_names():
    return [
        "web_search",
        "url_grounding",
        "arxiv_search",
        "doi_resolve",
        "reddit_search",
        "stackoverflow_search",
        "hackernews_search",
        "read_document",
    ]


def _extract_recent_sources(history, limit=12):
    urls = []
    seen = set()
    pattern = re.compile(r"https?://[^\s)\]>\"']+")
    for message in reversed(history):
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []) if isinstance(message.get("parts"), list) else []:
            if not isinstance(part, dict):
                continue
            text_blob = json.dumps(part, ensure_ascii=False, default=str)
            for match in pattern.findall(text_blob):
                if match in seen:
                    continue
                seen.add(match)
                urls.append(match)
                if len(urls) >= limit:
                    return urls
    return urls


def _slugify_feature_id(value):
    return (
        re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        or "feature"
    )


def _default_feature_directory(session, feature_name):
    workspace_root = (
        os.path.abspath(session.folder_context.folders[0])
        if session.folder_context.folders
        else os.getcwd()
    )
    return os.path.join(
        workspace_root,
        "documentation",
        f"feature_req_{_slugify_feature_id(feature_name)}",
    )


def refresh_feature_record(session, feature_id=None):
    record = session.session_manager.get_feature(feature_id)
    if not isinstance(record, dict):
        return None

    metadata_path = str(record.get("metadata_path", "") or "").strip()
    directory = str(record.get("directory", "") or "").strip()
    if not (metadata_path and directory and os.path.exists(metadata_path)):
        return record

    try:
        plan = refresh_and_persist_feature_plan(
            session.session_manager.current_session_name,
            metadata_path=metadata_path,
        )
    except (FileNotFoundError, OSError, ValueError):
        return record

    summary = summarize_feature_plan(plan)
    updated = {
        **record,
        "feature_id": summary["feature_id"],
        "feature_name": summary["feature_name"],
        "directory": summary["directory"],
        "metadata_path": summary.get("metadata_path"),
        "feature_plan": summary,
        "next_phase": summary.get("next_phase"),
        "status": derive_feature_state_status(summary),
        "updated_at": record.get("updated_at"),
    }
    session.session_manager.upsert_feature(updated)
    if session.session_manager.active_feature_id == updated["feature_id"]:
        session.session_manager.set_feature_state(updated, session.folder_context)
    else:
        session.session_manager.save_history(session.folder_context)
        session.sync_runtime_state()
    return session.session_manager.get_feature(updated["feature_id"])


def get_current_feature_task_label(session):
    feature_state = session.session_manager.get_feature_state()
    if not isinstance(feature_state, dict):
        return None

    feature_plan = feature_state.get("feature_plan")
    if not isinstance(feature_plan, dict):
        return None

    next_task = feature_plan.get("next_task") or feature_plan.get("next_phase")
    if isinstance(next_task, dict):
        title = str(next_task.get("title", "") or "").strip()
        return title or None
    return None


def get_feature_prompt_context(session):
    feature_state = session.session_manager.get_feature_state()
    if not isinstance(feature_state, dict):
        return None

    plan = feature_state.get("feature_plan")
    if not isinstance(plan, dict):
        return None

    tasks = plan.get("phases", [])
    overall_total = max(1, len(tasks))
    overall_done = sum(1 for task in tasks if task.get("status") == "completed")
    all_completed = bool(tasks) and (
        bool(plan.get("phases_completed"))
        or bool(plan.get("tasks_completed"))
        or overall_done >= len(tasks)
        or str(feature_state.get("status", "")).strip().lower() == "completed"
    )

    next_task = plan.get("next_task") or plan.get("next_phase")
    active_task = None
    if isinstance(next_task, dict):
        next_number = next_task.get("number") or next_task.get("id")
        active_task = next(
            (task for task in tasks if task.get("number") == next_number),
            None,
        )
    if active_task is None and tasks:
        active_task = next(
            (task for task in tasks if task.get("status") != "completed"),
            tasks[0],
        )

    phase_done = 0
    phase_total = 1
    task_title = "n/a"
    if all_completed:
        phase_done = 1
        phase_total = 1
        task_title = "completed"
    elif isinstance(active_task, dict):
        task_title = str(active_task.get("title", "") or "").strip() or "n/a"
        counts = active_task.get("task_counts", {}) or {}
        phase_done = int(counts.get("completed", 0) or 0)
        phase_total = int(sum(int(v or 0) for v in counts.values()) or 0)
        if phase_total <= 0:
            phase_total = 1
            if active_task.get("status") == "completed":
                phase_done = 1

    return {
        "status": str(feature_state.get("status", "unknown") or "unknown"),
        "task": task_title,
        "phase_done": phase_done,
        "phase_total": phase_total,
        "overall_done": overall_done,
        "overall_total": overall_total,
    }


def build_feature_markdown(feature, *, include_phases=True):
    if not isinstance(feature, dict):
        return "## Feature\n\nNo feature is currently selected."

    plan = (
        feature.get("feature_plan")
        if isinstance(feature.get("feature_plan"), dict)
        else {}
    )
    feature_name = (
        feature.get("feature_name")
        or plan.get("feature_name")
        or feature.get("feature_id", "feature")
    )
    lines = [
        f"# Feature: {feature_name}",
        "",
        f"- **ID:** `{feature.get('feature_id', 'unknown')}`",
        f"- **Status:** `{feature.get('status', 'unknown')}`",
        f"- **Directory:** `{feature.get('directory', 'n/a')}`",
        f"- **Metadata:** `{feature.get('metadata_path', 'n/a')}`",
        f"- **Approved:** `{plan.get('approved', False)}`",
        f"- **Review:** `{plan.get('review_status', 'pending')}`",
        "",
    ]

    request = str(plan.get("feature_request", "") or "").strip()
    if request:
        lines.extend(["## Request", "", request, ""])

    tasks = plan.get("phases", [])
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    total = len(tasks)
    started_at = float(feature.get("started_at", 0) or 0)
    elapsed = max(0, int(time.time() - started_at)) if started_at else 0
    token_total = int(feature.get("token_total", 0) or 0)
    start_tokens = int(feature.get("start_tokens", 0) or 0)
    token_delta = max(0, token_total - start_tokens)
    next_task = plan.get("next_phase")
    if not isinstance(next_task, dict):
        next_task = plan.get("next_task")

    def _fmt_elapsed(seconds):
        minutes, secs = divmod(max(0, int(seconds or 0)), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        return f"{minutes}m {secs}s"

    def _fmt_delta(tokens):
        if tokens >= 1000:
            return f"{tokens / 1000:.1f}k"
        return str(tokens)

    lines.extend(
        [
            "## Progress Snapshot",
            "",
            f"- **Completed:** {completed}/{total}",
            f"- **Elapsed:** {_fmt_elapsed(elapsed)}",
            f"- **Token delta:** ↓ {_fmt_delta(token_delta)} tokens",
            "",
        ]
    )

    if isinstance(next_task, dict):
        lines.extend(
            [
                "### Active Work",
                "",
                f"*Implementing {next_task.get('title', '')}… ({_fmt_elapsed(elapsed)} · ↓ {_fmt_delta(token_delta)} tokens)*",
                "",
            ]
        )

    if include_phases:
        lines.extend(["### Task Checklist", ""])
        if tasks:
            for task in tasks:
                counts = task.get("task_counts", {})
                icon = {
                    "completed": "✔",
                    "in_progress": "◼",
                    "not_started": "◻",
                }.get(task.get("status", "not_started"), "◻")
                lines.append(
                    f"- {icon} **{task.get('title', '')}** "
                    f"`{task.get('status', 'unknown')}` "
                    f"(done: {counts.get('completed', 0)}, in-progress: {counts.get('in_progress', 0)}, remaining: {counts.get('not_started', 0)})"
                )
        else:
            lines.append("- No tasks defined yet.")
        lines.append("")

    blocker = feature.get("blocker")
    if isinstance(blocker, dict) and any(blocker.values()):
        lines.extend(
            [
                "## Blocker",
                "",
                f"- **Summary:** {blocker.get('summary', '')}",
                f"- **Requested input:** {blocker.get('requested_input', '')}",
                "",
            ]
        )

    return "\n".join(lines).strip()


def _feature_three_option_prompt(question, options, *, allow_prompt):
    choices = options[:3]
    if len(choices) != 3:
        raise ValueError("feature prompt requires exactly three options")
    if not allow_prompt:
        return choices[0][0]
    console.print(f"[bold cyan]{safe_markup(question)}[/bold cyan]")
    for idx, (_, label) in enumerate(choices, start=1):
        console.print(f"  {idx}. {label}", markup=False)
    selected = IntPrompt.ask("Select option", choices=[1, 2, 3], default=1)
    return choices[selected - 1][0]


def _log_feature_cli_event(session, *, kind, payload):
    feature_state = session.session_manager.get_feature_state()
    if not isinstance(feature_state, dict):
        return
    metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
    if not metadata_path or not os.path.exists(metadata_path):
        return
    try:
        plan = load_feature_plan(metadata_path)
    except (FileNotFoundError, OSError, ValueError):
        return
    plan.add_event(
        kind=kind,
        entity="cli",
        entity_id=str(feature_state.get("feature_id", "unknown") or "unknown"),
        payload=payload,
        actor="cli",
    )
    save_feature_plan("", plan)
    refresh_feature_record(session, None)


def _feature_prompt_with_logging(
    session,
    *,
    question,
    options,
    allow_prompt,
    prompt_id,
    context=None,
):
    selected = _feature_three_option_prompt(question, options, allow_prompt=allow_prompt)
    _log_feature_cli_event(
        session,
        kind="cli_prompt_selected",
        payload={
            "prompt_id": prompt_id,
            "question": question,
            "selected": selected,
            "options": [option[0] for option in options[:3]],
            "context": context or {},
        },
    )
    return selected


def _feature_confirm_deny_edit_loop(
    session,
    *,
    label,
    value,
    allow_prompt,
    context=None,
):
    current_value = str(value or "").strip()
    while True:
        choice = _feature_prompt_with_logging(
            session,
            question=f"Confirm {label}: {current_value}",
            options=[
                ("confirm", "Confirm (Recommended): proceed"),
                ("edit", "Edit: change and re-confirm"),
                ("deny", "Deny: cancel command"),
            ],
            allow_prompt=allow_prompt,
            prompt_id=f"confirm_{label}",
            context={"label": label, **(context or {})},
        )
        if choice == "confirm":
            return {"decision": "confirm", "value": current_value}
        if choice == "deny":
            return {"decision": "deny", "value": current_value}
        current_value = Prompt.ask(f"Edit {label}", default=current_value).strip()


def _monitor_compact_line(snapshot):
    execution = snapshot.get("execution", {}) if isinstance(snapshot, dict) else {}
    next_phase = (execution.get("next_phase") or {}) if isinstance(execution, dict) else {}
    next_task = (execution.get("next_task") or {}) if isinstance(execution, dict) else {}
    blocked = execution.get("blocked_tasks", []) if isinstance(execution, dict) else []
    blockers = ", ".join(str(item.get("title", "")).strip() for item in blocked if isinstance(item, dict) and str(item.get("title", "")).strip())
    completion = "done" if execution.get("all_phases_completed") else "in_progress"
    return (
        f"phase={next_phase.get('title') or '-'} | "
        f"task={next_task.get('title') or '-'} | "
        f"blockers={len(blocked)}{f' ({blockers})' if blockers else ''} | "
        f"completion={completion}"
    )


def _execute_feature_tool(session, tool_name, args):
    raw = execute_tool(
        tool_name,
        args,
        session.folder_context,
        session.ui,
        session.variables,
        session=session,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": raw}


def build_stats_snapshot(session):
    stats = {
        "history_turns": len(session.session_manager.history),
        "summary_anchor": session.session_manager.summary_anchor,
        "active_turns": len(session.session_manager.history)
        - session.session_manager.summary_anchor,
        "token_counts": dict(session.session_manager.token_counts),
        "feature_state": session.session_manager.get_feature_state(),
        "feature_plan": None,
    }

    feature_state = stats["feature_state"]
    if isinstance(feature_state, dict):
        directory = str(feature_state.get("directory", "") or "").strip()
        metadata_path = str(feature_state.get("metadata_path", "") or "").strip()
        if directory:
            try:
                plan = refresh_and_persist_feature_plan(
                    directory,
                    metadata_path=metadata_path or None,
                )
                stats["feature_plan"] = summarize_feature_plan(plan)
            except (FileNotFoundError, OSError, ValueError):
                stats["feature_plan"] = None

    return stats


# Single source of truth for /help. Grouped by purpose. Aliases column
# only lists the ONE alias that survived the cleanup (most commands have
# no alias — /quit's /q is the only one kept for muscle memory).
_HELP_GROUPS = [
    (
        "Session",
        [
            ("/help", "", "Show this menu"),
            ("/quit", "/q", "Exit"),
            ("/session [list|load|new|delete]", "", "Manage saved sessions"),
            ("/clear", "", "Clear the terminal screen"),
            ("/history [clear]", "", "Show conversation history; clear wipes it"),
            ("/continue", "", "Resume last paused execution after Ctrl+C"),
        ],
    ),
    (
        "Workspace",
        [
            ("/workspace", "", "Show attached folders + staged files"),
            ("/workspace folder <path>", "", "Attach a folder"),
            ("/workspace folder remove <p>", "", "Detach a folder"),
            ("/workspace folder clear", "", "Detach all folders"),
            ("/workspace file <path>", "", "Stage a file for the next turn"),
            ("/workspace file clear", "", "Drop staged files"),
            ("/workspace clear", "", "Drop everything (folders + staged files)"),
        ],
    ),
    (
        "Model & provider",
        [
            ("/model [name]", "", "Show / change the model"),
            ("/provider [name]", "", "Switch provider (gemini, ollama, openai)"),
            ("/ollama [status|models|pull|options]", "", "Ollama-specific helpers"),
        ],
    ),
    (
        "Variables",
        [
            ("/set <key> <value>", "", "Set a session variable"),
            ("/get <key>", "", "Get a session variable"),
            ("/unset <key|--all>", "", "Unset a variable"),
            ("/variables", "", "Show all variables"),
        ],
    ),
    (
        "Modes & toggles",
        [
            ("/mode <name>", "", "Switch agent mode (default|debug|feature|research|loop|security|teacher)"),
            ("/plan [on|off|toggle]", "", "Toggle plan mode (read-only enforcement)"),
            ("/yolo", "", "Toggle YOLO mode (auto-approve writes)"),
            ("/agentic", "", "Toggle tool-calling mode"),
            ("/thinking", "", "Toggle extended thinking / reasoning"),
            ("/verbose [on|off|toggle]", "", "Toggle verbose rendering (tool dumps, token lines, etc.)"),
            ("/show-thinking [on|off|toggle]", "", "Toggle display of reasoning deltas"),
            ("/research [status|sources]", "", "Research workflow helpers"),
        ],
    ),
    (
        "Memory, tools, features",
        [
            ("/memory <status|list <target>|clear <target>>", "", "Inspect memory, scratchpad, or any layer (L1-L5)"),
            ("/tool <enable|disable|list>", "", "Enable/disable tools or list all"),
            ("/mcp [list|status|reload|debug <s>]", "", "Manage MCP servers"),
            (
                "/feature <list|new|load|delete|status|phases|create|show|move|block|review|archive|monitor>",
                "",
                "Manage feature-mode plans",
            ),
            (
                "/teach <list|new|load|exit|status|next|grades|curriculum|delete|help>",
                "/t",
                "Manage teacher-mode courses",
            ),
        ],
    ),
    (
        "Shell escape",
        [
            ("/bash <cmd>", "/sh /!", "Run a shell command in the workspace folder (60s timeout)"),
        ],
    ),
    (
        "Extensions",
        [
            ("/skills [<name>|reload|enable <n>|disable <n>]", "", "Manage installed skills"),
            ("/docs [<name>]", "", "Browse bundled documentation"),
        ],
    ),
    (
        "Diagnostics",
        [
            ("/stats", "", "Tokens, cost, memory, context — current snapshot"),
            ("/help", "/h", "Show this menu"),
        ],
    ),
]


def _curated_commands() -> set[str]:
    """Set of leading command names mentioned in the curated _HELP_GROUPS.
    Used by the auto-discovery safety net to find commands that are
    registered but missing from the curated layout."""
    covered: set[str] = set()
    for _, entries in _HELP_GROUPS:
        for cmd, alias, _desc in entries:
            head = cmd.split()[0] if cmd else ""
            if head.startswith("/"):
                covered.add(head)
            for token in (alias or "").replace(",", " ").split():
                token = token.strip()
                if token.startswith("/"):
                    covered.add(token)
    return covered


def _uncurated_commands_section():
    """Build an extra `(group_name, entries)` tuple for commands that are
    registered via `@command` but never made it into `_HELP_GROUPS`.

    Catches regressions where someone adds a slash command but forgets
    to update the curated list — the entry shows up under "Other"
    instead of being invisible.
    """
    from mu.commands import list_commands

    covered = _curated_commands()
    rows: list[tuple[str, str, str]] = []
    seen_specs: set[int] = set()
    for spec in list_commands():
        if id(spec) in seen_specs:
            continue
        seen_specs.add(id(spec))
        primary = spec.names[0]
        if primary in covered:
            continue
        if any(alias in covered for alias in spec.names):
            continue
        aliases = " ".join(spec.names[1:]) if len(spec.names) > 1 else ""
        rows.append((primary, aliases, spec.help or ""))
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    return ("Other", rows)


def print_help():
    groups = list(_HELP_GROUPS)
    extra = _uncurated_commands_section()
    if extra is not None:
        groups.append(extra)
    for group_name, entries in groups:
        table = Table(title=group_name, box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column("Command", style="cyan", no_wrap=True)
        table.add_column("Alias", style="magenta")
        table.add_column("Description", style="white")
        for cmd, alias, desc in entries:
            table.add_row(cmd, alias, desc)
        console.print(table)
    console.print(
        "[dim]Tip: end a line with '\\' to continue typing on the next line. "
        "Tab to autocomplete every command.[/dim]"
    )


def print_splash(session):
    welcome_text = Text()

    # Neon μCLI Cyberpunk Ascii Art
    welcome_text.append(" ██╗   ██╗", style="bold magenta")
    welcome_text.append("  ██████╗ ██╗     ██╗\n", style="bold cyan")
    welcome_text.append(" ██║   ██║", style="bold magenta")
    welcome_text.append(" ██╔════╝ ██║     ██║\n", style="bold cyan")
    welcome_text.append(" ██║   ██║", style="bold magenta")
    welcome_text.append(" ██║      ██║     ██║\n", style="bold cyan")
    welcome_text.append(" ██║   ██║", style="bold magenta")
    welcome_text.append(" ██║      ██║     ██║\n", style="bold cyan")
    welcome_text.append(" ███████╔╝", style="bold magenta")
    welcome_text.append(" ╚██████╗ ███████╗██║\n", style="bold cyan")
    welcome_text.append(" ██╔════╝ ", style="bold magenta")
    welcome_text.append("  ╚═════╝ ╚══════╝╚═╝\n", style="bold cyan")
    welcome_text.append(" ██║      \n", style="bold magenta")
    welcome_text.append(" ╚═╝      \n", style="bold magenta")

    welcome_text.append("\n > _AUTONOMOUS_AGENT_READY\n", style="bold yellow")

    sys_status = "SET" if session.system_instruction else "NONE"
    agent_mode = session.variables.get("agent_mode", "default")
    mode_meta = AGENT_MODE_METADATA.get(str(agent_mode), {})
    mode_description = mode_meta.get("description", "")
    yolo_status = "ON" if session.variables.get("yolo", False) else "OFF"

    # Workspace Folder info
    folders = session.folder_context.folders
    folder_count = len(folders)
    if folder_count == 0:
        folder_list = "None"
    elif folder_count == 1:
        folder_list = folders[0]
    else:
        folder_list = f"{folder_count} folders: " + ", ".join(
            [os.path.basename(f) for f in folders[:3]]
        )
        if folder_count > 3:
            folder_list += " ..."

    info_grid = f"""                                                                   
    [bold magenta]Session:[/bold magenta]  [bold yellow]{session.session_manager.current_session_name}[/bold yellow]
    [bold magenta]System:[/bold magenta]   {sys_status}                                
    [bold magenta]Model:[/bold magenta]    [bold cyan]{session.provider.model_name}[/bold cyan]       
    [bold magenta]Thinking:[/bold magenta] [bold cyan]{session.thinking}[/bold cyan] | [bold magenta]Agentic:[/bold magenta] [bold cyan]{session.agentic}[/bold cyan] | [bold magenta]YOLO:[/bold magenta] [bold cyan]{yolo_status}[/bold cyan]
    [bold magenta]Mode:[/bold magenta]     [bold cyan]{agent_mode}[/bold cyan] — {mode_description}
    [bold magenta]Workspace:[/bold magenta][bold green] {folder_list}[/bold green]
"""
    # Total context (sum of all 7 layers) vs. the global cap. Using the
    # total instead of just history tokens means the warning fires when
    # heavy workspace files + skills + tool activity push us toward the
    # cap, not only when conversation history gets long.
    from utils.runtime_metrics import estimate_active_context_tokens

    context_limit = int(session.variables.get("context_token_limit", 256000) or 256000)
    trim_threshold = float(session.variables.get("context_trim_threshold", 0.85) or 0.85)
    trim_threshold = max(0.10, min(trim_threshold, 1.0))
    context_tokens = int(estimate_active_context_tokens(session) or 0)
    threshold_tokens = int(context_limit * trim_threshold)
    if context_tokens >= threshold_tokens:
        info_grid += f"""
    [bold magenta]Context:[/bold magenta]   [bold cyan]{context_tokens:,}[/bold cyan] / {context_limit:,} tokens  [bold yellow]⚠[/bold yellow] [dim](trim threshold: {int(trim_threshold * 100)}%)[/dim]"""
    else:
        info_grid += f"""
    [bold magenta]Context:[/bold magenta]   [bold cyan]{context_tokens:,}[/bold cyan] / {context_limit:,} tokens"""

    info_grid += "\n    "

    console.print(
        Panel(
            Text.assemble(welcome_text, Text.from_markup(info_grid)),
            title="[bold yellow] // μCLI TERMINAL // [/bold yellow]",
            border_style="cyan",
            box=box.HEAVY,
        )
    )
    console.print("[dim] Type '/help' for commands.[/dim]\n")


def init_provider(provider_name, model_name, ollama_host=None):
    # Init provider contextually
    if provider_name == "ollama":
        if ollama_host:
            provider = OllamaProvider(model_name=model_name, host=ollama_host)
        else:
            provider = OllamaProvider(model_name=model_name)
    elif provider_name == "gemini":
        provider = GeminiProvider(model_name=model_name)
    elif provider_name == "openai":
        provider = OpenAIProvider(model_name=model_name)
    else:
        return None
    return provider


def select_provider_and_model(
    args_provider, args_model, ollama_host=None, allow_prompt=True
):
    providers = ["gemini", "ollama", "openai"]
    provider_name = args_provider

    if provider_name not in providers:
        if not allow_prompt:
            raise ValueError("A valid --provider is required in non-interactive mode.")
        console.print("\n[bold cyan]Available Providers:[/bold cyan]")
        for i, p in enumerate(providers, 1):
            console.print(f" {i}. {p}", markup=False)
        choice = IntPrompt.ask(
            "Select a provider", choices=[str(i) for i in range(1, len(providers) + 1)]
        )
        provider_name = providers[int(choice) - 1]

    provider = init_provider(provider_name, "", ollama_host)
    if not provider:
        raise ValueError(f"Unknown provider: {provider_name}")

    models = provider.get_available_models()
    model_name = args_model

    if not models:
        if not model_name:
            if not allow_prompt:
                raise ValueError(
                    f"A model name is required for provider '{provider_name}' in "
                    "non-interactive mode."
                )
            model_name = Prompt.ask(f"Enter model name manually for {provider_name}")
    elif model_name not in models:
        if not allow_prompt and model_name:
            raise ValueError(
                f"Model '{model_name}' is not available for provider '{provider_name}'."
            )
        if not allow_prompt:
            raise ValueError(
                f"A valid --model is required for provider '{provider_name}' in "
                "non-interactive mode."
            )
        console.print(f"\n[bold cyan]Available Models for {safe_markup(provider_name)}:[/bold cyan]")
        for i, m in enumerate(models, 1):
            console.print(f" {i}. {m}", markup=False)

        choice = IntPrompt.ask(
            "Select a model", choices=[str(i) for i in range(1, len(models) + 1)]
        )
        model_name = models[int(choice) - 1]

    provider.model_name = model_name
    return provider


def _safe_delete_session(session_manager, name: str, *, silent: bool = False) -> None:
    """Drop a session at startup, bypassing the active-session guard.

    `SessionManager.delete_session` refuses to remove the currently-
    active session — but at startup `current_session_name` is just the
    bootstrap default placeholder, the user hasn't loaded anything
    yet. Temporarily clear it so any session can be deleted, then
    restore (unless we just deleted the placeholder itself).

    When `silent=True` the SessionManager's UI is detached for the
    duration of the call so its `show_info("Deleted session: ...")`
    print doesn't punch a hole through an active TUI render (the
    interactive picker uses this)."""
    prior_active = session_manager.current_session_name
    prior_ui = getattr(session_manager, "ui", None)
    session_manager.current_session_name = None
    if silent:
        session_manager.ui = None
    try:
        session_manager.delete_session(name)
    finally:
        if prior_active and prior_active != name:
            session_manager.current_session_name = prior_active
        if silent:
            session_manager.ui = prior_ui


def choose_session(session_manager):
    """Interactive session picker at startup.

    Prefers an arrow-key + key-shortcut picker (prompt-toolkit). Falls
    back to a numbered IntPrompt menu when the TTY isn't suitable
    (CI, weird shells, redirected stdin)."""
    sessions = session_manager.get_session_list()
    if not sessions:
        return "new", None

    try:
        from mu.ui.session_picker import run_interactive_picker

        action, name = run_interactive_picker(
            sessions,
            on_delete=lambda n: _safe_delete_session(session_manager, n, silent=True),
        )
    except Exception:
        # Fall back to the numbered picker so non-TTY environments
        # (CI, pipes) still work.
        return _choose_session_numbered(session_manager)

    if action == "load":
        return "load", name
    if action == "new":
        from rich.prompt import Prompt

        raw = Prompt.ask(
            "Enter name for new session (optional, press enter for default)"
        )
        return "new", raw if raw else None
    # "quit" — caller treats this as a clean exit.
    raise SystemExit(0)


def _choose_session_numbered(session_manager):
    """Numbered fallback for environments where the prompt-toolkit picker
    can't run. Same behavior as before: numbered list + delete sub-flow."""
    while True:
        sessions = session_manager.get_session_list()
        if not sessions:
            return "new", None

        console.print("\n[bold cyan]Available Sessions:[/bold cyan]")
        for i, s in enumerate(sessions, 1):
            console.print(f" {i}. {s}", markup=False)
        new_idx = len(sessions) + 1
        delete_idx = len(sessions) + 2
        console.print(f" {new_idx}. [bold green][New Session][/bold green]")
        console.print(f" {delete_idx}. [bold red][Delete a session…][/bold red]")

        choice = IntPrompt.ask(
            "Select a session",
            choices=[str(i) for i in range(1, delete_idx + 1)],
        )

        if choice == new_idx:
            from rich.prompt import Prompt

            name = Prompt.ask(
                "Enter name for new session (optional, press enter for default)"
            )
            return "new", name if name else None
        if choice == delete_idx:
            _delete_session_flow(session_manager, sessions)
            continue
        return "load", sessions[choice - 1]


def _delete_session_flow(session_manager, sessions):
    """Numbered prompt → confirm → delete. Used by the fallback picker."""
    if not sessions:
        return

    console.print("\n[bold red]Delete a session[/bold red]")
    for i, s in enumerate(sessions, 1):
        console.print(f" {i}. {s}", markup=False)
    cancel_idx = len(sessions) + 1
    console.print(f" {cancel_idx}. [dim][Cancel][/dim]")

    choice = IntPrompt.ask(
        "Pick a session to delete",
        choices=[str(i) for i in range(1, cancel_idx + 1)],
    )
    if choice == cancel_idx:
        console.print("[dim]Cancelled.[/dim]")
        return

    target = sessions[choice - 1]
    from rich.prompt import Confirm

    if not Confirm.ask(
        f"Delete session [bold red]{target!r}[/bold red]? This cannot be undone.",
        default=False,
    ):
        console.print("[dim]Cancelled.[/dim]")
        return

    _safe_delete_session(session_manager, target)


def sync_provider_settings(session):
    if isinstance(session.provider, OllamaProvider):
        # Respect a per-session override for ollama_host; otherwise let the
        # provider's own resolution (OLLAMA_HOST env → OLLAMA_API_KEY hosted
        # → localhost) stand.
        host_override = session.variables.get("ollama_host")
        if host_override:
            session.provider.host = host_override
            session.provider.invalidate_preflight()
        # Bind variables so the provider picks up `/set ollama_num_ctx`
        # etc. on the next call.
        if hasattr(session.provider, "bind_session_variables"):
            session.provider.bind_session_variables(session.variables)


def build_session(args, ui, allow_prompt=True):
    session_manager = SessionManager(ui=ui, session_name=args.session)
    if ui and hasattr(ui, "set_variables"):
        ui.set_variables(session_manager.variables)

    ollama_host = session_manager.variables.get("ollama_host")

    if allow_prompt and not args.session:
        action, session_name = choose_session(session_manager)
        if action == "load":
            session_manager.switch_session(session_name)
            provider_config = session_manager.provider_config
            if provider_config.get("provider") and provider_config.get("model"):
                provider = init_provider(
                    provider_config["provider"],
                    provider_config["model"],
                    ollama_host=ollama_host,
                )
            else:
                provider = select_provider_and_model(
                    args.provider,
                    args.model,
                    ollama_host=ollama_host,
                    allow_prompt=allow_prompt,
                )
                session_manager.provider_config = {
                    "provider": provider.name,
                    "model": provider.model_name,
                }
                session_manager.save_history()
        else:
            provider = select_provider_and_model(
                args.provider,
                args.model,
                ollama_host=ollama_host,
                allow_prompt=allow_prompt,
            )
            session_manager.new_session(
                session_name, provider.name, provider.model_name
            )
    else:
        provider = None
        provider_name = args.provider
        model_name = args.model
        provider_config = session_manager.provider_config

        if provider_name and model_name:
            provider = select_provider_and_model(
                provider_name,
                model_name,
                ollama_host=ollama_host,
                allow_prompt=allow_prompt,
            )
            session_manager.provider_config = {
                "provider": provider.name,
                "model": provider.model_name,
            }
            session_manager.save_history()
        elif provider_config.get("provider") and provider_config.get("model"):
            provider = init_provider(
                provider_config["provider"],
                provider_config["model"],
                ollama_host=ollama_host,
            )
        elif provider_name and model_name:
            provider = init_provider(provider_name, model_name, ollama_host=ollama_host)

        if not provider:
            raise ValueError(
                "Unable to determine provider/model. Supply --provider and --model, "
                "or reuse a saved session with provider configuration."
            )

    session = Session(
        provider=provider,
        thinking=False,
        system_instruction=args.system,
        session_manager=session_manager,
        ui=ui,
        debug=args.debug,
    )

    # If the session we just loaded has in-flight teacher / feature
    # state, queue a resumption briefing so the agent's first turn
    # knows what's already running without making the user re-explain.
    try:
        from mu.commands.session import _queue_session_resumption_briefing

        _queue_session_resumption_briefing(session)
    except ImportError:
        pass

    if args.workspace:
        for workspace in args.workspace:
            session.folder_context.add_folder(workspace)
        session.session_manager.save_history(session.folder_context)

    if args.yolo:
        session.variables["yolo"] = True
        session.session_manager.save_history(session.folder_context)

    # Auto-load hooks.json and MCP servers from `.mu/`. Failures log a
    # warning and continue — one bad config file should not block the REPL.
    try:
        from mu.agent.hooks_config import load_hooks_from_config

        load_hooks_from_config()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("hooks.json: load failed: %s", exc)
    try:
        import atexit as _atexit

        from mu.mcp import close_all as _mcp_close_all
        from mu.mcp import register_all as _mcp_register_all

        session._mcp_clients = _mcp_register_all()
        if session._mcp_clients:
            # Make sure subprocess'd MCP servers don't outlive the REPL.
            # The closure captures the list reference, so `/mcp reload`
            # replacing `session._mcp_clients` is also picked up here.
            _atexit.register(lambda: _mcp_close_all(session._mcp_clients))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("mcp.json: load failed: %s", exc)
        session._mcp_clients = []

    sync_provider_settings(session)
    return session


def serialize_command_result(session, command, ok=True, message=None, data=None):
    return {
        "ok": ok,
        "command": command,
        "message": message,
        "data": data or {},
        "session_name": session.session_manager.current_session_name,
        "provider": session.provider.name,
        "model": session.provider.model_name,
        "variables": dict(session.variables),
        "folders": list(session.folder_context.folders),
        "history_length": len(session.session_manager.history),
    }


def handle_command(session, user_input, allow_prompt=True):
    """Thin shim around `mu.commands.dispatch`.

    Every slash command lives in `mu/commands/<module>.py`. This function
    exists only to serialize the registry's `CommandResult` into the
    dict shape callers (REPL loop, web UI, JSON output) expect.
    """
    parts = user_input.split(" ", 1)
    cmd = parts[0].lower()

    import mu.commands as _mu_commands

    new_result = _mu_commands.dispatch(session, user_input, allow_prompt=allow_prompt)
    if new_result is not None:
        data = dict(new_result.data or {})
        if new_result.exit:
            data["exit"] = True
        return serialize_command_result(
            session,
            cmd,
            ok=new_result.ok,
            message=new_result.message,
            data=data,
        )

    ui = session.ui
    if ui:
        ui.show_error(f"Unknown command: {cmd}")
    return serialize_command_result(
        session, cmd, ok=False, message=f"Unknown command: {cmd}"
    )


def main():
    logger.info("μCLI starting...")

    parser = argparse.ArgumentParser(description="Interactive AI CLI")
    parser.add_argument("--model", default=None, help="Default model")
    parser.add_argument(
        "--provider",
        default=None,
        choices=["gemini", "ollama", "openai"],
        help="LLM provider to use",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Load the specified saved session instead of prompting.",
    )
    parser.add_argument(
        "--workspace",
        action="append",
        default=[],
        help="Attach a workspace folder at startup. May be provided multiple times.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Enable YOLO mode at startup.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "--system",
        type=str,
        default="""You are a helpful assistant, answer all questions succinctly.
        When providing code changes or file content:

  1. Always use a standard markdown pattern for code blocks: (```language ... ```).
  2. For code modifications/diffs, use the same code block style as point .1
  3. For new files or partial snippets, use the specific language tag (e.g., 'python', 'cpp')
  4. Always precede the code block with a clear header including the file path, for example: \"### File: src/main.cpp\".
  5. Only provide the new code or specific changes; do not regenerate whole files unless specifically asked.
  """,
        help="Initial system instruction",
    )
    args = parser.parse_args()
    ui = RichUI()

    try:
        session = build_session(args, ui, allow_prompt=True)
    except Exception as exc:
        console.print(f"[red]Failed to initialize Session/Provider: {safe_markup(exc)}[/red]")
        sys.exit(1)

    print_splash(session)
    refresh_memory_hud(session, ui)

    while True:
        try:
            current_task = get_current_feature_task_label(session)
            feature_context = get_feature_prompt_context(session)
            user_input = ui.get_input(
                session.session_manager.current_session_name,
                session.staged_files,
                agent_mode=session.variables.get("agent_mode", "default"),
                current_task=current_task,
                feature_context=feature_context,
            )

            if not user_input:
                continue

            if user_input.startswith("/"):
                result = handle_command(session, user_input, allow_prompt=True)
                if result.get("data", {}).get("exit"):
                    break
                continue

            send_result = session.send_message(user_input)
            if send_result.get("status") == "interrupted":
                console.print(
                    "[dim]Execution paused. Type /continue to resume, or enter a new prompt to re-guide the agent.[/dim]"
                )
            refresh_memory_hud(session, ui)

        except KeyboardInterrupt:
            console.print("\n(Interrupted. Type /quit to exit)")
        except EOFError:
            console.print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
