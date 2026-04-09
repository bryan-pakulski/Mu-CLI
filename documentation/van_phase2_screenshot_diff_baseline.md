# VanJS Phase 2 Screenshot-Diff Baseline

## Scope

Baseline comparison between:

- **Legacy GUI (default)**
- **VanJS preview (`?ui=van`)**

## Captured baseline artifacts

This baseline was created from provided capture references in review:

1. VanJS preview current-state capture (sessions/chat/feature/activity snapshot layout)
2. Legacy GUI capture (full chat + activity + workspace + controls)

> Note: automated image diff tooling is planned but not yet wired in CI. This file records the manual baseline checkpoints that image-diff automation will validate.

## Visual checkpoints

- [x] Top header row present in both variants
- [x] Session list appears in left rail/column
- [x] Central chat surface exists
- [x] Right-side activity context exists
- [x] Feature/board read-only snapshot exists in Van preview
- [ ] Exact typography/spacing parity
- [ ] Exact control density parity
- [ ] Modal parity in Van preview

## Known deltas (expected in Phase 2)

- Van preview intentionally read-only and does not include full modal/control matrix.
- Legacy includes full composer/board modal workflows; Van preview surfaces summarized snapshots.
- Activity stream detail depth is lower in Van preview than legacy.

## Exit criteria tie-in

To complete Phase 2, screenshot-diff automation should assert:

1. Core layout zones remain stable across refreshes.
2. Read-only lists remain visible and non-empty when source data exists.
3. No catastrophic visual regressions (missing columns/overflow collapse).
