# Mu-CLI

Mu-CLI is a terminal-first coding assistant for local software projects.

## Requirements
- Python 3.10+
- `patch` command available on your system
- At least one provider key or local model setup:
  - `OPENAI_API_KEY` for OpenAI
  - `GEMINI_API_KEY` for Gemini
  - running Ollama instance for local models

## Installation
```bash
git clone https://github.com/your-repo/Mu-CLI.git
cd Mu-CLI
pip install -r requirements.txt
```

## Usage
Start CLI mode:
```bash
python mucli.py
```

Start server mode:
```bash
python mucli.py --server --provider openai --model gpt-4o-mini --workspace .
```

Start terminal GUI mode:
```bash
python mucli.py --gui
```

## Key features and functionality
- Multi-provider model support (OpenAI, Gemini, Ollama)
- Agentic tool use for file edits, shell commands, and workspace operations
- Workspace context from attached folders and files
- Session save/load and multi-session workflows
- Feature mode with persistent plans under `documentation/feature_req_<id>/`
- Research and debug strategies
- Runtime stats and memory/scratchpad support
- HTTP server API for external integrations

## Common commands
- `/help` show commands
- `/agentic` toggle tool-calling mode
- `/mode <default|debug|feature|research|git>` switch strategy
- `/feature <subcommand>` manage feature workflows
- `/folder <path>` attach workspace folder
- `/file <path>` attach file
- `/tool list` show available tools
- `/memory status` inspect memory usage
- `/stats` show runtime stats
- `/quit` exit

## Notes
- Feature and architecture docs live in the `documentation/` folder.
- In feature mode, plan state is stored in `feature_plan.json` and phase/task artifacts are tracked per feature directory.
