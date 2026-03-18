#!/usr/bin/env python

import argparse
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.text import Text
from rich.table import Table
from rich import box

# Import from our new modular structure
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from providers.openai import OpenAIProvider
from core.session import SessionManager, Session
from core.workspace import FolderContext
from ui.rich_ui import RichUI

console = Console()


def print_help():
    table = Table(title="Available Commands", box=box.SIMPLE)
    table.add_column("Command", style="cyan", no_wrap=True)
    table.add_column("Alias", style="magenta")
    table.add_column("Description", style="white")

    table.add_row("/clear", "", "Clear conversation history")
    table.add_row("/new [name]", "", "Start a new conversation")
    table.add_row("/file <path>", "/f", "Attach a file")
    table.add_row(
        "/folder <path>", "/dir", "Monitor a folder(s) for changes and use as context"
    )
    table.add_row("/help", "", "Show this help menu")
    table.add_row("/list", "/ls", "List saved conversations")
    table.add_row("/load [name]", "/open", "Load a conversation")
    table.add_row("/model [name]", "", "Show / change current model")
    table.add_row("/get [key]", "", "Get a variable")
    table.add_row("/yolo", "", "Toggle YOLO mode (no approvals)")
    table.add_row("/set [key] [value]", "", "Set a variable")
    table.add_row("/unset [key]", "", "Unset a variable (or --all)")
    table.add_row("/variables", "", "Show all variables")
    table.add_row("/agentic", "", "Toggle Agentic (Tool Calling) mode")
    table.add_row(
        "/tool <enable/disable/list> <toolname>",
        "",
        "Enable/Disable a tool or list all",
    )
    table.add_row(
        "/mode <mode>",
        "",
        "Change the agentic strategy (default, debug, feature, research)",
    )
    table.add_row("/provider [name]", "", "Change the LLM provider (gemini, ollama)")
    table.add_row("/quit", "/q", "Exit")
    table.add_row("/system <txt>", "/sys", "Update system prompt")
    table.add_row("/thinking", "", "Toggle thinking mode")
    table.add_row("/tokens", "", "Show context token usage")
    table.add_row("/view", "", "View conversation history")

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

    # History info
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


