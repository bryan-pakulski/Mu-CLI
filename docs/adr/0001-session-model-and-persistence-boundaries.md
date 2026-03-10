# 0001 - Session model and persistence boundaries

- Status: accepted
- Date: 2026-03-10

## Context

The web runtime can mutate provider/model/session/workspace state through multiple endpoints. Without explicit boundaries, behavioral drift appears between `/api/session`, `/api/settings`, and chat execution flows.

## Decision

- Treat a session as the authoritative boundary for:
  - message history
  - usage counters
  - uploaded context metadata
  - research artifacts
  - session-level feature toggles (`agentic_planning`, `research_mode`, runtime limits, condense settings, enabled skills)
- Persist session state exclusively through centralized runtime/session helpers (`_load_session`, `_persist`, and runtime mutation services).
- Route handlers must not implement bespoke session reset/new/load logic directly; they delegate to shared mutation helpers.

## Consequences

### Positive
- Fewer inconsistencies between `new`, `clear`, `load`, `switch`, and stream/background flows.
- Easier to reason about correctness and persistence behavior.
- Lower regression risk when changing session schema.

### Trade-offs
- Service helper layer becomes a key dependency and must be well-tested.
