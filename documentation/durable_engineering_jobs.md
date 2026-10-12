# Durable Engineering Jobs — Friday Five

## North star

> Friday at 4pm: give MuCLI five real engineering tickets. Close every control
> plane. Come back later and understand in under two minutes what every agent
> did, what succeeded, what failed, what needs a human, what it cost, and what
> is safe to merge.

## Architectural rule

Jobs are core runtime state, **not GUI state**.

```
                    durable job core
                          |
             +------------+------------+
             |            |            |
            GUI          TUI         Mobile
             |            |            |
             +----- same service/API --+
```

The three control planes may render and control jobs differently, but they must
never maintain independent lifecycle truth.

### Git artifact rule

A Git worktree is an **execution primitive only**. It must not be the durable
artifact a reviewer is expected to work with after implementation finishes.

Each workspace job owns a normal `mu/job-*` branch for its lifetime. While the
job is running, MuCLI may attach that branch to a managed worktree so several
jobs can execute safely in parallel. Once deterministic verification passes,
MuCLI must:

1. confirm the worktree is clean and its HEAD matches the job branch;
2. preserve the branch and verified HEAD as the authoritative review artifact;
3. retire the managed worktree;
4. clear the active worktree from the durable job record;
5. enter `READY_FOR_REVIEW` only after that branch handoff succeeds.

Review, diff, merge and eventual PR operations therefore use the normal branch,
not the managed worktree path. If a reviewer requests changes, MuCLI re-creates
a temporary worktree from the same branch and continues the same durable job.

In short:

```
execution:  branch + temporary worktree
                 |
                 v
verification passes
                 |
                 v
review:     normal branch only
                 |
        request changes?
          /           \
        yes            no
         |              |
recreate worktree     merge/PR
on same branch
```

## Lifecycle

```
QUEUED -> PREPARING -> RUNNING -> VERIFYING -> READY_FOR_REVIEW -> MERGED
                         |            |
                         v            v
                    NEEDS_HUMAN    CONFLICTED
                         |
                         +----------> RUNNING

Active execution may enter RECOVERING when its worker lease expires.
Failure states are retryable through QUEUED; CANCELLED and MERGED are terminal.
```

State changes and human/worker events are append-only records in the job event
log. Jobs use optimistic versions for concurrent control-plane updates.

`READY_FOR_REVIEW` means both verification and Git finalization have completed:
the verified changes are available on the durable review branch and the managed
execution worktree has been retired.

## Milestone 1 — Durable Jobs

**Goal:** closing a browser must not affect execution; job state survives process
and client restarts.

### Done
- [x] `Job`, `JobSpec`, `JobEvent`, `JobAttempt` domain types.
- [x] Explicit lifecycle state machine and structured human-attention reasons.
- [x] SQLite persistence under `$MUCLI_HOME/jobs/jobs.sqlite3`.
- [x] WAL mode, foreign keys, busy timeout, indexed state/event/lease tables.
- [x] Atomic job creation + event creation.
- [x] Atomic/versioned state transitions.
- [x] Append-only event timeline.
- [x] Execution-attempt records.
- [x] Worker lease, heartbeat, release and expired-lease detection.
- [x] Recovery transition for expired active worker ownership.
- [x] GUI HTTP contract (`/api/jobs`).
- [x] TUI `/job` + `/jobs` create/list/show/cancel/resume/retry controls.
- [x] Mobile typed `jobsApi` client against the same HTTP contract.
- [x] Core regression tests written.

### Left
- [x] Background controller/scheduler that leases queued jobs (`mu/jobs/controller.py`).
- [x] Existing MuCLI agent-runtime adapter for one job attempt (`mu/jobs/runner.py`).
- [x] Durable provider/model/execution policy captured on the job (`execution_json`).
- [x] Human-gate bridge from an executing job back into durable NEEDS_HUMAN state (`JobUI` + `InteractionRequired`).
- [x] Controller lifecycle independent of browser/mobile connection (detached daemon, `mu/jobs/host.py`).
- [x] Run the new regression tests in CI/local checkout (`tests/test_job_*.py`).

**Completion gate:** submit a job, close the browser, and observe the job continue
to an execution terminal/gate state from TUI/mobile later.

## Milestone 2 — Isolated Engineering Work

- [x] Repository registry / canonical repository identity.
- [x] Base branch + base SHA capture.
- [x] Per-job Git branch.
- [x] Per-job Git worktree for active execution.
- [x] Branch-first finalization after successful verification.
- [x] Worktree recreation on review changes.
- [x] Environment association.
- [x] Checkpoints.
- [x] Concurrent scheduler with collision-safe workspaces.

