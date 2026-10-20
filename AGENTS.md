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

The harness is lean by design: it validates the product's core requirements, not every past
regression (`documentation/testing/harness.md` lists the pillars, their requirements and the
files that prove them).

- Run the complete suite from the repository root (Docker is required; see below):

```bash
python3 -m pytest tests/ -q
```

- Tests live in pillar folders: `tests/agent`, `context`, `sessions`, `tools`, `modes`,
  `containers`, `jobs`, `orchestration`, `gui` (+ `gui/frontend`, node tests), `design`,
  `terminal`, `trace`. Shared helpers are in `tests/helpers/` (on `sys.path` via
  `tests/conftest.py`). `tests/` has no `__init__.py`; keep test file names unique.
- A test exercises behaviour. Never assert text in source files (Python, JS, CSS, HTML,
  templates, Markdown, prompt copy); the only exceptions are the rule-based lints listed in the
  harness doc (generated tokens fresh, CSS colour/variable rules, Rich markup escaping, node
  agent stdlib-only, orchestration schema parity, publish tokens never in sandbox code).
- Docker is mandatory: the Docker suites never skip; without a reachable daemon they fail.
  `tests/helpers/docker_helpers.py` builds a missing or stale `mucli-job-runtime` image from the
  tree. Only host features (an NVIDIA GPU, a running Sysbox) may skip.
- Old job stores are not supported: no migrations, no backfills, and no tests for them.
- New behaviour gets a behavioural test in its pillar folder; a test whose behaviour is no
  longer specified is deleted, not kept as a pin. Never weaken or skip a test to make a change
  pass; fix product code first.
- Use focused paths while iterating (`python3 -m pytest tests/<pillar> -q`), then run the
  complete suite.

## Repository layout

- `mu/agent/` — agent loop, approvals, hooks, compaction, retries, parallelism.
- `mu/session/` — sessions, history, layered context, budgets, and tool cache.
- `mu/gui/` — FastAPI web application, SSE event bus, routes, and static UI.
- `mu/container/` — Docker worker, lifecycle, networking, and container TUI.
- `mu/tools/` — tool registry, dispatch, policies, and domain handlers.
- `mu/ui/` — terminal input, rendering, selection, and streaming UI.
- `mu/trace/` — JSONL trace emission, parsing, and snapshots.
- `mu/jobs/` — job board, workers, review, verification, and receipts.
- `mu/orchestration/` — fleet orchestration master: catalog, runs, signing,
  access policy, node endpoint; `node/` is the node agent and must import only
  the Python standard library (it ships as a zipapp; a test enforces this).
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
  `session.history` is never rewritten. Provider replay blobs (`thought_signature`:
  thinking signatures, encrypted reasoning) are excluded from every token estimate
  and dropped from older turns in the request projection (`providers.base.project_replay`). `compactor._pressure_clear` runs even with
  `auto_compaction_enabled=False`. With `prompt_prefix_stable` the system prompt is
  byte-stable per turn; volatile blocks ride in the trailing RUNTIME STATE message
  (never persisted). Every history shrink records a ledger kind (`turn_fold`,
  `turn_roll`, `pressure_clear`, `pressure_forced`, ...).
- UI follows the Wash design system (`documentation/design/wash_design_guide.md`)
  composed per Mu (`documentation/design/mu_style_guide.md`, which wins where they
  differ): no top bar; the rail beside one glass leaf; a page header (title, state
  sentence, text tabs, one primary action; `WashHead.set` on hash routes); rows,
  not cards; every fact in one place; details in `WashSheet` or a disclosure.
  Mu components are in `static/css/wash/components/mu.css`.
  Colours, type, spacing and the job/status vocabulary come only from
  `mu/design/tokens.json` and `vocabulary.json`; after editing them run
  `python3 scripts/design/build_tokens.py` (web CSS/JS + Android TS are
  generated; tests fail when stale). Web uses `.w-*` components and
  `--wash-*` variables inside `static/css/wash/` cascade layers; Android uses
  `mobile/android/src/ui`; the terminal uses `mu/ui/theme` (`get_console()`,
  `wash.*` Rich styles). Escape non-literal values with `mu.ui.markup.esc`
  before putting them in Rich markup.
- Do not bypass approval gates for mutations. Write-side tools require approval
  unless the user explicitly enables YOLO mode; plan mode remains read-only.
  Jobs (`mu/jobs`) run unattended by user decision (2026-10-15): stage tool
  calls are never prompted. Implement/checks auto-approve inside the sandbox;
  plan/review are read-only and deny writes automatically; agent questions are
  auto-answered. Container isolation, egress allowlists, the secret scan and the
  plan/push pipeline gates are the controls. When checks can't run, an
  unattended repair stage may install dependencies in a repair container (wider
  package-registry egress, root inside the container with dropped
  capabilities); its result is committed as a job-scoped image. Interactive
  chat keeps its approval gates.
- Keep tool capability, bounds, secret-path, and result-scrubbing checks intact.
- Orchestration (`documentation/orchestration.md`): every mutation goes through
  `mu/orchestration/commands.dispatch`; the AI (`ai:*` actors) is limited to
  `AI_COMMANDS` and can never publish, approve, dispatch, discard, or touch policy
  or secrets. `orch_*` tools exist only in `orchestration` sessions; the read-only
  `fleet_overview` / `fleet_results` tools (`mu/orchestration/fleet_tools.py`) are available in every
  interactive session once orchestration exists on the machine, and never mutate or create it. Nodes verify
  signatures, parameters and their local policy before running anything. Tests of
  drafts (`test`, `runbook_test`) are decided by `run: test` access rules (default
  confirm), but canary-only targeting, test-key signing, the digest freeze and
  typed confirmation for destructive effects are enforced in `runs`/`hub`, never by
  policy. Runbook validation lives in `runbook.analyze` (structured problems with
  field paths); the web pickers' `schema.js` mirrors `node/schema.py` (a test
  compares them).
- Runtime state defaults to `~/.mucli/`; `MUCLI_HOME` overrides that root.
- Saved sessions are under `~/.mucli/sessions/`.
- Per-run trace files are JSONL under `~/.mucli/trace/`.
- The cross-session Memory Ledger is `~/.mucli/memory/memory.db`.

## Commits and pull requests

- Run the full test suite successfully before committing.
- Describe user-visible behavior changes and the verification performed.
- Preserve approval, security, persistence, and context-budget invariants.
