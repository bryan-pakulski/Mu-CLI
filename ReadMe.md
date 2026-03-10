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
  - `run_make_agent_job` (runs jobs from read-only `Makefile.agent`)
- workspace indexing + tool-run memory
- per-turn token/cost report with JSON-configurable provider pricing
- session persistence (resume conversations and workspace state)
- session-scoped model skills loaded from Markdown files

### Git workflow support

The `git` tool supports agent-friendly workflow actions in addition to basic status/diff/add/commit:

- create a branch: `create_branch`
- switch branches (e.g., back to `main`): `switch_branch`
- monitor branch delta vs base branch: `branch_changes`
- raise a pull request through GitHub CLI: `create_pr`

This enables iterative agent mode flows where the agent can plan, make changes on a feature branch,
inspect progress, and either open a PR when complete or continue iterating.

## Full HTML documentation

- Open the full guide at [`../doco.html`](../doco.html).
- Developer workflow rule: when CLI commands/tools/provider behavior changes, update `doco.html` in the same PR.

## Additional developer docs

- Architecture notes: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Testing guide: [`docs/TESTING.md`](docs/TESTING.md)

## Makefile shortcuts

```bash
make test
make test-fast
make test-verbose
make test-web
make check
make models
make run-echo
make run-openai   # requires OPENAI_API_KEY
make run-gemini   # requires GEMINI_API_KEY or GOOGLE_API_KEY
make run-web      # Flask GUI on http://localhost:5000
make docker-build
make docker-run-web
make docker-run-cli
make docker-models
```

## Dockerized runtime

- Build image: `make docker-build`
- Run GUI: `make docker-run-web` then open `http://localhost:5000`
- Run CLI: `make docker-run-cli`
- List model catalog in container: `make docker-models`
- Optional Compose paths:
  - `docker compose up mu-cli-web`
  - `docker compose run --rm mu-cli-cli`

Notes:
- Container persists app state via `./.mu_cli:/app/.mu_cli` volume.
- Current repo is mounted at `/workspace` for tool access in containerized CLI runs.

## Maintainability direction

The project is currently easiest to evolve when changes are kept small and bounded by layer:

- Provider/tool behavior in `agents/mu_cli/providers/` and `agents/mu_cli/tools/`.
- Session/runtime behavior in `agents/mu_cli/session.py`, `agents/mu_cli/workspace.py`, and `agents/mu_cli/web.py`.
- UI behavior in `agents/mu_cli/templates/index.html`.

For architectural refactor plans (including backend route decomposition and frontend modularization), see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## GUI (Flask)

- Start with `make run-web` then open `http://localhost:5000`.
- Includes a toggleable settings sidebar (provider/model/approval/workspace/debug/agentic).
- Includes a top-panel `GIT` button (visible for git workspaces) that opens a modal for repository/branch actions and current workspace diff view.
- During background agent sessions, a dedicated “Plan output” overlay shows plan, checkpoints, and execution stages without reloading chat messages.
- Includes built-in session management actions (new/load/delete/list/status).
- Includes settings controls to enable/disable available skills per session.
- Skills list in the left sidebar includes a `View` action to inspect skill markdown content.
- Settings include provider-specific API key overrides (`OpenAI`, `Google/Gemini`) instead of a single global key.
- Session row menu includes a clear action to wipe context and restart that session.
- Left runtime panels are collapsible (runtime, sessions, skills, usage).
- Right metadata panel is collapsible/resizable and groups compact debug/tool/research metadata.
- Session usage panel includes `Metrics` and `Detailed stats` actions for per-session analytics.
- Debug mode in GUI surfaces model tool requests and tool execution traces.

## Workspace context and memory

- Attach at startup with `--workspace <path>` or at runtime: `/workspace attach <path>`.
- Indexing respects `.gitignore` and filters common secret-like files/content.
- Tool runs are saved in `.mu_cli/workspaces/workspace_<hash>.json`.

## Session persistence

- Session state is saved in `.mu_cli/sessions/<session>.json`.
- Resume is automatic by default; disable with `--no-resume`.

## Session management

- `/session status`: show active session
- `/session list`: list available sessions
- `/session new <name>`: start fresh named session
- `/session load <name>`: load existing session
- `/session delete <name>`: delete a session (cannot delete active session)

## Skills

- Skills are Markdown files loaded from `./skills` by default.
- Configure a different folder with `--skills-dir <path>`.
- Skills are enabled per session and persisted in session state.
- Commands:
  - `/skills list`
  - `/skills status`
  - `/skills enable <name>`
  - `/skills disable <name>`

## Agentic planning prompt

- A planning-focused system prompt is auto-injected by default to keep the model transparent and tool-first.
- Check status with `/agentic status`.
- Disable injection with `--no-agentic-planning`.

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

## Debug mode

- Start with `--debug` or toggle in app with `/debug on|off`.
- `/debug status` shows current debug mode.
- When enabled, CLI prints model tool requests and tool execution traces.

## Tooling and product roadmap

1. **Tool-use reliability (near term)**
   - Strengthen tool-first prompting and runtime enforcement so repository/file requests always route through tools.
   - Add tool-selection feedback loops ("wrong tool" and "missing args") so models self-correct faster.
2. **Streaming UX (near term)**
   - Stream assistant output and trace events in the GUI for long-running turns.
   - Add explicit progress indicators for model-inference vs tool-execution phases.
3. **Developer ergonomics (mid term)**
   - Add keyboard shortcuts, richer command history, and sticky workspace/session context in GUI.
   - Add safer approval previews for mutating tools with diffs before apply.
4. **Tooling depth (mid term)**
   - Add high-signal read/search tools (symbol search, grep-like filtering, repo map summaries).
   - Add optional test/lint execution tools with controlled allowlists.
5. **Evaluation and guardrails (ongoing)**
   - Expand regression tests for tool-calling behavior and streamed-turn correctness.
   - Add benchmark prompts to track tool usage rate, turn latency, and successful edit completion.

## Basic usage

```bash
PYTHONPATH=agents python -m mu_cli.cli --provider echo --debug
```

Useful commands:

- `/help`, `/tools`, `/tool-help <tool_name>`
- `/workspace attach <path>`, `/workspace status`
- `/models`, `/models openai`, `/model select gpt-4o-mini`
- `/approvals status`, `/approvals set auto`
- `/agentic status`
- `/debug status`, `/debug on`, `/debug off`
- `/session status`, `/session list`, `/session new demo`, `/session load demo`
- `/skills list`, `/skills enable code-review`, `/skills status`
- `/quit`
