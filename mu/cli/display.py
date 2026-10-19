"""Stats snapshot, help layout, and splash-screen rendering.

Extracted from the mucli entry module; mucli.py re-exports the public
names so `from mucli import build_stats_snapshot` call sites are stable.
"""

from __future__ import annotations

import time

from rich.console import Group
from rich.table import Table
from rich.text import Text

from mu.design import load_tokens
from mu.ui.theme import get_console
from mu.ui.theme.components import gradient_rule, notice_text, table as wash_table
from mu.ui.theme.glyphs import WORDMARK

import os

from mu.feature.engine import (
    refresh_and_persist_feature_plan,
    summarize_feature_plan,
)
from mu.session.session import derive_feature_state_status
from utils.config import AGENT_MODE_METADATA, _DEFAULT_CONTEXT_TOKEN_LIMIT


console = get_console()


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
            ("/provider [name]", "", "Switch provider (anthropic, gemini, ollama, openai)"),
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
            ("/goal [<text>|clear|show]", "", "Pin top-level task into L3; survives compaction"),
            ("/research [status|sources]", "", "Research workflow helpers"),
        ],
    ),
    (
        "Memory, tools, features",
        [
            ("/memory <status|list <target>|clear <target>>", "", "Inspect memory, scratchpad, or any layer (L1-L5)"),
            ("/tool <enable|disable|list>", "", "Enable/disable tools or list all"),
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
            (
                "/orch [runs|run|nodes|approve|reject|accept|pause|resume|update-agents]",
                "/orchestration",
                "Orchestration: what needs you, and decide it",
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
            ("/prompts [reload|init|show|validate|edit]", "", "Manage file-based system-prompt overrides"),
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
        table = wash_table(title=group_name, show_header=False, padding=(0, 1))
        table.add_column("Command", style="wash.accent", no_wrap=True)
        table.add_column("Alias", style="wash.muted")
        table.add_column("Description", style="wash.ink2")
        for cmd, alias, desc in entries:
            table.add_row(cmd, alias, desc)
        console.print(table)
    console.print(
        "[wash.hint]Tip: end a line with '\\\\' to continue typing on the next line. "
        "Tab to autocomplete every command.[/]"
    )


def build_splash(session):
    """The Wash splash (G§8.4): a one-line ``μcli`` wordmark with a pigment
    gradient rule, then a 3-row info grid (session, model, workspace).

    Every value is added as literal ``Text`` (never parsed as markup), so
    session, model and folder names can contain brackets safely (B5).
    Returns ``(renderable, context_warning_or_None)``.
    """
    sys_status = "set" if session.system_instruction else "none"
    agent_mode = str(session.variables.get("agent_mode", "default"))
    session_type = str(session.variables.get("session_type", "workspace"))
    yolo_on = bool(session.variables.get("yolo", False))
    plan_on = bool(session.variables.get("plan_mode", False))
    _session_type_glyph = {"chat": "○", "workspace": "▱", "container": "◇"}
    session_type_glyph = _session_type_glyph.get(session_type, "▱")

    # Workspace Folder info
    folders = session.folder_context.folders
    folder_count = len(folders)
    if folder_count == 0:
        folder_list = "none attached"
    elif folder_count == 1:
        folder_list = str(folders[0])
    else:
        folder_list = f"{folder_count} folders: " + ", ".join(
            [os.path.basename(f) for f in folders[:3]]
        )
        if folder_count > 3:
            folder_list += " …"

    # Total context (sum of all layers) vs. the compactor's effective
    # ceiling (drift_corrected_context_limit), not the raw provider window.
    # The compactor fires on the effective ceiling — the raw
    # context_token_limit divided by the provider's safety factor (2.5 for
    # Ollama) and any learned cl100k→real drift — so the warning must use the
    # same ceiling or it can read "60% full" while emergency compaction is
    # already firing. No-op for providers with no safety factor (OpenAI/Gemini).
    from utils.runtime_metrics import estimate_active_context_tokens

    raw_limit = int(session.variables.get("context_token_limit", _DEFAULT_CONTEXT_TOKEN_LIMIT) or _DEFAULT_CONTEXT_TOKEN_LIMIT)
    try:
        from mu.session.budgets import drift_corrected_context_limit

        context_limit = max(1, int(drift_corrected_context_limit(session)))
    except Exception:  # noqa: BLE001
        context_limit = raw_limit
    trim_threshold = float(session.variables.get("context_trim_threshold", 0.85) or 0.85)
    trim_threshold = max(0.10, min(trim_threshold, 1.0))
    context_tokens = int(estimate_active_context_tokens(session) or 0)
    threshold_tokens = int(context_limit * trim_threshold)
    cap_note = (
        f" (effective cap ÷ safety; raw {raw_limit:,})" if context_limit < raw_limit else ""
    )

    splash = splash_renderable(
        session_name=str(session.session_manager.current_session_name),
        session_type=f"{session_type_glyph} {session_type}",
        agent_mode=agent_mode,
        model_name=str(session.provider.model_name),
        flags=(
            f"thinking {'on' if session.thinking else 'off'} · "
            f"agentic {'on' if session.agentic else 'off'} · "
            f"yolo {'on' if yolo_on else 'off'} · system {sys_status}"
        ),
        context=f"ctx {context_tokens:,} / {context_limit:,}{cap_note}",
        workspace=folder_list,
        has_workspace=bool(folder_count),
        plan=plan_on,
        yolo=yolo_on,
    )

    warning = None
    if context_tokens >= threshold_tokens:
        warning = (
            f"Context is {context_tokens:,} / {context_limit:,} tokens, past the "
            f"{int(trim_threshold * 100)}% trim threshold.{cap_note}"
        )
    return splash, warning


def splash_renderable(
    *,
    session_name: str,
    session_type: str,
    agent_mode: str,
    model_name: str,
    flags: str,
    context: str,
    workspace: str,
    has_workspace: bool = True,
    plan: bool = False,
    yolo: bool = False,
):
    """Wordmark + gradient rule + the 3-row info grid. All values are literal."""

    def sep(text):
        text.append("  ·  ", style="wash.muted")

    wordmark = Text()
    wordmark.append(f"{WORDMARK}  ", style="wash.wordmark")
    wordmark.append_text(gradient_rule(40))

    session_row = Text()
    session_row.append(session_name, style="wash.ink.bold")
    sep(session_row)
    session_row.append(session_type, style="wash.ink2")
    sep(session_row)
    mode_style = f"wash.mode.{agent_mode}" if agent_mode in load_tokens()["mode"] else "wash.ink2"
    session_row.append(agent_mode, style=mode_style)
    if plan:
        session_row.append("  [plan]", style="wash.chip.plan")
    if yolo:
        session_row.append("  [yolo]", style="wash.chip.yolo")

    model_row = Text()
    model_row.append(model_name, style="wash.ink.bold")
    sep(model_row)
    model_row.append(flags, style="wash.muted")
    sep(model_row)
    model_row.append(context, style="wash.ink2")

    workspace_row = Text(workspace, style="wash.path" if has_workspace else "wash.muted")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="wash.muted", no_wrap=True)
    grid.add_column()
    grid.add_row("session", session_row)
    grid.add_row("model", model_row)
    grid.add_row("workspace", workspace_row)
    return Group(wordmark, Text(""), grid)


def print_splash(session):
    splash, warning = build_splash(session)
    console.print(splash)
    if warning:
        console.print(notice_text("caution", warning))
    console.print("[wash.hint]Type /help for commands.[/]\n")
