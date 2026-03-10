# Stage 1 Slice 01: Shell and Interaction Foundation

This slice starts Stage 1 with low-risk, UI-only changes that improve information hierarchy and operator ergonomics.

## Scope

- Introduce surface-mode tabs (`Operate`, `Control`, `Review`) in the top bar.
- Add IA-oriented zone labels to major shell regions (navigate/scope, operate, inspect).
- Establish first-pass design tokens for spacing and typography.
- Add keyboard shortcut infrastructure for high-frequency shell actions.

## Delivered behaviors

1. Surface tabs persist the selected mode in local storage.
2. `Control` surface ensures the left sidebar is visible.
3. `Review` surface ensures metadata rail visibility for inspection-first workflows.
4. Keyboard shortcuts:
   - `Ctrl/Cmd+B`: toggle sidebar
   - `Ctrl/Cmd+\`: toggle metadata rail
   - `Ctrl/Cmd+,`: open settings modal

## Non-goals (for this slice)

- No backend or API contract changes.
- No data model migration.
- No behavior changes to chat/tool/session runtimes.

## Follow-up slices

- Introduce tokenized component primitives (cards/tabs/states) across remaining controls.
- Add panel resize persistence and per-surface layout memory.
- Add dedicated review drawer patterns for diff/code workflows.
