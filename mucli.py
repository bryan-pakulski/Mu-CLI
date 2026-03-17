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
        "/folder <path>", "/dir", "Monitor a folder for changes and use as context"
    )
    table.add_row("/help", "", "Show this help menu")
    table.add_row("/list", "/ls", "List saved conversations")
    table.add_row("/load [name]", "/open", "Load a conversation")
    table.add_row("/model [name]", "", "Show / change current model")
    table.add_row("/get [key]", "", "Get a variable")
    table.add_row("/set [key] [value]", "", "Set a variable")
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


def print_splash(
    session_model, session_thinking, session_sys, session_agentic, agent_mode
):
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

    info_grid = f"""                                                                   
    [bold magenta]System:[/bold magenta]  {session_sys}                                
    [bold magenta]Model:[/bold magenta]   [bold cyan]{session_model}[/bold cyan]       
    [bold magenta]Thinking:[/bold magenta] [bold cyan]{session_thinking}[/bold cyan]   
    [bold magenta]Agentic:[/bold magenta]  [bold cyan]{session_agentic}[/bold cyan]
    [bold magenta]Mode:[/bold magenta]     [bold cyan]{agent_mode}[/bold cyan]
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


def select_provider_and_model(args_provider, args_model):
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

    # Init provider contextually
    if provider_name == "ollama":
        provider = OllamaProvider(model_name="")
    elif provider_name == "gemini":
        provider = GeminiProvider(model_name="")
    elif provider_name == "openai":
        provider = OpenAIProvider(model_name="")
    else:
        console.print(f"[red]Unknown provider: {provider_name}")
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

    # --- Initialize Provider ---
    try:
        provider = select_provider_and_model(args.provider, args.model)

    except Exception as e:
        console.print(f"[red]Failed to initialize Provider: {e}[/red]")
        sys.exit(1)

    # --- Initialize UI ---
    ui = RichUI()

    # --- Initialize Session ---
    session_manager = SessionManager(ui=ui)
    session = Session(
        provider=provider,
        thinking=False,
        system_instruction=args.system,
        session_manager=session_manager,
        ui=ui,
        debug=args.debug,
    )

    sys_status = "SET" if session.system_instruction else "NONE"
    print_splash(
        session.provider.model_name,
        session.thinking,
        sys_status,
        session.agentic,
        session.variables.get("agent_mode", "default"),
    )

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
                            else:
                                console.print(
                                    f"[red]Folder not found in context: {path_to_remove}[/red]"
                                )
                        else:
                            path = arg.strip("'\"")
                            if session.folder_context.add_folder(path):
                                console.print(
                                    f"[green]Added folder context: {path}[/green]"
                                )
                                console.print(
                                    "[dim]Files cached as initial context. Changes will be provided as diffs.[/dim]"
                                )
                            else:
                                console.print(
                                    f"[red]Folder not found or invalid: {path}[/red]"
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
                    session.session_manager.new_session(name)
                    session.staged_files = []
                    session.folder_context = FolderContext()

                elif cmd in ["/load", "/open"]:
                    if arg:
                        session.session_manager.switch_session(arg.strip())
                        session.staged_files = []
                        session.folder_context = FolderContext()
                        if session.session_manager.folder_context_data:
                            session.folder_context.from_dict(
                                session.session_manager.folder_context_data
                            )
                            console.print(
                                f"[dim]Context restored for {len(session.folder_context.folders)} folders.[/dim]"
                            )
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
                            console.print(f"\n[bold cyan]Available Models:[/bold cyan]")
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
                            sys_status = "SET" if session.system_instruction else "NONE"
                            print_splash(
                                session.provider.model_name,
                                session.thinking,
                                sys_status,
                                session.agentic,
                                session.variables.get("agent_mode", "default"),
                            )

                elif cmd == "/provider":
                    try:
                        session.provider = select_provider_and_model(
                            arg.strip() if arg else None, None
                        )
                        console.print(f"[green]Provider changed successfully![/green]")
                        sys_status = "SET" if session.system_instruction else "NONE"
                        print_splash(
                            session.provider.model_name,
                            session.thinking,
                            sys_status,
                            session.agentic,
                            session.variables.get("agent_mode", "default"),
                        )
                    except Exception as e:
                        console.print(f"[red]Failed to change provider: {e}[/red]")

                elif cmd == "/set":
                    if arg and " " in arg:
                        k, v = arg.split(" ", 1)
                        session.variables[k.strip()] = v.strip()
                        session.session_manager.save_history(session.folder_context)
                        console.print(
                            f"[green]Set variable: {k.strip()} = {v.strip()}[/green]"
                        )
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

                    console.print("[yellow]--- Context Stats ---")
                    console.print(f"Total History Turns: {hist_len}")
                    console.print(f"Summarized Turns:    {anchor}")
                    console.print(f"Active Turns (Window): {hist_len - anchor}")
                    console.print(
                        "[dim](Actual token count is displayed after generation)"
                    )

                elif cmd == "/thinking":
                    session.thinking = not session.thinking
                    state = "ON" if session.thinking else "OFF"
                    console.print(f"Thinking mode: [green]{state}")
                elif cmd == "/agentic":
                    session.agentic = not session.agentic
                    state = "ON" if session.agentic else "OFF"
                    console.print(f"Agentic mode: {state}")
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
