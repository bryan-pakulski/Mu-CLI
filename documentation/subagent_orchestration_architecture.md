# Sub-Agent Orchestration Architecture

## Goal
Add first-class **sub-agent execution** so the main Mu-CLI agent can delegate bounded tasks to autonomous worker agents that run in parallel, then merge results back into the parent run.

This proposal is intentionally compatible with existing Session/Server/TUI architecture.

---

## Product Requirements Mapping

1. **Main agent can spawn sub-agents with defined tasks**
   - Add a new tool entrypoint (`spawn_sub_agents`) that accepts one or more task specs.
2. **Sub-agents are fully autonomous with tooling**
   - Each sub-agent runs a normal `Session` loop with tool access, but receives a constrained policy profile.
3. **Sandboxed, no recursive sub-agent spawning**
   - Child runtime disables `spawn_sub_agents` and related orchestration controls via runtime tool policy.
4. **Parallel fan-out (default 3, configurable variable)**
   - Add session variable `subagent_max_parallel` (default `3`) and enforce in scheduler.
5. **Visible status in TUI and CLI**
   - Extend runtime state payload with `subagents` list and render status rows in both terminal progress and GUI panels.

---

## High-Level Design

### New Runtime Components

1. **SubAgentManager** (`core/subagents.py`)
   - Owns worker lifecycle.
   - Queue + scheduler + dynamic re-queue + result collation.
   - APIs:
     - `submit(tasks: list[SubAgentTask]) -> list[str]` (returns worker ids)
     - `wait(worker_ids: list[str], timeout_s: int | None = None) -> SubAgentBatchResult`
     - `snapshot() -> list[SubAgentState]` (for CLI/TUI/server payloads)

2. **SubAgentWorker**
   - One isolated execution context per task.
   - Creates child `SessionManager` in ephemeral path: `~/.mucli/sessions/<parent>/subagents/<worker_id>/`.
   - Carries parent provider/model config by value; no shared mutable history object.

3. **SubAgentPolicyProfile**
   - Disables nested delegation (`allow_subagent_spawn=False`).
   - Optional tighter limits (iterations, timeout, filesystem scope, disabled tools).

---

## Data Model


### Scheduling Semantics (Work-Stealing Style)

The scheduler should not behave like a single fan-out then hard wait barrier.

- Maintain three sets: `pending`, `running`, `finished`.
- While `running < subagent_max_parallel` and `pending` not empty, dispatch next task.
- On worker completion:
  - move worker to `finished`
  - publish completion event immediately
  - allow parent orchestrator to enqueue new derived tasks
  - refill freed capacity right away if pending work exists
- Parent orchestration loop may interleave:
  - result inspection
  - task decomposition/refinement
  - integration/test execution
  - additional user-facing progress messages

This turns orchestration into a continuous pipeline instead of a blocking map/reduce barrier.


Add typed state records (dataclass or pydantic-style plain dict contract):

- `SubAgentTask`
  - `task_id`, `title`, `prompt`, `acceptance_criteria`, `priority`
- `SubAgentState`
  - `worker_id`, `task_id`, `status`, `started_at`, `updated_at`, `ended_at`, `summary`, `error`
  - `status in {queued, running, completed, failed, cancelled, timed_out}`
- `SubAgentResult`
  - `worker_id`, `status`, `final_message`, `tool_events`, `artifacts`, `patch_summary`

Persist lightweight status into parent session JSON under:

```json
"subagent_state": {
  "max_parallel": 3,
  "workers": []
}
```

---

## Variable System Changes

Add to `VARIABLE_SCHEMA` in `utils/config.py`:

- `subagent_enabled: bool = True`
- `subagent_max_parallel: int = 3`
- `subagent_task_timeout_s: int = 900`
- `subagent_max_iterations: int = 60`
- `subagent_allow_tooling: bool = True`

Validation rules:
- `subagent_max_parallel >= 1`
- Clamp to hard ceiling (ex: `16`) to prevent runaway process spawning.

---

## Execution Flow

1. Parent agent decides to delegate and calls `spawn_sub_agents` tool.
2. Tool validates variable gates and task count.
3. Manager enqueues tasks and starts up to `subagent_max_parallel` workers.
4. Parent enters a **non-blocking orchestration loop**:
   - periodically reads `SubAgentManager.snapshot()`
   - emits status to CLI/TUI/server event hub
   - continues parent-side work (planning, validation, integration, user updates) while workers run
   - can cancel all on Ctrl+C or hard failure policy.
