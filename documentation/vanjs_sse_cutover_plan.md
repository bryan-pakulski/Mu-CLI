# VanJS + SSE Frontend Cutover Plan (Comprehensive)

## 1) Objective

Migrate Mu-CLI's GUI from the current imperative DOM code (`gui/app.js`) to a **fully reactive VanJS frontend** that preserves complete feature and visual parity while improving maintainability, testability, and incremental delivery safety.

### Success criteria

1. **Feature parity:** no regressions across chat, approvals, board, memory, settings, workspaces, sessions, and modal workflows.
2. **Visual parity:** layout, spacing, typography, interactions, and theme behavior match the current UI.
3. **Realtime parity:** all server events currently consumed via SSE continue to render correctly and with equal-or-better latency.
4. **Operational safety:** migration can be rolled out behind flags, with rapid fallback to current implementation.

---

## 2) Current-state snapshot

### Frontend

- `gui/index.html`: static shell with complete UI structure.
- `gui/app.js`: large imperative script handling state, rendering, API requests, SSE streams, and event wiring.
- `gui/styles.css`: full styling system for dark/light, board/chat, modals, and interaction details.

### Backend (unchanged in cutover)

- Python server endpoints remain source-of-truth:
  - Core data/flows: `/api/state`, `/api/runtime`, `/api/sessions`, `/api/history`, `/api/message`, `/api/tasks`, `/api/events`, `/api/approvals`, `/api/workspaces`, `/api/features`, etc.
- SSE endpoint: `/api/events` with optional `task_id` query.

### Constraints

- Keep Python stack as-is.
- Use locally served VanJS runtime.
- Avoid breaking existing user workflows during migration.

---

## 3) Target architecture

## 3.1 High-level modules

Proposed files:

- `gui/van/app-shell.js` — top-level shell composition and app bootstrap.
- `gui/van/state/store.js` — central reactive state (`van.state` / `van.derive`) and domain slices.
- `gui/van/services/api.js` — fetch wrapper, typed payload guards, timeout/retry policy.
- `gui/van/services/sse.js` — EventSource lifecycle manager + reconnect/backoff.
- `gui/van/domains/`:
  - `sessions.js`
  - `chat.js`
  - `approvals.js`
  - `board.js`
  - `workspaces.js`
  - `memory.js`
  - `settings.js`
- `gui/van/components/`:
  - shell panes, message list, composer, board lanes, modals, notifications, status badges.
- `gui/van/adapters/legacy-compat.js` — compatibility shims during phased migration.

## 3.2 State model

Use explicit domain slices:

- `runtimeState`
- `sessionState`
- `chatState`
- `taskState`
- `approvalState`
- `boardState`
- `workspaceState`
- `memoryState`
- `uiState` (modals, toasts, active tabs, theme)

Guidelines:

- Keep server payloads normalized (IDs and maps where useful).
- Make derived views (`van.derive`) for computed UI state (active feature, filtered board lanes, unread counters, etc.).
- Separate ephemeral UI state from persistent session/runtime state.

## 3.3 SSE event pipeline

1. Open EventSource with contextual scope:
   - global stream (`/api/events`) and task stream (`/api/events?task_id=...`) as needed.
2. Parse envelope:
   - `id`, `event`, `task_id`, `timestamp`, `payload`.
3. Route to domain reducers:
   - `task.updated` -> task/chat/board reducers.
   - approval events -> approval reducer.
   - feature/board events -> board reducer.
4. Apply state updates atomically (single reducer transaction per event where feasible).
5. UI auto-updates through VanJS reactive bindings.

Reconnect policy:

- Exponential backoff with jitter (bounded).
- Heartbeat timeout detection for stale streams.
- Preserve last known state; rehydrate with targeted API refresh on reconnect.

---

## 4) Visual and feature parity matrix

Each row must be verified before cutover:

1. **Sessions** — list/create/load/rename/delete, pending markers, cached drafts.
2. **Chat** — message rendering, markdown/code highlighting, attachments, send queue, stop/cancel behavior.
3. **Approvals** — approval bar, approve/reject/explain pathways.
4. **Board** — feature selector/actions, run controls, search/filter, lane rendering, ticket modal interactions.
5. **Workspace** — add/remove folders, navigator modal, status badges.
6. **Memory** — memory modal sections, filters, context layers, activity display.
7. **Settings** — runtime/tools/variables/memory/theme tabs and persistence.
8. **Theme** — dark/light + accent swatches/custom accent.
9. **Notifications** — top flash status behavior and error handling.
10. **Keyboard/UI affordances** — composer behavior, focus handling, modal close semantics.

Acceptance for each row:

- Functional parity ✅
- Visual parity ✅
- SSE behavior parity ✅
- Error-state handling parity ✅

---

## 5) Cutover strategy (phased)

Progress indicators:

- `[]` - not started
- `[~]` - in progress
- `[x]` - completed

Current phase tracking:

- `[x]` Phase 0 — Baseline hardening
- `[x]` Phase 1 — Infrastructure scaffold
- `[~]` Phase 2 — Reactive read-only shell
- `[]` Phase 3 — Chat + SSE cutover
- `[]` Phase 4 — Board + feature workflows
- `[]` Phase 5 — Settings/workspace/memory migration
- `[]` Phase 6 — Legacy removal and cleanup

## [x] Phase 0 — Baseline hardening (now)

- Freeze legacy behavior with regression checklists.
- Add telemetry hooks for critical flows (send message, task complete/error, approval resolve, board refresh).
- Define fixture payloads for API/SSE contract tests.

Deliverables:

- Parity checklist doc
- Event contract fixtures
- Manual smoke script
- Captured artifacts:
  - `documentation/van_phase0_baseline_checklist.md`
  - `documentation/fixtures/sse_event_samples.json`
  - `documentation/fixtures/api_response_samples.json`

