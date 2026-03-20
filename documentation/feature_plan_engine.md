# Feature Plan Engine

## Overview

The feature plan engine provides a persistent workflow for implementing new features in phases instead of relying on a single free-form implementation turn.

The engine is centered around a generated directory under `documentation/feature_req_<id>/` containing:

- `feature_plan.json` — machine-readable plan metadata for the harness and server APIs.
- `phase_1.md`, `phase_2.md`, ... — human-readable implementation phases that the model updates as work progresses.

This design allows a harness to:

1. ask the model to create a plan,
2. wait for user approval,
3. execute one phase at a time,
4. re-prompt the model until the current phase is actually complete,
5. run a final review pass before returning success, and
6. pause safely when the model raises a blocker that requires user input.

## Directory Layout

```text
documentation/
  feature_req_example_feature/
    feature_plan.json
    phase_1.md
    phase_2.md
    phase_3.md
```

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

### 1. Planning

The agent should call `create_feature_plan` when operating in feature mode.

That tool creates:

- the `documentation/feature_req_<id>/` directory,
- `feature_plan.json`,
- all `phase_N.md` files.

After plan creation, the agent should stop and ask the user for approval.

### 2. Approval

The user or harness sets plan approval metadata once the proposed phases are accepted.

Approval can be updated through:

- the `update_feature_plan` tool, or
- the `/api/feature-plan/approve` server endpoint.

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

When a blocker is raised, the harness should pause the feature loop, expose the task state and conversation history to the user, collect additional context, and then resume the loop with that context.

### 5. Review Loop

After all phases are complete, the harness should ask the model to review the completed work against the phase files and actual code changes.

If review fails, the model should:

- change the relevant phase items back to `[~]`,
- explain what is missing,
- return to implementation.

If review succeeds, the model should set `review_status` to `completed` and summarize the result.

## Tools

The engine currently exposes three tools:

### `create_feature_plan`

Creates the plan directory, manifest, and phase markdown files.

### `get_feature_plan`

Loads the plan, re-parses the markdown files, and returns a summarized machine-readable status object.

### `update_feature_plan`

Updates plan metadata such as:

- `approved`
- `review_status`
- `review_notes`

## Server Endpoints

The headless server exposes feature-plan endpoints for GUI and external harnesses:

- `GET /api/feature-plan?directory=<path>`
- `POST /api/feature-plan/approve`
- `POST /api/feature-loop`
- `POST /api/feature-loop/resolve`

### `/api/feature-loop`

`/api/feature-loop` runs the approved feature implementation loop on the server side.

Behavior:

1. refreshes plan state from disk,
2. verifies the plan is approved,
3. prompts the model for the next incomplete phase,
4. repeats until phases are complete,
5. triggers a review prompt,
6. pauses when a blocker is raised,
7. resumes after user input is supplied,
8. stops when review is completed or the loop becomes permanently blocked.

## Prompting Contract

Feature mode prompts should instruct the agent to:

- always use the feature plan engine,
- call `raise_blocker` instead of spinning when user help is required,
- never skip user approval,
- never start phase `N + 1` before phase `N` is fully complete,
- keep markdown and code changes synchronized,
- use review to validate exit criteria instead of assuming success.

## Recommended Harness Flow

1. User requests a feature.
2. Agent runs in feature mode and creates the phased plan.
3. User reviews and approves the plan.
4. Harness starts `/api/feature-loop`.
5. Harness monitors the returned plan summary after each cycle.
6. If review passes, report completion to the user.
7. If review fails, continue the loop until the plan truly satisfies its criteria.
