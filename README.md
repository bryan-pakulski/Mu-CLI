# Mu-CLI

Terminal-first multi-provider coding assistant.

## Requirements
- Python 3.10+
- `patch` command available on your system
- At least one provider key or local model setup:
  - `OPENAI_API_KEY` for OpenAI
  - `GEMINI_API_KEY` or `GOOGLE_API_KEY` for Gemini
  - `OLLAMA_API_KEY` for the hosted Ollama service (optional — self-hosted
    Ollama only needs `OLLAMA_HOST` and a running instance, below)
  - running Ollama instance for local models

## Installation
```bash
git clone https://github.com/your-repo/Mu-CLI.git
cd Mu-CLI
pip install -r requirements.txt

# Put `mucli` on your PATH so you can run it from any directory.
mkdir -p ~/.local/bin
ln -sf "$(pwd)/mucli.py" ~/.local/bin/mucli
```
Ensure `~/.local/bin` is on your `PATH` (most distros add it automatically; if not, add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc).

## Usage

Run `mucli` to start. You'll get a session picker — choose `[+ New Session]` (or load a previous one; sessions persist under `~/.mucli/`). Then attach a workspace and prompt:

```
/workspace folder /path/to/your/repo
explain how authentication works in this codebase
```

## Mobile app

Mucli has a mobile sister app to interact with the --gui sse session. Can be built under `mobile/android` using:

```
make prebuild
make apk
```

and subsequently when an android device is connected to the computer:

`install apk`

### Workflows
- **Quick fix or question** — default mode. Just describe the change.
- **Staged feature** — `/mode feature` breaks the work into approvable phases/tasks.
- **Bug hunt** — `/mode debug` walks reproduce → locate → fix.
- **Read-only investigation** — `/mode research` for citation-aware analysis with no edits.
- **Long-horizon autonomous** — `/mode loop` to work a backlog unattended.

### Useful CLI flags
```bash
mucli --workspace ./src                   # attach folder(s) at startup
mucli --provider openai --model gpt-4o    # pick provider+model
mucli --session my-session                # load a saved session non-interactively
mucli --yolo                              # auto-approve writes
mucli --system "be concise"               # override system instruction
mucli --debug                             # verbose logging
mucli --gui                               # launch the browser GUI (port 30311)
mucli --trace                             # launch GUI and open the Trace Analyzer
mucli --trace-analyze ~/.mucli/trace/X.jsonl  # headless trace summary
```

## Key features
- Multi-provider model support (OpenAI, Gemini, Ollama) with real streaming, parallel tool execution, prompt caching, reasoning effort, and cached/reasoning token telemetry.
- Workspace context from attached folders and files.
- Session save/load and multi-session workflows.
- Feature mode with persistent plans stored in session metadata.
- Plan mode (`/plan`) — read-only tool enforcement.
- Hooks (`.mu/hooks.json`) — shell-cmd hooks at five lifecycle points; plus built-in Python hooks for plan-mode enforcement, secret-path guarding, auto-compaction, and usage tracking. See [documentation/hooks.md](documentation/hooks.md).
- TodoWrite-style task tracking (`todo_write`, `todo_set_status`, `todo_list` tools).
- Sub-agent spawning (`spawn_agent`) — isolated child sessions for focused side quests, depth-capped, plan-mode-aware.
- Skills (`mu/skills/`, `~/.mu/skills/`, `<ws>/.mu/skills/`) — declarative agent extensions with regex triggers; compact index injected by default, bodies auto-load on trigger match or via `invoke_skill`. See [documentation/skills.md](documentation/skills.md).
- Runtime stats with token + cache + reasoning + cost accounting.
- Run tracing + Trace Analyzer dashboard — a per-run JSONL trace of every iteration (context layers, real vs estimated tokens, compactions, nudges, tools, subagents, memory) visualized at `/trace` (or `mucli --trace`) with context-growth curves, tokenizer drift, compaction/nudge/tool timelines, redundant-read heatmaps, and more. The data for harness-performance decisions. See [documentation/tracing.md](documentation/tracing.md).

## Common slash commands

The most common day-to-day commands — see [documentation/commands.md](documentation/commands.md) for the full reference.

| Command | Description |
| --- | --- |
| `/help` | List commands |
| `/plan [on\|off\|toggle]` | Toggle read-only plan mode |
| `/agentic` | Toggle tool-calling mode |
| `/thinking` | Toggle reasoning / extended thinking |
| `/yolo` | Toggle auto-approval for write-side tools |
| `/mode <default\|debug\|feature\|research\|loop\|security\|teacher>` | Switch agent strategy |
| `/feature <subcommand>` | Manage feature workflows (`new`, `list`, `load`, `show`, `delete`, ...) |
| `/workspace` | Show attached folders + staged files |
| `/workspace folder <path>` (`remove`/`clear`) | Manage workspace folders |
| `/workspace file <path>` (`clear`) | Stage or drop files |
| `/workspace clear` | Drop everything (folders + staged files) |
| `/tool <list\|enable\|disable> [name]` | Show or toggle tools |
| `/skills [<name>\|reload\|enable <name>\|disable <name>]` | List, inspect, reload, or toggle skills |
| `/docs [<name>]` | List or render bundled documentation (Tab autocompletes names) |
| `/memory [status\|list <target>\|clear <target>\|save <name>\|load <name>]` | Inspect / wipe stores, inspect any prompt layer (`L0`, `L1`, `L1B`, `L2`, `L3`, `L5`), or snapshot/restore memory across sessions |
| `/research <query>` | Web search + synthesis |
| `/set <key> <value>` / `/get <key>` / `/unset <key>` / `/variables` | Manage session variables |
| `/model [name]` / `/provider <name>` | Show or switch model / provider |
| `/workspace` | Show attached workspace context |
| `/session [list\|load <n>\|new [n]\|delete <n>]` | Session management |
| `/clear` | Clear the terminal screen |
| `/history [clear]` | Show or wipe the conversation history |
| `/continue` | Resume after a blocker / pause |
| `/stats` | Runtime token + cost stats |
| `/quit` (`/q`) | Exit |

