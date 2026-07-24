# Feature Plan Engine

## Overview

The feature plan engine provides a persistent workflow for implementing new features in phases instead of relying on a single free-form implementation turn.

The engine is centered around a single `feature_plan.json` file (stored in the workspace root or session directory) containing:

- `feature_plan.json` — machine-readable plan metadata for the harness and server APIs.
- Phase definitions embedded in the JSON plan, with human-readable descriptions the model updates as work progresses.

This design allows a harness to:

1. ask the model to create a plan,
2. wait for user approval,
3. execute one phase at a time,
4. re-prompt the model until the current phase is actually complete,
5. run a final review pass before returning success, and
6. pause safely when the model raises a blocker that requires user input.

The feature plan and its phase/task state are persisted (to `feature_plan.json` and mirrored into the active session JSON), so a blocked or interrupted feature run can be reloaded after a disconnect or application restart and resumed from the last saved task.

## File Layout

The plan metadata is stored as a single `feature_plan.json` file in either the workspace root or the session directory. No `documentation/feature_req_<id>/` directory structure is created.

## Phase File Format

Each phase file must contain the following sections:

- `Objectives`
- `Action Points`
- `Exit Criteria`

Every checklist entry must use one of these markers:

- `[ ]` — not started
- `[~]` — in progress or blocked
- `[x]` — completed

Example:

```md
# Phase 1: Build the API surface

## Objectives
- [x] Confirm the data model changes required
- [~] Define the server contract for the new feature

## Action Points
- [ ] Add the new request handler
- [ ] Add structured tests for error paths

## Exit Criteria
- [ ] Endpoint works in the happy path
- [ ] Tests cover the new behavior
```

## `feature_plan.json` Responsibilities

`feature_plan.json` is the structured source used by the harness and server API.

It stores:

- `feature_id`
- `feature_name`
- `feature_request`
- `directory`
- `approved`
- `review_status`
- `review_notes`
- `phases`

The markdown phase files remain the day-to-day execution surface, while the JSON file gives the harness a stable object to parse.

## Engine Lifecycle

### 0. Propose (TEXT ONLY, no tool calls)

The agent MUST present the full plan as text in chat before making any tool calls. This includes:

- Feature name and description
- Phase breakdown with titles and goals
- Per-phase tasks with objectives, action points, and exit criteria

The agent waits for the user to explicitly approve (e.g. "approved", "go ahead", "looks good") before proceeding. This prevents duplicate features from premature tool calls and allows easy tweaks before persistence.

### 1. Planning (staged tools — after approval only)

After explicit user approval, the agent uses the staged planning tools to persist the plan:

1. `create_feature` (Stage 1: plan shell + requirements),
2. `create_phases` (Stage 2: epics/phases),
3. `create_task` (Stage 3: tickets with exit criteria).

Legacy compatibility still exists for `create_feature_task`, but staged calls are the default contract.

The agent must NOT call these tools until the user has approved the plan text in chat.

### 2. Approval

The user or harness sets plan approval metadata once the proposed phases are accepted.

Approval can be updated through:

- the `approve_feature_task` tool, or
- the `POST /api/feature/{feature_id}/approve` server endpoint.

### 3. Implementation Loop

Once approved, the harness should:

1. load the plan,
2. identify the next incomplete phase,
3. prompt the model to work on only that phase,
4. require the model to update the phase markdown file as work progresses,
5. re-run until every checklist item in that phase is `[x]`.

### 4. Blocker Pause / Resume

If the model cannot safely continue because it needs missing requirements, credentials, product decisions, or other user-provided context, it should call `raise_blocker`.

That blocker should include:

- a short summary,
- what has already been tried,
- the exact input needed from the user,
- any focused follow-up questions.

When a blocker is raised, the agent pauses the feature run, exposes the task state and conversation history to the user, collects additional context, and then resumes with that context.

The persisted feature state (plan + task statuses in the session JSON and `feature_plan.json`) captures the current directory, last completed task, and blocker payload, so the harness can reconstruct the paused run after reconnecting.

### 5. Review Loop

After all phases are complete, the harness should ask the model to review the completed work against the phase files and actual code changes.

If review fails, the model should:

- change the relevant phase items back to `[~]`,
- explain what is missing,
- return to implementation.

If review succeeds, the model should set `review_status` to `completed` and summarize the result.

## Tools

The engine now exposes staged planning, execution, review, and archive tools:

### Planning

- `create_feature`
- `create_phases`
- `create_task`
- `create_feature_task` (legacy single-shot compatibility — staged calls above are the default)

### Execution

