# μCLI (Mu-CLI)

μCLI is an interactive, autonomous AI terminal interface designed for software development. It enables seamless collaboration with Large Language Models (LLMs) directly within your terminal, providing them with the ability to "see" and "interact" with your local codebase.

## Features

- **Multi-Provider Support**: Switch between Google Gemini, OpenAI, and local Ollama models.
- **Agentic Mode**: Enable tool-calling capabilities that allow the AI to read files, search code, apply diffs, and run terminal tasks autonomously.
- **Workspace Context**: Monitor entire directories or specific files. The agent maintains a "workspace map" to understand your project structure.
- **Session Management**: Save, load, and manage multiple conversation histories.
- **Smart Context Management**: Automatic history truncation and summarization to stay within model context limits while keeping costs down.
- **In-Task Memory Engine**: Agent mode can store and recall concise facts so it does not need to replay large tool outputs.
- **Turn Scratchpads**: Agent mode can keep temporary notes for the active task loop without polluting durable memory.
- **Runtime Stats & Live Metrics**: Use `/stats` for a centered snapshot panel, and watch the generating status line for compact context/memory/scratch/queue utilization bars during agent loops.
- **Customizable Strategies**: Specialized agent modes for Debugging, phased Feature Implementation, and Research.
- **Feature Plan Engine**: Feature mode can persist phased plans in `documentation/feature_req_<id>/`, pause on structured blockers, and drive implementation/review loops from those plan files.
- **Resumable Feature Loops**: The latest feature-loop state is also stored in the session JSON so a blocked or interrupted feature run can be reloaded and continued later.
- **YOLO Mode**: Optional hands-free execution for trusted environments (removes manual tool approval).
- **Server Mode for Integrations**: Launch μCLI with an HTTP API so external clients can drive sessions, commands, and tool execution.
- **Terminal GUI (`--gui`)**: Launch a full-screen TUI with mode tabs and a multi-session navigator. Feature mode includes a kanban-style board and interactive session switching.

## Installation

### Prerequisites
- Python 3.10+
- `patch` utility (usually pre-installed on Linux/macOS, available via Git Bash or Chocolatey on Windows).

### Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/your-repo/Mu-CLI.git
   cd Mu-CLI
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your API keys:
   ```bash
   export GEMINI_API_KEY='your-key-here'
   export OPENAI_API_KEY='your-key-here'
   ```

## Usage Guide

Run the tool using:
```bash
python mucli.py
```

Run the server/API mode using:
```bash
python mucli.py --server --provider openai --model gpt-4o-mini --workspace .
```

Run the terminal GUI mode using:
```bash
python mucli.py --gui
```
Inside GUI mode: use `↑/↓` (`j/k`) to select sessions, `Enter` to open/pin a session board, `Tab` to switch focus between the session list and board, and while on the board press `Enter` to open full card details (`j/k` scroll, `b`/`Esc` back). Press `H` to open the history browser (feature events, all features including archived, and recent chat) and `q` to exit back to CLI.

### Getting Started
1. **Select a Provider**: Choose between Gemini, OpenAI, or Ollama.
2. **Select a Model**: Pick from the available models for that provider.
3. **Set Workspace**: Use `/folder .` to add your current directory to the agent's context.
4. **Enable Agentic Mode**: Type `/agentic` to allow the model to use tools.
5. **Chat**: Ask questions like "How does the authentication logic work?" or "Fix the bug in the user controller."
6. **Watch Runtime Metrics**: Use `/stats` for a snapshot panel and check the `Generating ...` status line for live context, memory, scratchpad, and queue usage.

On startup, μCLI checks for newer GitHub releases (when `origin` points to GitHub) and can prompt you to run `/update` automatically.

## Command Overview

