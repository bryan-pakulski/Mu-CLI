# Production Agent Harness Architecture (Long-Running + Context-Safe)

This document defines a production-ready harness architecture for long-running, complex AI task execution.

## 1) Core Runtime Model

- **Job Orchestrator**: Owns job lifecycle, budgets, retries, and cancellation.
- **Planner/Critic loop**: Creates and revises plans; validates progress against acceptance contract.
- **Executor loop**: Runs tool actions, captures evidence, and updates checkpoints.
- **Verifier**: Enforces required checks (tests/lint/type/security/targeted validation).
- **Finalizer**: Requires explicit completion contract before marking a job done.

### Job state machine

`queued -> planning -> awaiting_plan_approval|running -> verifying -> completed|failed|timed_out|killed`

## 2) Long-Running Reliability Controls

- **Adaptive iteration budget** per job (time + token + tool-run budgets).
- **Satisfaction nudges** when response quality is insufficient.
- **Bounded corrective attempts** to avoid infinite loops.
- **Stall detection** (repeated/empty outputs or no tool progress).
- **Replan path** with explicit event trail.

## 3) Context Window Safety

- Keep full transcript in durable storage, but only send a **working set** to model.
- Maintain three context tiers:
  1. **Pinned instructions** (system, policy, task contract)
  2. **Active working memory** (current plan, latest evidence, unresolved blockers)
  3. **Archive summaries** (older turns, compressed)
- Trim internal automation chatter aggressively and preserve only the latest loop window.

## 4) Smart Workspace Context Index

Use a persistent workspace index to avoid injecting large raw codebases into prompt context.

### Index contents per file

- Relative path
- Size, hash/fingerprint
- Short preview
- Generated **purpose description**
- Last-updated timestamp / version marker

### Update strategy

- Reuse descriptions when fingerprints are unchanged.
- Recompute descriptions only for modified files.
- Expose index via tools for low-cost retrieval and targeted file expansion.

## 5) Evidence-First Completion Contract

A job is satisfactory only if all required parts are present:

- Explicit completion marker (`PLAN_COMPLETE`)
- `Confidence:` section
- `Evidence:` section tied to tool outputs
- Verification evidence for required checks, or explicit blockers

## 6) Observability and Operations

- Structured event stream (`tool-request`, `tool-run`, checkpoints, verification, nudges)
- Job metrics: runtime, token usage, tool success rate, retries, stalls
- Trace retention policy with rollups for analytics
- Alerting for repeated failures, timeout spikes, verifier gaps

## 7) Safety and Governance

- Approval policy modes (`auto`, `manual`, `deny`) with auditable decisions
- Tool reliability scoring and dynamic guidance
- Secrets/redaction safeguards in logs and prompts
- Hard limits for runtime and side-effecting tools

## 8) Horizontal Scale Design

- Split API from workers (queue-based execution)
- Durable job/event store
- Idempotent worker execution for retries
- Distributed lock/cancellation signaling

## 9) Multi-Agent Evolution (Optional)

- Specialist agents (planner, coder, tester, reviewer)
- Shared evidence ledger + retrieval index
- Arbiter agent for final acceptance decision

## 10) Near-Term Implementation Checklist

1. Harden state machine transitions and persistence boundaries.
2. Enforce satisfaction contract before completion.
3. Keep internal loop context bounded.
4. Expand workspace index to purpose-aware retrieval with incremental refresh.
5. Add reliability dashboards and SLOs.
## Implementation Plan

For phased execution details, see `agents/docs/agent_harness_implementation_plan.md`.

