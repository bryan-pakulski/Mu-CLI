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
python mucli.py --yolo                           # auto-approve writes
```

## Key features
- Multi-provider model support (OpenAI, Gemini, Ollama) with real streaming, parallel tool execution, prompt caching, reasoning effort, and cached/reasoning token telemetry.
- Workspace context from attached folders and files.
- Session save/load and multi-session workflows.
- Feature mode with persistent plans under `documentation/feature_req_<id>/`.
- Plan mode (`/plan`) — read-only tool enforcement.
- Hooks (`.mu/hooks.json`) — shell-cmd hooks at five lifecycle points.
- MCP support (`.mu/mcp.json`) — discover and register tools from any MCP server.
- TodoWrite-style task tracking (`todo_write`, `todo_set_status`, `todo_list` tools).
- Sub-agent spawning (`spawn_agent`) — isolated child sessions for focused side quests, depth-capped, plan-mode-aware.
- Runtime stats with token + cache + reasoning + cost accounting.

## Common slash commands
- `/help` — list commands
- `/plan [on|off|toggle]` — toggle read-only plan mode
- `/agentic` — toggle tool-calling mode
- `/thinking` — toggle reasoning / extended thinking
- `/yolo` — toggle auto-approval for write-side tools
- `/mode <default|debug|feature|research|git>` — switch strategy
- `/feature <subcommand>` — manage feature workflows
- `/folder <path>` — attach workspace folder
- `/file <path>` — attach file
- `/tool list` — show available tools
- `/memory status` — inspect memory usage
- `/stats` — runtime stats
- `/quit` — exit

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
Each server's tools register as `mcp__<server>__<tool>` in the unified tool registry.

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
- `tooling_harness_architecture.md` — tool lifecycle
- `feature_plan_engine.md` — feature mode
- `loop_mode.md` — autonomous loop mode
- `memory_guide.md` — memory vs scratchpad

## Testing
```bash
make test
```

Tests cover the core loop, every provider, every research tool, the new `mu/` package (tools registry, command registry, hooks, plan mode, parallel dispatch, history mixin, TodoWrite, hooks config, MCP client + registry, AgentLoop façade), and the feature-mode state machine.
