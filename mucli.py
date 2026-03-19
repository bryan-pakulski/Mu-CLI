#!/usr/bin/env python

import argparse
import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from core.session import Session, SessionManager
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from providers.openai import OpenAIProvider
from ui.textual_ui import TextualUI
from utils.logger import logger

console = Console()


def refresh_memory_hud(session, ui):
    if ui and hasattr(ui, "show_memory_monitor"):
        ui.show_memory_monitor(session)


def build_help_table():
    table = Table(title="Available Commands", box=box.SIMPLE)
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Alias", style="magenta")
    table.add_column("Description", style="white")

    table.add_row("/clear", "", "Clear conversation history")
    table.add_row("/new [name]", "", "Start a new conversation")
    table.add_row("/file <path>", "/f", "Attach a file")
    table.add_row(
        "/folder <path>", "/dir", "Monitor folder(s) for changes and use as context"
    )
    table.add_row("/help", "", "Show this help menu")
    table.add_row("/list", "/ls", "List saved conversations")
    table.add_row("/load [name]", "/open", "Load a conversation")
    table.add_row("/model [name]", "", "Show / change current model")
    table.add_row("/get [key]", "", "Get a variable")
    table.add_row("/yolo", "", "Toggle YOLO mode (no approvals)")
    table.add_row("/set [key] [value]", "", "Set a variable")
    table.add_row("/unset [key]", "", "Unset a variable (or --all)")
    table.add_row("/flush", "", "Flush the collation buffer into the next turn")
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
        "Change the agentic strategy (default, debug, feature, research, git)",
    )
    table.add_row("/provider [name]", "", "Change the LLM provider")
    table.add_row("/quit", "/q", "Exit")
    table.add_row("/system <txt>", "/sys", "Update system prompt")
    table.add_row("/thinking", "", "Toggle thinking mode")
    table.add_row("/tokens", "", "Show context token usage")
    table.add_row("/view", "", "View conversation history")
    return table


def show_help(ui):
    ui.print(build_help_table())
    ui.show_info("[dim]Type a message or command in the input bar below.[/dim]")


def build_splash_panel(session):
    welcome_text = Text()
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
    yolo_status = "ON" if session.variables.get("yolo", False) else "OFF"

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

    total_history = len(session.session_manager.history)
    active_history = total_history - session.session_manager.summary_anchor

    info_grid = f"""
    [bold magenta]Session:[/bold magenta]  [bold yellow]{session.session_manager.current_session_name}[/bold yellow]
    [bold magenta]System:[/bold magenta]   {sys_status}
    [bold magenta]Model:[/bold magenta]    [bold cyan]{session.provider.model_name}[/bold cyan]
    [bold magenta]Thinking:[/bold magenta] [bold cyan]{session.thinking}[/bold cyan] | [bold magenta]Agentic:[/bold magenta] [bold cyan]{session.agentic}[/bold cyan] | [bold magenta]YOLO:[/bold magenta] [bold cyan]{yolo_status}[/bold cyan]
    [bold magenta]Mode:[/bold magenta]     [bold cyan]{agent_mode}[/bold cyan]
    [bold magenta]Workspace:[/bold magenta][bold green] {folder_list}[/bold green]
    [bold magenta]Context:[/bold magenta]   [bold cyan]{active_history}[/bold cyan] / {total_history} turns
    """

    return Panel(
        Text.assemble(welcome_text, Text.from_markup(info_grid)),
        title="[bold yellow] // μCLI TERMINAL // [/bold yellow]",
        border_style="cyan",
        box=box.HEAVY,
    )


def show_splash(session, ui):
    ui.print(build_splash_panel(session))
    ui.show_info("[dim]Type '/help' for commands.[/dim]")


def init_provider(provider_name, model_name, ollama_host=None):
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


def select_provider_and_model(args_provider, args_model, ollama_host=None):
    providers = ["gemini", "ollama", "openai"]
    provider_name = args_provider

    if provider_name not in providers:
        console.print("\n[bold cyan]Available Providers:[/bold cyan]")
        for i, p in enumerate(providers, 1):
            console.print(f" {i}. {p}")
        choice = IntPrompt.ask(
            "Select a provider", choices=[str(i) for i in range(1, len(providers) + 1)]
        )
        provider_name = providers[int(choice) - 1]

    provider = init_provider(provider_name, "", ollama_host)
    if not provider:
        console.print(f"[red]Unknown provider: {provider_name}[/red]")
        sys.exit(1)

    models = provider.get_available_models()
    model_name = args_model

    if not models:
        if not model_name:
            model_name = Prompt.ask(f"Enter model name manually for {provider_name}")
    elif model_name not in models:
        console.print(f"\n[bold cyan]Available Models for {provider_name}:[/bold cyan]")
        for i, m in enumerate(models, 1):
            console.print(f" {i}. {m}")

        choice = IntPrompt.ask(
            "Select a model", choices=[str(i) for i in range(1, len(models) + 1)]
        )
        model_name = models[int(choice) - 1]

    provider.model_name = model_name
    return provider