5. If one worker completes while others are still running, parent may immediately enqueue follow-up tasks using the freed slot (up to `subagent_max_parallel`).
6. Manager continues scheduling until global completion criteria are met (no pending tasks and no running workers, or orchestrator stop condition).
7. Parent synthesizes final answer / integration step (can apply patches, run tests, summarize).

---

## Sandboxing and Safety

Each worker must run with child-scoped constraints:

- **No recursive delegation**:
  - Remove/disable `spawn_sub_agents` from child tool registry.
- **Directory scope**:
  - Inherit parent workspace roots but optionally narrowed to task-specific root.
- **Resource bounds**:
  - Enforce per-worker timeout + max iterations.
- **Approval model**:
  - Respect parent approval mode (`yolo`/strict), but allow independent approval queue tagging by `worker_id`.

---

## CLI and TUI Status Indicators

### Server/state payload

Extend `build_state_payload(...)` shape in `core/server.py`:

- `subagents: [{ worker_id, task_id, status, summary, started_at, updated_at }]`
- `subagent_counts: { queued, running, completed, failed, cancelled, timed_out }`

### CLI

Add a compact “Sub-Agents” section in the live status renderer (same area as feature progress):

- `SA running:2 queued:1 done:4 failed:0`
- Per worker row (truncated):
  - `• [running] sa-02  Extract API contracts…`
  - `• [completed] sa-01  Wrote unit tests`

### TUI (`ui/gui_tui.py`)

Add a new panel (ex: right column, below tool heatmap):

- Title: `Sub-Agents`
- Badge per state with color mapping:
  - queued=yellow, running=cyan, completed=green, failed=red, timed_out=magenta
- Includes progress bar: `completed / total`

---

## Tooling Interface

### New Tool: `spawn_sub_agents`

Input schema:

- `tasks: [{title, prompt, acceptance_criteria?}]`
- `wait_for_completion: bool = true`
- `max_parallel: int | null` (defaults to session variable)

Return schema:

- `batch_id`
- `workers`
- `results` (if waited)
- `status_summary`

Companion read-only tools (optional but recommended):

- `list_sub_agents(batch_id?)`
- `cancel_sub_agents(batch_id | worker_ids)`

---

## Integration Points

1. `core/tools.py`
   - Register new orchestration tool(s).
2. `core/session.py`
   - Attach `SubAgentManager` to session runtime.
   - Add event emission hooks for periodic snapshots.
3. `core/server.py`
   - Include sub-agent snapshot in API payload/events.
4. `mucli` (CLI)
   - Render text status block for sub-agents.
5. `ui/gui_tui.py`
   - Render structured sub-agent panel.

---

## Rollout Plan

### Phase 1: Data contracts + variable plumbing
- Add variable schema entries + session persistence for `subagent_state`.
- Add no-op snapshot fields into server payload/CLI/TUI.

### Phase 2: Worker runtime + scheduler
- Implement manager/worker with bounded parallelism and timeout handling.
- Add child policy guard to prohibit nested spawn.

### Phase 3: UX + telemetry
- Finalize CLI/TUI indicators.
- Emit per-worker lifecycle events for logs and GUI streaming.

### Phase 4: Hardening
- Add cancellation, retry policy, and failure summarization.
- Stress test with mixed long/short tasks.

---

## Test Plan

1. **Unit**
   - Scheduler obeys `subagent_max_parallel`.
   - Child policy blocks nested delegation.
   - Timeout transitions to `timed_out`.
2. **Integration**
   - Spawn 5 tasks with max parallel=3; assert only 3 running simultaneously.
   - Validate state payload exposes live worker statuses.
3. **UI**
   - Snapshot-driven render tests for CLI/TUI status sections.
4. **Recovery**
   - Restart server mid-run; ensure persisted `subagent_state` restores visible status.

---

## Notes on Compatibility

- Design reuses current `Session` + tool harness instead of introducing a second execution engine.
- Existing non-agentic flows remain unchanged when `subagent_enabled=false`.
- This architecture keeps sub-agents autonomous while preserving top-level control, observability, and safety constraints.