Exit criteria:

- Baseline parity checklist created and reviewed with owners.
- Critical user journeys documented with pass/fail checkpoints.
- SSE/API fixtures captured for current production behavior.

## [x] Phase 1 — Infrastructure scaffold

- Add VanJS runtime + module loader.
- Implement `store.js`, `api.js`, `sse.js` skeletons.
- Introduce feature flag:
  - `window.__MUCLI_USE_VAN__` or URL flag `?ui=van`.

Deliverables:

- Side-by-side bootstrap support
- No user-visible behavioral changes by default

Exit criteria:

- Feature-flagged bootstrap path merged and verified.
- VanJS runtime loads locally with no CDN runtime dependency.
- Legacy default path remains stable (no regressions in smoke checks).

## [~] Phase 2 — Reactive read-only shell

- Render read-only VanJS shell from API snapshots (sessions/history/runtime).
- Keep legacy UI active for write actions.
- Verify visual parity in static states.
- Current progress:
  - Read-only sessions/chat rendering is active in `?ui=van`.
  - Added read-only activity and feature snapshots (tasks, approvals, features).
  - SSE-triggered refresh updates snapshots on task/approval events.

Deliverables:

- VanJS shell with parity snapshots
- Screenshot diff baseline

Exit criteria:

- Read-only VanJS shell renders sessions/history/runtime status from live API.
- SSE connection status and latest events are visible in Van shell.
- Visual review confirms no regressions on legacy default path.

## [] Phase 3 — Chat + SSE cutover

- Move chat composer, feed, task status, and approvals to VanJS.
- Route send/cancel and task event handling through VanJS store.
- Keep board/settings/workspace/memory on legacy implementation.

Deliverables:

- Stable chat path in VanJS
- SSE reconnect and error recovery validated

Exit criteria:

- Message compose/send/cancel works in Van path with parity behavior.
- Task lifecycle states (`started`, `updated`, `completed`, `error`) render correctly.
- Approval flows (approve/reject/explain) complete successfully in Van path.

## [] Phase 4 — Board + feature workflows

- Migrate feature board controls, filters, lanes, and ticket modal to VanJS.
- Preserve drag/drop semantics and task update reactions.

Deliverables:

- Board parity complete

Exit criteria:

- Board columns, filters, task cards, and ticket modal reach parity.
- Feature actions (activate/archive/delete/unload) behave identically to legacy.
- Board updates correctly from SSE and manual refresh paths.

## [] Phase 5 — Settings/workspace/memory migration

- Migrate remaining modal-heavy and config-heavy surfaces.
- Ensure theme/accent behavior fully preserved.

Deliverables:

- Full parity on all core domains

Exit criteria:

- Settings tabs (runtime/tools/variables/memory/theme) function with parity.
- Workspace attach/remove/browser flows match legacy behavior.
- Memory inspector and context-layer presentation reach parity.

## [] Phase 6 — Legacy removal and cleanup

- Remove legacy imperative render paths once parity tests pass.
- Retain compatibility adapter for one release window, then remove.

Deliverables:

- Simplified codebase
- Updated architecture docs

Exit criteria:

- Legacy imperative rendering path removed or archived behind explicit fallback.
- Van path is default for all users with no P1/P2 regressions in release window.
- Documentation and runbooks updated to the final architecture.

---

## 6) Testing strategy

## 6.1 Automated

1. **Contract tests (API/SSE):**
   - Validate expected payload shapes from fixtures.
2. **State reducer tests:**
   - Domain reducers for task/approval/board/session updates.
3. **Component tests:**
   - Render behavior for critical components under state permutations.
4. **E2E smoke tests:**
   - session switch, send message, await completion, approvals, board run, setting save.

## 6.2 Manual parity QA

- Side-by-side legacy vs Van UI script with deterministic scenarios.
- Verify timing-sensitive behavior:
  - thinking placeholders
  - status updates
  - board refresh races
  - approval interleaving with task updates

## 6.3 Performance

- Measure:
  - initial render time
  - message append latency
  - SSE event-to-paint latency
  - memory growth after long session usage

---

## 7) Rollout and risk management

## Rollout

1. Internal flag-only.
2. Limited cohort opt-in (`?ui=van`).
3. Default-on with fallback toggle retained.
4. Remove fallback after stability window.

## Fallback

- Hard switch to legacy UI entrypoint at boot-time.
- Soft fallback if Van bootstrap fails (catch top-level module errors and load legacy script path).

## Risks and mitigations

- **Event ordering bugs:** central reducer ordering + monotonic event timestamp checks.
- **State divergence from server:** periodic re-sync on reconnect and after critical mutations.
- **Visual drift:** screenshot diff gates and parity checklist signoff.
- **Scope creep:** phase gates with strict “done” criteria.

---

## 8) Implementation standards

- Keep domain logic out of components where possible.
- No direct DOM mutation outside Van component boundaries except legacy interoperability adapters during migration.
- All server calls through shared API service.
- All SSE handling through shared stream service.
- Add inline docs for non-obvious transitions and race handling.

---

## 9) Proposed timeline (example)

- Week 1: Phase 0-1
- Week 2: Phase 2
- Week 3: Phase 3
- Week 4: Phase 4
- Week 5: Phase 5
- Week 6: Phase 6 + cleanup

Adjust based on bug load and QA capacity.

---

## 10) Definition of done

Cutover is complete when:

1. All parity matrix rows are signed off.
2. Automated contract/reducer/e2e suites pass in CI.
3. No P1/P2 regressions for one release window.
4. Legacy UI code path is removed or fully deprecated with documented fallback policy.
