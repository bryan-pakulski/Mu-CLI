# Agent Harness Event Taxonomy (Phase 0)

This document defines stable event categories and minimal payload semantics for the harness.

## Goals

- Provide consistent, parseable operational events.
- Make terminal outcomes explainable from event history.
- Support future dashboards and alerting without schema churn.

## Event channels

1. **Job events** (`job["events"]`): durable lifecycle and operator-readable facts.
2. **Stream events** (`job["stream_events"]`): incremental UI stream updates.
3. **Trace lines** (`runtime.traces`): debug detail and request/tool logs.
4. **Telemetry counters** (`telemetry.*_counts`): numeric rollups.

## Canonical job event forms

- `status: <value>`
  - Examples: `status: iteration_cap_reached (24)`, `status: killed (user requested stop)`
- `plan: <value>`
  - Examples: `plan: critic_passed`, `plan: replan_triggered #1`
- `verification: <value>`
  - Examples: `verification: passed`, `verification: gaps=tests,lint`
- `checkpoint: <value>`
  - Example: `checkpoint: restored 6`
- `tool-request: ...`
- `tool-run: ...`
- `cancel_requested: <reason>`

## Canonical stream event payloads

- `status`
  - keys: `status`, optional `reason`, optional `last_step`
- `assistant_chunk`
  - keys: `chunk`
- `thinking_chunk`
  - keys: `chunk`
- `trace`
  - keys: `line`
- `checkpoint`
  - keys: `checkpoint` object
- `assistant_message`
  - keys: `content`
- `error`
  - keys: `error`
- `done`
  - keys: `status`

## Status lifecycle alignment

Statuses are governed by `mu_cli.webapp.job_state`:

`queued -> planning -> awaiting_plan_approval|running -> verifying -> completed|failed|timed_out|killed`

Every accepted transition should also append to `status_transitions` for auditability.

## Telemetry counters (Phase 0)

`telemetry.harness_counts` currently tracks:

- `nudges`
- `stalls`
- `replans`
- `verification_failures`
- `iteration_caps`
- `unsatisfactory_limits`
- `failures`

## Compatibility guidance

- New event types should be additive.
- Existing keys should remain stable.
- Human-readable strings may evolve, but event category prefixes (`status:`, `plan:`, `verification:`) should not.
