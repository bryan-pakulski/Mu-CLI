# Feature Plan Engine

## Overview

Feature mode implements a feature from a plan the user approves. The plan, its
gates and its evidence live in the engine (`mu/feature/engine.py`), not in the
prompt: every surface (agent tools, the GUI panel, mobile, `/feature`) calls
the same engine functions, and every transition records an `actor` (`agent`,
`user:gui`, `user:mobile`, `user:cli`).

A plan moves through five states:

```
draft → approved → executing → review → done
```

- The agent proposes and revises a **draft**. Only the user approves it.
- Execution has **one task cursor**: one task `in_progress` at a time.
- Each exit criterion is met by **evidence** (a passing check run or a file
  reference with a justification), not by the model saying so.
- Each completed task gets a **review record**. The feature is complete when
  every task is completed and reviewed, no blockers are open, and the full
  suite passed after the last change. The user marks it complete; the agent can
  only request it.

## Storage

Each plan is one JSON file under the session:
`~/.mucli/sessions/<session>/features/<feature_id>.json` (`MUCLI_HOME`
overrides the root). The session keeps only a pointer record per feature
(`feature_registry`, built by `registry_record`: id, name, directory, metadata
path, status, revision, task counts); panels, the mode capsule and L3 read the
file through one mtime-keyed cached loader.

The file holds the phases (`phases_meta`), tasks with criteria and evidence,
the approval and its history, review records, diff proposals, the event log
(last 500 events), the current task id and completion data.

### Earlier plans

Older plan files still load; there is no migration step. Unknown keys are
ignored. A file without `status` is mapped from its `approved` /
`review_status` fields (`completed` → `done`; approved → `approved`, or
`executing` once any task has started; otherwise `draft`), and an approved
file gets an approval recorded with actor `legacy`. Plain `exit_criteria`
strings become criteria `c1..cn`; those listed in `verified_exit_criteria` are
marked met with an `attested` evidence item from `legacy`. Task `objectives` /
`action_points` map to `objective` / `actions`.

## Lifecycle

### 1. Propose (draft)

The agent reads the code, then calls `propose_feature_plan(name, goal,
phases)` once. Each phase has a `title`, an optional `goal` and `tasks`; each
task has a `title`, `objective`, `actions` and at least one exit criterion.
A criterion is a string or `{text, check}`, where `check` is a command that
proves it (pass = exit code 0).

Calling `propose_feature_plan` again replaces the draft. `revise_feature_plan`
edits it with a patch: `add_phases`, `add_tasks` (`[{phase_id, ...}]`),
`update_tasks` (`[{id, title?, objective?, actions?, exit_criteria?}]`),
`remove_tasks`, `goal`. In-progress or completed tasks cannot be removed, and
a completed task must be reopened before it changes.

### 2. Approve (user only)

No approval tool is exposed to the model. The user approves from the panel
(Now: *Approve plan*), `/feature approve`, or mobile. Approval refuses an
agent actor and records `{actor, at, plan_hash, revision, task_hashes}`.

The plan hash covers the goal, the phases and the planned tasks (title, phase,
objective, actions, criteria). Review follow-up tasks are within the approved
scope and are not hashed.

**Revisions.** If a revision changes the hash of an approved plan, the
approval moves to `approval_history`, the revision number increases and the
plan returns to `draft` for re-approval. Only the tasks whose content changed
are reset (status, review flag and criterion evidence); unchanged tasks keep
their progress.

**Edit gate.** In feature mode, before the plan is approved, the first
write-side edit of a turn (`write_file`, `apply_diff`,
`search_and_replace_file`) is refused with a warning envelope
(`feature_not_approved`); repeating it proceeds. With the
`feature_require_approval` variable on, every edit is refused until approval.

### 3. Execute

- `start_task(id)` needs an approved plan whose hash still matches. It refuses
  while another task is in progress, records the git tree at task start
  (`git stash create`, or `HEAD` when the tree is clean) and moves the plan to
  `executing`.
