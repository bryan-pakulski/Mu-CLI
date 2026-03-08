# Mu-CLI (Provider Agnostic Foundation)

Provider-agnostic CLI scaffold intended for a **human-in-the-loop development workflow**.

## What is included

- provider-neutral core message model
- pluggable provider adapter interface
- providers:
  - local `echo` provider (for development)
  - `openai` provider via Chat Completions API
  - `gemini` provider via Google Generative Language API
- Tooling:
  - (`read_file`) with structured schema
- Minimal interactive CLI loop

## Run

From repo root:

```bash
PYTHONPATH=agents python -m mu_cli.cli --provider echo
```

## Basic usage

- Ask normal prompts.
- Exit with `/quit`.
- Discover commands and tool tips with `/help`, `/tools`, and `/tool-help read_file`.
- Use **TAB autocomplete** for slash commands and tool names (when `readline` is available).
- Trigger a tool call through the local echo provider:

```text
/tool read_file {"path":"agents/ReadMe.md"}
```

## Real providers

OpenAI:

```bash
export OPENAI_API_KEY=...
PYTHONPATH=agents python -m mu_cli.cli --provider openai --model gpt-4o-mini
```

Gemini:

```bash
export GEMINI_API_KEY=...
PYTHONPATH=agents python -m mu_cli.cli --provider gemini --model gemini-2.0-flash
```

(You can also pass `--api-key` directly.)

## Structure

```text
mu_cli/
  core/types.py          # canonical message/tool/provider types
  agent.py               # provider-neutral agent loop
  providers/echo.py      # local development provider adapter
  providers/openai.py    # OpenAI chat completions adapter
  providers/gemini.py    # Gemini generateContent adapter
  tools/base.py          # tool protocol + result type
  tools/filesystem.py    # read_file tool
  cli.py                 # interactive CLI entrypoint
```

## Next steps

1. Add policy/approval checkpoints before tool execution.
2. Add write/patch/git tools.
3. Persist session history (e.g., sqlite/jsonl).
