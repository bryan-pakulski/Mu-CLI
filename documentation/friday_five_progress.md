# Friday Five — Implementation Progress

North star:

> Give MuCLI five real engineering tickets, close every control plane, return
> later, and understand in under two minutes what each agent did, what passed,
> what failed, what needs a human, what it cost, and what is safe to merge.

This ledger distinguishes **source complete** from **validated complete**.

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
- TUI `/job`/`/jobs`, GUI `/api/jobs`, and mobile `jobsApi` share the same core.

Still required for validated completion:
- Green durable-jobs CI.
- Exercise one real provider job end-to-end with browser/TUI closed.

## Milestone 2 — Isolated Engineering Work

**Source status: COMPLETE**  
**Runtime validation: PENDING**

Done:
- Durable canonical Git repository registry.
- Concrete base SHA resolved before implementation.
- Deterministic per-job `mu/job-*` branch and managed Git worktree.
- Checkpoint commits on complete/block/failure/cancel boundaries.
- One Python subprocess per active job: Session/CWD/runtime isolation.
- Worker-owned heartbeat/lease, surviving controller/browser restart.
- Scheduler target of five concurrent isolated job processes.
- Targeted cancellation and duplicate-controller lease protection.

Still required for validated completion:
- Green Git fixture/subprocess scheduler CI.
- Run five simultaneous real tickets against one repository.
- Container-backed durable jobs remain deliberately gated until their own adapter exists.

## Milestone 3 — Deterministic Verification

**Source status: COMPLETE**  
**Runtime validation: PENDING**

Done:
- First-class validation commands and separate verifier subprocess.
- Exit code/stdout/stderr/timeout/duration captured per check.
- Persistent SQLite + JSON verification evidence.
- Git base/head, changed files, additions/deletions, diff stat and dirty-state evidence.
- Clean all-pass verification is required for `READY_FOR_REVIEW`.
- Missing validation => `NEEDS_HUMAN / verification_required`.
- Failed verification => automatic repair attempt while retry budget remains.
- Retry exhaustion => `NEEDS_HUMAN / test_failure`.
- Failed verification evidence enters the next implementation prompt.
- Durable work receipt aggregates outcome, attempts, cost/tokens, Git and verification evidence.

Still required for validated completion:
- Green verifier/receipt CI.
- Optional independent verifier-agent remains a strengthening layer.

## Milestone 4 — Review + Attention UI

**Source status: COMPLETE**  
**Automated validation: RUNNING**  
**Manual three-plane validation: READY AFTER CI GREEN**

Shared review core:
- Shared board projection: `needs_you`, `running`, `queued`, `ready`, `failed`, `done`.
- Shared work receipt, verification evidence, Git diff and activity timeline contracts.
- Durable human interaction responses consumed exactly once by the next worker.
- Tool approvals support approve/deny/explain and preserve preview safety.
- `verification_required` can be resolved by supplying validation commands.
- Request changes requeues the same durable job/branch/worktree/session with reviewer feedback.
- Continue/retry and discard are common actions across all control planes.

GUI:
- First-class Engineering Work briefcase action in the product header.
- Dedicated `/work` board/detail page using the alpine glass visual language.
- New-job form with current-session inheritance.
- Receipt, deterministic verification output, Git diff and activity timeline.
- Approval/question/validation gates, request changes, continue/retry and discard.

TUI:
- `/jobs` / `/job board` shared queue view.
- `/job show`, `/job receipt`, `/job diff`.
- `/job respond`, `/job changes`, `/job continue`, `/job discard`.
- Queued work ensures the detached controller daemon is running.

Mobile:
- First-class Work briefcase action in the chat header/settings.
- `Work` board and `JobDetail` review routes.
- New-job flow inheriting the active session.
- Receipt, verifier evidence, Git diff and activity timeline.
- Approval/question/validation response, request changes, continue/retry and discard.

Validation gate:
- M4 shared review workflow regression tests added.
- CI now runs the durable Python suite plus `npm run typecheck` for mobile.
- Do not mark M4 validated complete until those checks are green.

## Milestone 5 — Git / PR Completion

**Status: NOT STARTED**

Left:
- Structured final commits/rebase policy.
- Base drift/conflict handling.
- PR creation/linkage.
- CI/check state ingestion.
- Mergeability and merge action.

## Milestone 6 — Unattended Reliability

**Status: PARTIAL FOUNDATION**

Already present:
- leases/heartbeats;
- process-loss recovery;
- controller restart independence;
- attempt history and checkpoints;
- implementation retry after deterministic verification failure.

Left:
- enforce cost/runtime/iteration/subagent budgets;
- job-level provider retry/watchdogs;
- state-driven notifications;
- failure-injection harness;
- Friday Five benchmark/release gate.

## Trust rule

`READY_FOR_REVIEW` is never set because an agent says it is done. It requires a
persisted deterministic verification run with all configured checks passing and
a clean isolated job worktree. Missing or failed evidence blocks readiness.
