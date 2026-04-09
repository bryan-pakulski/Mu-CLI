# VanJS Phase 2 Formal Pass Record

## Dataset/Evidence context

This pass uses a non-empty UI state (multiple sessions, populated chat count, and active feature snapshot) based on reviewer-provided captures and live endpoint snapshots.

## Toggle regression pass (legacy <-> ?ui=van)

### Procedure

1. Open legacy GUI (`/`).
2. Validate core zones: sessions, chat, activity panel.
3. Open Van preview (`/?ui=van`).
4. Validate read-only zones: sessions, chat, feature snapshot, activity snapshot.
5. Toggle back to legacy (`/`) and verify legacy still renders correctly.

### Result

- [x] Legacy loads via bootstrap default path.
- [x] Van preview loads via `?ui=van` path.
- [x] Returning to legacy after Van preview keeps legacy UI functional.

## Read-only parity checkpoints

- [x] Sessions list renders with non-zero count and selectable items.
- [x] Chat count and message blocks render when history exists.
- [x] Feature selector and lane groups render in feature snapshot.
- [x] Activity snapshot renders task/approval/runtime/workspace summaries.

## Artifacts

- Screenshot baseline record: `documentation/van_phase2_screenshot_diff_baseline.md`
- Parity walkthrough: `documentation/van_phase2_parity_walkthrough.md`
- CI screenshot diff workflow: `.github/workflows/van-ui-diff.yml`
- Diff script: `tools/van_ui_screenshot_diff.mjs`
