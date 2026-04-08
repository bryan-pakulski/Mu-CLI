# VanJS Phase 0 Baseline Checklist

This document records Phase 0 hardening artifacts for the VanJS cutover.

## 1) Legacy parity checklist (baseline)

- [x] Sessions lifecycle (list/create/load/rename/delete)
- [x] Chat read/render and message send lifecycle
- [x] Approval bar and resolve paths
- [x] Board summary/filters/lanes/ticket modal visibility
- [x] Workspace attach/remove/status rendering
- [x] Memory modal visibility and core sections
- [x] Settings tabs and theme controls visible
- [x] SSE connection status surfaced in UI

## 2) Critical journeys with pass/fail checkpoints

### Journey A — Send + complete task

1. Open session.
2. Send prompt.
3. Observe pending state.
4. Observe task completion.
5. Confirm assistant response rendered.

Pass criteria:
- Task transitions through expected status states.
- Final answer appears in chat and history refresh remains consistent.

### Journey B — Approval resolve path

1. Trigger action requiring approval.
2. Observe approval banner.
3. Approve/reject/explain.
4. Confirm server acknowledgement + UI update.

Pass criteria:
- Approval bar updates and clears correctly.
- Follow-on task behavior matches decision.

### Journey C — Board refresh + feature actions

1. Switch to board mode.
2. Refresh board.
3. Activate/unload/archive feature.
4. Open ticket detail.

Pass criteria:
- Board columns and counters update.
- Feature action state changes are reflected in UI.

## 3) Fixture capture references

- SSE fixture set: `documentation/fixtures/sse_event_samples.json`
- API fixture set: `documentation/fixtures/api_response_samples.json`

## 4) Phase-0 exit checkpoint

- [x] Baseline parity checklist captured.
- [x] Critical journeys documented.
- [x] SSE/API fixture examples captured.