def select_provider_and_model_ui(ui, provider_name=None, model_name=None, ollama_host=None):
    providers = ["gemini", "ollama", "openai"]
    if provider_name not in providers:
        provider_name = ui.prompt_choices("Select a provider", providers, default=providers[0])

    provider = init_provider(provider_name, "", ollama_host)
    if not provider:
        raise ValueError(f"Unknown provider: {provider_name}")

    models = provider.get_available_models()
    if not models:
        if not model_name:
            model_name = ui.prompt(f"Enter model name manually for {provider_name}")
    elif model_name not in models:
        model_name = ui.prompt_choices(
            f"Select a model for {provider_name}", models, default=models[0]
        )

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
        name = Prompt.ask("Enter name for new session (optional, press enter for default)")
        return "new", name if name else None
    return "load", sessions[choice - 1]


def sync_provider_settings(session):
    if isinstance(session.provider, OllamaProvider):
        host = session.variables.get("ollama_host", "http://localhost:11434")
        session.provider.host = host


def handle_user_input(session, ui, user_input, args):
    if not user_input:
        return True

    if user_input.startswith("/"):
        parts = user_input.split(" ", 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ["/quit", "/exit", "/q"]:
            ui.show_info("Goodbye!")
            return False

        if cmd in ["/help", "/h"]:
            show_help(ui)
            return True

        if cmd in ["/clear", "/c"]:
            session.session_manager.clear_current_history()
            session.staged_files = []
            ui.show_info("Conversation history cleared.")
            refresh_memory_hud(session, ui)
            return True

        if cmd in ["/view", "/v"]:
            history_table = Table(title="Conversation History", box=box.ROUNDED)
            history_table.add_column("Role", style="cyan", no_wrap=True)
            history_table.add_column("Content", style="white")
            for message in session.session_manager.history:
                fragments = []
                for part in message.get("parts", []):
                    if part.get("type") == "text":
                        fragments.append(part.get("text", ""))
                    elif part.get("type") == "tool_call":
                        fragments.append(f"tool_call: {part.get('tool_name')}")
                    elif part.get("type") == "tool_result":
                        fragments.append(f"tool_result: {part.get('tool_name')}")
                history_table.add_row(message.get("role", "?"), "\n".join(fragments) or "-")
            ui.print(history_table)
            return True

        if cmd in ["/file", "/f", "/add"]:
            if arg:
                session.add_file(arg)
            else:
                ui.show_error("Usage: /file <path_to_file>")
            return True

        if cmd in ["/clearfiles", "/cf"]:
            session.clear_files()
            ui.show_info("Cleared staged files.")
            return True

        if cmd in ["/folder", "/dir"]:
            if arg:
                import shlex

                sub_parts = arg.split(" ", 1)
                if sub_parts[0] == "remove" and len(sub_parts) > 1:
                    path_to_remove = sub_parts[1].strip("'\"")
                    if session.folder_context.remove_folder(path_to_remove):
                        ui.show_info(f"[green]Removed folder from context: {path_to_remove}[/green]")
                        session.session_manager.save_history(session.folder_context)
                        refresh_memory_hud(session, ui)
                    else:
                        ui.show_error(f"Folder not found in context: {path_to_remove}")
                    return True

                try:
                    paths = shlex.split(arg)
                except ValueError:
                    paths = [arg.strip("'\"")]

                for path in paths:
                    path = path.strip("'\"")
                    if session.folder_context.add_folder(path):
                        ui.show_info(f"[green]Added folder context: {path}[/green]")
                        if len(session.folder_context.folders) == 1:
                            try:
                                os.chdir(session.folder_context.folders[0])
                                ui.show_info(f"[dim]Switched workspace to: {os.getcwd()}[/dim]")
                            except Exception:
                                pass
                    else:
                        ui.show_error(f"Folder not found or invalid: {path}")

                session.session_manager.save_history(session.folder_context)
                ui.show_info("[dim]Files cached as initial context. Changes will be provided as diffs.[/dim]")
                refresh_memory_hud(session, ui)
                return True

            if not session.folder_context.folders:
                ui.show_info("[yellow]No folders currently monitored.[/yellow]")
                ui.show_info("Usage: /folder <path> OR /folder remove <path>")
                return True

            grid = Table(title="Current Folder Context", box=box.ROUNDED)
            grid.add_column("Folder", style="green")
            for folder in session.folder_context.folders:
                grid.add_row(f"📁 {folder}")
            ui.print(grid)

            files = session.folder_context.get_file_list()
            ui.show_info(f"[dim]Total Tracked Files: {len(files)}[/dim]")
            for file_name in files[:10]:
                ui.show_info(f" - {os.path.basename(file_name)} [dim]({file_name})[/dim]")
            if len(files) > 10:
                ui.show_info(f"[dim]... and {len(files) - 10} more[/dim]")
            return True

        if cmd in ["/list", "/ls"]:
            sessions = session.session_manager.get_session_list()
            table = Table(title="Saved Sessions", box=box.ROUNDED)
            table.add_column("Session", style="cyan")
            for session_name in sessions:
                table.add_row(session_name)
            ui.print(table)
            return True

        if cmd in ["/new"]:
            name = arg.strip() if arg else None
            ollama_host = session.variables.get("ollama_host")
            new_provider = select_provider_and_model_ui(ui, None, None, ollama_host=ollama_host)
            session.provider = new_provider
            session.session_manager.new_session(name, new_provider.name, new_provider.model_name)
            session.staged_files = []
            session.sync_runtime_state()
            ui.set_variables(session.variables)
            show_splash(session, ui)
            refresh_memory_hud(session, ui)
            return True

        if cmd in ["/load", "/open"]:
            if not arg:
                ui.show_error("Usage: /load <session_name>")
                return True
            session.session_manager.switch_session(arg.strip())
            session.staged_files = []
            session.sync_runtime_state()
            ui.set_variables(session.variables)
            p_cfg = session.session_manager.provider_config
            if p_cfg.get("provider") and p_cfg.get("model"):
                ollama_host = session.variables.get("ollama_host")
                session.provider = init_provider(p_cfg["provider"], p_cfg["model"], ollama_host)
            sync_provider_settings(session)
            show_splash(session, ui)
            refresh_memory_hud(session, ui)
            return True

        if cmd in ["/delete", "/rm"]:
            if arg:
                session.session_manager.delete_session(arg.strip())
                ui.show_info(f"Deleted session: {arg.strip()}")
            else:
                ui.show_error("Usage: /delete <session_name>")
            return True

        if cmd in ["/system", "/sys"]:
            if arg:
                session.system_instruction = arg
                ui.show_info("[green]System prompt updated.[/green]")
            else:
                curr = session.system_instruction if session.system_instruction else "None"
                ui.print(Panel(curr, title="Current System Prompt", border_style="blue"))
            return True

        if cmd == "/model":
            if arg:
                session.provider.model_name = arg.strip()
                ui.show_info(f"Model changed to: [green]{session.provider.model_name}[/green]")
            else:
                models = session.provider.get_available_models()
                if models:
                    ui.show_info("Available models: " + ", ".join(models))
                    choice = ui.prompt_choices("Select a model", models, default=models[0])
                    session.provider.model_name = choice
                    ui.show_info(f"Model changed to: [green]{session.provider.model_name}[/green]")
                    session.session_manager.provider_config = {
                        "provider": session.provider.name,
                        "model": session.provider.model_name,
                    }
                    session.session_manager.save_history()
                    show_splash(session, ui)
            refresh_memory_hud(session, ui)
            return True

        if cmd == "/provider":
            try:
                ollama_host = session.variables.get("ollama_host")
                session.provider = select_provider_and_model_ui(ui, arg.strip() if arg else None, None, ollama_host=ollama_host)
                session.session_manager.provider_config = {
                    "provider": session.provider.name,
                    "model": session.provider.model_name,
                }
                session.session_manager.save_history()
                ui.show_info("[green]Provider changed successfully![/green]")
                show_splash(session, ui)
                refresh_memory_hud(session, ui)
            except Exception as exc:
                ui.show_error(f"Failed to change provider: {exc}")
            return True

        if cmd == "/set":
            if not arg:
                ui.show_error("Usage: /set <key> <value>")
                return True
            if "=" in arg:
                key, value = arg.split("=", 1)
            elif " " in arg:
                key, value = arg.split(" ", 1)
            else:
                ui.show_error("Usage: /set <key> <value> OR /set <key>=<value>")
                return True

            key = key.strip()
            value = value.strip()
            try:
                from utils.config import validate_and_cast

                session.variables[key] = validate_and_cast(key, value)
                session.session_manager.save_history(session.folder_context)
                ui.show_info(
                    f"[green]Set variable: {key} = {session.variables[key]} ({type(session.variables[key]).__name__})[/green]"
                )
                if key == "ollama_host":
                    sync_provider_settings(session)
                refresh_memory_hud(session, ui)
            except ValueError as exc:
                ui.show_error(f"Error: {exc}")
            return True

        if cmd == "/get":
            key = arg.strip()
            if not key:
                table = Table(title="Variables", box=box.ROUNDED)
                table.add_column("Key", style="cyan")
                table.add_column("Value", style="green")
                for var_key, var_value in session.variables.items():
                    table.add_row(var_key, str(var_value))
                ui.print(table)
            else:
                ui.show_info(str(session.variables.get(key, "Not set")))
            return True

        if cmd == "/unset":
            key = arg.strip()
            if not key:
                ui.show_error("Usage: /unset <key> OR /unset --all")
                return True
            if key == "--all":
                from utils.config import DEFAULT_VARIABLES

                session.variables.clear()
                session.variables.update(DEFAULT_VARIABLES)
                session.session_manager.save_history(session.folder_context)
                ui.show_info("[green]All variables reset to defaults.[/green]")
                sync_provider_settings(session)
                refresh_memory_hud(session, ui)
                return True

            if key in session.variables:
                from utils.config import VARIABLE_SCHEMA

                if key in VARIABLE_SCHEMA:
                    session.variables[key] = VARIABLE_SCHEMA[key]["default"]
                    ui.show_info(f"[green]Reset variable to default: {key} = {session.variables[key]}[/green]")
                else:
                    del session.variables[key]
                    ui.show_info(f"[green]Unset variable: {key}[/green]")
                session.session_manager.save_history(session.folder_context)
                if key == "ollama_host":
                    sync_provider_settings(session)
                refresh_memory_hud(session, ui)
            else:
                ui.show_info(f"[yellow]Variable '{key}' not found.[/yellow]")
            return True

        if cmd == "/flush":
            count = len(session.collation_buffer.entries)
            if count == 0:
                ui.show_info("[yellow]Collation buffer is empty.[/yellow]")
            else:
                collated = session.collation_buffer.flush()
                text = "### Collated Context Flushed by User:\n\n" + "\n\n".join(collated)
                session.send_message(text)
                ui.show_info(f"[green]Flushed {count} items from buffer into conversation history.[/green]")
                refresh_memory_hud(session, ui)
            return True

        if cmd == "/variables":
            table = Table(title="Variables", box=box.ROUNDED)
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="green")
            for var_key, var_value in session.variables.items():
                table.add_row(var_key, str(var_value))
            ui.print(table)
            return True

        if cmd == "/mode":
            valid_modes = ["default", "debug", "feature", "research", "git"]
            if arg and arg.lower() in valid_modes:
                session.variables["agent_mode"] = arg.lower()
                session.session_manager.save_history(session.folder_context)
                ui.show_info(f"Agent strategy set to: {arg.upper()}")
            else:
                curr = session.variables.get("agent_mode", "default")
                ui.show_info("Usage: /mode <default|debug|feature|research|git>")
                ui.show_info(f"Current mode: {curr}")
            refresh_memory_hud(session, ui)
            return True

        if cmd in ["/tool", "/tools"]:
            t_parts = arg.split(" ", 1) if arg else ["list"]
            t_cmd = t_parts[0].lower()
            t_name = t_parts[1].strip() if len(t_parts) > 1 else ""

            if t_cmd == "disable" and t_name:
                if t_name not in session.disabled_tools:
                    session.disabled_tools.append(t_name)
                ui.show_info(f"Tool '{t_name}' disabled.")
            elif t_cmd == "enable" and t_name:
                if t_name in session.disabled_tools:
                    session.disabled_tools.remove(t_name)
                ui.show_info(f"Tool '{t_name}' enabled.")
            elif t_cmd == "list":
                from core.tools import TOOLS

                table = Table(title="Available Tools", box=box.ROUNDED, show_lines=True)
                table.add_column("Tool", style="cyan", no_wrap=True)
                table.add_column("Description", style="white", width=40)
                table.add_column("Parameters", style="magenta")
                table.add_column("Approval", style="yellow", justify="center")
                table.add_column("Status", style="green", justify="center")

                for tool in TOOLS:
                    status = "OFF" if tool.name in session.disabled_tools else "ON"
                    approval = "Yes" if tool.requires_approval else "No"
                    params = []
                    props = tool.parameters.get("properties", {})
                    required = tool.parameters.get("required", [])
                    for prop_name, prop_info in props.items():
                        req_star = "*" if prop_name in required else ""
                        prop_type = prop_info.get("type", "any")
                        params.append(f"{prop_name}{req_star} ({prop_type})")
                    table.add_row(tool.name, tool.description, "\n".join(params) if params else "None", approval, status)
                ui.print(table)
            else:
                ui.show_error(f"Usage: {cmd} <enable|disable|list> [toolname]")
            return True

        if cmd == "/tokens":
            hist_len = len(session.session_manager.history)
            anchor = session.session_manager.summary_anchor
            tokens = session.session_manager.token_counts
            table = Table(title="Context Stats", box=box.ROUNDED)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")
            table.add_row("Total History Turns", str(hist_len))
            table.add_row("Summarized Turns", str(anchor))
            table.add_row("Active Turns", str(hist_len - anchor))
            table.add_row("Session Tokens (In)", str(tokens['input']))
            table.add_row("Session Tokens (Out)", str(tokens['output']))
            table.add_row("Session Tokens (Total)", str(tokens['total']))
            table.add_row("Session Est. Cost", f"${tokens.get('total_cost', 0.0):.5f}")
            ui.print(table)
            refresh_memory_hud(session, ui)
            return True

        if cmd == "/thinking":
            session.thinking = not session.thinking
            ui.show_info(f"Thinking mode: [green]{'ON' if session.thinking else 'OFF'}[/green]")
            refresh_memory_hud(session, ui)
            return True

        if cmd == "/agentic":
            session.agentic = not session.agentic
            ui.show_info(f"Agentic mode: {'ON' if session.agentic else 'OFF'}")
            refresh_memory_hud(session, ui)
            return True

        if cmd == "/yolo":
            current = session.variables.get("yolo", False)
            session.variables["yolo"] = not current
            state = "ON" if session.variables["yolo"] else "OFF"
            ui.show_info(f"YOLO mode: {'[green]ON[/green]' if state == 'ON' else '[red]OFF[/red]'}")
            session.session_manager.save_history(session.folder_context)
            refresh_memory_hud(session, ui)
            return True

        if cmd == "/splash":
            show_splash(session, ui)
            refresh_memory_hud(session, ui)
            return True

        ui.show_error(f"Unknown command: {cmd}")
        return True

    session.send_message(user_input)
    refresh_memory_hud(session, ui)
    return True


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

    ui = TextualUI()

    session_manager = SessionManager(ui=ui)
    ui.set_variables(session_manager.variables)
    ollama_host = session_manager.variables.get("ollama_host")

    try:
        action, session_name = choose_session(session_manager)
        if action == "load":
            session_manager.switch_session(session_name)
            p_cfg = session_manager.provider_config
            if p_cfg.get("provider") and p_cfg.get("model"):
                provider = init_provider(p_cfg["provider"], p_cfg["model"], ollama_host=ollama_host)
            else:
                provider = select_provider_and_model(args.provider, args.model, ollama_host=ollama_host)
                session_manager.provider_config = {
                    "provider": provider.name,
                    "model": provider.model_name,
                }
                session_manager.save_history()
        else:
            provider = select_provider_and_model(args.provider, args.model, ollama_host=ollama_host)
            session_manager.new_session(session_name, provider.name, provider.model_name)
    except Exception as exc:
        console.print(f"[red]Failed to initialize Session/Provider: {exc}[/red]")
        sys.exit(1)

    session = Session(
        provider=provider,
        thinking=False,
        system_instruction=args.system,
        session_manager=session_manager,
        ui=ui,
        debug=args.debug,
    )

    ui.set_submission_callback(lambda user_input: handle_user_input(session, ui, user_input, args))
    show_splash(session, ui)
    refresh_memory_hud(session, ui)
    ui.run()


if __name__ == "__main__":
    main()
