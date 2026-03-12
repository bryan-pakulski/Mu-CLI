# Mu-CLI CLI

Phase 4 CLI MVP entrypoint:

```bash
python cli/mu_cli.py --help
```

Examples:

```bash
# Create session
python cli/mu_cli.py session-create /path/to/workspace --mode interactive

# Start a job and watch events (includes approval checks + optional user input prompts)
python cli/mu_cli.py job-start <session_id> "implement feature" --watch

# Submit user input to a job
python cli/mu_cli.py job-input <job_id> "continue with option B"

# Review/decide approvals
python cli/mu_cli.py approvals <session_id> --interactive

# Full interactive coding loop (create session + start job + live interaction)
python cli/mu_cli.py loop /path/to/workspace "build API endpoint" --tool shell.exec
```
