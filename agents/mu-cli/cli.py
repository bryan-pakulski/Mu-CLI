#!/usr/bin/env python
from __future__ import annotations

import argparse

# Set python path to current dir so no matter where this script is called from
# we can run it
import sys
import os
sys.path.append(os.path.dirname(os.path.realpath(__file__)))

from agent import Agent
from providers.echo import EchoProvider
from tools.filesystem import ReadFileTool

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provider-agnostic AI CLI (human-in-the-loop)")
    parser.add_argument(
        "--provider",
        default="echo",
        choices=["echo"],
        help="Model provider adapter to use.",
    )
    parser.add_argument(
        "--system",
        default="You are a helpful coding assistant. Keep responses concise.",
        help="Initial system instruction",
    )
    return parser

def run() -> int:
    args = build_parser().parse_args()

    if args.provider != "echo":
        raise ValueError(f"Unsupported provider: {args.provider}")

    agent = Agent(provider=EchoProvider(), tools=[ReadFileTool()])
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