## Configuration files

`.mu/hooks.json` — shell-command hooks fired at five lifecycle points (`pre_provider_call`, `post_provider_call`, `pre_tool`, `post_tool`, `on_stop`). Exit 0 = continue; non-zero with `on_failure: short_circuit` at `pre_tool` denies the call. Full reference, env vars, and the Python decorator path in [documentation/hooks.md](documentation/hooks.md).

**Workspace context files** — by default mucli auto-loads any of `AGENTS.md`, `CLAUDE.md`, `MUCLI.md`, and `.mu/CONTEXT.md` from each attached workspace folder, injecting them as LAYER 1 of the system prompt (up to `workspace_context_max_chars`, default `40000` chars at the default `context_token_limit`; scales with it). Customize the list via `/set workspace_context_files <comma-separated>`.

**Session variables** — every knob (memory limits, context budgets, skill mode, Ollama parameters, etc.) is a session variable settable via `/set <key> <value>`. See [documentation/configuration.md](documentation/configuration.md) for the full reference.

## Architecture

```
mu/
  agent/          AgentLoop façade, hooks, parallel dispatch, plan mode,
                  compactor, retry/loop-detection, approval, collation,
                  secret guard, usage tracker
  commands/       Slash-command registry with @command decorator
  feature/        Feature plan engine
  gui/            FastAPI web UI server (SSE chat, inspector, multi-session)
  memory/         TaskMemoryStore + ScratchpadStore (lifecycle, eviction)
  retrieval/      SemanticCodeIndex for L4B context retrieval
  security/       Always-on secret_paths denylist + scrubber
  session/        Session, SessionManager, per-turn context/messages/
                  budgets/tools_glue, history search, tool-result cache
  skills/         Bundled skills (markdown)
  teacher/        Teacher-mode course engine
  tools/          @tool decorator + per-domain handlers
  trace/          Per-run JSONL trace emitter + parser/snapshot (Trace
                  Analyzer dashboard data foundation)
  ui/             RichUI, stream renderer, input handler, subagent UI
  workspace/      FolderContext (sandbox + gitignore)
providers/        Gemini, OpenAI, Ollama, base classes
utils/            Config, runtime metrics, token estimator, citation
                  manager, anti-detection, helpers, logger
```

## Documentation
See `documentation/`:
- [`commands.md`](documentation/commands.md) — every slash command and its arguments
- [`configuration.md`](documentation/configuration.md) — env vars, session variables, config files
- [`skills.md`](documentation/skills.md) — declarative agent extensions
- [`hooks.md`](documentation/hooks.md) — lifecycle hooks (Python decorator + `.mu/hooks.json`)
- [`security.md`](documentation/security.md) — full security model: workspace sandbox, secret filtering, approval flow, plan mode, sub-agent isolation, limitations
- [`security_controls.md`](documentation/security_controls.md) — the always-on secret-path denylist and scrubber controls
- [`tooling_harness_architecture.md`](documentation/tooling_harness_architecture.md) — tool lifecycle
- [`tracing.md`](documentation/tracing.md) — run tracing & the Trace Analyzer dashboard (per-iteration context/drift/compaction/nudge/tool telemetry)
- [`memory_guide.md`](documentation/memory_guide.md) — memory vs scratchpad
- [`session_guide.md`](documentation/session_guide.md) — session memory architecture, history search, context-preservation (compaction floors, oversized-message handling)
- [`harness-investigation.md`](documentation/harness-investigation.md) — context-management roadmap (R1–R12) with implementation status

Agent modes (one doc per mode):
- [`default_mode.md`](documentation/default_mode.md) — general coding workflow
- [`debug_mode.md`](documentation/debug_mode.md) — root-cause debugging
- [`feature_plan_engine.md`](documentation/feature_plan_engine.md) — feature mode (staged plan engine)
- [`research_mode.md`](documentation/research_mode.md) — research and explanation
- [`loop_mode.md`](documentation/loop_mode.md) — long-horizon autonomous loop
- [`security_mode.md`](documentation/security_mode.md) — verified-PoC security audit
- [`teacher_mode.md`](documentation/teacher_mode.md) — structured course engine

## Testing
```bash
make test
```