- `check_criterion(task_id, criterion)` runs the criterion's declared check
  through `run_check` (purpose `exit_criterion`; approval-gated, refused in
  plan mode). The criterion is met only if the run passes.
- `cite_evidence(task_id, criterion, runs | file + justification)` meets a
  criterion with evidence that already exists: a passing check run (`r<n>`,
  e.g. a `bash` test run recorded as a check), or a workspace file with a
  one-line justification for criteria that are not commands. A failing run is
  not evidence.
- `complete_task(id)` completes the in-progress task and refuses while any
  criterion is unmet. On completion the engine records the task's diff stat
  against its start tree. When every live task is completed the plan moves to
  `review`.

User actions (recorded with the user actor and shown differently in the panel):

- **Attest** a criterion with a note (`/feature attest`, the panel, mobile): an
  `attested` evidence item.
- **Override** completion of a task with unmet criteria: allowed only for a
  user actor and only with a reason; recorded on the task and as an `override`
  event.
- **Move** a task (panel, mobile, `/feature move`): the generic transition
  routes to the same gated call (`in_progress` → start, `completed` →
  complete, `blocked` → block, `archived` → archive, anything else → reopen).
  Archiving is user-only.

### 4. Blockers

Feature mode uses the session's core blockers. `block_task(id, summary,
needs)` marks the task `blocked` and raises a blocker with `ref=t<id>`;
`raise_blocker` works too. The user's next message, the panel's *Resolve*, or
`/blocker resolve` resolves it; when the session's blockers are all resolved,
blocked tasks with no open blocker return to `not_started`.

### 5. Review

`review_task(id, summary, issues)` reviews a completed task against its
objective and criteria. It needs:

- the task's diff (from the start tree, or the edits recorded since the task
  started), and
- at least one passing check run after the last edit.

The review record stores the summary, the diff stat and the check run id.
Issues are `follow_up` (a new task in the same phase, with origin `review`;
the plan goes back to `executing`) or `blocking` (a blocker on the task).

The earlier review-phase diff gate on `apply_diff` is gone: review records
are the review.

### 6. Complete

The feature is ready when:

- the plan is approved,
- every live task is completed and reviewed,
- no feature blocker is open,
- a passing full-suite run (`run_feature_suite(command)`, purpose `suite`) ran
  after the last edit.

`request_feature_completion` lists what is missing, or marks the completion as
requested. The user then marks it complete (panel *Mark complete*,
`/feature complete`); that call re-checks the preconditions, records the actor
and the suite run, moves the plan to `done` and archives the feature.

### Diff proposals

`propose_task_diff(task_id, diff, rationale)` records a diff for the user to
decide on. Deciding is user-only (panel Now row, `/feature diff`, mobile); the
model has no decision tool.

## Context

The feature prompt (`mu/prompts/templates/feature.md`) describes the loop
only. Volatile state rides in the **mode capsule** in the request-only TURN
CONTEXT: feature, status, revision, task progress, the current task with its
criteria (met / unmet, check commands), the next task to start, tasks waiting
for review, and whether completion was requested. Nothing is prepended to the
user's message or saved to history. L3 shows the feature id, status, active
phase and next task.

## Tools

All are `phase="feature"` (visible in feature mode only):

| Tool | Behaviour |
| --- | --- |
| `propose_feature_plan` | Persist (or replace) the draft plan. |
| `revise_feature_plan` | Patch the plan; a changed approved plan needs re-approval. |
| `feature_status` | Read the plan. |
| `start_task` | Start a task (one in progress). |
| `check_criterion` | Run a criterion's declared check (approval-gated). |
| `cite_evidence` | Meet a criterion with passing runs or a file + justification. |
| `complete_task` | Complete the in-progress task; refused with unmet criteria. |
| `block_task` | Block a task and raise a blocker for it. |
| `review_task` | Record a review; issues become follow-ups or blockers. |
| `run_feature_suite` | Run the full suite as completion evidence (approval-gated). |
| `propose_task_diff` | Record a diff for the user to decide on. |
| `request_feature_completion` | Ask the user to mark the feature complete. |

`raise_blocker` (core) is also available. Approval, attestation, overrides,
archiving, diff decisions and feature completion are user actions with no
tool.

## `/feature`

```
/feature                                  list session features (= /feature list)
/feature list | new <name> | load <id> | delete <id> | exit
/feature status | phases [<id>]           the plan: approval, tasks, criteria, evidence
/feature approve                          approve the draft (records you and the plan hash)
/feature start <task>                     start a task
/feature move <task> <status> [reason]    the same gated move as the panel
/feature attest <task> <criterion> <why>  mark a criterion met without a run (recorded)
/feature done <task> <reason>             complete with unmet criteria (override, recorded)
/feature diff <proposal> approve|reject [reason]
/feature review                           what still blocks completion
/feature complete                         mark the feature complete (preconditions enforced)
/feature help
```

`/feature load` activates a feature and queues a resumption briefing so the
agent continues instead of re-planning.

## Panel

The feature panel uses the shared mode shell:

- **State sentence:** *Draft · 3 phases · 9 tasks*, then *t3 of 9 · 1 blocked*,
  then *Review · 2 to review*.
- **Now:** *Approve plan* (*Approve and start* / *Request changes*) while the
  plan is a draft, pending diff proposals (*Approve* / *Reject*), blockers, and
  *Mark feature complete?* once completion is requested and nothing is
  missing.
- **Tabs:** Plan · Tasks · Criteria · Reviews. Tasks are rows grouped by phase;
  a task sheet shows the objective, criteria with their evidence marks
  (verified run, user-attested, missing), the diff stat, reviews and events.
  *Override…* asks for a reason.
- **Expand board** opens the board (phases as columns of rows) in the chat's
  main pane; the URL keeps the state (`?mode-view=feature&open=t4`).
- The composer foot shows the mode's word (*draft*, *t3 of 9*, *review*).

Check runs cited by the plan render as shared check-run rows that open a sheet
with the command, exit code, excerpt and full output.

## Server Endpoints

Router: `mu/gui/routers/feature.py`, prefix `/api/feature`. Mutations call the
engine with the request's user surface as the actor; gate refusals return 409
with `{message, code}`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/state` | Active plan view (status, approval, tasks with criteria and evidence, reviews, diff proposals, events, completion gaps), the session's features, open feature blockers and cited check runs. |
| `POST` | `/approve` | Approve the active draft. |
| `POST` | `/{feature_id}/approve` | Same, for earlier clients; the feature must be loaded. |
| `POST` | `/tasks/{task_id}/start` | Start a task. |
| `POST` | `/tasks/{task_id}/transition` | Gated move (`to_status`, optional reason). |
| `POST` | `/tasks/{task_id}/criteria/{criterion_id}/attest` | Attest a criterion (`note`). |
| `POST` | `/tasks/{task_id}/complete` | Complete a task; `override_reason` records an override. |
| `POST` | `/diffs/{proposal_id}/decide` | Approve or reject a diff proposal. |
| `POST` | `/complete` | Mark the feature complete. |
| `POST` | `/create` | Create a feature record; with `phases`, fill in a draft (still needs approval). |
| `POST` | `/{feature_id}/load`, `/unload` | Activate or clear the active feature. |
| `POST` | `/{feature_id}/archive`, `/unarchive` | Archive or restore a feature record. |
| `GET` | `/{feature_id}/preview` | Read-only view of a saved plan. |
| `DELETE` | `/{feature_id}` | Delete a feature (409 while it is loaded). |

There is no server-side runner: the agent drives execution through its tools
in feature mode, and the endpoints are short request/response calls.

## Tests

`tests/modes/test_feature_gates.py`: the model cannot approve; approval records
actor and hash and revisions need re-approval; tasks complete only with
evidence and one at a time; a GUI transition cannot complete without evidence
while a user override is recorded; `/feature move` routes through the engine;
review needs a passing check after the last edit and completion needs a suite
run; edits before approval are warned once or blocked; history holds only the
user's text while the capsule rides the request; L3 shows the next task; plan
files survive reload and earlier plans still load.
