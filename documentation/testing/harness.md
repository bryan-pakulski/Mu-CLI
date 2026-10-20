# The test harness

mucli's tests validate the product's core requirements. They are grouped by pillar, one folder each, and every test exercises behaviour. This page lists what each pillar must prove and the files that prove it. AGENTS.md has the rules in short.

**Size:** 1,528 tests in 100 files, plus 8 node tests in `tests/gui/frontend/`. A full run takes about 7 minutes: ~6.1 min is the Docker suites, the rest ~67 s.

```bash
python3 -m pytest tests/ -q                 # everything (Docker required)
python3 -m pytest tests/context -q          # one pillar
NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.cjs
```

## Rules

- **Behaviour only.** A test calls the code and checks what it does. Tests that assert text in source files (Python, JS, CSS, HTML, templates, Markdown, prompt copy) are not allowed.
- **Rule-based lints are the only exception**, and only for critical rules:
  - `design/test_wash_tokens.py`: generated web/Android tokens and icons match `tokens.json` / `vocabulary.json`
  - `design/test_wash_css_contract.py`: no colour literal outside `tokens.css`; every `--wash-*` variable used exists
  - `terminal/test_wash_markup_lint.py`: Rich markup f-strings escape dynamic values (injection safety)
  - `orchestration/test_orch_surfaces.py`: the node agent imports only the standard library (it ships as a zipapp)
  - `orchestration/test_orch_web_pickers.py`: the web schema validator agrees with `node/schema.py` (run under node)
  - `jobs/test_job_git_publish.py`: publish tokens never appear in sandbox or harness code
- **Docker is mandatory.** The Docker suites never skip: without a reachable daemon they fail. `tests/helpers/docker_helpers.py` builds `mucli-job-runtime` from this tree when it's missing or stale. Only host features may skip: an NVIDIA GPU, and Sysbox installed *and running*.
- **No old data.** Old job stores aren't supported: there are no migrations, backfills or tests for them.
- **Removing tests.** A test whose behaviour is no longer specified is deleted, not kept as a pin. New behaviour gets a behavioural test in its pillar.
- **Layout.** Pillar folders, one file per subsystem, unique file names (no `__init__.py`). Shared helpers (`job_helpers`, `orch_helpers`, `docker_helpers`) are in `tests/helpers/`.

## Pillars

| Pillar | Folder | Files | Tests | Time |
|---|---|---|---|---|
| Agent loop & providers | `tests/agent/` | 9 | 113 | 2 s |
| Context engineering | `tests/context/` | 13 | 141 | 11 s |
| Sessions & memory | `tests/sessions/` | 8 | 100 | 1 s |
| Tools & safety gates | `tests/tools/` | 11 | 171 | 1 s |
| Modes & commands | `tests/modes/` | 9 | 178 | 1 s |
| Containers | `tests/containers/` | 4 | 31 | 0 s |
| Engineering jobs | `tests/jobs/` | 20 | 396 | 370 s |
| Orchestration | `tests/orchestration/` | 11 | 192 | 36 s |
| Web GUI | `tests/gui/` | 4 | 52 | 3 s |
| Design system | `tests/design/` | 3 | 13 | 1 s |
| Terminal UI | `tests/terminal/` | 5 | 79 | 6 s |
| Tracing & costs | `tests/trace/` | 3 | 62 | 1 s |

### Agent loop & providers — `tests/agent/`

Must prove:

- A turn runs to completion across OpenAI, Anthropic, Gemini and Ollama with tool calls round-tripped
- Streamed output assembles once
- Transient provider errors retry; fatal ones surface
- Subagents spawn, report back and are bounded; parallel dispatch keeps results in order
- Loops and stalls are detected

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_anthropic_provider.py` | 39 | 0.0 s | Anthropic (Claude) provider: request shaping, tool-id pairing, thinking |
| `test_loop_detection.py` | 10 | 0.0 s |  |
| `test_mu_agent_session_integration.py` | 9 | 0.2 s | Integration tests: hooks + plan mode fire from the existing Session loop |
| `test_mu_parallel_dispatch.py` | 8 | 0.5 s | Tests that the session agentic loop dispatches eligible tool calls in |
| `test_mu_spawn_agent.py` | 20 | 0.8 s | Tests for the real `spawn_agent` implementation (async orchestrator) |
| `test_ollama_routes.py` | 11 | 0.0 s | Ollama routes (cloud / daemon→cloud / local): stable num_ctx, think mapping, side calls, |
| `test_provider_retry.py` | 6 | 0.0 s | Pin the exponential-backoff retry around provider calls |
| `test_subagent_history_cards.py` | 5 | 0.0 s | Durable, time-anchored subagent cards for web and mobile history |
| `test_vision_e2e.py` | 5 | 0.0 s | Pin the /file → capability-gated native-media pipeline end-to-end |

### Context engineering — `tests/context/`

Must prove:

- L0–L5 layers assemble in order; non-L5 layers consume the cap before L5's budget
- Pressure gates compare the cl100k estimate to drift_corrected_context_limit
- Turn end folds tool results into the store and rolls big turns into L2
- Answered tool-call args are stubbed in the projection only; history is never rewritten
- Replay blobs are excluded from estimates and dropped from older turns
- pressure_clear runs with auto-compaction off; every shrink records a ledger kind
- prompt_prefix_stable keeps the system prompt byte-stable

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_context_packaging_replay.py` | 5 | 5.4 s | context_packaging_v2 / P5-T11: multi-turn replay regression |
| `test_continuous_compaction.py` | 7 | 3.7 s | Long-running work should retain a working set, not an ever-growing log |
| `test_effective_fill_gates.py` | 8 | 0.0 s | context_packaging_v2 / P1-T3: every pressure gate reasons about the REAL |
| `test_history_shrink_ledger.py` | 5 | 0.0 s | Governance: every history shrink path leaves a compaction record |
| `test_job_no_overflow_stop.py` | 27 | 0.5 s | Job stages never stop dead on a context overflow (documentation/design/ledger/no-overflow-stop.md) |
| `test_pressure_clear.py` | 7 | 0.1 s | context_packaging_v2 / P3-T7: summarizer-free in-turn working-set cap |
| `test_prompt_prefix_stability.py` | 5 | 0.1 s | context_packaging_v2 / P4-T9: byte-stable system prompt across a turn |
| `test_result_store.py` | 13 | 0.0 s | Durable ResultStore tests (spec #1, #6, #11) |
| `test_selective_context.py` | 16 | 0.3 s | Model context edits preserve durable evidence and measure the wire projection |
| `test_token_estimator.py` | 21 | 0.5 s | Pin the token estimator and its integration with /memory layers |
| `test_tool_call_arg_stub.py` | 8 | 0.0 s | context_packaging_v2 / P2-T5: stub oversized tool-call args once consumed |
| `test_turn_fold.py` | 9 | 0.0 s | context_packaging_v2 / P2-T4: turn-boundary fold |
| `test_turn_roll.py` | 10 | 0.0 s | context_packaging_v2 / P2-T6: roll an over-budget finished turn into L2 |

### Sessions & memory — `tests/sessions/`

Must prove:

- Sessions save/load with revision CAS; unreadable docs are never overwritten
- History pages correctly on every surface
- Threads create, coordinate and delete safely
- The Memory Ledger stores and recalls; two surfaces stay consistent

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_durable_memory.py` | 12 | 0.2 s | Cross-session Memory Ledger: persistence, scope, audit and UX contracts |
| `test_gui_session_history.py` | 6 | 0.0 s |  |
| `test_gui_unreadable_session.py` | 5 | 0.0 s | GUI must surface an unreadable session.json, never demote it to workspace |
| `test_mu_tools_memory.py` | 15 | 0.0 s | Pin the migrated memory + scratchpad tools (`mu/tools/memory/`) |
| `test_session_revision.py` | 32 | 0.1 s | Cross-surface continuity phase 1: session revision counter + CAS + If-Match |
| `test_session_unreadable_doc.py` | 4 | 0.0 s | SessionManager must never clobber a session.json it cannot read |
| `test_surface_sync.py` | 13 | 0.7 s | Tests for the CLI-side surface sync watcher (cross-surface phase 2, G1) |
| `test_threads.py` | 13 | 0.1 s |  |

### Tools & safety gates — `tests/tools/`

Must prove:

- Write-side tools need approval unless YOLO; plan mode is read-only
- Secret paths are refused and secrets scrubbed from results and shell output
- Tool bounds and path resolution hold; the shell is non-interactive
- Dispatch envelopes, batch tools and retries behave

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_bash_secret_guard.py` | 27 | 0.0 s | Unit tests for the bash secret-guard pre_tool hook |
| `test_batch_tool.py` | 8 | 0.0 s |  |
| `test_envelope_hints_retry.py` | 21 | 0.0 s | Tests for the hint + retryable envelope extensions and the session-level |
| `test_gui_plan_mode.py` | 7 | 0.0 s | Plan mode is a first-class session variable for the GUI and mobile: listed, |
| `test_harness_layers.py` | 6 | 0.0 s |  |
| `test_mu_agent_plan_mode.py` | 5 | 0.0 s | Tests for `mu.agent.plan_mode` — read-only enforcement |
| `test_mu_tools_registry.py` | 6 | 0.0 s | Tests for the tool registry in `mu.tools` |
| `test_secret_paths.py` | 59 | 0.1 s | Unit tests for the always-on path denylist |
| `test_secret_scrubber.py` | 20 | 0.0 s | Unit tests for the secret-output scrubber |
| `test_shell_noninteractive.py` | 6 | 1.1 s | Unattended shell commands must fail prompts fast and clean up descendants |
| `test_tool_path_resolution.py` | 6 | 0.0 s | Relative tool paths resolve against the session workspace, not process cwd |

### Modes & commands — `tests/modes/`

Must prove:

- Commands are registered, discoverable and listed in help
- The feature/plan engine transitions correctly
- Teacher, security and skills work; citations resolve

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_citation_manager.py` | 25 | 0.0 s | Tests for the CitationManager module |
| `test_command_surface.py` | 5 | 0.0 s | Pin the slash-command surface so cleanup decisions don't drift |
| `test_feature_mode_phase_one.py` | 4 | 0.0 s |  |
| `test_mode_workspace.py` | 9 | 0.0 s | Mode workspace contracts expose evidence without inventing certainty |
| `test_mu_commands_registry.py` | 23 | 0.0 s | Tests for the new slash-command registry in `mu.commands` |
| `test_security_mode.py` | 23 | 0.1 s | Tests for the security audit engine |
| `test_skills.py` | 42 | 0.1 s | Pin the skills discovery + injection system |
| `test_teacher_engine.py` | 35 | 0.1 s | Tests for the teacher-mode engine |
| `test_teacher_grading.py` | 12 | 1.0 s | Tests for teacher-mode assignment grading |

### Containers — `tests/containers/`

Must prove:

- Container sessions create, attach and recover
- Egress is limited to the allowlist
- The shell is loopback-only; lifecycle APIs serve loopback and private networks only

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_container_access_scope.py` | 11 | 0.1 s | Who may control containers from the GUI: the interactive shell is loopback-only (a client on |
| `test_container_commands.py` | 3 | 0.0 s |  |
| `test_container_egress_proxy.py` | 9 | 0.1 s |  |
| `test_container_supervisor_recovery.py` | 8 | 0.0 s |  |

### Engineering jobs — `tests/jobs/`

Must prove:

- The state machine, store and API are consistent; leases recover
- Stages run unattended in the sandbox; plan/review deny writes
- Isolation, egress allowlist and the secret scan hold (Docker suites)
- Checks run, triage, gate dirty trees and keep artifacts
- Push squashes onto base with a verified tree; publish tokens never reach containers
- Every stage fails safe

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_job_build_environments.py` | 32 | 1.0 s | Job build environments (documentation/plans/job_build_environments.md): resolution, selection, |
| `test_job_build_environments_docker.py` | 7 | 207.3 s | Real Docker integration for job build environments (Docker is mandatory for the suite) |
| `test_job_check_artifacts.py` | 19 | 0.3 s | Check run artifacts: everything a suite writes is kept (untracked or ignored, never tracked |
| `test_job_checks_stage.py` | 11 | 0.5 s |  |
| `test_job_core_api.py` | 14 | 0.2 s | Phase 3 exit tests: commands, view models, change stream, jobs_v2 API, import guard |
| `test_job_core_state.py` | 153 | 0.2 s | Core state machine, the one status/core transition table, pipeline, actions |
| `test_job_env_repair_docker.py` | 1 | 77.7 s | Environment repair against real Docker (requires Docker and the locally built |
| `test_job_failure_injection.py` | 9 | 2.8 s | Milestone 6: failure-injection suite |
| `test_job_git_publish.py` | 40 | 4.8 s | W3 git and publish: squash + tree gate, PR re-push, base resolution, fetch, |
| `test_job_harness_docker.py` | 4 | 18.5 s | Claude Code / Codex stages in a real stage container, with fake CLIs speaking the recorded |
| `test_job_harness_sandbox_docker.py` | 3 | 14.2 s | Sandbox permissions of job stage containers for every agent harness (mucli, Claude Code, Codex), |
| `test_job_sandbox.py` | 27 | 0.1 s |  |
| `test_job_sandbox_docker.py` | 2 | 9.7 s | Real isolation smoke tests; require Docker and the locally built shared image |
| `test_job_stage_loop.py` | 9 | 0.1 s | Durable job v2 task 4: plan + review stages and the automated review loop |
| `test_job_stage_resume_docker.py` | 2 | 17.1 s | A stage stopped by its time limit resumes its own conversation (harness latency plan, 5.1) |
| `test_job_templates_docker.py` | 1 | 14.1 s | Real template job setup (documentation/plans/job_templates.md §4): a tiny Node repository, a |
| `test_job_worktree.py` | 8 | 0.5 s |  |
| `test_jobs_core.py` | 13 | 0.1 s |  |
| `test_jobs_unattended.py` | 25 | 0.9 s | Jobs run unattended (user decision 2026-10-15, ``mu.jobs.unattended``) |
| `test_tracker_sync.py` | 16 | 0.1 s | W4 trackers: Tracker protocol, Jira/GitHub adapters, writeback, dedupe, watcher |

### Orchestration — `tests/orchestration/`

Must prove:

- Every mutation goes through commands.dispatch; AI actors are limited to AI_COMMANDS
- Nodes verify signatures, parameters and local policy; the node agent is stdlib-only
- Runs plan, batch, pause and roll back; canary/test-key/digest/typed-confirm are enforced
- Remote policy edits are operator-only; CSRF on every mutating route
- The web schema mirrors node/schema.py

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_fleet_tools.py` | 6 | 0.3 s | Fleet results from every part of μcli: read-only fleet_* tools for any interactive session, |
| `test_orch_container_nodes.py` | 1 | 7.6 s | Disposable container test nodes (plan §6.8): needs Docker (mandatory) and pulls an image |
| `test_orch_crypto.py` | 25 | 0.2 s | Vendored Ed25519 verification and signed envelopes (orchestration plan §6.3) |
| `test_orch_integration.py` | 10 | 14.9 s | Master and real node agents over HTTPS long-poll on loopback (plan §11 integration, P0/P1) |
| `test_orch_maintenance.py` | 11 | 1.0 s | Node maintenance without custom tools: every node reports whether it needs a reboot (agent |
| `test_orch_master.py` | 38 | 6.0 s | Master-side flows with in-process nodes: catalog, planning, approvals, runs, runbooks, |
| `test_orch_node_policy.py` | 72 | 0.1 s | Node-local policy (plan §6.7): parsing (fail closed) and an exhaustive ``decide`` |
| `test_orch_remote_policy.py` | 5 | 0.1 s | Editing a node's policy.yaml from the master: operator only (never the AI), audited, no |
| `test_orch_results.py` | 5 | 0.4 s | Fleet results (mu/orchestration/results.py): a tool's output schema becomes sortable tables |
| `test_orch_surfaces.py` | 16 | 5.4 s | Security regressions and surfaces (plan §11): CSRF, session-type gating, AI tool limits, |
| `test_orch_web_pickers.py` | 3 | 0.1 s | Web pickers (documentation/plans/runbook_editor.md §4.1): the JS schema validator agrees with |

### Web GUI — `tests/gui/`

Must prove:

- The SSE bus delivers and coalesces per session
- Prompts wait indefinitely and Stop cancels them
- The shell serves destinations and embeds
- File panels serve correct data; the browser renders dialogs and restored traces (node tests)

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_gui_files.py` | 32 | 0.3 s | GUI Files panel — workspace explorer + editor backend |
| `test_gui_webui_coalescing.py` | 9 | 0.0 s | GUI WebUI delta coalescing |
| `test_web_persistent_shell.py` | 8 | 1.4 s | The persistent shell (mu/gui/shell.py; Mu revision 2): browsers get one shell document |
| `test_web_prompts_wait.py` | 3 | 0.7 s | Agent questions never time out (user decision, 2026-10-20): when the model hands the turn to |

### Design system — `tests/design/`

Must prove:

- Generated web/Android tokens and icons are current
- No colour literal outside tokens.css; every --wash-* variable exists
- Contrast floors hold

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_wash_css_contract.py` | 2 | 0.0 s | Wash CSS rules that keep the design system intact (AGENTS.md: colours, type and spacing come |
| `test_wash_tokens.py` | 5 | 1.3 s | Wash token contract: schema, contrast floors, and generated outputs in sync |
| `test_wash_vocabulary.py` | 6 | 0.0 s | The shared Wash vocabulary covers every job enum the UIs can display |

### Terminal UI — `tests/terminal/`

Must prove:

- Rich markup escapes dynamic values
- The session picker, editor bridge and jobs TUI work

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_editor.py` | 19 | 0.4 s | Editor integration contracts: context safety, provider isolation, and transport |
| `test_jobs_tui.py` | 37 | 2.1 s | Terminal job dashboard (/jobs): render snapshots, breakpoints, key → command mapping |
| `test_markup_safety.py` | 11 | 0.0 s | Rich markup is only interpreted where our own code wrote it |
| `test_startup_session_picker.py` | 9 | 0.0 s | Startup control-centre flow for terminal users |
| `test_wash_markup_lint.py` | 3 | 3.3 s | Lint: dynamic values must be escaped before they go into Rich markup |

### Tracing & costs — `tests/trace/`

Must prove:

- Traces emit valid JSONL and parse back
- Costs are priced correctly per provider

| File | Tests | Time | What it covers |
|---|---|---|---|
| `test_model_pricing.py` | 17 | 0.1 s |  |
| `test_trace_emitter.py` | 32 | 0.7 s | Tests for the per-run trace emitter (`mu/trace/emitter.py`) |
| `test_trace_parser.py` | 13 | 0.0 s | Tests for the trace parser/series/snapshot (``mu/trace/parser.py``, |

## Known gaps

- The web chat's thread-delete guard (unload a loaded thread first; never unload the current or a running thread) lives in `app.js` and has no behavioural test. Server-side thread deletion is covered in `sessions/test_threads.py`.
- The Sysbox engine test skips on hosts where the Sysbox service isn't running (`sudo systemctl enable --now sysbox`).

## How the harness got here (2026-10-20)

An audit of the previous suite (5,001 tests, 379 files) classified every file against these pillars. About 700 tests asserted text in source files, 13 Python files duplicated the Android Jest suite, job tests were organised by milestone, and some tests guarded removed features or old-store migrations. The decision was to keep only the files that prove each pillar's requirements, organise them by pillar, drop text assertions, make Docker mandatory and stop supporting old job stores. Running the formerly opt-in Docker suites found two real issues, both fixed: the secret scan flagged the `/etc/ssh/ssh_config.d` directory as key material, and Sysbox detection didn't notice a stopped Sysbox service. It also found two tests that still described superseded behaviour; those were updated to match the current product.
