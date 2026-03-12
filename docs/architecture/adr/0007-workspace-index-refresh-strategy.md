# ADR 0007: Workspace Index Refresh Strategy

## Status
Accepted

## Context
Phase 3.5 requires workspace mapping that stays fresh without expensive full re-indexes on every operation.

## Decision
Adopt a dual indexing model:
- Full build endpoint for initial indexing.
- Incremental refresh endpoint that computes add/update/remove deltas using path+hash comparison.

Index records include a priority score used for default query ordering so agents get high-signal files first.

## Consequences
- Pros: fast follow-up refresh cycles and lower runtime overhead.
- Pros: deterministic delta accounting for observability and debugging.
- Cons: hash/mtime-only change detection is coarse for some edge cases.
- Mitigation: allow periodic full rebuilds and later upgrade to content-aware fingerprinting.