**Completion gate:** five jobs can safely work against the same repository at once,
and finished jobs can be reviewed from ordinary branches without keeping their
execution worktrees alive.

## Milestone 3 — Verification

- [x] Acceptance criteria contract.
- [x] Validation command plan.
- [x] Deterministic verifier runner.
- [x] Structured verification evidence.
- [x] Optional independent verifier-agent pass (`mu/jobs/acceptance.py`): opt-in via
      `execution.acceptance_review=true` or `MUCLI_JOBS_ACCEPTANCE_REVIEW=1`; read-only
      reviewer session grades each acceptance criterion pass/fail/unknown from the
      verified diff; `fail` gates READY behind a human; verdict stored as evidence
      (`acceptance-review.json`, receipt `acceptance_review`,
      `acceptance_criteria_machine_verified` is now truthful).
- [x] Work receipt/result manifest.

**Completion gate:** `READY_FOR_REVIEW` always has evidence and never means merely
"the builder said it was done". It also means the verified implementation has
been handed off to its normal Git review branch.

## Milestone 4 — Review + Attention UI

- [x] Shared query model: Needs You / Running / Ready / Failed.
- [x] GUI work queue and job detail.
- [x] TUI work queue/detail/attention workflow.
- [x] Mobile work queue/detail/attention workflow.
- [x] Diff review surface.
- [x] Evidence/Trace/cost summary.
- [x] Continue / request-changes / discard controls.

**Completion gate:** understand five jobs without opening five chat transcripts.

TUI: `/job summary [N|all]` renders one five-line block per job (headline, time/cost/
attempts/changes, verify + drift + acceptance verdict, branch@head, next action),
attention first.

## Milestone 5 — Git / PR Completion

- [x] Verified implementation materialized as a normal review branch.
- [x] Execution worktree retired before `READY_FOR_REVIEW`.
- [ ] Final commit policy / commit cleanup.
- [ ] PR creation.
- [x] Base-drift detection (`mu/jobs/base_drift.py`, `git merge-tree --write-tree`):
      assessed after verification, on a 5-minute controller sweep of READY jobs, and via
      `GET /api/jobs/{id}/drift[?apply=true]`; receipt `base_drift`.
- [x] Merge-conflict state: READY_FOR_REVIEW + not mergeable -> CONFLICTED
      (attention `merge_conflict`, pushed to surfaces); `continue` requeues with a
      rebase instruction (CONFLICTED -> QUEUED).
- [ ] CI/check state.
- [x] Mergeability state (`base_drift.mergeable`, surfaced in receipt/`/job show`/`/job summary`).
- [ ] Merge action.

**Completion gate:** ticket -> autonomous implementation -> branch review -> merge.

## Milestone 6 — Unattended Reliability

- [x] Process/worker crash recovery (lease CAS + idempotent replay).
- [x] Provider retry policy: headless sessions run `provider_recovery_policy=auto`
      (transient errors retry in-loop with backoff, never a human question);
      a transient implementation failure requeues with 30s→10min backoff
      (`next_run_at`) while attempts ≤ `max_retries`, then terminal FAILED.
- [x] Stuck/loop watchdogs (agent-loop watchdog + job runtime deadline).
- [x] Runtime/cost/iteration/subagent budgets (enforced by controller tick).
- [x] Artifact retention: `MUCLI_JOBS_RETENTION_DAYS` (default 30, 0 = off)
      purges evidence/logs/worktrees of ARCHIVED historic jobs on an hourly
      controller sweep; review branches are preserved.
- [x] Durable human gates across controller restart (event-backed responses; loader filters by type).
- [x] State-driven notifications: controller pushes `job_attention` (SSE) on
      enter/leave of NEEDS_HUMAN/CONFLICTED; `GET /api/jobs/attention`; web
      badge + toast; TUI startup banner and `/jobs` header.
- [x] Failure injection suite: `tests/test_job_failure_injection.py` (disconnect, worker
      kill, controller restart, transient/hard provider failure, base movement, late
      human answer on a chatty job, runtime deadline, five-ticket invariant sweep).
- [ ] Friday Five benchmark.

**Completion gate:** five jobs can be trusted overnight through deliberate failure
injection and still produce truthful, actionable outcomes.

## Friday Five release benchmark

Every release of this subsystem should eventually exercise five real tickets:

1. straightforward feature;
2. investigation-heavy bug;
3. refactor;
4. dependency upgrade;
5. ambiguous requirement requiring human input.

Inject browser/mobile disconnect, controller restart, provider failure and base
branch movement. Targets: zero workspace collisions, zero false-ready jobs,
100% cost attribution, 100% surfaced blockers, and <2 minutes to understand all
five outcomes.

