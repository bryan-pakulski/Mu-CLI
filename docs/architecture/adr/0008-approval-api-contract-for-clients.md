# ADR 0008: Approval API Contract for CLI/GUI Clients

## Status
Accepted

## Context
Phase 3 requires operational approval workflows consumable by both CLI and GUI before dedicated client implementations are complete.

## Decision
Define server-side approval contract with endpoints for:
- listing pending approvals at session scope,
- listing approvals per job,
- submitting approval decisions (`approved` / `denied`).

All approval lifecycle transitions emit structured events for streaming subscribers.

## Consequences
- Pros: unblocks client development with stable API primitives.
- Pros: preserves auditability and consistent state transitions.
- Cons: polling-based client behavior may be needed initially.
- Mitigation: maintain websocket stream events as first-class path for live UX.
