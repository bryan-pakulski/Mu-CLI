# Mu-CLI (Provider Agnostic Foundation)

Provider-agnostic CLI scaffold intended for a **human-in-the-loop development workflow**.

## What is included

- provider-neutral core message model
- pluggable provider adapter interface
- providers:
  - local `echo` provider (for development)
  - `openai` provider via Chat Completions API (with structured tool-calling)
  - `gemini` provider via Google Generative Language API (with structured tool-calling)
- tooling:
  - `read_file`
  - `write_file` (mutating; approval-gated)
  - `apply_patch` (mutating; approval-gated)
  - `git` (mutating; approval-gated)
  - `list_workspace_files`
  - `get_workspace_file_context`
- workspace indexing + tool-run memory
- per-turn token/cost report with JSON-configurable provider pricing
- session persistence (resume conversations and workspace state)

## Makefile shortcuts

From repo root:

```bash
make test
make test-verbose
make models
make run-echo
make run-openai   # requires OPENAI_API_KEY
make run-gemini   # requires GEMINI_API_KEY or GOOGLE_API_KEY
```

## Workspace context and memory

- Attach at startup with `--workspace <path>` or at runtime: `/workspace attach <path>`.
- Indexing respects `.gitignore` and filters common secret-like files/content.
- Tool runs are saved in `.mu_cli/workspaces/workspace_<hash>.json`.

## Session persistence

- Session state is saved in `.mu_cli/sessions/<session>.json`.
- Resume is automatic by default; disable with `--no-resume`.

## Approval policy

- Approval mode controls mutating tools: `ask`, `auto`, `deny`.
- Configure at startup with `--approval-mode`.
- Change in app with `/approvals set <ask|auto|deny>`.

## Model selection

- List catalog: `--list-models` or `/models [provider]`.
- Switch active model at runtime: `/model select <name>`.

## Token usage + pricing report

- Each prompt prints a turn report with token usage and estimated USD cost.
- Pricing config is user-adjustable JSON at `.mu_cli/pricing.json` by default.

## Basic usage

```bash
PYTHONPATH=agents python -m mu_cli.cli --provider echo
```

Useful commands:

- `/help`, `/tools`, `/tool-help <tool_name>`
- `/workspace attach <path>`, `/workspace status`
- `/models`, `/models openai`, `/model select gpt-4o-mini`
- `/approvals status`, `/approvals set auto`
- `/quit`

