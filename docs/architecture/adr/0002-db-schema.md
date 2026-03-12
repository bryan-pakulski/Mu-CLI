# ADR 0002: Database Schema and Persistence Strategy

## Status
Accepted

## Context
Mu-CLI requires durable session/job state and auditable events. The design calls for PostgreSQL in production while supporting easy local startup.

## Decision
Adopt SQLAlchemy 2.0 models with relational entities:
- `sessions`
- `jobs`
- `events`

Use configurable DB URL:
- default local: SQLite (`sqlite+aiosqlite`)
- production target: PostgreSQL

## Consequences
- Pros: durable resume state and queryable history from day one.
- Pros: schema maps directly to design data model and lifecycle reporting.
- Cons: SQLite JSON/type semantics differ slightly from PostgreSQL.
- Mitigation: keep JSON fields generic and avoid DB-specific SQL in core logic.
