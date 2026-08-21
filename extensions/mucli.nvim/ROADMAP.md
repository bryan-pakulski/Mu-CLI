# MUCLI Neovim direction

The extension should feel like part of the editing loop, not a chat window that
happens to run inside Neovim. The default path must preserve focus, make agent
inputs visible, and keep every mutation reviewable and reversible.

## Product principles

1. **The editor is the source of truth.** Unsaved buffers, the last-focused
   editor window, its exact viewport, and its cursor beat older source mentioned
   in conversation.
2. **Context has an explicit lifetime.** Live state is recomputed, turn context
   is consumed after acceptance, pins persist intentionally, and raw source is
   never conversation memory.
3. **Never steal the developer's train of thought.** Drafts survive failures,
   long work is asynchronous, results appear where the code already is, and
   navigation returns to the originating window.
4. **Show before acting.** The user can inspect context, see tool activity, and
   review edits at hunk granularity. Conflicts stop the edit instead of silently
   replacing newer buffer text.
5. **Keyboard-first, integration-friendly.** Core workflows use native Neovim
   primitives; Telescope, snacks.nvim, and fzf-lua can enhance discovery without
   becoming hard dependencies.

## Foundation now in place

- Structured Context v2 transport with content-free conversation receipts.
- Exact live viewport and cursor from the last-focused real editor window.
- Multiple extmark-backed selections across buffers, split into turn and pinned
  lifetimes, with stale/unsaved/truncated state surfaced in a context drawer.
- Deterministic budgeting that protects turn context and current viewport before
  older pins, plus a local exact-payload inspector.
- Clean retry behavior: a failed request keeps its draft and turn context but
  removes the failed optimistic echo.
- Provider tools for live buffers, all context items, diagnostics, symbols,
  navigation, and changedtick-guarded edits; source-bearing observations expire
  to metadata-only receipts after the active turn.

## Build next

### P0 — Editing and task-flow loop

1. **Transactional, hunk-level edits**
   - Define a structured edit-set protocol with file hashes and changedticks.
   - Render inline or tabbed diffs with accept/reject per hunk, accept file, and
     accept all actions.
   - Rebase clean hunks after buffer changes; block and explain real conflicts.
   - Keep a single native undo transaction for each accepted edit set.

2. **Fresh task, follow-up, and branch semantics**
   - Make the composer mode visible: `FOLLOW UP`, `FRESH`, or `BRANCH`.
   - Follow-up keeps conversational intent; Fresh starts with only system state
     and the current Context v2 payload; Branch forks the current thread.
   - Retain drafts and explicit pins across a branch only when the user chooses.
   - Add `:MucliAskFresh`, thread picker, and a fast keyboard toggle.

3. **Context expansion without manual hunting**
   - Add actions for current Tree-sitter/LSP symbol, definition, references,
     callers, implementations, imports, git hunk, quickfix entries, and test
     failures.
   - Preview the expansion as a context graph before send, with one-key removal.
   - De-duplicate overlapping snippets and explain why every item was included.

4. **Steerable, non-blocking turns**
   - Let the composer accept a queued follow-up while an agent is working.
   - Support steer, stop-and-replace, and run-next semantics without losing text.
   - Show a compact queue and preserve one draft per thread/workspace.

### P1 — Intelligence where the code lives

5. **Inline change annotations**
   - Show proposed insertions/deletions as extmarks near the affected lines.
   - Provide next/previous change, explain this hunk, revise this hunk, and apply
     actions without forcing a switch to the chat dock.

6. **Test and diagnostic feedback loop**
   - Run project-aware tests through Neovim jobs, stream progress, parse failures
     into quickfix/diagnostics, and offer a focused fix turn with those failures
     as turn context.
   - Re-run only impacted tests after an accepted edit, with full-suite opt-in.

7. **Agent activity and context trace**
   - Add a quiet activity drawer showing plan, active tool, files read, context
     revision, elapsed time, and pending approval.
   - Make every read/navigation/edit jumpable while keeping verbose traces folded.

8. **Opt-in ambient hints and completion**
   - Debounce on idle, cancel on buffer change, cache by changedtick, and refuse
     stale results.
   - Rank a small number of high-confidence hints; never stream noise into normal
     typing or send source without a visible privacy setting.

9. **Workspace threads**
   - Add fast thread create/switch/archive, per-thread drafts, meaningful titles,
     and clear busy/unread indicators.
   - Keep workspace pins separate from thread pins and make inheritance explicit.

### P2 — Scale, extensibility, and polish

10. **Persistent named context sets** using paths, symbols, and anchors rather
    than stored raw source; re-resolve and mark drift when a workspace reopens.
11. **Semantic context ranking** with symbol relationships, git state, recency,
    overlap de-duplication, per-model token estimates, and user-controlled budget
    lanes.
12. **Extension API and optional pickers** for custom actions, context providers,
    renderers, Telescope, snacks.nvim, and fzf-lua.
13. **Resilience and performance** for large files, binary/generated files,
    reconnects, session ownership changes, cancellation races, and multi-client
    workspaces.
14. **Accessibility and theming** with highlight-only color integration, narrow
    layouts, screen-reader-friendly labels, configurable icons, and no required
    Nerd Font.

## Definition of a first-class workflow

A developer can select several snippets in different files, inspect exactly what
will be sent, ask either a fresh question or a follow-up, continue editing while
the agent works, review only the affected hunks, accept a subset, undo the result
natively, and re-run the relevant tests—without losing their draft, focus, cursor,
or understanding of what the agent saw.
