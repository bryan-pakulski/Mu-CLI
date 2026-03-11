# Agent Harness Implementation Plan

This plan operationalizes `agent_harness_architecture.md` into phased delivery so we can implement safely while preserving service quality.

## Goals

1. Support long-running, complex tasks with robust retry/replan behavior.
2. Prevent early model tap-out by enforcing a completion contract and corrective nudges.
3. Build a scalable context system that remains effective on large repos.
4. Improve observability, safety, and production operability.

## Delivery Principles

- **Ship in thin vertical slices** with feature flags.
- **Measure before/after** for every phase.
- **Backwards-compatible schema changes** only.
- **Default-safe behavior** (timeouts, hard limits, approval controls).

## Phase 0 — Baseline & Instrumentation (1 sprint)

### Scope
- Define canonical job lifecycle enums and transition validation.
- Add missing counters for retries, replans, stalls, verification failures.
- Introduce dashboards for baseline p50/p95 runtime, completion %, timeout %, verifier gap rate.

### Implementation tasks
- Add typed job-state transition helper + guardrail tests.
- Expand event taxonomy doc and ensure every event has stable shape.
- Add lightweight runtime health endpoint for queue depth/job backlog.

### Exit criteria
- Every background job transition is validated and test-covered.
- We can answer: *why did this job fail or stop?* from events only.

## Phase 1 — Reliable Long-Running Loop (1–2 sprints)

### Scope
- Harden iterative loop behavior (nudge/replan/stall recovery).
- Add adaptive budgets (time + token + tool-call ceilings).
- Keep bounded corrective retries with deterministic stop reasons.

### Implementation tasks
- Add `BudgetPolicy` object: `max_runtime_s`, `max_tokens`, `max_tool_calls`, `max_replans`.
- Add policy-driven retry matrix (stall, missing evidence, tool failure, parser failure).
- Add explicit terminal reasons:
  - `completed_satisfactory`
  - `completed_with_blockers`
  - `timed_out`
  - `budget_exhausted`
  - `killed`
  - `failed_unrecoverable`

### Exit criteria
- No infinite loops in adversarial tests.
- “Tap-out early” rate reduced against baseline.

## Phase 2 — Smart Context Memory System (2 sprints)

### Scope
- Implement 3-tier memory model:
  1. pinned instructions,
  2. active working memory,
  3. archived summaries.
- Add context budget allocator with deterministic trimming policy.

### Implementation tasks
- Introduce `ContextAssembler` module with budget-aware packing.
- Add summarization checkpoints per N turns and per major milestone.
- Persist compressed turn summaries keyed by job/session.
- Add per-message “importance” scoring signals (tool evidence, blockers, decisions).

### Exit criteria
- Prompt size remains within target budget on long jobs.
- Answer quality remains stable when transcript length grows.

## Phase 3 — Workspace Semantic Index v2 (2 sprints)

### Scope
- Evolve file index from static preview to reusable semantic retrieval substrate.
- Add change detection and selective re-indexing.

### Implementation tasks
- Add index metadata schema:
  - `path`, `fingerprint`, `size`, `language`, `purpose`, `symbols(optional)`, `updated_at`.
- Add file watcher / periodic scanner for changed files.
- Recompute purpose/symbols only for changed files.
- Add retrieval APIs:
  - search by filename/purpose,
  - nearest neighbors by query,
  - “expand to full file” controls.

### Exit criteria
- Incremental indexing cost grows with changed files, not repository size.
- New session startup can reason about repo structure without loading bulk files.

## Phase 4 — Verifier & Evidence Ledger (1 sprint)

### Scope
- Make verification first-class and durable.
- Separate generated narrative from machine-checkable evidence.

### Implementation tasks
- Add `EvidenceLedger` persisted per job:
  - command/tool run,
  - output digest,
  - pass/fail,
  - timestamp,
  - mapped required check.
- Finalizer must read ledger for contract satisfaction.
- Add policy presets by task type (bugfix/refactor/security/docs).

### Exit criteria
- “Verified” means evidence-backed, not string-matched.
- Missing checks are always explicit and explainable.

## Phase 5 — Production Hardening & Scale (2+ sprints)

### Scope
- Split API and worker execution paths.
- Make job execution resumable and idempotent.

### Implementation tasks
- Queue-backed workers + durable event store.
- Checkpointed execution state (resume after process crash).
- Distributed cancellation and lock lease renewal.
- SLOs + alerts for timeout spikes and verifier regressions.

### Exit criteria
- Worker crash does not lose in-flight job intent.
- Horizontal scaling improves throughput without correctness regressions.

## Cross-Cutting Workstreams

### Safety & Governance
- Enforce approval policy at tool boundary.
- Redact secrets in traces/events.
- Add allow/deny lists for mutating tools by environment.

### Developer Experience
- Add runbook for debugging stuck jobs.
- Add synthetic scenario suite (looping model, flaky tools, large repo, no-test repo).

### Testing Strategy
- Unit: state machine, budget policy, context assembler, index diffing.
- Integration: long-running jobs with forced stalls + replans.
- End-to-end: queue + worker + API + UI event stream.
- Regression gates on timeout %, satisfactory completion %, verifier coverage.

## Suggested Execution Order (first 6 weeks)

1. Phase 0 baseline + state transition guards.
2. Phase 1 loop reliability + budgets.
3. Phase 2 context assembler skeleton + archive summaries.
4. Phase 4 evidence ledger (minimal viable).
5. Phase 3 indexing v2 enhancements.
6. Stabilization pass + readiness review for Phase 5.

## Initial Backlog (ready to implement next)

- [ ] Introduce `JobTerminalReason` enum and wire to job payload.
- [ ] Add `BudgetPolicy` to runtime settings and enforcement in background loop.
- [ ] Implement `ContextAssembler` interface with pluggable packers.
- [ ] Add `EvidenceLedger` model and persistence.
- [ ] Add `workspace index refresh` endpoint for changed-files-only updates.
- [ ] Add observability panels for retries/replans/verifier gaps.
