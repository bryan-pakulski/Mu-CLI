# Per-Mode GUI Panels — Implementation Plan

## Context

`teacher` mode is the only mode with a dedicated GUI side-panel today. The pattern works well: chat stays in the center, mode-specific state and actions live in a panel that only shows when `$store.mode.active === '<mode>'`. The user gets a single coherent surface — chat for the conversation, panel for the structured state the agent is mutating.

This plan extends that pattern to the remaining six modes (`default`, `debug`, `feature`, `research`, `loop`, `security`) so each one has a thoughtfully-designed surface that matches how it's actually used.

Goal: **surface state + the actions that mutate it**, not raw JSON dumps. The TUI already exposes the data; the GUI should make it navigable.

## Architecture pattern (reusable)

Every mode panel follows the same shape, established by teacher:

1. **Backend router** at `mu/gui/routers/<mode>.py`
   - `GET /api/<mode>/state` returns a JSON payload hydrated from the session and any on-disk artifacts (mirror `_hydrate_from_disk` in `teacher.py:34` for modes whose canonical store is a file).
   - Read-only by default; mutations go through chat (e.g. `/feature task X advance` works fine — the agent already wires it).
   - Mutating endpoints only when the GUI offers an action that doesn't exist as a slash command (e.g. drag-and-drop, in-place edit).
   - Accept optional `?session_name=` query (multi-session-safe; see `deps.py:18`).

2. **Frontend Alpine store** at the top of `mu/gui/static/js/app.js`
   - `Alpine.store("<mode>", { state, loaded, openSections, load(), … })`.
   - Load on three triggers: page boot, mode flip to this mode, `turn_complete` event.
   - View helpers (`statusGlyph`, `formatTimestamp`, computed groupings) co-located with the store.

3. **Panel template** at `mu/gui/templates/fragments/<mode>_panel.html`
   - `x-show="$store.mode.active === '<mode>'"` at root.
   - Header (subject/id + status pills) → collapsible sections → diagnostic footer.
   - Same `.teacher-section` / `.teacher-section-head` / `.teacher-section-body` class shapes — rename to neutral `.panel-section` so all modes can share the CSS.

4. **Layout slot in `index.html`** — already exists for teacher (`fragments/teacher_panel.html`). Add one include per mode. Only one panel renders at a time because of the `x-show` guard.

5. **CSS** — generalize the existing `.teacher-panel` styles into `.mode-panel` so we don't fork per-mode stylesheets. Mode-unique flourishes (e.g. severity colors for security) are local additions.

### Shared infra to land first

Before building per-mode panels, do this once:

- Rename `.teacher-panel`, `.teacher-section*`, `.teacher-head`, `.teacher-footnote` → `.mode-panel`, `.panel-section*`, `.panel-head`, `.panel-footnote`. Add a `--accent` override via `data-mode` attribute so each mode can have a subtle color signature (security=red, research=cyan, etc.) without forking the CSS.
- Extract the section-accordion render pattern into a small shared snippet (`fragments/_section.html`) so each panel template stays focused on data binding, not boilerplate.
- Add a `mode-panel.css` chunk with the shared styles; per-mode template files only add what's genuinely unique.

---

## Mode-by-mode plans

### `feature` mode — phased plan kanban

**Why a panel here?** Feature mode is the most state-heavy mode. The chat scrollback is a terrible UI for "what task is next, what's blocked, which phase are we in." Kanban with drag-and-drop is what users expect from any task tracker.

**Data sources**
- `session_manager.feature_state` — currently-loaded plan (full structure).
- `session_manager.feature_registry` — `{feature_id: stub}` of all known features.
- `summarize_feature_plan()` in `mu/feature/engine.py:228` returns the panel-friendly shape; reuse it.
- Per-feature directory: `~/.mucli/sessions/<name>/features/<id>.json` (hydrate from disk if the in-memory plan is a stub, same pattern as teacher).

**Panel sections (top → bottom)**

