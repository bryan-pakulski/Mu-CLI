# AI CLI (Provider Agnostic Foundation)

This directory now contains a fresh provider-agnostic CLI scaffold intended for a **human-in-the-loop development workflow**.

## What changed

The previous Gemini-specific interactive implementation was replaced with:

- a provider-neutral core message model
- a pluggable provider adapter interface
- a simple local `echo` provider (for development)
- a first tool (`read_file`) with structured schema
- a minimal interactive CLI loop

## Run

From this directory:

```bash
PYTHONPATH=. ./gemini_interactive
```

or:

```bash
PYTHONPATH=. python -m ai_cli.cli
```

## Basic usage

- Ask normal prompts.
- Exit with `/quit`.
- Trigger a tool call through the local echo provider:

```text
/tool read_file {"path":"ReadMe.md"}
```

## Structure

```text
ai_cli/
  core/types.py          # canonical message/tool/provider types
  agent.py               # provider-neutral agent loop
  providers/echo.py      # local development provider adapter
  tools/base.py          # tool protocol + result type
  tools/filesystem.py    # read_file tool
  cli.py                 # interactive CLI entrypoint
```

## Next steps

1. Add real provider adapters (`gemini`, `anthropic`, `openai`).
2. Add policy/approval checkpoints before tool execution.
3. Add write/patch/git tools.
4. Persist session history (e.g., sqlite/jsonl).
