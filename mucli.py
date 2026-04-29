#!/usr/bin/env python

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.text import Text
from rich.table import Table
from rich import box

# Import from our new modular structure
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from utils.logger import logger
from providers.openai import OpenAIProvider
from core.server import HeadlessUI, serve
from core.session import SessionManager, Session, derive_feature_state_status
from core.feature_mode import (
    load_feature_plan,
    refresh_and_persist_feature_plan,
    save_feature_plan,
    summarize_feature_plan,
)
from core.tools import execute_tool
from ui.rich_ui import RichUI
from ui.gui_tui import run_gui_mode
from utils.config import AGENT_MODE_METADATA
from utils.config import SESSION_DIR
from utils.runtime_metrics import collect_context_layers

console = Console()
GITHUB_API_BASE = "https://api.github.com"


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
    console.print(f"[dim]Current mode: {current_mode}[/dim]")


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
    console.print(f"[bold cyan]{question}[/bold cyan]")
    for idx, (_, label) in enumerate(choices, start=1):
        console.print(f"  {idx}. {label}")
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


def print_help():
    table = Table(title="Available Commands", box=box.SIMPLE)
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Alias", style="magenta")
    table.add_column("Description", style="white")

    table.add_row("/clear", "", "Clear conversation history")
    table.add_row("/new [name]", "", "Start a new conversation")
    table.add_row("/delete <name>", "/rm", "Delete a saved conversation")
    table.add_row("/file <path>", "/f", "Attach a file")
    table.add_row("/clearfiles", "/cf", "Clear staged files")
    table.add_row("/clear-workspace", "/cw", "Clear all workspace folders")
    table.add_row(
        "/folder <path>", "/dir", "Monitor a folder(s) for changes and use as context"
    )
    table.add_row(
        "/memory <status|list|clear>", "", "Manage memory (e.g. clear scratch|task|all)"
    )
    table.add_row("/help", "", "Show this help menu")
    table.add_row("/list", "/ls", "List saved conversations")
    table.add_row("/load [name]", "/open", "Load a conversation")
    table.add_row("/model [name]", "", "Show / change current model")
    table.add_row("/get [key]", "", "Get a variable")
    table.add_row("/yolo", "", "Toggle YOLO mode (no approvals)")
    table.add_row("/set [key] [value]", "", "Set a variable")
    table.add_row("/unset [key]", "", "Unset a variable (or --all)")
    table.add_row(
        "/flush", "", "Flush the collation buffer and inject context into the next turn"
    )
    table.add_row("/variables", "", "Show all variables")
    table.add_row("/agentic", "", "Toggle Agentic (Tool Calling) mode")
    table.add_row(
        "/tool <enable/disable/list>",
        "/tools",
        "Enable/Disable a tool or list all available tools",
    )
    table.add_row(
        "/mode <mode>",
        "",
        "Change the agentic strategy (default, debug, feature, research)",
    )
    table.add_row(
        "/feature <list|new|load|delete|status|phases|exit|create|show|move|block|review|archive|monitor>",
        "/features",
        "Manage per-session feature plans and switch the active feature",
    )
    table.add_row(
        "/research <status|sources|query>",
        "",
        "Research workflow commands (citation-first prompts, source review)",
    )
    table.add_row("/provider [name]", "", "Change the LLM provider (gemini, ollama)")
    table.add_row(
        "/update", "", "Attempt to update μCLI from the configured git remote"
    )
    table.add_row("/quit", "/q", "Exit")
    table.add_row("/continue", "", "Resume last paused execution after Ctrl+C")
    table.add_row(
        "/stats",
        "",
        "Show runtime stats, token/cost totals, and feature progress",
    )
    table.add_row("/system <txt>", "/sys", "Update system prompt")
    table.add_row("/thinking", "", "Toggle thinking mode")
    table.add_row("/view", "", "View conversation history")
    table.add_row("/workspace [clear]", "", "List or clear workspace metadata")

    console.print(table)
    console.print(
        "[dim]Tip: End a line with '\\' to continue typing on the next line.[/dim]"
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
    subagent_counts = session.get_subagent_counts() if hasattr(session, "get_subagent_counts") else {}
    subagent_running = int(subagent_counts.get("running", 0) or 0)
    subagent_queued = int(subagent_counts.get("queued", 0) or 0)
    subagent_done = int(subagent_counts.get("completed", 0) or 0)

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
    [bold magenta]SubAgents:[/bold magenta] [bold cyan]running={subagent_running} queued={subagent_queued} done={subagent_done}[/bold cyan]
"""
    # Add context warning if nearing token limit
    context_limit = int(session.variables.get("context_token_limit", 256000) or 256000)
    trim_threshold = float(session.variables.get("context_trim_threshold", 0.85) or 0.85)
    trim_threshold = max(0.10, min(trim_threshold, 1.0))
    context_tokens = int(session.session_manager.estimate_runtime_history_tokens() or 0)
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


def _run_command(command, cwd=None):
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _parse_github_repo(remote_url):
    if not remote_url:
        return None
    value = remote_url.strip()
    if value.endswith(".git"):
        value = value[:-4]

    ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+)$", value)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    parsed = urllib.parse.urlparse(value)
    if parsed.netloc.lower() != "github.com":
        return None
    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def fetch_latest_github_release(repo_slug):
    url = f"{GITHUB_API_BASE}/repos/{repo_slug}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mucli-updater",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {
        "tag_name": str(payload.get("tag_name", "") or "").strip(),
        "name": str(payload.get("name", "") or "").strip(),
        "html_url": str(payload.get("html_url", "") or "").strip(),
    }


def get_release_update_status():
    origin = _run_command(["git", "remote", "get-url", "origin"])
    if origin.returncode != 0:
        return {"ok": False, "message": "No git origin configured for update checks."}

    repo_slug = _parse_github_repo(origin.stdout.strip())
    if not repo_slug:
        return {"ok": False, "message": "Origin is not a GitHub repository."}

    try:
        release = fetch_latest_github_release(repo_slug)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {
                "ok": False,
                "message": "No published GitHub releases found for this repository.",
            }
        return {"ok": False, "message": f"Release lookup failed (HTTP {exc.code})."}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "message": f"Release lookup failed: {exc}"}

    latest_tag = release.get("tag_name")
    if not latest_tag:
        return {"ok": False, "message": "Latest release did not include a tag."}

    local_tags = _run_command(["git", "tag", "--points-at", "HEAD"])
    if local_tags.returncode != 0:
        return {"ok": False, "message": "Unable to inspect local git tags."}
    head_tags = {tag.strip() for tag in local_tags.stdout.splitlines() if tag.strip()}

    return {
        "ok": True,
        "repo": repo_slug,
        "latest_release": release,
        "head_tags": sorted(head_tags),
        "update_available": latest_tag not in head_tags,
    }


def run_auto_update():
    repo_root_result = _run_command(["git", "rev-parse", "--show-toplevel"])
    if repo_root_result.returncode != 0:
        return {
            "ok": False,
            "message": "Unable to locate git repository root for update.",
            "steps": [],
        }

    repo_root = repo_root_result.stdout.strip()
    steps = []

    pull_result = _run_command(["git", "pull", "--ff-only"], cwd=repo_root)
    steps.append(
        {
            "name": "git pull --ff-only",
            "returncode": pull_result.returncode,
            "stdout": pull_result.stdout.strip(),
            "stderr": pull_result.stderr.strip(),
        }
    )
    if pull_result.returncode != 0:
        return {
            "ok": False,
            "message": "Update failed while pulling latest changes from git remote.",
            "steps": steps,
        }

    requirements_path = os.path.join(repo_root, "requirements.txt")
    if os.path.exists(requirements_path):
        pip_result = _run_command(
            [sys.executable, "-m", "pip", "install", "-r", requirements_path],
            cwd=repo_root,
        )
        steps.append(
            {
                "name": f"{sys.executable} -m pip install -r requirements.txt",
                "returncode": pip_result.returncode,
                "stdout": pip_result.stdout.strip(),
                "stderr": pip_result.stderr.strip(),
            }
        )
        if pip_result.returncode != 0:
            return {
                "ok": False,
                "message": "Git update succeeded, but dependency refresh failed.",
                "steps": steps,
            }

    return {
        "ok": True,
        "message": "μCLI update completed successfully.",
        "steps": steps,
    }


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
            console.print(f" {i}. {p}")
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
        console.print(f"\n[bold cyan]Available Models for {provider_name}:[/bold cyan]")
        for i, m in enumerate(models, 1):
            console.print(f" {i}. {m}")

        choice = IntPrompt.ask(
            "Select a model", choices=[str(i) for i in range(1, len(models) + 1)]
        )
        model_name = models[int(choice) - 1]

    provider.model_name = model_name
    return provider


def choose_session(session_manager):
    sessions = session_manager.get_session_list()
    if not sessions:
        return "new", None

    console.print("\n[bold cyan]Available Sessions:[/bold cyan]")
    for i, s in enumerate(sessions, 1):
        console.print(f" {i}. {s}")
    console.print(f" {len(sessions) + 1}. [New Session]")

    choice = IntPrompt.ask(
        "Select a session", choices=[str(i) for i in range(1, len(sessions) + 2)]
    )

    if choice == len(sessions) + 1:
        from rich.prompt import Prompt

        name = Prompt.ask(
            "Enter name for new session (optional, press enter for default)"
        )
        return "new", name if name else None
    else:
        return "load", sessions[choice - 1]


def sync_provider_settings(session):
    if isinstance(session.provider, OllamaProvider):
        # Default Ollama host is http://localhost:11434
        host = session.variables.get("ollama_host", "http://localhost:11434")
        session.provider.host = host


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

    if args.workspace:
        for workspace in args.workspace:
            session.folder_context.add_folder(workspace)
        session.session_manager.save_history(session.folder_context)

    if args.yolo:
        session.variables["yolo"] = True
        session.session_manager.save_history(session.folder_context)

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
    ui = session.ui
    parts = user_input.split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ["/quit", "/exit", "/q"]:
        if allow_prompt:
            print("Goodbye!")
        return serialize_command_result(
            session, cmd, message="Goodbye!", data={"exit": True}
        )

    if cmd == "/continue":
        paused_text = str(getattr(session, "paused_execution_text", "") or "").strip()
        if not paused_text:
            return serialize_command_result(
                session,
                cmd,
                ok=False,
                message="No paused execution to continue.",
            )
        if allow_prompt:
            console.print("[dim]Resuming paused execution...[/dim]")
        send_result = session.send_message(paused_text)
        return serialize_command_result(
            session,
            cmd,
            ok=bool(send_result.get("ok", True)),
            message="Resumed paused execution.",
            data={"resumed_text": paused_text, "send_result": send_result},
        )

    if cmd in ["/help", "/h"]:
        if allow_prompt:
            print_help()
        return serialize_command_result(session, cmd, data={"commands_help": True})

    if cmd in ["/clear", "/c"]:
        session.session_manager.clear_current_history()
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message="Conversation history cleared."
        )

    if cmd in ["/view", "/v"]:
        if allow_prompt:
            session.session_manager.view_history()
        return serialize_command_result(
            session,
            cmd,
            data={"history": session.session_manager.history},
        )

    if cmd in ["/file", "/f", "/add"]:
        if not arg:
            if ui:
                ui.show_error("Usage: /file <path_to_file>")
            return serialize_command_result(
                session, cmd, ok=False, message="Usage: /file <path_to_file>"
            )
        session.add_file(arg)
        return serialize_command_result(
            session,
            cmd,
            message=f"Staged file: {arg}",
            data={"staged_files": list(session.staged_files)},
        )

    if cmd in ["/clearfiles", "/cf"]:
        session.clear_files()
        return serialize_command_result(session, cmd, message="Staged files cleared.")

    if cmd in ["/clear-workspace", "/cw"]:
        session.folder_context.folders.clear()
        session.folder_context.workspace_file_tree = None
        session.session_manager.save_history(session.folder_context)
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message="Workspace folders cleared."
        )

    if cmd in ["/folder", "/dir"]:
        if arg:
            sub_parts = arg.split(" ", 1)
            if sub_parts[0] == "clear":
                session.folder_context.folders.clear()
                session.folder_context.workspace_file_tree = None
                session.session_manager.save_history(session.folder_context)
                refresh_memory_hud(session, ui)
                return serialize_command_result(
                    session, cmd, message="Workspace folders cleared."
                )
            if sub_parts[0] == "remove" and len(sub_parts) > 1:
                path_to_remove = sub_parts[1].strip("'\"")
                removed = session.folder_context.remove_folder(path_to_remove)
                if removed:
                    if ui:
                        ui.show_info(f"Removed folder from context: {path_to_remove}")
                    session.session_manager.save_history(session.folder_context)
                    refresh_memory_hud(session, ui)
                    return serialize_command_result(
                        session,
                        cmd,
                        message=f"Removed folder from context: {path_to_remove}",
                    )
                if ui:
                    ui.show_error(f"Folder not found in context: {path_to_remove}")
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message=f"Folder not found in context: {path_to_remove}",
                )

            added = []
            invalid = []
            try:
                paths = shlex.split(arg)
            except ValueError:
                paths = [arg.strip("'\"")]

            for path in paths:
                path = path.strip("'\"")
                if session.folder_context.add_folder(path):
                    added.append(path)
                    if ui:
                        ui.show_info(f"Added folder context: {path}")
                    if len(session.folder_context.folders) == 1:
                        try:
                            os.chdir(session.folder_context.folders[0])
                            if ui:
                                ui.show_info(f"Switched workspace to: {os.getcwd()}")
                        except Exception:
                            pass
                else:
                    invalid.append(path)
                    if ui:
                        ui.show_error(f"Folder not found or invalid: {path}")

            session.session_manager.save_history(session.folder_context)
            if added and ui:
                ui.show_info(
                    "Files cached as initial context. Changes will be provided as diffs."
                )
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                ok=not invalid,
                message="Workspace folders updated.",
                data={"added": added, "invalid": invalid},
            )

        if allow_prompt and not session.folder_context.folders:
            console.print("[yellow]No folders currently monitored.[/yellow]")
            console.print("Usage: /folder <path> OR /folder remove <path>")
        else:
            console.print(
                f"[dim]Workspace folders: {session.folder_context.folders}[/dim]"
            )

        return serialize_command_result(
            session,
            cmd,
            data={"folders": list(session.folder_context.folders)},
        )

    if cmd in ["/list", "/ls"]:
        if allow_prompt:
            session.session_manager.list_sessions()
        return serialize_command_result(
            session,
            cmd,
            data={"sessions": session.session_manager.get_session_list()},
        )

    if cmd in ["/new"]:
        name = arg.strip() if arg else None
        ollama_host = session.variables.get("ollama_host")
        if not allow_prompt and not (
            session.provider.name and session.provider.model_name
        ):
            return serialize_command_result(
                session,
                cmd,
                ok=False,
                message="Non-interactive mode requires an active provider/model to create a new session.",
            )
        if allow_prompt:
            new_provider = select_provider_and_model(
                None,
                None,
                ollama_host=ollama_host,
                allow_prompt=allow_prompt,
            )
            session.provider = new_provider
        session.session_manager.new_session(
            name,
            session.provider.name,
            session.provider.model_name,
        )
        session.staged_files = []
        session.sync_runtime_state()
        if ui and hasattr(ui, "set_variables"):
            ui.set_variables(session.variables)
        if allow_prompt:
            print_splash(session)
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session,
            cmd,
            message=f"Started new session: {session.session_manager.current_session_name}",
        )

    if cmd in ["/load", "/open"]:
        if not arg:
            if ui:
                ui.show_error("Usage: /load <session_name>")
            return serialize_command_result(
                session, cmd, ok=False, message="Usage: /load <session_name>"
            )
        session.session_manager.switch_session(arg.strip())
        session.staged_files = []
        session.sync_runtime_state()
        if ui and hasattr(ui, "set_variables"):
            ui.set_variables(session.variables)
        provider_config = session.session_manager.provider_config
        if provider_config.get("provider") and provider_config.get("model"):
            ollama_host = session.variables.get("ollama_host")
            session.provider = init_provider(
                provider_config["provider"],
                provider_config["model"],
                ollama_host,
            )
        sync_provider_settings(session)
        if allow_prompt:
            print_splash(session)
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session,
            cmd,
            message=f"Loaded session: {session.session_manager.current_session_name}",
        )

    if cmd in ["/delete", "/rm"]:
        if not arg:
            if ui:
                ui.show_error("Usage: /delete <session_name>")
            return serialize_command_result(
                session, cmd, ok=False, message="Usage: /delete <session_name>"
            )
        session.session_manager.delete_session(arg.strip())
        return serialize_command_result(
            session, cmd, message=f"Deleted session request: {arg.strip()}"
        )

    if cmd in ["/system", "/sys"]:
        if arg:
            session.system_instruction = arg
            if ui:
                ui.show_info("System prompt updated.")
            return serialize_command_result(
                session, cmd, message="System prompt updated."
            )
        current = session.system_instruction if session.system_instruction else "None"
        if allow_prompt:
            console.print(f"[blue]Current System Prompt:\n{current}")
        return serialize_command_result(
            session, cmd, data={"system_instruction": current}
        )

    if cmd == "/model":
        if not arg:
            if not allow_prompt:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Non-interactive mode requires /model <name>.",
                )
            models = session.provider.get_available_models()
            if models:
                console.print("\n[bold cyan]Available Models:[/bold cyan]")
                for i, model in enumerate(models, 1):
                    console.print(f" {i}. {model}")
                choice = IntPrompt.ask(
                    "Select a model",
                    choices=[str(i) for i in range(1, len(models) + 1)],
                )
                arg = models[int(choice) - 1]
            else:
                return serialize_command_result(
                    session, cmd, ok=False, message="No models available."
                )

        session.provider.model_name = arg.strip()
        session.session_manager.provider_config = {
            "provider": session.provider.name,
            "model": session.provider.model_name,
        }
        session.session_manager.save_history()
        if ui:
            ui.show_info(f"Model changed to: {session.provider.model_name}")
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message=f"Model changed to {session.provider.model_name}."
        )

    if cmd == "/provider":
        if not arg and not allow_prompt:
            return serialize_command_result(
                session,
                cmd,
                ok=False,
                message="Non-interactive mode requires /provider <name>.",
            )
        try:
            ollama_host = session.variables.get("ollama_host")
            session.provider = select_provider_and_model(
                arg.strip() if arg else None,
                session.provider.model_name if not allow_prompt else None,
                ollama_host=ollama_host,
                allow_prompt=allow_prompt,
            )
            session.session_manager.provider_config = {
                "provider": session.provider.name,
                "model": session.provider.model_name,
            }
            session.session_manager.save_history()
            if ui:
                ui.show_info("Provider changed successfully!")
            if allow_prompt:
                print_splash(session)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session, cmd, message="Provider changed successfully."
            )
        except Exception as exc:
            if ui:
                ui.show_error(f"Failed to change provider: {exc}")
            return serialize_command_result(session, cmd, ok=False, message=str(exc))

    if cmd == "/set":
        if not arg:
            if ui:
                ui.show_error("Usage: /set <key> <value>")
            return serialize_command_result(
                session, cmd, ok=False, message="Usage: /set <key> <value>"
            )
        if "=" in arg:
            key, value = arg.split("=", 1)
        elif " " in arg:
            key, value = arg.split(" ", 1)
        else:
            if ui:
                ui.show_error("Usage: /set <key> <value> OR /set <key>=<value>")
            return serialize_command_result(
                session,
                cmd,
                ok=False,
                message="Usage: /set <key> <value> OR /set <key>=<value>",
            )
        key = key.strip()
        value = value.strip()
        try:
            from utils.config import validate_and_cast

            session.variables[key] = validate_and_cast(key, value)
            session.session_manager.save_history(session.folder_context)
            if ui:
                ui.show_info(
                    f"Set variable: {key} = {session.variables[key]} ({type(session.variables[key]).__name__})"
                )
            if key == "ollama_host":
                sync_provider_settings(session)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message=f"Set variable: {key}",
                data={"key": key, "value": session.variables[key]},
            )
        except ValueError as exc:
            if ui:
                ui.show_error(f"Error: {exc}")
            return serialize_command_result(session, cmd, ok=False, message=str(exc))

    if cmd == "/get":
        key = arg.strip()
        if not key:
            if allow_prompt:
                for variable_key, variable_value in session.variables.items():
                    console.print(f"[blue]{variable_key}[/blue] = {variable_value}")
            return serialize_command_result(
                session, cmd, data={"variables": dict(session.variables)}
            )
        return serialize_command_result(
            session,
            cmd,
            data={"key": key, "value": session.variables.get(key)},
        )

    if cmd == "/unset":
        key = arg.strip()
        if not key:
            if ui:
                ui.show_error("Usage: /unset <key> OR /unset --all")
            return serialize_command_result(
                session, cmd, ok=False, message="Usage: /unset <key> OR /unset --all"
            )
        if key == "--all":
            session.variables.clear()
            from utils.config import DEFAULT_VARIABLES

            session.variables.update(DEFAULT_VARIABLES)
            session.session_manager.save_history(session.folder_context)
            sync_provider_settings(session)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session, cmd, message="All variables reset to defaults."
            )
        if key in session.variables:
            from utils.config import VARIABLE_SCHEMA

            if key in VARIABLE_SCHEMA:
                session.variables[key] = VARIABLE_SCHEMA[key]["default"]
            else:
                del session.variables[key]
            session.session_manager.save_history(session.folder_context)
            if key == "ollama_host":
                sync_provider_settings(session)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message=f"Unset variable: {key}",
                data={"key": key, "value": session.variables.get(key)},
            )
        return serialize_command_result(
            session, cmd, ok=False, message=f"Variable '{key}' not found."
        )

    if cmd == "/flush":
        if hasattr(session, "collation_buffer"):
            count = len(session.collation_buffer.entries)
            if count == 0:
                if ui:
                    ui.show_info("Collation buffer is empty.")
                return serialize_command_result(
                    session, cmd, message="Collation buffer is empty."
                )
            collated = session.collation_buffer.flush()
            text = "### Collated Context Flushed by User:\n\n" + "\n\n".join(collated)
            send_result = session.send_message(text)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message=f"Flushed {count} items from buffer into conversation history.",
                data={"flushed_items": count, "send_result": send_result},
            )

    if cmd == "/variables":
        if allow_prompt:
            for variable_key, variable_value in session.variables.items():
                console.print(
                    f"[blue]{variable_key}[/blue] = [green]{variable_value}[/green]"
                )
        return serialize_command_result(
            session, cmd, data={"variables": dict(session.variables)}
        )

    if cmd == "/research":
        research_query = (arg or "").strip()
        research_cmd = research_query.lower()

        if research_cmd in {"status", ""}:
            active_mode = str(session.variables.get("agent_mode", "default"))
            sources = _extract_recent_sources(session.session_manager.history, limit=6)
            return serialize_command_result(
                session,
                cmd,
                message="Research status snapshot.",
                data={
                    "current_mode": active_mode,
                    "available_tools": _research_tool_names(),
                    "recent_sources": sources,
                    "citation_policy": "When researching, include source URLs and cite claims.",
                },
            )

        if research_cmd == "sources":
            sources = _extract_recent_sources(session.session_manager.history, limit=20)
            return serialize_command_result(
                session,
                cmd,
                message="Collected recent research sources.",
                data={"sources": sources},
            )

        if not research_query:
            return serialize_command_result(
                session,
                cmd,
                ok=False,
                message="Usage: /research <status|sources|query>",
            )
        session.variables["agent_mode"] = "research"
        session.session_manager.save_history(session.folder_context)
        refresh_memory_hud(session, ui)
        research_prompt = (
            "Research request:\n"
            f"{research_query}\n\n"
            "Requirements:\n"
            "- Prefer primary/official sources when possible.\n"
            "- Include explicit source URLs.\n"
            "- Clearly separate facts vs inference.\n"
        )
        send_result = session.send_message(research_prompt)
        return serialize_command_result(
            session,
            cmd,
            ok=bool(send_result.get("ok", True)),
            message="Executed research query.",
            data={"query": research_query, "send_result": send_result},
        )

    if cmd == "/mode":
        valid_modes = list(AGENT_MODE_METADATA.keys())
        if arg and arg.lower() in valid_modes:
            session.variables["agent_mode"] = arg.lower()
            session.session_manager.save_history(session.folder_context)
            refresh_memory_hud(session, ui)
            mode_meta = AGENT_MODE_METADATA[arg.lower()]
            return serialize_command_result(
                session,
                cmd,
                message=(
                    f"Agent strategy set to: {arg.lower()} — "
                    f"{mode_meta.get('description', '')} "
                    f"({mode_meta.get('documentation', '')})"
                ).strip(),
                data={
                    "current_mode": arg.lower(),
                    "mode": {
                        "name": arg.lower(),
                        **mode_meta,
                    },
                    "available_modes": AGENT_MODE_METADATA,
                },
            )
        if not arg:
            if allow_prompt:
                print_mode_overview(session)
            return serialize_command_result(
                session,
                cmd,
                message="Listed available agent modes.",
                data={
                    "current_mode": session.variables.get("agent_mode", "default"),
                    "available_modes": AGENT_MODE_METADATA,
                },
            )
        if allow_prompt:
            print_mode_overview(session)
        return serialize_command_result(
            session,
            cmd,
            ok=False,
            message=f"Unknown mode: {arg}",
            data={
                "current_mode": session.variables.get("agent_mode", "default"),
                "available_modes": AGENT_MODE_METADATA,
            },
        )

    if cmd == "/workspace":
        workspace_arg = arg.strip().lower()
        if workspace_arg == "clear":
            session.folder_context.folders.clear()
            session.folder_context.workspace_file_tree = None
            session.session_manager.save_history(session.folder_context)
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session, cmd, message="Workspace folders cleared."
            )

        console.print("\n[bold cyan]Workspace Folders:[/bold cyan]")
        console.print(session.folder_context.get_tree_map())
        return serialize_command_result(
            session,
            cmd,
            data={"folders": list(session.folder_context.folders)},
        )

    if cmd in ["/feature", "/features"]:
        feature_parts = arg.split(" ", 1) if arg else ["list"]
        feature_cmd = feature_parts[0].lower()
        feature_arg = feature_parts[1].strip() if len(feature_parts) > 1 else ""

        if feature_cmd in {"exit", "unload"}:
            if not isinstance(session.session_manager.get_feature_state(), dict):
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="No active feature to exit.",
                )
            session.session_manager.clear_feature_state(session.folder_context)
            session.sync_runtime_state()
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message="Exited active feature context.",
                data={
                    "active_feature_id": session.session_manager.active_feature_id,
                    "feature": session.session_manager.get_feature_state(),
                },
            )

        if feature_cmd == "new":
            if not feature_arg:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Usage: /feature new <feature_name>",
                )
            record = session.session_manager.create_feature_record(
                feature_arg,
                directory=_default_feature_directory(session, feature_arg),
                feature_request=feature_arg,
            )
            session.sync_runtime_state()
            markdown = build_feature_markdown(
                {
                    **record,
                    "token_total": session.session_manager.token_counts.get("total", 0),
                }
            )
            if allow_prompt:
                console.print(Markdown(markdown))
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message=f"Created feature: {record['feature_id']}",
                data={
                    "feature": record,
                    "markdown": markdown,
                    "features": session.session_manager.list_features(),
                },
            )

        if feature_cmd in {"list", ""}:
            features = [
                refresh_feature_record(session, feature["feature_id"]) or feature
                for feature in session.session_manager.list_features()
            ]
            if allow_prompt:
                table = Table(title="Session Features", box=box.ROUNDED)
                table.add_column("ID", style="cyan", no_wrap=True)
                table.add_column("Current", style="yellow", justify="center")
                table.add_column("Status", style="green")
                table.add_column("Name", style="white")
                table.add_column("Directory", style="magenta")
                if features:
                    for feature in features:
                        table.add_row(
                            feature.get("feature_id", ""),
                            (
                                "*"
                                if feature.get("feature_id")
                                == session.session_manager.active_feature_id
                                else ""
                            ),
                            feature.get("status", "unknown"),
                            feature.get("feature_name", ""),
                            feature.get("directory", ""),
                        )
                else:
                    table.add_row("-", "", "none", "No features saved", "")
                console.print(table)
            return serialize_command_result(
                session,
                cmd,
                message="Listed session features.",
                data={
                    "features": features,
                    "active_feature_id": session.session_manager.active_feature_id,
                },
            )

        if feature_cmd == "load":
            if not feature_arg:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Usage: /feature load <feature_id>",
                )
            record = refresh_feature_record(session, feature_arg)
            if not isinstance(record, dict):
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message=f"Feature '{feature_arg}' not found.",
                )
            activated = session.session_manager.activate_feature(record["feature_id"])
            session.sync_runtime_state()
            markdown = build_feature_markdown(
                {
                    **activated,
                    "token_total": session.session_manager.token_counts.get("total", 0),
                }
            )
            if allow_prompt:
                console.print(Markdown(markdown))
            refresh_memory_hud(session, ui)
            return serialize_command_result(
                session,
                cmd,
                message=f"Loaded feature: {record['feature_id']}",
                data={"feature": activated, "markdown": markdown},
            )

        if feature_cmd == "delete":
            if not feature_arg:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Usage: /feature delete <feature_id>",
                )
            deleted = session.session_manager.delete_feature(feature_arg)
            session.sync_runtime_state()
            refresh_memory_hud(session, ui)
            if not isinstance(deleted, dict):
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message=f"Feature '{feature_arg}' not found.",
                )
            return serialize_command_result(
                session,
                cmd,
                message=f"Deleted feature: {deleted['feature_id']}",
                data={"deleted_feature": deleted},
            )

        if feature_cmd == "help":
            usage = (
                "Feature workflow commands:\n"
                "- /feature create plan <name>\n"
                "- /feature create phase <title> | <goal>\n"
                "- /feature create task <phase_id> | <title> | <overview> | <exit1;exit2>\n"
                "- /feature show <board|execution|reviews>\n"
                "- /feature move <task_id> <status>\n"
                "- /feature block <task_id> <reason>\n"
                "- /feature review auto\n"
                "- /feature review <task_id> <summary>\n"
                "- /feature archive <task_id>\n"
                "- /feature monitor [refresh_seconds] [iterations|continuous]\n"
                "\nExamples:\n"
                "- /feature create plan Checkout Cleanup\n"
                "- /feature create phase API Hardening | Reduce flaky retries\n"
                "- /feature create task 1 | Validate headers | Add strict parsing | Unit tests;Docs updated\n"
                "- /feature move 1 in_progress\n"
                "- /feature block 1 waiting_for_user_input\n"
                "- /feature show board\n"
                "- /feature monitor 1.0 10\n"
            )
            if allow_prompt:
                console.print(Panel(usage, title="Feature Command Help", border_style="cyan"))
            return serialize_command_result(session, cmd, message="Rendered feature help.", data={"usage": usage})

        if feature_cmd == "create":
            create_parts = feature_arg.split(" ", 1) if feature_arg else []
            if len(create_parts) < 2:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Usage: /feature create <plan|phase|task> <args>",
                )
            create_kind = create_parts[0].lower().strip()
            create_payload = create_parts[1].strip()
            if create_kind == "plan":
                mode_choice = _feature_prompt_with_logging(
                    session,
                    question="Select planning style",
                    options=[
                        ("balanced", "Balanced (Recommended): detail + speed"),
                        ("fast", "Fast: minimal planning, rapid execution"),
                        ("thorough", "Thorough: deep planning before coding"),
                    ],
                    prompt_id="plan_style",
                    allow_prompt=allow_prompt,
                    context={"feature_name": create_payload},
                )
                confirm_result = _feature_confirm_deny_edit_loop(
                    session,
                    label="plan request",
                    value=create_payload,
                    allow_prompt=allow_prompt,
                    context={"kind": "plan_create"},
                )
                if confirm_result["decision"] == "deny":
                    return serialize_command_result(
                        session,
                        cmd,
                        ok=False,
                        message="Plan creation cancelled. Re-run with a revised name.",
                    )
                response = _execute_feature_tool(
                    session,
                    "create_feature",
                    {
                        "feature_name": confirm_result["value"],
                        "feature_request": confirm_result["value"],
                        "design_plan": f"cli_planning_style={mode_choice}",
                    },
                )
                ok = bool(response.get("ok"))
                return serialize_command_result(
                    session,
                    cmd,
                    ok=ok,
                    message="Created feature plan shell." if ok else str(response.get("error", response)),
                    data=response,
                )
            if create_kind == "phase":
                phase_parts = [part.strip() for part in create_payload.split("|", 1)]
                if len(phase_parts) != 2:
                    return serialize_command_result(
                        session, cmd, ok=False, message="Usage: /feature create phase <title> | <goal>"
                    )
                confirm_result = _feature_confirm_deny_edit_loop(
                    session,
                    label="phase title",
                    value=phase_parts[0],
                    allow_prompt=allow_prompt,
                    context={"kind": "phase_create"},
                )
                if confirm_result["decision"] == "deny":
                    return serialize_command_result(
                        session,
                        cmd,
                        ok=False,
                        message="Phase creation cancelled. Re-run with updated title/goal.",
                    )
                feature_state = session.session_manager.get_feature_state() or {}
                plan = (feature_state.get("feature_plan") if isinstance(feature_state, dict) else {}) or {}
                existing = list(plan.get("phases_meta", []))
                next_id = len(existing) + 1
                existing.append({"id": next_id, "title": confirm_result["value"], "goal": phase_parts[1], "order": next_id})
                response = _execute_feature_tool(
                    session,
                    "create_phases",
                    {"phases": existing, "replace_existing": True},
                )
                ok = bool(response.get("ok"))
                return serialize_command_result(session, cmd, ok=ok, message="Phase created." if ok else str(response.get("error", response)), data=response)
            if create_kind == "task":
                task_parts = [part.strip() for part in create_payload.split("|")]
                if len(task_parts) != 4:
                    return serialize_command_result(
                        session,
                        cmd,
                        ok=False,
                        message="Usage: /feature create task <phase_id> | <title> | <overview> | <exit1;exit2>",
                    )
                exit_criteria = [item.strip() for item in task_parts[3].split(";") if item.strip()]
                confirm_result = _feature_confirm_deny_edit_loop(
                    session,
                    label="task title",
                    value=task_parts[1],
                    allow_prompt=allow_prompt,
                    context={"kind": "task_create", "phase_id": task_parts[0]},
                )
                if confirm_result["decision"] == "deny":
                    return serialize_command_result(
                        session,
                        cmd,
                        ok=False,
                        message="Task creation cancelled. Re-run with an updated task payload.",
                    )
                response = _execute_feature_tool(
                    session,
                    "create_task",
                    {
                        "phase_id": int(task_parts[0]),
                        "title": confirm_result["value"],
                        "overview": task_parts[2],
                        "exit_criteria": exit_criteria,
                    },
                )
                ok = bool(response.get("ok"))
                return serialize_command_result(session, cmd, ok=ok, message="Task created." if ok else str(response.get("error", response)), data=response)
            return serialize_command_result(session, cmd, ok=False, message="Unknown create target. Use plan|phase|task.")

        if feature_cmd == "show":
            view = (feature_arg or "board").strip().lower()
            feature = refresh_feature_record(session, None)
            if not isinstance(feature, dict):
                return serialize_command_result(session, cmd, ok=False, message="No feature selected.")
            plan = feature.get("feature_plan", {}) if isinstance(feature.get("feature_plan"), dict) else {}
            if view == "execution":
                payload = _execute_feature_tool(session, "get_execution_state", {})
                return serialize_command_result(session, cmd, message="Rendered execution view.", data=payload)
            if view == "reviews":
                return serialize_command_result(
                    session,
                    cmd,
                    message="Rendered review summaries.",
                    data={"review_summaries": plan.get("review_summaries", []), "review_count": plan.get("review_count", 0)},
                )
            if view == "board":
                return serialize_command_result(
                    session,
                    cmd,
                    message="Rendered board snapshot.",
                    data={"active_tasks": plan.get("active_tasks", []), "execution": plan.get("execution", {})},
                )
            return serialize_command_result(session, cmd, ok=False, message="Unknown show target. Use board|execution|reviews.")

        if feature_cmd == "move":
            parts = feature_arg.split(" ", 1)
            if len(parts) != 2:
                return serialize_command_result(session, cmd, ok=False, message="Usage: /feature move <task_id> <status>")
            task_id = int(parts[0])
            status = parts[1].strip()
            args = {"task_id": task_id, "status": status}
            if status == "completed":
                feature = refresh_feature_record(session, None) or {}
                plan = feature.get("feature_plan", {}) if isinstance(feature, dict) else {}
                criteria = []
                for task in plan.get("phases", []):
                    if int(task.get("id", -1)) == task_id:
                        criteria = list(task.get("exit_criteria", []))
                        break
                args["verified_exit_criteria"] = criteria
            response = _execute_feature_tool(session, "update_task_status", args)
            return serialize_command_result(session, cmd, ok=bool(response.get("ok")), message="Task moved." if response.get("ok") else str(response.get("error", response)), data=response)

        if feature_cmd == "block":
            parts = feature_arg.split(" ", 1)
            if len(parts) != 2:
                return serialize_command_result(session, cmd, ok=False, message="Usage: /feature block <task_id> <reason>")
            response = _execute_feature_tool(
                session,
                "block_task",
                {"task_id": int(parts[0]), "reason": parts[1]},
            )
            return serialize_command_result(session, cmd, ok=bool(response.get("ok")), message="Task blocked." if response.get("ok") else str(response.get("error", response)), data=response)

        if feature_cmd == "review":
            if (feature_arg or "").strip().lower() == "auto":
                response = _execute_feature_tool(session, "review_all_completed_tasks", {})
                return serialize_command_result(session, cmd, ok=bool(response.get("ok")), message="Auto-review completed tasks." if response.get("ok") else str(response.get("error", response)), data=response)
            parts = feature_arg.split(" ", 1)
            if len(parts) != 2:
                return serialize_command_result(session, cmd, ok=False, message="Usage: /feature review <task_id> <summary> OR /feature review auto")
            response = _execute_feature_tool(
                session,
                "review_completed_tasks",
                {"task_id": int(parts[0]), "summary": parts[1], "issues": []},
            )
            return serialize_command_result(session, cmd, ok=bool(response.get("ok")), message="Review recorded." if response.get("ok") else str(response.get("error", response)), data=response)

        if feature_cmd == "archive":
            if not feature_arg:
                return serialize_command_result(session, cmd, ok=False, message="Usage: /feature archive <task_id>")
            response = _execute_feature_tool(
                session,
                "archive_task",
                {"task_id": int(feature_arg)},
            )
            return serialize_command_result(session, cmd, ok=bool(response.get("ok")), message="Task archived." if response.get("ok") else str(response.get("error", response)), data=response)

        if feature_cmd == "monitor":
            refresh_seconds = 2.0
            if feature_arg:
                try:
                    refresh_seconds = max(0.5, float(feature_arg))
                except ValueError:
                    pieces = feature_arg.split()
                    try:
                        refresh_seconds = max(0.5, float(pieces[0]))
                    except (ValueError, IndexError):
                        return serialize_command_result(session, cmd, ok=False, message="Usage: /feature monitor [refresh_seconds] [iterations|continuous]")
                    mode_arg = pieces[1].strip().lower() if len(pieces) > 1 else ""
                else:
                    mode_arg = ""
            else:
                mode_arg = ""
            snapshots = []
            if not allow_prompt:
                iterations = 1
            elif mode_arg == "continuous":
                iterations = None
            elif mode_arg:
                try:
                    iterations = max(1, int(mode_arg))
                except ValueError:
                    return serialize_command_result(session, cmd, ok=False, message="Usage: /feature monitor [refresh_seconds] [iterations|continuous]")
            else:
                iterations = 5
            last_line = None
            tick = 0
            try:
                while iterations is None or tick < iterations:
                    payload = _execute_feature_tool(session, "get_execution_state", {})
                    snapshots.append(payload)
                    if allow_prompt:
                        line = _monitor_compact_line(payload)
                        if line != last_line:
                            console.print(f"[cyan]{line}[/cyan]")
                            last_line = line
                    tick += 1
                    if allow_prompt and (iterations is None or tick < iterations):
                        time.sleep(refresh_seconds)
            except KeyboardInterrupt:
                if allow_prompt:
                    console.print("[yellow]Monitor stopped by user.[/yellow]")
            return serialize_command_result(
                session,
                cmd,
                message="Rendered feature monitor.",
                data={"snapshots": snapshots, "refresh_seconds": refresh_seconds, "mode": mode_arg or "fixed", "iterations": iterations if iterations is not None else "continuous"},
            )

        if feature_cmd in {"status", "phases"}:
            feature = refresh_feature_record(session, feature_arg or None)
            if not isinstance(feature, dict):
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="No feature selected.",
                )
            markdown = build_feature_markdown(
                {
                    **feature,
                    "token_total": session.session_manager.token_counts.get("total", 0),
                },
                include_phases=feature_cmd == "phases",
            )
            if allow_prompt:
                console.print(Markdown(markdown))
            return serialize_command_result(
                session,
                cmd,
                message=f"Rendered feature {feature_cmd}.",
                data={"feature": feature, "markdown": markdown},
            )

        return serialize_command_result(
            session,
            cmd,
            ok=False,
            message=f"Unknown feature command: {feature_cmd}. Use '/feature help' for workflow-aligned guidance.",
        )

    if cmd in ["/tool", "/tools"]:
        tool_parts = arg.split(" ", 1) if arg else ["list"]
        tool_cmd = tool_parts[0].lower()
        tool_name = tool_parts[1].strip() if len(tool_parts) > 1 else ""

        if tool_cmd == "disable" and tool_name:
            if tool_name not in session.disabled_tools:
                session.disabled_tools.append(tool_name)
            return serialize_command_result(
                session, cmd, message=f"Tool '{tool_name}' disabled."
            )
        if tool_cmd == "enable" and tool_name:
            if tool_name in session.disabled_tools:
                session.disabled_tools.remove(tool_name)
            return serialize_command_result(
                session, cmd, message=f"Tool '{tool_name}' enabled."
            )
        if tool_cmd == "list":
            from core.tools import TOOLS

            if allow_prompt:
                table = Table(title="Available Tools", box=box.ROUNDED, show_lines=True)
                table.add_column("Tool", style="cyan", no_wrap=True)
                table.add_column("Description", style="white", width=40)
                table.add_column("Parameters", style="magenta")
                table.add_column("Approval", style="yellow", justify="center")
                table.add_column("Status", style="green", justify="center")

                for tool in TOOLS:
                    status = (
                        "[red]OFF[/red]"
                        if tool.name in session.disabled_tools
                        else "[green]ON[/green]"
                    )
                    approval = "Yes" if tool.requires_approval else "No"
                    params = []
                    props = tool.parameters.get("properties", {})
                    required = tool.parameters.get("required", [])
                    for param_name, param_info in props.items():
                        required_star = "[red]*[/red]" if param_name in required else ""
                        param_type = param_info.get("type", "any")
                        params.append(
                            f"{param_name}{required_star} [dim]({param_type})[/dim]"
                        )
                    params_str = "\n".join(params) if params else "None"
                    table.add_row(
                        tool.name, tool.description, params_str, approval, status
                    )
                console.print(table)
                console.print("[dim] [red]*[/red] indicates required parameter[/dim]")

            tools_data = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "requires_approval": tool.requires_approval,
                    "enabled": tool.name not in session.disabled_tools,
                }
                for tool in TOOLS
            ]
            return serialize_command_result(session, cmd, data={"tools": tools_data})

        return serialize_command_result(
            session,
            cmd,
            ok=False,
            message=f"Usage: {cmd} <enable|disable|list> [toolname]",
        )

    if cmd == "/memory":
        parts = user_input.split()
        subcommand = parts[1].lower() if len(parts) > 1 else "status"

        def build_memory_stats(store):
            entries = list(store.entries)
            total_hits = sum(int(entry.hits or 0) for entry in entries)
            top_entries = sorted(
                entries,
                key=lambda entry: (int(entry.hits or 0), float(entry.updated_at or 0)),
                reverse=True,
            )[:3]
            return {
                "entries": len(entries),
                "total_hits": total_hits,
                "avg_hits": (total_hits / len(entries)) if entries else 0.0,
                "top_entries": [entry.to_dict() for entry in top_entries],
            }

        if subcommand in ["status", "s"]:
            task_stats = build_memory_stats(session.task_memory)
            scratch_stats = build_memory_stats(session.turn_scratchpad)
            layer_stats = collect_context_layers(session)
            if allow_prompt:
                table = Table(title="Memory Status", box=box.ROUNDED)
                table.add_column("Type", style="cyan")
                table.add_column("Entries", style="green", justify="right")
                table.add_column("Hits", style="yellow", justify="right")
                table.add_column("Avg Hits", style="magenta", justify="right")
                table.add_column("Description", style="dim")

                table.add_row(
                    "Task Memory",
                    str(task_stats["entries"]),
                    str(task_stats["total_hits"]),
                    f"{task_stats['avg_hits']:.2f}",
                    "Longer-term task context",
                )
                table.add_row(
                    "Scratchpad",
                    str(scratch_stats["entries"]),
                    str(scratch_stats["total_hits"]),
                    f"{scratch_stats['avg_hits']:.2f}",
                    "Short-term turn context",
                )
                console.print(table)

                def print_top_entries(title, stats):
                    console.print(f"[bold cyan]{title} Top Entries[/bold cyan]")
                    if not stats["top_entries"]:
                        console.print("[dim]No entries yet.[/dim]")
                        return
                    top_table = Table(box=box.SIMPLE)
                    top_table.add_column("ID", style="dim", justify="right")
                    top_table.add_column("Hits", style="yellow", justify="right")
                    top_table.add_column("Tags", style="magenta")
                    top_table.add_column("Source", style="blue")
                    top_table.add_column("Preview", style="white")
                    for entry in stats["top_entries"]:
                        tags = ", ".join(entry.get("tags", [])) or "-"
                        preview = str(entry.get("content", "")).replace("\n", " ").strip()
                        if len(preview) > 90:
                            preview = preview[:87] + "..."
                        top_table.add_row(
                            f"#{entry.get('id')}",
                            str(entry.get("hits", 0)),
                            tags,
                            entry.get("source") or "-",
                            preview or "(empty)",
                        )
                    console.print(top_table)

                print_top_entries("Task Memory", task_stats)
                print_top_entries("Scratchpad", scratch_stats)

                layer_table = Table(
                    title="Hierarchical Context Layers",
                    box=box.SIMPLE,
                )
                layer_table.add_column("Layer", style="cyan")
                layer_table.add_column("Name", style="white")
                layer_table.add_column("Usage", style="yellow", justify="right")
                layer_table.add_column("Fill", style="green", justify="right")
                layer_table.add_column("Description", style="dim")
                for layer in layer_stats:
                    current = int(layer.get("current", 0) or 0)
                    maximum = max(1, int(layer.get("maximum", 1) or 1))
                    pct = min(100, int(round((current / maximum) * 100)))
                    layer_table.add_row(
                        str(layer.get("layer", "")),
                        str(layer.get("name", "")),
                        f"{current}/{maximum}",
                        f"{pct}%",
                        str(layer.get("description", "")),
                    )
                console.print(layer_table)
            return serialize_command_result(
                session,
                cmd,
                data={
                    "task_memory_count": task_stats["entries"],
                    "scratchpad_count": scratch_stats["entries"],
                    "task_memory_stats": task_stats,
                    "scratchpad_stats": scratch_stats,
                    "context_layers": layer_stats,
                },
            )

        if subcommand in ["list", "ls"]:
            target = parts[2].lower() if len(parts) > 2 else "all"

            def get_entries_data(store):
                return [entry.to_dict() for entry in store.entries]

            if allow_prompt:

                def print_entries(store, title):
                    if not store.entries:
                        console.print(f"[dim]No entries in {title}.[/dim]")
                        return
                    table = Table(title=title, box=box.SIMPLE)
                    table.add_column("ID", style="dim", justify="right")
                    table.add_column("Hits", style="yellow", justify="right")
                    table.add_column("Tags", style="yellow")
                    table.add_column("Source", style="blue")
                    table.add_column("Content")
                    for entry in store.entries:
                        tags = ", ".join(entry.tags) if entry.tags else "-"
                        table.add_row(
                            f"#{entry.id}",
                            str(entry.hits),
                            tags,
                            entry.source or "-",
                            entry.content,
                        )
                    console.print(table)

                if target in ["all", "task"]:
                    print_entries(session.task_memory, "Task Memory")
                if target in ["all", "scratchpad"]:
                    print_entries(session.turn_scratchpad, "Turn Scratchpad")

            return serialize_command_result(
                session,
                cmd,
                data={
                    "task_memory": (
                        get_entries_data(session.task_memory)
                        if target in ["all", "task"]
                        else []
                    ),
                    "scratchpad": (
                        get_entries_data(session.turn_scratchpad)
                        if target in ["all", "scratchpad"]
                        else []
                    ),
                },
            )

        if subcommand == "clear":
            target = parts[2].lower() if len(parts) > 2 else "all"
            target_aliases = {
                "scratch": "scratchpad",
                "scratchpad": "scratchpad",
                "task": "task",
                "longterm": "task",
                "long-term": "task",
                "all": "all",
            }
            target = target_aliases.get(target, target)
            msg_parts = []
            if target in ["all", "task"]:
                session.task_memory.clear()
                msg_parts.append("Task memory")
            if target in ["all", "scratchpad"]:
                session.turn_scratchpad.clear()
                msg_parts.append("Turn scratchpad")

            if not msg_parts:
                return serialize_command_result(
                    session,
                    cmd,
                    ok=False,
                    message="Usage: /memory clear [task|scratchpad|all]",
                )

            msg = " and ".join(msg_parts) + " cleared."
            if allow_prompt:
                console.print(f"[green]{msg}[/green]")
            return serialize_command_result(session, cmd, message=msg)

    if cmd == "/stats":
        stats = build_stats_snapshot(session)
        if allow_prompt:
            refresh_memory_hud(session, ui, force=True)
        return serialize_command_result(session, cmd, data=stats)

    if cmd == "/thinking":
        session.thinking = not session.thinking
        console.print(f"[dim]Thinking mode: {session.thinking}[/dim]")
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message=f"Thinking mode: {session.thinking}"
        )

    if cmd == "/agentic":
        session.agentic = not session.agentic
        console.print(f"[dim]Agentic mode: {session.agentic}[/dim]")
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message=f"Agentic mode: {session.agentic}"
        )

    if cmd == "/yolo":
        current = session.variables.get("yolo", False)
        session.variables["yolo"] = not current
        session.session_manager.save_history(session.folder_context)
        console.print(f"[dim]YOLO mode: {session.variables['yolo']}[/dim]")
        refresh_memory_hud(session, ui)
        return serialize_command_result(
            session, cmd, message=f"YOLO mode: {session.variables['yolo']}"
        )

    if cmd == "/splash":
        if allow_prompt:
            print_splash(session)
        refresh_memory_hud(session, ui)
        return serialize_command_result(session, cmd, data={"splash": True})

    if cmd == "/update":
        if allow_prompt:
            console.print("[dim]Running manual update (git pull + dependency refresh)...[/dim]")
        update_result = run_auto_update()
        if allow_prompt:
            if update_result["ok"]:
                ui.show_info(update_result["message"])
            else:
                ui.show_error(update_result["message"])
            for step in update_result.get("steps", []):
                status = "OK" if step["returncode"] == 0 else "FAILED"
                console.print(f"[dim]{status} · {step['name']}[/dim]")
                if step.get("stderr"):
                    console.print(f"[dim]{step['stderr']}[/dim]")
        return serialize_command_result(
            session,
            cmd,
            ok=update_result["ok"],
            message=update_result["message"],
            data={"steps": update_result.get("steps", [])},
        )

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
        "--server",
        action="store_true",
        help="Run μCLI in HTTP server mode for API clients.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for --server mode.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for --server mode.",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Enable YOLO mode at startup.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch full-screen terminal GUI.",
    )
    parser.add_argument(
        "--gui-refresh",
        type=float,
        default=1.0,
        help="Refresh interval (seconds) for --gui mode.",
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
    ui = HeadlessUI(auto_approve=args.yolo) if args.server else RichUI()

    try:
        session = build_session(args, ui, allow_prompt=not (args.server or args.gui))
    except Exception as exc:
        console.print(f"[red]Failed to initialize Session/Provider: {exc}[/red]")
        sys.exit(1)

    if args.gui:
        run_gui_mode(SESSION_DIR, refresh_seconds=args.gui_refresh)
        return

    if args.server:
        serve(session, args.host, args.port, handle_command)
        return

    print_splash(session)
    release_status = get_release_update_status()
    if release_status.get("ok") and release_status.get("update_available"):
        release = release_status.get("latest_release", {})
        tag = release.get("tag_name", "unknown")
        release_url = release.get("html_url", "")
        console.print(
            f"[yellow]New μCLI release available: {tag} "
            f"(repo: {release_status.get('repo')}).[/yellow]"
        )
        if release_url:
            console.print(f"[dim]{release_url}[/dim]")
        if Confirm.ask("Would you like to update now?", default=False):
            update_result = run_auto_update()
            if update_result["ok"]:
                ui.show_info(update_result["message"])
            else:
                ui.show_error(update_result["message"])
            for step in update_result.get("steps", []):
                status = "OK" if step["returncode"] == 0 else "FAILED"
                console.print(f"[dim]{status} · {step['name']}[/dim]")
                if step.get("stderr"):
                    console.print(f"[dim]{step['stderr']}[/dim]")
    elif release_status.get("ok") and release_status.get("latest_release"):
        latest_tag = release_status["latest_release"].get("tag_name", "")
        if latest_tag:
            console.print(f"[dim]μCLI is up to date ({latest_tag}).[/dim]")
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
