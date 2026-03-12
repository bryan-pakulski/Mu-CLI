# ADR 0003: Event Transport for Streaming

## Status
Accepted

## Context
CLI/GUI require low-latency live updates for tokens, job-state transitions, and logs.

## Decision
Use **WebSocket streaming** at `/stream/sessions/{id}` with a structured event envelope:
- `event_type`
- `session_id`
- `job_id`
- `payload`

In-process async pub/sub fanout is used initially; future external broker support remains possible.

## Consequences
- Pros: bi-directional channel ready for future interactive controls.
- Pros: event structure aligns with telemetry and audit records.
- Cons: multi-instance fanout requires shared pub/sub later.
- Mitigation: retain an internal event bus abstraction.