| Command | Alias | Description |
| :--- | :--- | :--- |
| `/help` | `/h` | Show the help menu |
| `/agentic` | | Toggle Agentic (Tool Calling) mode |
| `/folder <path>` | `/dir` | Monitor a folder for context |
| `/file <path>` | `/f` | Attach a specific file as context |
| `/mode <mode>` | | Change strategy: `default`, `debug`, `feature`, `research`, `git` |
| `/feature <cmd>` | `/features` | Manage feature flows (`list`, `new`, `load`, `status`, `phases`, `exit`, `create`, `show`, `move`, `block`, `review`, `archive`, `monitor`, `help`) |
| `/yolo` | | Toggle YOLO mode (no tool approvals) |
| `/tool <cmd>` | `/tools` | Manage tools: `enable`, `disable`, `list` |
| `/list` | `/ls` | List saved conversations |
| `/memory <cmd>` | | View and manage memory: `status`, `list`, `clear scratch|task|all` |
| `/load [name]` | `/open` | Load a conversation session |
| `/new [name]` | | Start a fresh session |
| `/model [name]` | | Show or change the current model |
| `/provider [name]`| | Change the LLM provider |
| `/update` | | Pull latest changes and refresh dependencies |
| `/flush` | | Flush the collation buffer into the next model turn |
| `/stats` | | Show runtime stats, token/cost totals, and current feature progress |
| `/view` | `/v` | View full conversation history |
| `/clear` | `/c` | Clear conversation history |
| `/clearfiles` | `/cf` | Clear all staged files |
| `/clear-workspace` | `/cw` | Clear all workspace folders |
| `/workspace [clear]` | | View workspace metadata or clear all workspace folders |
| `/system <txt>` | `/sys` | Update or view the system prompt |
| `/thinking` | | Toggle thinking/reasoning mode (for supported models) |
| `/variables` | | Show all session variables |
| `/set <k> <v>` | | Set a session variable (e.g., `ollama_host`) |
| `/get [key]` | | Get the value of a session variable |
| `/unset [key]` | | Reset a variable to default (or `--all`) |
| `/splash` | | Show the welcome splash screen |
| `/quit` | `/q` | Exit the application |

Loop detection is configurable via session variables:
- `/set loop_detection_enabled true|false`
- `/set loop_detection_repeat_threshold <int>`

## Server Mode

μCLI can run as a lightweight HTTP server so API clients can interact with the same session, command, and tooling primitives used by the terminal UI.

### Starting the server

```bash
python mucli.py \
  --server \
  --host 127.0.0.1 \
  --port 8765 \
  --provider openai \
  --model gpt-4o-mini \
  --workspace .
```

Notes:

- Use `--session <name>` to reuse an existing saved session instead of the default session.
- Use `--workspace <path>` multiple times to preload one or more folders into context.
- Use `--yolo` if you want server-driven tool calls to auto-approve modifying tools for trusted local integrations.

### API endpoints

For a higher-level assessment of the server stack, see `documentation/server_architecture_review.md`.
For the phased feature workflow and plan file format, see `documentation/feature_plan_engine.md`.

### Feature CLI loop quickstart

```bash
/mode feature
/feature create plan "My Feature"
/feature create phase "Build Core" | "Implement core workflow"
/feature create task 1 | "Add API" | "Create endpoint and validation" | "Endpoint returns 200;Tests pass"
/feature show execution
/feature move 1 in_progress
/feature block 1 "Need product decision"
/feature review auto
/feature monitor 1
```

- `GET /health` — basic health check.
- `GET /api/state` — current session state, active model, variables, workspaces, and tool metadata.
- `GET /api/tools` — available tool definitions, descriptor metadata (`execution_kind`, `preview_policy`, `server_policy`, `result_mode`, etc.), and whether each tool is enabled.
- `GET /api/history?limit=50` — serialized conversation history.
- `GET /api/sessions` — list saved sessions and the currently loaded session.
- `POST /api/sessions/new` — create a new session using the current provider/model.
- `POST /api/sessions/load` — switch to another saved session.
- `POST /api/sessions/delete` — delete a saved session.
- `POST /api/sessions/rename` — rename an existing saved session.
- `GET /api/events` — subscribe to live server-sent events for task, approval, command, tool, runtime, workspace, staged-file, stream lifecycle, and live trace updates (`trace.info`, `trace.tool`, `trace.tool_result`, etc.).
- `GET /api/tasks` / `GET /api/tasks/<task_id>` — inspect async message task state.
- `GET /api/approvals` / `GET /api/approvals/<approval_id>` — inspect pending approval requests for modifying tools.
- `POST /api/approvals/resolve` — approve, reject, or explain a pending modifying tool request.
- `GET /api/runtime` — inspect the current runtime state (`thinking`, `agentic`, model, variables, disabled tools, system prompt).
- `POST /api/runtime` — update runtime state such as system prompt, model, booleans, disabled tools, and variables.
- `GET /api/workspaces` — inspect attached workspace folders and tracked files.
- `POST /api/workspaces/add` — attach a workspace folder.
- `POST /api/workspaces/remove` — detach a workspace folder.
- `GET /api/feature-plan?directory=<path>` — inspect a persisted feature-plan summary refreshed from `feature_plan.json` and `phase_N.md` files.
- `GET /api/staged-files` — inspect currently staged files for the next turn.
- `POST /api/staged-files/add` — stage a file by path for the next turn.
- `POST /api/staged-files/clear` — clear staged files.
- `POST /api/feature-plan/approve` — update feature-plan approval and review metadata.
- `POST /api/feature-loop` — run the approved phase-by-phase implementation/review loop on the server.
- `POST /api/feature-loop/resolve` — resume a paused feature loop after the user supplies blocker context.
- `POST /api/message` — send a normal chat turn to the active session.
- `POST /api/command` — execute a slash command such as `/folder .`, `/set yolo true`, or `/tool list`.
- `POST /api/tool` — invoke a tool directly with JSON arguments for integration workflows that want structured tool access. Structured tool responses include envelope fields such as `error`, `error_code`, `modified_files`, `artifacts`, and `telemetry`.

