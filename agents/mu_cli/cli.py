#!/usr/bin/env python
from __future__ import annotations

import argparse

from mu_cli.agent import Agent
from mu_cli.providers.echo import EchoProvider
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.openai import OpenAIProvider
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


def run() -> int:
    args = build_parser().parse_args()
    provider = _build_provider(args.provider, args.model, args.api_key)

    agent = Agent(provider=provider, tools=[ReadFileTool()])
    agent.add_system_prompt(args.system)

    print(f"ai-cli [{args.provider}] started. Type /quit to exit.")
    print("Tip: /tool read_file {\"path\":\"ReadMe.md\"}")

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

        message = agent.step(user_input)
        print(f"\nassistant> {message.content}")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
