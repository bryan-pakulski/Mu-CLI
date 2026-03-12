# ADR 0001: Worker Model

## Status
Accepted

## Context
Mu-CLI needs long-running job execution, resumability, and streaming events while remaining simple for local-first development.

## Decision
Use an **asyncio-native in-process worker model** for initial delivery, with a clean abstraction boundary to allow migration to external queues/workers later.

## Consequences
- Pros: low setup complexity, fast local iteration, no mandatory broker in MVP.
- Pros: straightforward shared-memory event fanout for WebSocket streaming.
- Cons: horizontal scaling limitations in single-process mode.
- Mitigation: job orchestration APIs and state transitions are persistence-backed so the transport can be swapped in later.