For provider switching, you can still use `POST /api/command` with a slash command such as `{"command":"/provider ollama"}`.

### Example requests

Send a chat message:

```bash
curl -X POST http://127.0.0.1:8765/api/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"Summarize the current workspace."}'
```

Send an async chat message for async client workflows:

```bash
curl -X POST http://127.0.0.1:8765/api/message \
  -H 'Content-Type: application/json' \
  -d '{"text":"Make the requested file change.","async":true}'
```

Subscribe to live task/approval events:

```bash
curl -N http://127.0.0.1:8765/api/events
```

Run a slash command:

```bash
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"/folder ."}'
```

Update runtime state:

```bash
curl -X POST http://127.0.0.1:8765/api/runtime \
  -H 'Content-Type: application/json' \
  -d '{"thinking":true,"agentic":true,"variables":{"yolo":true}}'
```

Resolve a pending approval:

```bash
curl -X POST http://127.0.0.1:8765/api/approvals/resolve \
  -H 'Content-Type: application/json' \
  -d '{"approval_id":"<approval_id>","decision":"approve"}'
```

Execute a tool directly:

```bash
curl -X POST http://127.0.0.1:8765/api/tool \
  -H 'Content-Type: application/json' \
  -d '{"tool_name":"list_dir","tool_args":{"path":"."}}'
```

## Agent Modes

μCLI supports different agent modes (strategies) to tailor the AI's behavior to specific tasks. Use `/mode <mode>` to switch.

- **`default`**: Standard programming assistant. Best for general questions and small code changes.
- **`debug`**: Optimized for bug hunting. The agent focuses on error logs, searching for root causes, and providing precise fixes.
- **`feature`**: Uses the feature-plan engine. The agent must create `documentation/feature_req_<id>/feature_plan.json` plus `phase_N.md` files, wait for approval, implement one phase at a time, raise blockers when user input is needed, and complete a review pass before finishing.
- **`research`**: Focuses on codebase exploration and documentation. The agent traverses function calls and imports to explain how systems work.
- **`git`**: **Autonomous Software Engineer Mode.** The agent works through a structured workflow: Requirements -> Implementation Plan -> Verification Plan -> Implementation (in a new git branch) -> Verification -> Self-Review -> Merge Request. It creates and follows its own documentation and ensures task completion before finalizing.

## Tools Overview

When in **Agentic Mode**, the AI can invoke the following tools:

- `get_workspace_details`: Provides the AI with the file structure of your attached folders.
- `read_file`: Reads the full content of a specific file.
- `get_chunk`: Reads a specific line range of a file (useful for large files).
- `search_for_string`: Grep-like search across the workspace.
- `list_dir`: Lists files and subdirectories.
- `write_file`: Creates or overwrites a file.
- `apply_diff`: Applies unified diffs (patches) to existing files for precise edits.
- `list_agent_tasks`: Discovers automation scripts in `Makefile.agents`.
- `run_agent_task`: Executes tasks defined in a `Makefile.agents` (e.g., running tests).
- `bash`: Executes raw shell commands in the attached workspace.
- `batch_job`: Executes multiple of the above tools in a single turn.
- `get_current_time`: Provides the current system time in ISO format.
- `save_memory`, `search_memory`, `list_memory`: Persist and retrieve concise high-value notes during an agentic task.
- `save_scratchpad`, `search_scratchpad`, `list_scratchpad`, `clear_scratchpad`: Manage turn-local notes separately from durable memory.
- Structured tool result summaries are attached during agentic execution so the model can reason over smaller schemas instead of large raw blobs.
- Structured tool result envelopes now track normalized error state, modified files, artifacts, and telemetry so session and server consumers can share the same result contract.
- **Git Suite**: `git_init`, `git_status`, `git_log`, `git_diff`, `git_checkout`, `git_add`, `git_commit`, `git_push`, `git_pull`, `git_branch`, `git_merge_request`.


## Additional Notes

- **Configuration**: Local settings and session history are stored in `~/.mucli_chats/`.
- **Safety**: By default, any tool that modifies your filesystem (`write_file`, `apply_diff`, `run_agent_task`, `bash`) requires manual `[y/n]` approval unless `/yolo` is enabled.
- **Makefile.agents**: You can define custom automation tasks in a file named `Makefile.agents` in your workspace root, and the AI will be able to discover and run them.
