# Friday Five — Implementation Progress

North star:

> Give MuCLI five real engineering tickets, close every control plane, return
> later, and understand in under two minutes what each agent did, what passed,
> what failed, what needs a human, what it cost, and what is safe to merge.

This ledger distinguishes **source complete** from **validated complete**. The
current development environment has no GitHub workflow runs for this branch and
cannot execute the new repository tests, so no milestone is labelled runtime-
validated until those tests are run externally/locally.

## Architectural invariant

GUI, TUI and mobile are control planes over one durable core. They do not own
job lifecycle state.

```
              SQLite/WAL durable job core
                         |
              controller / workers
                         |
          +--------------+--------------+
          |              |              |
         GUI            TUI           Mobile
```

## Milestone 1 — Durable Jobs

**Source status: COMPLETE**  
**Runtime validation: PENDING**

Done:
- Durable `Job`, `JobSpec`, `JobEvent`, `JobAttempt` domain model.
- Explicit lifecycle state machine and structured attention reasons.
- SQLite/WAL storage under `$MUCLI_HOME/jobs/jobs.sqlite3`.
- Atomic/versioned state transitions and append-only events.
- Attempts, worker leases, heartbeats and expired-owner recovery.
- Reproducible execution profile: provider/model/mode/session type/write policy.
- Noninteractive `JobUI`; approvals/questions become durable `NEEDS_HUMAN` gates.
- Existing MuCLI `Session`/agent runtime adapter; no second agent stack.
- Detached controller daemon starts independently of browser/TUI lifecycle.
- TUI `/job`/`/jobs` job creation/control path.
- GUI `/api/jobs` shared job API.
- Mobile typed `jobsApi` shared API client.
- Core, runner and controller regression tests written.

Still required for validated completion:
- Run the new test suite in a real checkout/CI.
- Exercise one real provider job end-to-end with browser/TUI closed.

## Milestone 2 — Isolated Engineering Work

**Source status: COMPLETE**  
**Runtime validation: PENDING**

Done:
- Durable canonical Git repository registry keyed by Git common-dir identity.
- Concrete base SHA resolved before implementation.
- Deterministic per-job `mu/job-*` branch.
- Managed per-job Git worktree under MuCLI state.
- Job environment records repository/worktree/branch identity.
- Checkpoint commits on implementation complete/block/failure/cancel boundaries.
- One Python subprocess per active job: Session/CWD/runtime isolation.
- Worker process owns its heartbeat/lease and can outlive daemon restart.
- Scheduler supports five concurrent isolated job processes.
- A second controller cannot duplicate an active leased job.
- Cancellation targets only the matching worker process.
- Real temporary-Git worktree/repository/checkpoint tests written.
- Subprocess scheduler tests written.

Still required for validated completion:
- Run Git fixture + subprocess scheduler tests.
- Run five real simultaneous tickets against one repository.
- Container-backed durable job environment adapter is still intentionally gated;
  host workspace jobs are the first supported unattended execution environment.

## Milestone 3 — Verification

**Source status: COMPLETE (deterministic path)**  
**Runtime validation: PENDING**

Done:
- Validation commands are a first-class ticket contract.
- Separate deterministic verifier subprocess for `VERIFYING` jobs.
- Bounded stdout/stderr, exit codes, timeout and timing captured per check.
- Verification runs persisted in SQLite and JSON evidence manifests.
- Git evidence: base/head, changed files, additions/deletions, diff stat, dirty state.
- Exit-0 validation that dirties tracked/untracked job state is not considered ready.
- No validation contract => `NEEDS_HUMAN / verification_required`.
- All checks pass => `READY_FOR_REVIEW`.
- Failed checks with retry budget => same job/session/branch requeued for repair.
- Verification failure evidence is injected into the next implementation prompt.
- Retry exhaustion => `NEEDS_HUMAN / test_failure`.
- Verification worker crash retries verification, not implementation.
- Work receipt aggregates status, attempts, elapsed time, cost, tokens, Git diff,
  verification evidence, checkpoints and activity.
- GUI/mobile evidence endpoints/types implemented.
- Deterministic verification, repair-loop and receipt tests written.

Strengthening work still left:
- Optional independent verifier-agent review of the diff/acceptance criteria.
- Machine-readable acceptance criteria beyond declared validation commands.
- Run verification/receipt tests in a real checkout/CI.

## Milestone 4 — Review + Attention UI

**Status: IN PROGRESS**

Done:
- Shared core board projection with exactly these buckets:
  `needs_you`, `running`, `queued`, `ready`, `failed`, `done`.
- Shared work receipt/evidence schema is ready for presentation.

Left:
- GUI work queue + detail/review surface.
- TUI board/detail/evidence workflow.
- Mobile work queue + detail/attention workflow.
- Diff review presentation.
- Request-changes/continue/discard controls.

## Milestone 5 — Git / PR Completion

**Status: NOT STARTED**

Left:
- Structured final commits/rebase policy.
- Base drift detection.
- Merge conflicts as durable state.
- PR creation/linkage.
- CI/check state ingestion.
- Mergeability and merge action.

## Milestone 6 — Unattended Reliability

**Status: PARTIAL FOUNDATION**

Already present from M1/M2:
- leases/heartbeats;
- process-loss recovery;
- controller restart independence;
- attempt history;
- isolated checkpoints;
- implementation retry after deterministic verification failure.

Left:
- enforce cost/runtime/iteration/subagent budgets during execution;
- provider retry policy at job level;
- stuck/loop watchdogs at controller level;
- state-driven notifications;
- durable notification delivery;
- failure-injection harness;
- Friday Five benchmark run and release gate.

## Current trust rule

`READY_FOR_REVIEW` is never set because an agent says it is done. It requires a
persisted deterministic verification run with all configured checks passing and
a clean job worktree. Missing or failed evidence blocks readiness.
