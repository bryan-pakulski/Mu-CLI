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

- [ ] Repository registry / canonical repository identity.
- [ ] Base branch + base SHA capture.
- [ ] Per-job Git branch.
- [ ] Per-job Git worktree.
- [ ] Environment association.
- [ ] Checkpoints.
- [ ] Concurrent scheduler with collision-safe workspaces.

**Completion gate:** five jobs can safely work against the same repository at once.

## Milestone 3 — Verification

- [ ] Acceptance criteria contract.
- [ ] Validation command plan.
- [ ] Deterministic verifier runner.
- [ ] Structured verification evidence.
- [ ] Optional independent verifier-agent pass.
- [ ] Work receipt/result manifest.

**Completion gate:** `READY_FOR_REVIEW` always has evidence and never means merely
"the builder said it was done".

## Milestone 4 — Review + Attention UI

- [ ] Shared query model: Needs You / Running / Ready / Failed.
- [ ] GUI work queue and job detail.
- [ ] TUI work queue/detail/attention workflow.
- [ ] Mobile work queue/detail/attention workflow.
- [ ] Diff review surface.
- [ ] Evidence/Trace/cost summary.
- [ ] Continue / request-changes / discard controls.

**Completion gate:** understand five jobs without opening five chat transcripts.

## Milestone 5 — Git / PR Completion

- [ ] Structured commits.
- [ ] PR creation.
- [ ] Base-drift detection.
- [ ] Merge-conflict state.
- [ ] CI/check state.
- [ ] Mergeability state.
- [ ] Merge action.

**Completion gate:** ticket -> autonomous implementation -> review -> merge.

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
