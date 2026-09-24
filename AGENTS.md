# AGENTS.md

## Project overview

Mucli is a Python CLI harness for agentic AI workflows. It supports OpenAI,
Gemini, Anthropic, and Ollama providers; assembles hierarchical context layers L0-L5;
offers a FastAPI browser GUI, container-backed sessions, and an Android mobile
client that connects to the GUI's SSE and REST interfaces.

## Requirements and setup

- Use Python 3.10 or newer.
- The system `patch` command is required.
- Install Python dependencies from the repository root:

```bash
python3 -m pip install -r requirements.txt
```

- Configure at least one provider: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` or
  `GOOGLE_API_KEY`, or a running Ollama instance via `OLLAMA_HOST`.

## Running Mucli

- Start the terminal CLI from the repository root:

```bash
python3 mucli.py
```

- Start the browser GUI, which listens on `127.0.0.1:30311` by default:

```bash
python3 mucli.py --gui
```

- Stop the detached GUI with `python3 mucli.py --gui-stop`.
- Use `--workspace PATH` to attach a workspace and `--session NAME` to bypass
  interactive session selection.

## Testing

- Run the complete Python suite from the repository root:

```bash
python3 -m pytest tests/ -q
```

- The full suite is approximately 2,400 tests and normally takes about 30s.
- `tests/` intentionally has no `__init__.py`; do not add one.
- Never weaken, skip, or delete tests to make a change pass.
- Fix product code first; change a test only when the specified behavior changes.
- Use focused test paths while iterating, then run the complete suite.

## Repository layout

- `mu/agent/` — agent loop, approvals, hooks, compaction, retries, parallelism.
- `mu/session/` — sessions, history, layered context, budgets, and tool cache.
- `mu/gui/` — FastAPI web application, SSE event bus, routes, and static UI.
- `mu/container/` — Docker worker, lifecycle, networking, and container TUI.
- `mu/tools/` — tool registry, dispatch, policies, and domain handlers.
- `mu/ui/` — terminal input, rendering, selection, and streaming UI.
- `mu/trace/` — JSONL trace emission, parsing, and snapshots.
- `mu/jobs/` — job board, workers, review, verification, and receipts.
- `providers/` — OpenAI, Gemini, Anthropic, Ollama, and provider base classes.
- `utils/` — configuration, logging, metrics, token estimation, and helpers.
- `tests/` — Python unit, integration, GUI, container, and regression tests.
- `mobile/android/` — React Native Android client, assets, scripts, and tests.

## Engineering conventions

- Preserve the L0-L5 context contract and its token/character budgets.
- L0 is the base system layer; L1A holds workspace context files and L1B skills.
- L2 is the conversation summary; L3 carries active goals and memory.
- L4/L4B carry recent tool activity and retrieved context; L5 is live history.
- Non-L5 layers consume the global context cap before the L5 budget is derived.
- Context packaging (`mu/session/budgets.py`, `context_maintenance.py`,
  `runtime_state.py`, `mu/agent/compactor.py`): every pressure gate compares
  the cl100k estimate against `drift_corrected_context_limit` (learned real/cl100k
  drift applies to all providers); `budgets.effective_fill` is the real-frame view —
  never scale both sides. At turn end `loop_body._fold_turn_context` folds the
  finished turn's tool results into the durable result store, then rolls the turn
  into L2 when it exceeds `turn_keep_budget_tokens`. Answered tool-call args above
  `tool_call_arg_stub_threshold_chars` are stubbed in the projected request only;
  `session.history` is never rewritten. `compactor._pressure_clear` runs even with
  `auto_compaction_enabled=False`. With `prompt_prefix_stable` the system prompt is
  byte-stable per turn; volatile blocks ride in the trailing RUNTIME STATE message
  (never persisted). Every history shrink records a ledger kind (`turn_fold`,
  `turn_roll`, `pressure_clear`, `pressure_forced`, ...).
- Do not bypass approval gates for mutations. Write-side tools require approval
  unless the user explicitly enables YOLO mode; plan mode remains read-only.
- Keep tool capability, bounds, secret-path, and result-scrubbing checks intact.
- Runtime state defaults to `~/.mucli/`; `MUCLI_HOME` overrides that root.
- Saved sessions are under `~/.mucli/sessions/`.
- Per-run trace files are JSONL under `~/.mucli/trace/`.
- The cross-session Memory Ledger is `~/.mucli/memory/memory.db`.

## Commits and pull requests

- Run the full test suite successfully before committing.
- Describe user-visible behavior changes and the verification performed.
- Preserve approval, security, persistence, and context-budget invariants.
