# Agent Harness Rework Progress

This tracker records implemented workstreams so we can measure progress against the phased plan.

## Snapshot

- Last updated: 2026-03-11
- Current focus: **Phase 0 — Baseline & Instrumentation**

## Phase 0 checklist

- [x] Canonical job status enum + guarded transitions (`job_state.py`)
- [x] Background runner uses explicit status transitions and terminal reasons
- [x] Harness counters added (`telemetry.harness_counts`)
- [x] `/api/health` endpoint for backlog/status visibility
- [x] Event taxonomy documented (`agent_event_taxonomy.md`)
- [x] Guardrail tests for transition helper
- [ ] Dashboard views for p50/p95 runtime + verifier gap trend (pending UI/ops surface)

## Delivered artifacts

- Runtime/job state guard module: `agents/mu_cli/webapp/job_state.py`
- Health endpoint + telemetry extensions: `agents/mu_cli/web.py`, `agents/mu_cli/webapp/routes_state.py`
- Event taxonomy: `agents/docs/agent_event_taxonomy.md`
- Plan: `agents/docs/agent_harness_implementation_plan.md`

## Next up (Phase 0 completion)

1. Add basic metrics dashboard cards for runtime percentiles and verifier-gap rate.
2. Add regression assertions for event category prefixes and stream payload keys.
3. Lock API contract snapshots for `/api/health` and `/api/telemetry`.
