# Mu-CLI

Terminal-first multi-provider coding assistant.

## Requirements
- Python 3.10+
- `patch` command available on your system
- At least one provider key or local model setup:
  - `OPENAI_API_KEY` for OpenAI
  - `GEMINI_API_KEY` or `GOOGLE_API_KEY` for Gemini
  - running Ollama instance for local models

## Installation
```bash
git clone https://github.com/your-repo/Mu-CLI.git
cd Mu-CLI
pip install -r requirements.txt
```

## Usage
```bash
python mucli.py                                  # interactive REPL
python mucli.py --provider openai --model gpt-4o # specific provider+model
python mucli.py --workspace ./src                # attach a folder at startup
python mucli.py --workspace ./a --workspace ./b  # multiple folders
python mucli.py --session my-session             # load saved session non-interactively
python mucli.py --yolo                           # auto-approve writes
python mucli.py --debug                          # verbose logging
python mucli.py --system "be concise"            # override system instruction
```

## Key features
- Multi-provider model support (OpenAI, Gemini, Ollama) with real streaming, parallel tool execution, prompt caching, reasoning effort, and cached/reasoning token telemetry.
- Workspace context from attached folders and files.
- Session save/load and multi-session workflows.
- Feature mode with persistent plans under `documentation/feature_req_<id>/`.
- Plan mode (`/plan`) — read-only tool enforcement.
- Hooks (`.mu/hooks.json`) — shell-cmd hooks at five lifecycle points.
- MCP support (`.mu/mcp.json`) — discover and register tools from any MCP server. See [documentation/mcp.md](documentation/mcp.md).
- TodoWrite-style task tracking (`todo_write`, `todo_set_status`, `todo_list` tools).
- Sub-agent spawning (`spawn_agent`) — isolated child sessions for focused side quests, depth-capped, plan-mode-aware.
- Skills (`mu/skills/`, `~/.mu/skills/`, `<ws>/.mu/skills/`) — declarative agent extensions with regex triggers; compact index injected by default, bodies auto-load on trigger match or via `invoke_skill`. See [documentation/skills.md](documentation/skills.md).
- Runtime stats with token + cache + reasoning + cost accounting.

## Common slash commands

The most common day-to-day commands — see [documentation/commands.md](documentation/commands.md) for the full reference.

| Command | Description |
| --- | --- |
| `/help` | List commands |
| `/plan [on\|off\|toggle]` | Toggle read-only plan mode |
| `/agentic` | Toggle tool-calling mode |
| `/thinking` | Toggle reasoning / extended thinking |
| `/yolo` | Toggle auto-approval for write-side tools |
| `/mode <default\|debug\|feature\|research\|loop\|security>` | Switch agent strategy |
| `/feature <subcommand>` | Manage feature workflows (`new`, `list`, `load`, `show`, `delete`, ...) |
| `/workspace` | Show attached folders + staged files |
| `/workspace folder <path>` (`remove`/`clear`) | Manage workspace folders |
| `/workspace file <path>` (`clear`) | Stage or drop files |
| `/workspace clear` | Drop everything (folders + staged files) |
| `/tool <list\|enable\|disable> [name]` | Show or toggle tools |
| `/mcp [list\|status\|reload\|debug <server>]` | Manage MCP servers |
| `/skills [<name>\|reload\|enable <name>\|disable <name>]` | List, inspect, reload, or toggle skills |
| `/docs [<name>]` | List or render bundled documentation (Tab autocompletes names) |
| `/memory [status\|list <target>\|clear <target>]` | Inspect / wipe stores and inspect any of the 7 prompt layers (L1–L5) |
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

`.mu/hooks.json` — shell-command hooks fired at lifecycle points:
```json
{
  "hooks": [
    {
      "name": "log-tool-calls",
      "point": "post_tool",
      "command": "echo $MU_TOOL_NAME >> /tmp/tools.log"
    },
    {
      "name": "deny-rm-rf",
      "point": "pre_tool",
      "command": "case \"$MU_TOOL_ARGS_JSON\" in *rm\\ -rf*) exit 1;; *) exit 0;; esac",
      "on_failure": "short_circuit",
      "message": "rm -rf detected; refuse"
    }
  ]
}
```
Valid `point` values: `pre_provider_call`, `post_provider_call`, `pre_tool`, `post_tool`, `on_stop`. Exit 0 = continue; non-zero with `on_failure: short_circuit` at `pre_tool` denies the call.

`.mu/mcp.json` — Model Context Protocol servers to auto-start:
```json
{
  "servers": {
    "fs":  {"command": ["npx", "@modelcontextprotocol/server-filesystem", "/workspace"]},
    "git": {"command": ["mcp-server-git"], "env": {"GIT_REPO": "/workspace"}}
  }
}
```
Each server's tools register as `mcp__<server>__<tool>` in the unified tool registry — e.g. `mcp__fs__read_file`, `mcp__git__log`. They show up alongside built-in tools in `/tool list`.

**Workspace context files** — by default mucli auto-loads any of `AGENTS.md`, `CLAUDE.md`, `MUCLI.md`, and `.mu/CONTEXT.md` from each attached workspace folder, injecting them as LAYER 1 of the system prompt (up to `workspace_context_max_chars`, default 8192). Customize the list via `/set workspace_context_files <comma-separated>`.

**Session variables** — every knob (memory limits, context budgets, skill mode, Ollama parameters, etc.) is a session variable settable via `/set <key> <value>`. See [documentation/configuration.md](documentation/configuration.md) for the full reference.

## Architecture

```
mu/
  agent/          AgentLoop façade, hooks, parallel dispatch, plan mode, compactor
  commands/       Slash-command registry with @command decorator
  mcp/            MCP stdio client + auto-registry
  session/        HistoryMixin (token-budget rolling, summarization)
  tools/          @tool decorator + legacy bridge (61 native + N MCP tools)
  ui/             Stream renderer

providers/        Gemini, OpenAI, Ollama — all streaming, all cache-aware
core/             Session class (loop body) — migration in progress
utils/            Config, metrics, citation manager, logger
```

## Documentation
See `documentation/`:
- [`commands.md`](documentation/commands.md) — every slash command and its arguments
- [`configuration.md`](documentation/configuration.md) — env vars, session variables, config files
- [`skills.md`](documentation/skills.md) — declarative agent extensions
- [`mcp.md`](documentation/mcp.md) — Model Context Protocol setup, auth, and management
- [`tooling_harness_architecture.md`](documentation/tooling_harness_architecture.md) — tool lifecycle
- [`memory_guide.md`](documentation/memory_guide.md) — memory vs scratchpad

Agent modes (one doc per mode):
- [`default_mode.md`](documentation/default_mode.md) — general coding workflow
- [`debug_mode.md`](documentation/debug_mode.md) — root-cause debugging
- [`feature_plan_engine.md`](documentation/feature_plan_engine.md) — feature mode (staged plan engine)
- [`research_mode.md`](documentation/research_mode.md) — research and explanation
- [`loop_mode.md`](documentation/loop_mode.md) — long-horizon autonomous loop
- [`security_mode.md`](documentation/security_mode.md) — verified-PoC security audit

## Testing
```bash
make test
```

Tests cover the core loop, every provider, every research tool, the new `mu/` package (tools registry, command registry, hooks, plan mode, parallel dispatch, history mixin, TodoWrite, hooks config, MCP client + registry, AgentLoop façade), and the feature-mode state machine.