## Durable job v2 — staged pipeline, Jira intake, PR publish

### Workspace-anchored jobs

Jobs no longer take a `repository`. Creating a job resolves its workspace from
the session that creates it (`workspace_root`, filled in by the GUI, mobile and
TUI from the active session's folder context). Payloads that send
`repository`/`repo` are rejected. Non-git workspaces are allowed; they run with
`execution.git_enabled = false`, without a base commit, review branch or PR.
Jobs stored before v2 that have `repository` set still load unchanged.

### Stages and per-stage models

New jobs default to `execution.pipeline = "staged"`:

```
QUEUED → PREPARING → PLANNING → RUNNING → VERIFYING → REVIEWING
       → PUBLISHING (git + publish enabled) → READY_FOR_REVIEW
REVIEWING --request_changes--> QUEUED (full re-implement + re-verify)
```

Each stage can use its own model. Lookup order is stage, then job execution,
then session:

```json
{"execution": {
  "provider": "anthropic", "model": "sonnet",
  "stages": {
    "plan":      {"provider": "anthropic", "model": "opus"},
    "implement": {"model": "haiku"},
    "review":    {"model": "opus"}
  },
  "max_review_rounds": 3
}}
```

- **Plan** runs once per job and stores `metadata.plan` (summary, steps,
  acceptance criteria, validation commands). If the planner fails, the job goes
  to `needs_human` with `provider_error`.
- **Implement** is the existing worker. Its prompt includes the plan and the
  latest reviewer feedback.
- **Review** is a read-only model session over the diff. It returns
  `approve` or `request_changes`. A response that can't be parsed counts as
  `request_changes`. Each round is appended to `metadata.review_rounds`.
  After `max_review_rounds` (default 3, range 1–20) the job goes to
  `needs_human` with `review_stalled`.

The human `READY_FOR_REVIEW` gate stays in place: the pipeline never merges.

### Dynamic acceptance and validation

The planner's acceptance criteria and validation commands are merged into the
job contract (`mu/jobs/dynamic_checks.py`):

- User-supplied criteria and commands are never dropped and always come first.
- Planner commands must start with an allow-listed executable (pytest, npm,
  make, cargo, go, tsc, …). Shell operators (`; && || | > < ` $( &`) are
  rejected. Rejected commands are recorded in `metadata.plan_contract`.
  `execution.validation_allowlist` extends the allow-list for a single job.
- At most 8 planner commands are accepted.
- Creating a job no longer requires validation commands. If no commands
  remain after planning, the job goes to `needs_human` with `no_validation`.

### Jira intake

Credentials are read from the server environment only. They are never taken
from API payloads and never stored on a job.

| Variable | Purpose |
|---|---|
| `JIRA_BASE_URL` | `https://acme.atlassian.net` or Data Center URL |
| `JIRA_EMAIL` + `JIRA_API_TOKEN` | Cloud (basic auth, REST v3) |
| `JIRA_PAT` | Data Center personal access token (bearer, REST v2) |
| `JIRA_WRITEBACK` | `1` to post a "job created" comment (off by default) |

- TUI: `/job jira pull PROJ-1 PROJ-2` or `/job jira pull --jql "project = PROJ AND assignee = currentUser()"`
- API: `POST /api/jobs/jira/import` `{"keys": [...]}` or `{"jql": "..."}`.
  Bodies that contain credential fields get a 400. Jira errors return 502.

Each issue becomes one job: the title is `KEY: summary`, the description is the
issue text with ADF converted to plain text, and acceptance criteria come from an
"Acceptance criteria" heading. `metadata.jira` holds the key, URL, status and
type. An issue that already has a live (non-terminal) job is skipped.

### PR publish

For git workspaces the default is `execution.publish_mode = "auto"`. For
non-git workspaces it is `"none"`. Once the reviewer approves, the job moves to
`PUBLISHING`. That stage pushes the review branch to `origin` and opens a
**draft** PR:

| Mode | Behaviour |
|---|---|
| `auto` | `gh` if it is installed and origin is GitHub; otherwise REST chosen by origin host |
| `gh` | `gh pr create --draft` |
| `github` | GitHub REST `draft: true` (`GITHUB_TOKEN` / `GH_TOKEN`) |
| `gitlab` | GitLab merge request titled `Draft: …` (`GITLAB_TOKEN`) |
| `none` | Stage skipped |

On success the PR details go into `metadata.pr` and the job moves to
`READY_FOR_REVIEW`. If the push or PR call fails, the job goes to
`needs_human` with `publish_failed`. Tokens are redacted from error text.
