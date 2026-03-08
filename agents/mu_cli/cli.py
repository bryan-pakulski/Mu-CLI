#!/usr/bin/env python
from __future__ import annotations

import argparse
from typing import Sequence

from mu_cli.agent import Agent
from mu_cli.providers.echo import EchoProvider
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.openai import OpenAIProvider
from mu_cli.tools.base import Tool
from mu_cli.tools.filesystem import ReadFileTool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provider-agnostic AI CLI (human-in-the-loop)")
    parser.add_argument(
        "--provider",
        default="echo",
        choices=["echo", "openai", "gemini"],
        help="Model provider adapter to use.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model name override for selected provider.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key override (otherwise reads provider env var).",
    )
    parser.add_argument(
        "--system",
        default="You are a helpful coding assistant. Keep responses concise.",
        help="Initial system instruction",
    )
    return parser


def _build_provider(name: str, model: str | None, api_key: str | None):
    if name == "echo":
        return EchoProvider()
    if name == "openai":
        return OpenAIProvider(model=model or "gpt-4o-mini", api_key=api_key)
    if name == "gemini":
        return GeminiProvider(model=model or "gemini-2.0-flash", api_key=api_key)
    raise ValueError(f"Unsupported provider: {name}")


def _format_tool_tip(tool: Tool) -> str:
    required = set(tool.schema.get("required", []))
    properties: dict[str, dict[str, str]] = tool.schema.get("properties", {})
    arg_parts = []
    for name, prop in properties.items():
        suffix = " (required)" if name in required else ""
        description = prop.get("description", "")
        arg_parts.append(f"- {name}{suffix}: {description}".rstrip())

    args_block = "\n".join(arg_parts) if arg_parts else "- (no args)"
    return f"{tool.name}: {tool.description}\n{args_block}"


def build_help_text(tools: Sequence[Tool]) -> str:
    tool_lines = "\n\n".join(_format_tool_tip(tool) for tool in tools)
    return (
        "Commands:\n"
        "- /help: Show this help text.\n"
        "- /tools: List available tools and their arguments.\n"
        "- /tool-help <name>: Show detailed help for one tool.\n"
        "- /tool <name> {json_args}: Ask the model to run a tool call (echo provider supports this directly).\n"
        "- /quit (or /q, exit): Exit the CLI.\n\n"
        f"Tools:\n{tool_lines}"
    )


class CommandCompleter:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self.tool_names = sorted(tool.name for tool in tools)
        self.commands = ["/help", "/tools", "/tool-help", "/tool", "/quit", "/q", "exit"]

    def matches(self, text: str, line_buffer: str) -> list[str]:
        stripped = line_buffer.lstrip()
        if stripped.startswith("/tool-help "):
            return [name for name in self.tool_names if name.startswith(text)]
        if stripped.startswith("/tool "):
            return [name for name in self.tool_names if name.startswith(text)]
        return [command for command in self.commands if command.startswith(text)]

    def complete(self, text: str, state: int) -> str | None:
        try:
            import readline
        except ImportError:
            return None

        options = self.matches(text, readline.get_line_buffer())
        if state < len(options):
            return options[state]
        return None


def setup_autocomplete(tools: Sequence[Tool]) -> None:
    try:
        import readline
    except ImportError:
        return

    completer = CommandCompleter(tools)
    readline.parse_and_bind("tab: complete")
    readline.set_completer(completer.complete)


def _handle_local_command(user_input: str, tools: Sequence[Tool]) -> tuple[bool, str | None]:
    if user_input in {"/help", "/tools"}:
        return True, build_help_text(tools)

    if user_input.startswith("/tool-help"):
        _, _, raw_name = user_input.partition(" ")
        name = raw_name.strip()
        if not name:
            return True, "Usage: /tool-help <name>"

        tool = next((item for item in tools if item.name == name), None)
        if tool is None:
            return True, f"Unknown tool: {name}"
        return True, _format_tool_tip(tool)

    return False, None


def run() -> int:
    args = build_parser().parse_args()
    provider = _build_provider(args.provider, args.model, args.api_key)

    tools = [ReadFileTool()]
    setup_autocomplete(tools)

    agent = Agent(provider=provider, tools=tools)
    agent.add_system_prompt(args.system)

    print(f"ai-cli [{args.provider}] started. Type /quit to exit.")
    print("Tip: use /help for commands and tool descriptions.")

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return 0

        if not user_input:
            continue
        if user_input in {"/quit", "/q", "exit"}:
            print("Goodbye.")
            return 0

        handled, output = _handle_local_command(user_input, tools)
        if handled:
            print(f"\n{output}")
            continue

        message = agent.step(user_input)
        print(f"\nassistant> {message.content}")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