def main():
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

    # --- Initialize UI ---
    ui = RichUI()

    # --- Initialize Session Manager ---
    session_manager = SessionManager(ui=ui)
    ui.set_variables(session_manager.variables)
    ollama_host = session_manager.variables.get("ollama_host")

    # --- Initialize Session and Provider ---
    try:
        action, session_name = choose_session(session_manager)
        if action == "load":
            session_manager.switch_session(session_name)
            p_cfg = session_manager.provider_config
            if p_cfg.get("provider") and p_cfg.get("model"):
                provider = init_provider(
                    p_cfg["provider"], p_cfg["model"], ollama_host=ollama_host
                )
            else:
                # Fallback if config is missing
                provider = select_provider_and_model(
                    args.provider, args.model, ollama_host=ollama_host
                )
                session_manager.provider_config = {
                    "provider": provider.name,
                    "model": provider.model_name,
                }
                session_manager.save_history()
        else:
            provider = select_provider_and_model(
                args.provider, args.model, ollama_host=ollama_host
            )
            session_manager.new_session(
                session_name, provider.name, provider.model_name
            )
    except Exception as e:
        console.print(f"[red]Failed to initialize Session/Provider: {e}[/red]")
        sys.exit(1)

    # --- Initialize Session ---
    session = Session(
        provider=provider,
        thinking=False,
        system_instruction=args.system,
        session_manager=session_manager,
        ui=ui,
        debug=args.debug,
    )

    print_splash(session)

    while True:
        try:
            user_input = ui.get_input(
                session.session_manager.current_session_name, session.staged_files
            )

            if not user_input:
                continue

            if user_input.startswith("/"):
                parts = user_input.split(" ", 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ["/quit", "/exit", "/q"]:
                    print("Goodbye!")
                    break

                elif cmd in ["/help", "/h"]:
                    print_help()

                elif cmd in ["/clear", "/c"]:
                    session.session_manager.clear_current_history()
                    session.staged_files = []

                elif cmd in ["/view", "/v"]:
                    session.session_manager.view_history()

                elif cmd in ["/file", "/f", "/add"]:
                    if arg:
                        session.add_file(arg)
                    else:
                        console.print("[red]Usage: /file <path_to_file>")

                elif cmd in ["/clearfiles", "/cf"]:
                    session.clear_files()

                elif cmd in ["/folder", "/dir"]:
                    if arg:
                        sub_parts = arg.split(" ", 1)
                        if sub_parts[0] == "remove" and len(sub_parts) > 1:
                            path_to_remove = sub_parts[1].strip("'\"")
                            if session.folder_context.remove_folder(path_to_remove):
                                console.print(
                                    f"[green]Removed folder from context: {path_to_remove}[/green]"
                                )
                                session.session_manager.save_history(
                                    session.folder_context
                                )
                            else:
                                console.print(
                                    f"[red]Folder not found in context: {path_to_remove}[/red]"
                                )
                        else:
                            # Support multiple folders
                            import shlex

                            try:
                                paths = shlex.split(arg)
                            except ValueError:
                                paths = [arg.strip("'\"")]

                            for path in paths:
                                path = path.strip("'\"")
                                if session.folder_context.add_folder(path):
                                    console.print(
                                        f"[green]Added folder context: {path}[/green]"
                                    )
                                    if len(session.folder_context.folders) == 1:
                                        try:
                                            os.chdir(session.folder_context.folders[0])
                                            console.print(
                                                f"[dim]Switched workspace to: {os.getcwd()}[/dim]"
                                            )
                                        except Exception:
                                            pass
                                else:
                                    console.print(
                                        f"[red]Folder not found or invalid: {path}[/red]"
                                    )

                            session.session_manager.save_history(session.folder_context)
                            console.print(
                                "[dim]Files cached as initial context. Changes will be provided as diffs.[/dim]"
                            )
                    else:
                        if not session.folder_context.folders:
                            console.print(
                                "[yellow]No folders currently monitored.[/yellow]"
                            )
                            console.print(
                                "Usage: /folder <path> OR /folder remove <path>"
                            )
                        else:
                            console.print(
                                "\n[bold cyan]Current Folder Context:[/bold cyan]"
                            )
                            grid = Table.grid(padding=1)
                            grid.add_column(style="green", justify="left")
                            for f in session.folder_context.folders:
                                grid.add_row(f"📁 {f}")
                            console.print(grid)

                            files = session.folder_context.get_file_list()
                            console.print(
                                f"\n[dim]Total Tracked Files: {len(files)}[/dim]"
                            )
                            if files:
                                console.print("[dim]Tracked Files:[/dim]")
                                for f in files[:10]:
                                    console.print(
                                        f" - {os.path.basename(f)} [dim]({f})[/dim]"
                                    )
                                if len(files) > 10:
                                    console.print(
                                        f"   [dim]... and {len(files)-10} more[/dim]"
                                    )

                elif cmd in ["/list", "/ls"]:
                    session.session_manager.list_sessions()

                elif cmd in ["/new"]:
                    name = arg.strip() if arg else None
                    # Prompt for provider/model on new session
                    ollama_host = session.variables.get("ollama_host")
                    new_provider = select_provider_and_model(
                        None, None, ollama_host=ollama_host
                    )
                    session.provider = new_provider
                    session.session_manager.new_session(
                        name, new_provider.name, new_provider.model_name
                    )
                    session.staged_files = []
                    session.folder_context = session.session_manager.folder_context
                    ui.set_variables(session.variables)
                    print_splash(session)
                elif cmd in ["/load", "/open"]:
                    if arg:
                        session.session_manager.switch_session(arg.strip())
                        session.staged_files = []
                        session.folder_context = session.session_manager.folder_context
                        ui.set_variables(session.variables)
                        # Update provider based on session config
                        p_cfg = session.session_manager.provider_config
                        if p_cfg.get("provider") and p_cfg.get("model"):
                            ollama_host = session.variables.get("ollama_host")
                            session.provider = init_provider(
                                p_cfg["provider"], p_cfg["model"], ollama_host
                            )

                        sync_provider_settings(session)
                        print_splash(session)
                    else:
                        console.print("[yellow]Usage: /load <session_name>")

                elif cmd in ["/delete", "/rm"]:
                    if arg:
                        session.session_manager.delete_session(arg.strip())
                    else:
                        console.print("[yellow]Usage: /delete <session_name>")

                elif cmd in ["/system", "/sys"]:
                    if arg:
                        session.system_instruction = arg
                        console.print("[green]System prompt updated.")
                    else:
                        curr = (
                            session.system_instruction
                            if session.system_instruction
                            else "None"
                        )
                        console.print(f"[blue]Current System Prompt:\n{curr}")

                elif cmd == "/model":
                    if arg:
                        session.provider.model_name = arg.strip()
                        console.print(
                            f"Model changed to: [green]{session.provider.model_name}"
                        )
                    else:
                        models = session.provider.get_available_models()
                        if models:
                            console.print("\n[bold cyan]Available Models:[/bold cyan]")
                            for i, m in enumerate(models, 1):
                                console.print(f" {i}. {m}")
                            choice = IntPrompt.ask(
                                "Select a model",
                                choices=[str(i) for i in range(1, len(models) + 1)],
                            )
                            session.provider.model_name = models[int(choice) - 1]
                            console.print(
                                f"Model changed to: [green]{session.provider.model_name}"
                            )
                            # Update provider config in session
                            session.session_manager.provider_config = {
                                "provider": session.provider.name,
                                "model": session.provider.model_name,
                            }
                            session.session_manager.save_history()
                            print_splash(session)

                elif cmd == "/provider":
                    try:
                        ollama_host = session.variables.get("ollama_host")
                        session.provider = select_provider_and_model(
                            arg.strip() if arg else None, None, ollama_host=ollama_host
                        )
                        session.session_manager.provider_config = {
                            "provider": session.provider.name,
                            "model": session.provider.model_name,
                        }
                        session.session_manager.save_history()
                        console.print("[green]Provider changed successfully![/green]")
                        print_splash(session)
                    except Exception as e:
                        console.print(f"[red]Failed to change provider: {e}[/red]")

                elif cmd == "/set":
                    if arg:
                        if "=" in arg:
                            k, v = arg.split("=", 1)
                        elif " " in arg:
                            k, v = arg.split(" ", 1)
                        else:
                            console.print(
                                "[red]Usage: /set <key> <value> OR /set <key>=<value>[/red]"
                            )
                            continue

                        k = k.strip()
                        v = v.strip()
                        try:
                            from utils.config import validate_and_cast

                            session.variables[k] = validate_and_cast(k, v)
                            session.session_manager.save_history(session.folder_context)
                            console.print(
                                f"[green]Set variable: {k} = {session.variables[k]} ({type(session.variables[k]).__name__})[/green]"
                            )
                            if k == "ollama_host":
                                sync_provider_settings(session)
                        except ValueError as e:
                            console.print(f"[red]Error: {e}[/red]")
                    else:
                        console.print("[red]Usage: /set <key> <value>[/red]")

                elif cmd == "/get":
                    k = arg.strip()
                    if not k:
                        for vk, vv in session.variables.items():
                            console.print(f"[blue]{vk}[/blue] = {vv}")
                    else:
                        console.print(
                            f"{session.variables.get(k, '[dim]Not set[/dim]')}"
                        )

                elif cmd == "/unset":
                    k = arg.strip()
                    if not k:
                        console.print("[red]Usage: /unset <key> OR /unset --all[/red]")
                    elif k == "--all":
                        session.variables.clear()
                        # Restore defaults after clear
                        from utils.config import DEFAULT_VARIABLES

                        session.variables.update(DEFAULT_VARIABLES)
                        session.session_manager.save_history(session.folder_context)
                        console.print("[green]All variables reset to defaults.[/green]")
                        sync_provider_settings(session)
                    else:
                        if k in session.variables:
                            from utils.config import VARIABLE_SCHEMA

                            if k in VARIABLE_SCHEMA:
                                session.variables[k] = VARIABLE_SCHEMA[k]["default"]
                                console.print(
                                    f"[green]Reset variable to default: {k} = {session.variables[k]}[/green]"
                                )
                            else:
                                del session.variables[k]
                                console.print(f"[green]Unset variable: {k}[/green]")
                            session.session_manager.save_history(session.folder_context)
                            if k == "ollama_host":
                                sync_provider_settings(session)
                        else:
                            console.print(f"[yellow]Variable '{k}' not found.[/yellow]")

                elif cmd == "/variables":
                    for vk, vv in session.variables.items():
                        console.print(f"[blue]{vk}[/blue] = [green]{vv}[/green]")

                elif cmd == "/mode":
                    valid_modes = ["default", "debug", "feature", "research"]
                    if arg and arg.lower() in valid_modes:
                        session.variables["agent_mode"] = arg.lower()
                        session.session_manager.save_history(session.folder_context)
                        console.print(f"Agent strategy set to: {arg.upper()}")
                    else:
                        console.print("Usage: /mode <default|debug|feature|research>")
                        curr = session.variables.get("agent_mode", "default")
                        console.print(f"Current mode: {curr}")

                elif cmd == "/tool":
                    if not arg:
                        console.print("Usage: /tool <enable|disable|list> ")
                    else:
                        t_parts = arg.split(" ", 1)
                        t_cmd = t_parts[0].lower()
                        t_name = t_parts[1].strip() if len(t_parts) > 1 else ""

                        if t_cmd == "disable" and t_name:
                            if t_name not in session.disabled_tools:
                                session.disabled_tools.append(t_name)
                            console.print(f"Tool '{t_name}' disabled.")
                        elif t_cmd == "enable" and t_name:
                            if t_name in session.disabled_tools:
                                session.disabled_tools.remove(t_name)
                            console.print(f"Tool '{t_name}' enabled.")
                        elif t_cmd == "list":
                            console.print(f"Disabled Tools: {session.disabled_tools}")
                        else:
                            console.print(
                                "Invalid /tool command. Use enable, disable, or list."
                            )

                elif cmd == "/tokens":
                    hist_len = len(session.session_manager.history)
                    anchor = session.session_manager.summary_anchor
                    tokens = session.session_manager.token_counts

                    console.print("[yellow]--- Context Stats ---")
                    console.print(f"Total History Turns: {hist_len}")
                    console.print(f"Summarized Turns:    {anchor}")
                    console.print(f"Active Turns (Window): {hist_len - anchor}")
                    console.print(f"Session Tokens (In):  {tokens['input']}")
                    console.print(f"Session Tokens (Out): {tokens['output']}")
                    console.print(f"Session Tokens (Total): {tokens['total']}")
                    console.print(
                        f"Session Est. Cost:    ${tokens.get('total_cost', 0.0):.5f}"
                    )
                    console.print(
                        "[dim](Actual token count is also displayed after each generation)[/dim]"
                    )

                elif cmd == "/thinking":
                    session.thinking = not session.thinking
                    state = "ON" if session.thinking else "OFF"
                    console.print(f"Thinking mode: [green]{state}")
                elif cmd == "/agentic":
                    session.agentic = not session.agentic
                    state = "ON" if session.agentic else "OFF"
                    console.print(f"Agentic mode: {state}")
                elif cmd == "/yolo":
                    current = session.variables.get("yolo", False)
                    session.variables["yolo"] = not current
                    state = "ON" if session.variables["yolo"] else "OFF"
                    if state == "ON":
                        console.print("YOLO mode: [green]ON[/green]")
                    else:
                        console.print("YOLO mode: [red]OFF[/red]")
                    session.session_manager.save_history(session.folder_context)
                elif cmd == "/splash":
                    print_splash(session)
                else:
                    console.print(f"[red]Unknown command: {cmd}")

                continue

            session.send_message(user_input)

        except KeyboardInterrupt:
            console.print("\n(Interrupted. Type /quit to exit)")
        except EOFError:
            console.print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
