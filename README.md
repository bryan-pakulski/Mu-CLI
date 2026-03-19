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
- **Live Memory HUD**: A fixed right-hand terminal sidebar visualizes context, durable memory, scratchpad usage, and the collation queue while a live runtime feed updates in place beside it during agentic loops.
- **Customizable Strategies**: Specialized agent modes for Debugging, Feature Implementation, and Research.
- **YOLO Mode**: Optional hands-free execution for trusted environments (removes manual tool approval).

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

### Getting Started
1. **Select a Provider**: Choose between Gemini, OpenAI, or Ollama.
2. **Select a Model**: Pick from the available models for that provider.
3. **Set Workspace**: Use `/folder .` to add your current directory to the agent's context.
4. **Enable Agentic Mode**: Type `/agentic` to allow the model to use tools.
5. **Chat**: Ask questions like "How does the authentication logic work?" or "Fix the bug in the user controller."
6. **Watch the HUD**: Keep an eye on the fixed right-hand Memory HUD for context growth, memory slots, scratchpad activity, and pending collated tool output while the runtime feed updates beside it.

## Command Overview

| Command | Alias | Description |
| :--- | :--- | :--- |
| `/help` | `/h` | Show the help menu |
| `/agentic` | | Toggle Agentic (Tool Calling) mode |
| `/folder <path>` | `/dir` | Monitor a folder for context |
| `/file <path>` | `/f` | Attach a specific file as context |
| `/mode <mode>` | | Change strategy: `default`, `debug`, `feature`, `research`, `git` |
| `/yolo` | | Toggle YOLO mode (no tool approvals) |
| `/tool <cmd>` | `/tools` | Manage tools: `enable`, `disable`, `list` |
| `/list` | `/ls` | List saved conversations |
| `/load [name]` | `/open` | Load a conversation session |
| `/new [name]` | | Start a fresh session |
| `/model [name]` | | Show or change the current model |
| `/provider [name]`| | Change the LLM provider |
| `/flush` | | Flush the collation buffer into the next model turn |
| `/tokens` | | Show current context token usage and estimated cost |
| `/view` | `/v` | View full conversation history |
| `/clear` | `/c` | Clear conversation history |
| `/clearfiles` | `/cf` | Clear all staged files |
| `/system <txt>` | `/sys` | Update or view the system prompt |
| `/thinking` | | Toggle thinking/reasoning mode (for supported models) |
| `/variables` | | Show all session variables |
| `/set <k> <v>` | | Set a session variable (e.g., `ollama_host`) |
| `/get [key]` | | Get the value of a session variable |
| `/unset [key]` | | Reset a variable to default (or `--all`) |
| `/splash` | | Show the welcome splash screen |
| `/quit` | `/q` | Exit the application |

## Agent Modes

μCLI supports different agent modes (strategies) to tailor the AI's behavior to specific tasks. Use `/mode <mode>` to switch.

- **`default`**: Standard programming assistant. Best for general questions and small code changes.
- **`debug`**: Optimized for bug hunting. The agent focuses on error logs, searching for root causes, and providing precise fixes.
- **`feature`**: Designed for implementing new features. The agent creates an architecture plan (`FEATURE_<name>.md`) before writing any code.
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
- `batch_job`: Executes multiple of the above tools in a single turn.
- `get_current_time`: Provides the current system time in ISO format.
- `save_memory`, `search_memory`, `list_memory`: Persist and retrieve concise high-value notes during an agentic task.
- `save_scratchpad`, `search_scratchpad`, `list_scratchpad`, `clear_scratchpad`: Manage turn-local notes separately from durable memory.
- Structured tool result summaries are attached during agentic execution so the model can reason over smaller schemas instead of large raw blobs.
- **Git Suite**: `git_init`, `git_status`, `git_log`, `git_diff`, `git_checkout`, `git_add`, `git_commit`, `git_push`, `git_pull`, `git_branch`, `git_merge_request`.

## Additional Notes

- **Configuration**: Local settings and session history are stored in `~/.mucli_chats/`.
- **Safety**: By default, any tool that modifies your filesystem (`write_file`, `apply_diff`, `run_agent_task`) requires manual `[y/n]` approval unless `/yolo` is enabled.
- **Makefile.agents**: You can define custom automation tasks in a file named `Makefile.agents` in your workspace root, and the AI will be able to discover and run them.
