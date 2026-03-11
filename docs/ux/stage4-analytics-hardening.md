# Stage 4 Slice: Analytics + Hardening + Rollout

This slice begins Stage 4 by instrumenting a telemetry dashboard and adding regression coverage for the upgraded UX/control-plane flows.

## Implemented in this pass

- **Telemetry dashboard instrumentation (UI + runtime)**
  - Added runtime telemetry snapshot data to `/api/state` and `/api/telemetry`.
  - Added request/action counters and runtime health summary fields (uptime, request totals, tool-failure estimate, approval-wait estimate, background-job outcomes).
  - Added telemetry cards + action-count table to the Metrics modal.

- **Telemetry persistence + reset semantics**
  - Telemetry counters persist in `.mu_cli/telemetry.json`.
  - `Clear all stored data` now also resets persisted telemetry to defaults.

- **Regression test expansion**
  - Added tests that assert telemetry appears in state payload.
  - Added tests that action counters increment for key flows (`chat_turn`, `session_clear`).

## UX sign-off targets (initial)

These are baseline targets for Stage 4 sign-off; they can be tightened after real usage telemetry accrues.

- **Responsiveness**
  - State refresh (`GET /api/state`) under typical local runs should complete in under **500ms p95**.
  - Settings write (`POST /api/settings`) should complete in under **500ms p95** for non-networked operations.

- **Reliability**
  - No server exceptions in critical flows: chat turn, session switch/clear, uploads clear, and clear-all action.
  - Telemetry counters and dashboard values remain available after restart.

- **Operator confidence**
  - Metrics modal shows both usage analytics and runtime telemetry in one place.
  - Clear-all action provides full reset behavior without leaving stale dashboard data.

## Deferred in this pass

- Feature-flag framework and phased rollout path (explicitly deferred per request).
