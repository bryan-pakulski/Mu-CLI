# VanJS Phase 2 Read-Only Parity Walkthrough

## Goal

Document read-only parity checks between legacy GUI and `?ui=van` preview.

## Environment

- API base: `http://127.0.0.1:8765`
- GUI base: `http://127.0.0.1:4173`
- Van mode: `http://127.0.0.1:4173/?ui=van`

## Surface-by-surface walkthrough

### 1) Sessions snapshot

Checks:

- Session count shown
- Session items render and can switch history context
- Selected session highlight updates

Status: **Pass (read-only)**

### 2) Chat snapshot

Checks:

- Message count shown
- Messages render role + content blocks
- Empty-state copy appears when no history

Status: **Pass (read-only)**

### 3) Feature snapshot

Checks:

- Feature count shown
- Feature selector populated
- Feature-plan lanes grouped by status

Status: **Pass (read-only)**

### 4) Activity snapshot

Checks:

- Task/approval counts shown
- Runtime/workspace/staged summaries shown
- Empty-state copy appears when no tasks/approvals

Status: **Pass (read-only)**

### 5) Settings/runtime snapshot scope

Checks:

- Runtime model surfaced in Van activity panel
- Full settings panel parity intentionally deferred to later phases

Status: **Partial (expected for Phase 2)**

## Async stability validation

Phase 2 includes a built-in “Validate Lists” action in Van header that runs repeated refresh cycles and checks rendered list counts against state.

Expected result:

- `Validation: passed (N refresh cycles)`

## Remaining before Phase 2 close

- Add automated screenshot-diff tooling output artifacts.
- Run formal parity walkthrough against a non-empty feature/task dataset and store evidence.
- Confirm no regressions in legacy default path when toggling in/out of `?ui=van`.
