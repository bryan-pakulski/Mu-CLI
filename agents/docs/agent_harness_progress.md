# Agent Harness Rework Progress

This tracker records implemented workstreams so we can measure progress against the phased plan.

## Snapshot

- Last updated: 2026-03-11
- Current focus: **Phase 1 — Reliable Long-Running Loop**

## Phase 0 checklist

- [x] Canonical job status enum + guarded transitions (`job_state.py`)
- [x] Background runner uses explicit status transitions and terminal reasons
- [x] Harness counters added (`telemetry.harness_counts`)
- [x] `/api/health` endpoint for backlog/status visibility
- [x] Event taxonomy documented (`agent_event_taxonomy.md`)
- [x] Guardrail tests for transition helper
- [x] Dashboard-ready telemetry metrics for p50/p95 runtime + verifier gap trend

## Delivered artifacts

- Runtime/job state guard module: `agents/mu_cli/webapp/job_state.py`
- Health endpoint + telemetry extensions: `agents/mu_cli/web.py`, `agents/mu_cli/webapp/routes_state.py`
- Event taxonomy: `agents/docs/agent_event_taxonomy.md`
- Plan: `agents/docs/agent_harness_implementation_plan.md`

## Phase 0 Status

Phase 0 is now functionally complete from a backend/runtime perspective (state lifecycle guards, health surface, telemetry counters, event taxonomy, and progress tracking).

## Next up

1. Begin Phase 1: add explicit `BudgetPolicy` object and enforce token/tool-call budgets.
2. Add policy-driven retry matrix and terminal reasons for budget exhaustion.
3. Add UI dashboard cards that consume the new telemetry metrics.


## Phase 1 early progress

- [x] Introduced `BudgetPolicy` model for runtime/token/tool/replan limits.
- [x] Wired budget policy into background job payloads (`budget_policy`) for visibility.
- [x] Enforced token/tool-call budget exhaustion with explicit terminal reason `budget_exhausted`.
- [x] Added tests for budget policy helper and job budget-policy exposure.

- [x] Added policy-driven retry controls for stall/missing-evidence/tool-failure triggers.
