# Stage 3 Slice: Control Plane Foundations

This slice begins Stage 3 by introducing practical control-plane surfaces in the existing settings UI.

## Implemented

- **System prompt & rules manager (UI-managed, persisted locally)**
  - System prompt override text area.
  - Rules checklist text area.
  - Local persistence for iterative operator workflows.

- **Context budget manager (live estimate)**
  - Context footprint estimate by segment (messages, traces, uploads, tools, rules/system).
  - Running total with target threshold visualization.

- **Model behavior knobs (UI-managed)**
  - Temperature, top-p, tool eagerness, verbosity controls.
  - Live value labels and persistence.

## Notes

- This slice is intentionally backend-safe: control-plane values are persisted client-side and do not alter existing backend contracts.
- Next slice can map selected controls to backend/runtime settings where API support exists.


## Per-session overrides (new)

- Added Session Overrides editor in session options menu.
- Supports per-session runtime settings overrides (provider/model/approval/workspace/agentic/research/condense/runtime).
- Supports per-session control-plane text overrides (system prompt + rules) layered over global defaults.
- Global defaults remain lowest-priority and are editable from main Settings.


## Stage 3 continuation (this pass)

- Added skills preset save/apply/delete controls for lifecycle management in Settings.
- Added tools console analytics table (risk/calls/failures/latency estimate).
- Added context controls (exclude traces/uploads/tools and summarize rules action).
- Added prompt/rules versioning controls (save, rollback, delete).
- Added model behavior profiles (save/apply/delete).
