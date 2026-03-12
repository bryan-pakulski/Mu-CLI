# ADR 0005: Policy Engine and Approval Gate Model

## Status
Accepted

## Context
Phase 3 requires dynamic tool usage with safety controls. Risky actions must be governable by policy and explicit approvals.

## Decision
Implement a policy engine that evaluates tool invocations and returns one of:
- `allow`
- `ask`
- `deny`

When `ask` is returned:
- persist an approval request record,
- move job state to `awaiting_approval`,
- unblock only after an explicit `approved` decision,
- transition to `blocked` on deny/timeout.

## Consequences
- Pros: auditable and explicit approval workflow.
- Pros: foundation for CLI/GUI intervention features.
- Cons: introduces wait states and timeout edge cases.
- Mitigation: emit structured events for approval lifecycle and expose approval APIs.
