# ADR 0004: Provider Routing and Fallback Strategy

## Status
Accepted

## Context
Mu-CLI must be provider-agnostic while prioritizing Ollama. Runtime reliability cannot depend on a single provider endpoint always being reachable.

## Decision
Introduce a provider router that:
- receives ordered provider preferences per session,
- retries each provider a bounded number of times,
- falls back to the next provider deterministically,
- records attempted providers and errors in failure paths.

A lightweight `mock` provider is included for validation and local fallback.

## Consequences
- Pros: resilient execution when preferred provider is unavailable.
- Pros: deterministic behavior and traceable routing decisions.
- Cons: fallback output quality can vary across providers.
- Mitigation: keep provider order explicit and configurable at session level.