- `get_execution_state`
- `get_tasks`
- `get_current_task`
- `update_task_status`
- `update_feature_task`
- `block_task`
- `resume_task`
- `raise_blocker`

### Review & Archive

- `review_all_completed_tasks`
- `review_completed_tasks`
- `complete_review`
- `schedule_review`
- `get_due_reviews`
- `propose_task_diff`
- `decide_task_diff`
- `archive_task`
- `approve_feature_task` (sets plan approval / final review completion status)

## CLI (Phase 5 command loop)

Feature mode now includes a command surface that maps directly to workflow steps:

- `/feature create plan <name>`
- `/feature create phase <title> | <goal>`
- `/feature create task <phase_id> | <title> | <overview> | <exit1;exit2>`
- `/feature show <board|execution|reviews>`
- `/feature move <task_id> <status>`
- `/feature block <task_id> <reason>`
- `/feature review auto`
- `/feature review <task_id> <summary>`
- `/feature archive <task_id>`
- `/feature monitor [refresh_seconds]`
- `/feature help`

## Server Endpoints

The GUI server exposes feature endpoints under the `/api/feature` prefix
(router in `mu/gui/routers/feature.py`) for the web UI and external
harnesses:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/feature/state` | Active feature + plan summary (kanban phase columns, task list, metadata path). Returns `{active: false, ...}` when no session is active. |
| `POST` | `/api/feature/create` | Create a new feature with optional `phases`/`tasks` in the body (`feature_name` required, `directory` and `feature_request` optional). |
| `POST` | `/api/feature/{feature_id}/approve` | Mark the plan approved so implementation can begin. |
| `POST` | `/api/feature/{feature_id}/load` | Load a feature into the active slot. |
| `POST` | `/api/feature/{feature_id}/unload` | Clear the active feature (alias of `/feature exit`). |
| `POST` | `/api/feature/{feature_id}/archive` | Archive a completed feature. |
| `POST` | `/api/feature/{feature_id}/unarchive` | Restore an archived feature. |
| `DELETE` | `/api/feature/{feature_id}` | Delete a feature (refuses 409 if it is currently loaded). |
| `POST` | `/api/feature/tasks/{task_id}/transition` | Move a task to a new status (`to_status`, optional `notes`/`blocked_reason`). Invalid transitions return 409. |
| `POST` | `/api/feature/tasks/{task_id}/exit-criteria/{idx}/toggle` | Flip one exit-criterion checkbox for a task. |

There is **no** server-side `/api/feature-loop` runner. The phase-by-phase
implementation loop is driven by the agent in feature mode (the system
prompt's prompting contract below), not by a long-running server
endpoint. The server endpoints above are short request/response calls the
UI uses to read and mutate plan state between agent turns; the agent
itself advances phases, raises blockers, and runs review via the tools.

## Prompting Contract

Feature mode prompts should instruct the agent to:

- always use the feature plan engine,
- **present the plan as text in chat BEFORE calling any feature creation tools** — do not call `create_feature`, `create_feature_task`, `create_phases`, or `create_task` until the user has explicitly approved,
- gather read-only investigation context into the collation buffer before acting when a phase requires substantial discovery,
- store short-lived file targets, hypotheses, and verification checklists in the scratchpad during a phase,
- call `flush` once enough context has been gathered so implementation decisions are based on the complete collected context,
- call `raise_blocker` instead of spinning when user help is required,
- never skip user approval,
- never start phase `N + 1` before phase `N` is fully complete,
- keep markdown and code changes synchronized,
- use review to validate exit criteria instead of assuming success.

## Recommended Harness Flow

1. User requests a feature.
2. Agent designs the plan as text in chat — feature name, phases, tasks, objectives, exit criteria. No tool calls yet.
3. User reviews and approves the plan text.
4. After approval, agent creates the feature using tool calls (`create_feature` → `create_phases` → `create_task`).
5. User confirms plan approval via `approve_feature_task` tool, `POST /api/feature/{feature_id}/approve`, or the `/feature` approval flow.
6. Agent advances phase-by-phase in feature mode, updating task status via `update_task_status` and the `/api/feature/tasks/{task_id}/transition` endpoint; the UI reads `GET /api/feature/state` between turns.
7. If the agent hits a missing requirement, it calls `raise_blocker` and pauses for user input; the user supplies context and the agent resumes.
8. Once all phases are complete, the agent runs review (`review_all_completed_tasks` or `/feature review auto`).
9. If review passes, report completion; if it fails, the agent reopens the relevant tasks and continues until the plan satisfies its exit criteria.