| Section | Content |
|---|---|
| Header | feature_id (mono), title, status pill, percent-complete bar, blocker badge if blocked |
| Feature switcher chips | other features in registry; click → `/feature load <id>` |
| Phase kanban | columns = `FeaturePhase` values (Plan / Implement / Verify / Review …). Cards = tasks; status glyph + exit-criteria summary `(2/5)` |
| Current task detail | when one task is `in_progress`: title, description, exit criteria checklist, blocker reason if any |
| Event log | last 5 entries from `feature_plan.event_log` (timestamp · who · what) |
| Review records | rolled up per-phase; show pass/fail glyph + reviewer note |
| Footnote | path to `<feature_id>.json` |

**GUI-driven actions**
- **Drag a card** between phase columns → `POST /api/feature/tasks/{tid}/transition` (reuses `transition_task_status` at `feature/engine.py:674`). Only allow transitions the engine accepts; reject in UI on 409.
- **Toggle exit criterion** → `POST /api/feature/tasks/{tid}/exit-criteria/{idx}/toggle`. Reuses `update_task_status` at `feature/engine.py:767`.
- **Unblock task** → cursor-anchored confirm popover (`$store.confirm.ask`), then POST `/unblock` (or fire `/feature task X unblock` through chat).
- **Switch feature** → chip click sends `/feature load <id>` via the chat path (so the engine handles registry sync, just like teacher's course switcher).

**Risks**
- Card drag during a live agent turn — server-side `feature_state` may mutate from under the UI. The chat send path already serializes, but the drag endpoint needs the same per-session lock (`request.app.state.session_lock_for(session_name)`).
- "Blocked" is a fuzzy concept; mirror engine's classification rather than re-deriving in the frontend.

---

### `research` mode — sources + bibliography

**Why a panel here?** Sources accumulate fast (often 30+ per question). Scrolling chat to find "which paper mentioned X" is painful; a sortable table is the natural fit.

**Data sources**
- `utils.citation_manager.CitationManager` — singleton holds all sources for the running process; `compile_bibliography()` produces a printable string but the underlying `Source` dataclasses carry the structured fields we want.
- `session_manager.task_memory` — research findings saved across turns.
- `variables["research_question"]` if pinned (verify the key name; the mode prompts the agent to track it).

**Panel sections**

| Section | Content |
|---|---|
| Header | active research question (editable inline), source count, last-updated timestamp |
| Source table | sortable: title · type · credibility (0.0–1.0 bar) · URL · last-cited. Filter chips by type (academic / web / docs / forum) |
| Source detail | on row click: excerpt, full URL, credibility breakdown, "used in turns: 3, 7, 11" |
| Bibliography preview | formatted citation list (BibTeX-ish or Markdown), copy button |
| Saved findings | from `task_memory` filtered to research namespace; one-line summaries |
| Footnote | source-manager state path if persisted; else "(in-memory only — sources clear when the daemon restarts)" |

**GUI-driven actions**
- **Filter by credibility threshold** — local UI filter, no backend change.
- **Copy bibliography** — clipboard write of the formatted output.
- **Open source URL** — `<a target="_blank">` plus a confirm popover since clicking opens external content.
- **Reframe question** → inline edit pushes a chat message `/research question <new text>` (don't write directly to variables — let the agent maintain the chain-of-evidence narrative).

**Risks**
- `CitationManager` is process-local; if the user runs `mucli` separately in TUI and GUI, sources don't sync. Document the limitation in the footnote rather than building cross-process sync (out of scope).
- Credibility scoring is heuristic — surface the breakdown (which signals contributed) so users can judge, not just the final number.

---

### `security` mode — finding workflow

**Why a panel here?** Security mode's contract is *every claim is gated on a verified PoC + verified patch*. The state machine (`new → proof_attached → exploit_verified → remediation_verified → approved/refuted`) is exactly what users want to track. Chat-only is a poor fit.

**Data sources**
- `documentation/security_scan_<scan_id>/report.json` — top-level scan record.
- `findings/<finding_id>/` — proof.{sh,py}, patch.diff, verification logs.
- `mu/security/engine.py` for state-machine definitions; reuse the status enum exactly.

**Panel sections**

| Section | Content |
|---|---|
| Header | scan_id (mono), status pill, total findings, severity distribution bar (red/orange/yellow/blue stripes proportional to counts) |
| Severity filter | chip row: `critical / high / medium / low / info` — click toggles which sections of the list show |
| Findings list | one row per finding: severity dot · title · status badge · `has_proof?` and `has_patch?` glyphs · approval state |
| Finding detail | on row expand: description, file:line refs, proof artifact preview (syntax-highlighted), patch diff, verification log, approval reason if rejected |
| Scan stats | findings counted by status (helps the user see "how many still need PoCs") |
| Footnote | path to `report.json` |

**GUI-driven actions**
- **Re-verify proof** → `POST /api/security/findings/{id}/reverify` (engine already has the verification harness — wrap it).
- **Approve / refute** → cursor-anchored confirm popover with a textarea for the reason (required by engine for refute). Maps to whatever engine method records the decision.
- **Open finding artifacts** in a side panel (proof + patch diff) — read-only.
- **Export report** → `GET /api/security/scans/{id}/report.md` returns the rendered Markdown for `report.json`. Browser download.

**Risks**
- The "verified" flags can lie if the verification harness is bypassed (e.g. manual edits to `report.json`). The panel should show *how* something was verified (timestamp + command), not just a green check, so users can scrutinize.
- Exporting/sharing security findings has confidentiality implications — confirm with the user before any "share link" feature, do not auto-upload.

---

### `loop` mode — autonomous loop dashboard

**Why a panel here?** When `/loop <goal>` is running, the user is genuinely hands-off and wants a dashboard to glance at: what's the goal, what has the agent done this loop, what's queued, when did it last act.

**Data sources**
- `variables["loop_goal"]`, `variables["loop_active"]`, `variables["loop_features"]` (JSON array of features the loop has spawned).
- `task_memory` for durable cross-loop notes.
- `turn_scratchpad` for in-loop hypotheses.
- The `todo_list()` tool's current list — best read off the session if cached; else last-seen from the agent loop's emitted state.

**Panel sections**

| Section | Content |
|---|---|
| Header | locked goal (read-only mono block), active/inactive pill, time-since-last-turn ("acted 3 min ago") |
| Goal editor | textarea + save → `POST /api/loop/goal` (reuses the `/loop` command path so the agent records the change) |
| Backlog | current todo_list grouped by status (pending/in_progress/completed) |
| Features spawned | `loop_features` array → one row per (id, created_at). Click → `/feature load <id>` |
| Memory snapshot | top 5 `task_memory` entries by recency, with a "search…" inline |
| Footnote | session.json path; loop ticks since boot |

**GUI-driven actions**
- **Pause / resume loop** → `POST /api/loop/active` with `{active: false}`. Server flips the flag; the agent's next loop check reads it and stops self-pacing.
- **Edit goal** — inline textarea (re-uses `/loop <goal>` plumbing).
- **Promote backlog item** → fire `/todo promote <id>` through chat.
- **Clear backlog** → confirm popover, then `/todo clear`.

**Risks**
- Looping agents can run for hours; the GUI must not poll aggressively. SSE event `turn_complete` already triggers a panel refresh — keep it that way and avoid setIntervals.
- Pause via UI vs. pause via chat: both should set the same flag, no parallel state.

---

### `debug` mode — hypothesis tracker

**Why a panel here?** Debug is iterative — write hypothesis, test, refine. A panel that pins the *current hypothesis*, the *repro command*, and any *suspect locations* lets the user keep state visible while the chat is busy generating verifications.

**Data sources**
- `turn_scratchpad` — agent writes hypotheses here during the turn.
- `task_memory` for durable bug findings ("last time errno was set, X was the cause").
- `variables["debug_target"]` if the agent pins one (verify key name; agent prompt encourages it).
- No dedicated engine file — debug is primarily a system-prompt mode.

**Panel sections**

| Section | Content |
|---|---|
| Header | current target (file, function, symptom) — agent-set; user can override inline |
| Hypothesis stack | LIFO list of hypotheses from scratchpad, each with status (untested / disproved / supported / confirmed). Click → "make this current" |
| Repro command | pinned command block with copy-button; "run in chat" sends it as a bash tool call |
| Suspect locations | file:line refs the agent has called out this session |
| Past root causes | `task_memory` filtered to debug findings |
| Footnote | scratchpad entry count |

**GUI-driven actions**
- **Add hypothesis** → inline text input → `POST /api/debug/hypothesis` (writes to scratchpad through the agent rather than direct mutation, so the chain stays consistent).
- **Mark hypothesis disproved/confirmed** → status flip via chat command (`/scratchpad …`) — reuses existing handler.
- **Copy repro command** → clipboard.

**Risks**
- Scratchpad keys aren't standardized; debug mode may not currently namespace its hypothesis entries. May need a small backend convention (e.g. `kind:"hypothesis"` on scratchpad entries) before the panel can reliably parse them. Confirm before building.

---

### `default` mode — light shell

**Why a panel here?** Default mode is the catch-all; users in this mode usually want chat-first. The existing **inspector drawer** (`mu/gui/templates/fragments/inspector.html`) already covers workspace / memory / stats / variables — that's the right surface for "default mode" affordances.

**Recommendation**: do *not* build a dedicated default-mode panel. Instead:

- When `mode.active === 'default'`, the panel area shows nothing (full-width chat).
- Surface a **header chip** showing the pinned `session_goal` (if any) — a small reminder next to the mode picker, not a panel.

This keeps default-mode focused, which is what it's for.

---

## Cross-cutting work

| Item | Why | Where |
|---|---|---|
| Rename teacher-specific CSS classes to mode-neutral | So all panels share styles | `static/css/app.css`, `templates/fragments/teacher_panel.html` |
| Add `data-mode="<name>"` to panel root | Mode-specific accent color via CSS custom prop | each panel template |
| Add `<mode>.router` files | One per new panel | `mu/gui/routers/` |
| Wire panels into `index.html` | One `{% include %}` per panel | `mu/gui/templates/index.html` |
| Multi-session sanity | Each panel's store needs to refresh on `routeEvent` when `ev.session_name === currentName` | each store |
| `mode.load()` refreshes the relevant panel | Existing teacher pattern → generalize | `app.js` mode store |

## Sequencing

The right order is: **feature → security → research → loop → debug**, then trim default if anything is left.

1. **Feature mode** first. Highest state complexity, biggest gap vs. TUI, and it lets us prove the drag-and-drop interaction model. Tackle the shared CSS rename as part of this PR.
2. **Security mode** second. Workflow is the most distinctive (gated state machine); panel design will surface gaps in the engine we may want to fix.
3. **Research mode** third. Mostly read-heavy; benefits from the patterns landed by 1+2.
4. **Loop mode** fourth. Smaller surface; pairs well with a once-over to confirm SSE refresh cadence is sane.
5. **Debug mode** fifth — only after confirming the scratchpad has a stable contract for hypothesis entries (may need engine changes).
6. **Default mode** — at most a header chip, no panel.

Land each as its own PR so they can be reviewed and merged independently.

## Verification (per mode)

For each mode:
1. Hydrate a real session that exercises the mode (use existing sessions or seed one).
2. Open the GUI, switch to the mode, confirm the panel populates without errors.
3. Send a chat message that mutates state (e.g. for feature: advance a task) — confirm the panel re-renders via `turn_complete`.
4. Trigger any GUI-driven action — confirm it produces the same engine state as the equivalent chat command.
5. With multi-session loaded: confirm switching sessions in the sidebar swaps the panel data correctly.
6. Watch `~/.mucli/logs/gui.log` for warnings while clicking around.

## Risks (cross-cutting)

1. **Engine state drift** — disk file is the source of truth, but the in-memory session has a stub. The `_hydrate_from_disk` pattern from teacher must be applied to every mode whose canonical store is a file (feature, security at minimum).
2. **Mutation conflicts** — a GUI-driven action and an in-flight chat turn could race on the same session. Already mitigated by `session_lock_for(name)` at `mu/gui/app.py:75`; every mutating endpoint must use it.
3. **Per-mode polling traps** — resist the urge to `setInterval` panel refreshes. SSE events should drive everything. Polling exists only in the sessions sidebar (5s) and the live-clock (500ms while busy).
4. **CSS regression risk** — renaming `teacher-*` → `panel-*` will touch many selectors; do it under one PR, with one screenshot diff before merging.
5. **Mode-switch race** — flipping modes mid-turn shouldn't leave the wrong panel showing. The `mode-<name>` body class plus `x-show` on each panel's root handles this; verify under load.
