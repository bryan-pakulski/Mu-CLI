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
- [ ] Background controller/scheduler that leases queued jobs.
- [ ] Existing MuCLI agent-runtime adapter for one job attempt.
- [ ] Durable provider/model/execution policy captured on the job.
- [ ] Human-gate bridge from an executing job back into durable NEEDS_HUMAN state.
- [ ] Controller lifecycle independent of browser/mobile connection.
- [ ] Run the new regression tests in CI/local checkout.

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
- [ ] Optional independent verifier-agent pass.
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

## Milestone 5 — Git / PR Completion

- [x] Verified implementation materialized as a normal review branch.
- [x] Execution worktree retired before `READY_FOR_REVIEW`.
- [ ] Final commit policy / commit cleanup.
- [ ] PR creation.
- [ ] Base-drift detection.
- [ ] Merge-conflict state.
- [ ] CI/check state.
- [ ] Mergeability state.
- [ ] Merge action.

**Completion gate:** ticket -> autonomous implementation -> branch review -> merge.

## Milestone 6 — Unattended Reliability

- [ ] Process/worker crash recovery.
- [ ] Provider retry policy.
- [ ] Stuck/loop watchdogs.
- [ ] Runtime/cost/iteration/subagent budgets.
- [ ] Durable human gates across controller restart.
- [ ] State-driven notifications.
- [ ] Failure injection suite.
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
