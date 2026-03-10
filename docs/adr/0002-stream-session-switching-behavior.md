# 0002 - Stream session switching behavior

- Status: accepted
- Date: 2026-03-10

## Context

`/api/chat/stream` can receive a target `session`. Historically this area was ambiguous and could run against whichever session happened to be active in runtime.

## Decision

- `/api/chat/stream` accepts an optional `session` field.
- If provided and different from active session, runtime loads/switches to that session before executing streamed turn.
- If omitted, stream runs in current active session.
- This mirrors `/api/chat/background` behavior to keep execution semantics consistent.

## Consequences

### Positive
- Deterministic stream targeting for multi-session UI usage.
- Better user expectation alignment with selected session in GUI.

### Trade-offs
- Stream execution can mutate active runtime session, which may surprise callers that assume isolation.
- Requires explicit test coverage for session-targeted streaming behavior.
