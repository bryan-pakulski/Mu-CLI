# Android parity with the Mu GUI — plan

Status: implemented 2026-10-20 as release 0.6 (decisions in section 9). Open before release: the
device walkthroughs (TalkBack, 200% font, composited contrast; `mobile/android/ACCESSIBILITY_AUDIT.md`)
and screenshots, which need a phone or an accelerated emulator. Not ported: join tokens/enrolment,
groups, schedules, secrets and audit editing in Orchestration settings (read-only on the phone),
and the repair card's "View transcript" link.

The web GUI was rebuilt (Mu Revision 2, `documentation/design/mu_style_guide.md` §11b): a persistent shell of
destinations, flat surfaces with each destination's watercolour as an accent, rows instead of cards, one
state sentence per page, the shared tool-card / approval / composer components, palettes, glass and the new
Work and Orchestration features. The Android client (`mobile/android`, Expo 51 / RN 0.74, ~35k lines) predates
most of that. This plan brings it to parity: the same destinations, vocabulary, components and behaviours,
adapted to a phone per §11 (the tab bar is the rail; sheets are bottom sheets; one primary action as a floating
pill; one pane at a time).

## 1. Principles

1. **The server is the source of truth.** Screens render server view models (`/api/jobs/vm/*`,
   `/api/orchestration/*`) and offer the actions the server lists (`job.actions`, run/node `actions`), exactly
   as the web does. No client-side business rules.
2. **Generated, not copied.** Colours, type, spacing, vocabulary, palettes, destination accents, icons, fonts
   and art come from `mu/design/*` through `scripts/design/build_*.py`. Anything the web has that mobile
   needs (palettes, destination accents) is moved into tokens first.
3. **One component per idea.** Every screen is built from `src/ui`; no screen-local look-alikes, no raw
   `TextInput`/`Switch`/`Modal`/`ActivityIndicator`, no inline colour or spacing.
4. **Same words.** Labels, state sentences and status marks match the web (◆ needs you, ● running, ✕ failed,
   ✓ done, ○ at rest; `vocabulary.json`).
5. **Lean tests.** Behavioural jest tests per destination; no tests that grep source for text
   (`documentation/testing/harness.md` rules apply to mobile too).

## 2. Where we are

| Area | Web today | Android today | Gap |
|---|---|---|---|
| Shell | Rail of destinations: Chat, Work, Orchestration, Environments; Insights and Settings in the foot; badges; mode accent fade | Tabs: Chat, Work, Orchestration, **Workspace**, **More**; Work/Orchestration badges | Environments and Insights are not destinations; Workspace/More are grab-bags; no destination accents or fade |
| Theme | Paper/Dusk × Wash + Darcula, One, Material, Tokyo Night; glass strength, blur, decoration | Paper/Dusk only; glass levels fixed; reduce decoration | Palettes not generated for TS; no glass strength; settings split over 3 places |
| Layout | Page header: title, state sentence, tabs, one primary; rows; Now boards; list → main → aside | Mixed: cards, stat tiles, hand-built rows, `ModernHeader` with chips | Header anatomy, rows, boards, state sentence, primary pill |
| Chat | `.w-msg` prose; toolcard.js (pairs, groups, folding summary); `.w-approval` (queue, all shapes, remember, risk); `.w-resolved`; composer foot (mode, model, effort, plan, auto, context ring, mic); threads + sessions in rail | Close (prose, tool cards, "Worked" folding, approval sheet) but via local Composer and a separate PromptHost sheet | Inline approval cards + resolved breadcrumbs; composer as a kit component; composer foot facts; threads/sessions as one drawer |
| Work | Now board, jobs list, job detail tabs (Notes, Session, Plan, Diff + review mode, Review, Checks + **artifact browser**, Diagnostics, Cost & audit), push dialog, environments, setup/doctor, tickets, history, activity, "Open a shell" | Dashboard, jobs, detail tabs (no Notes, no artifacts, diff without review mode), activity, tickets, history, setup; two job API clients | Notes tab, artifacts viewer, diff review mode, push preview parity, `job.actions`-driven bars everywhere, one API client |
| Orchestration | Now, Runs, Nodes (presence/connecting, busy, maintenance panel, remote policy, open runs), Catalog/Studio/Runbooks, Results tables, Settings, **fleet chat** | Now (attention, kill switch, approvals, recent runs), run, nodes, node | Presence, busy, maintenance, open runs, results, catalog (read), fleet chat |
| Environments | List of environments/templates with live meters; detail with charts, processes, terminal, sessions, actions, configuration | `ContainerManagerSheet` from the chat; Shell screen (Workspace → Runtime) | A destination; detail screen; the shell is **loopback-only** on the server (closes 1008 for LAN clients), so the mobile terminal cannot work today |
| Insights | Runs (trace), Jobs analysis, Costs | Session trace (V2), job analysis | Costs; one Insights destination |
| Settings | Appearance (theme cards, light/dark, glass, blur, decoration), Areas links, Keyboard | Settings screen + AdvancedSettingsSheet + SessionSettingsSheet | One Settings destination with Appearance and Areas |

Health of the code base:

- **Kit coverage.** `src/ui` exists (~23 components) and the generated tokens are current. Screens still
  bypass it: there are 99 inline styles in `MemoryScreen` and 70 in `JobTabs`, 13 screens have no
  StyleSheet at all, and screen-local copies of StatusPill, buttons, rows, sections and charts.
- **Dead code.** Unreachable: `History` (placeholder), `Audio`, `JobDoctor` (legacy), `PlaceholderScreen`
  and the `api/index.ts` barrel. `routeAmbient` points at route names that don't exist.
- **Duplication.** Two jobs API clients (`jobs.ts` REST and `jobCore.ts` VM/cmd), two memory clients,
  duplicated prompt/confirm sheets in Jobs and Orchestration, and settings in three places.
- **Tests.** 53 jest files, mostly behavioural. Memory, Files, Traces, Shell, Artifacts, the mode screens
  and navigation have none. The accessibility audit (0.5.0) has open items: TalkBack, 200% font scale and
  composited contrast.

## 3. Target information architecture

Bottom tabs = the web rail, in the same order, each with the destination's accent and badge:

| Tab | Opens on | Contains |
|---|---|---|
| **Chat** | the current conversation | Sessions + threads drawer (left edge / μ mark); composer; workspace panel (the mode panels: teacher, feature, research, security, loop, debug, memory/context, files, artifacts) as a bottom sheet from the session header, like the web's ⋯ menu |
| **Work** | Now board | Jobs, Tickets, History as header tabs; Activity, Settings, Setup under ⋯; job detail as a pushed screen |
| **Orchestration** | Now | Runs, Nodes, Catalog as header tabs; run, node, tool detail pushed; fleet chat as a bottom sheet from an Ask button |
| **Environments** | environments list | Environment detail: meters, processes, sessions, actions, configuration, **terminal**; templates |
| **Insights** | Runs | Runs (traces), Jobs (analysis), Costs as header tabs |
| **More** | Settings | Settings (Appearance, Areas, **Devices**), Connection, Providers |

Six tabs: the web's four primary destinations, Insights, and More for Settings. The **Workspace** tab
goes away: its groups were the web's workspace panel (now under Chat) and runtime settings (now under
More). The **mode fade**: switching tabs cross-fades the ambient wash and the accent,
1.4 s, disabled under reduced motion or decoration.

Every destination follows the same anatomy (§4, §11):

- **Header:** the title, the state sentence underneath, then tabs.
- **Body:** a Now board or a list of rows.
- **Primary action:** at most one, as a floating ink pill.
- **Details:** in bottom sheets.

Deep links (`mucli://work/job/<id>?tab=diff`, `…/orchestration/node/<id>`) mirror the web's hash routes.

## 4. Design system parity

1. **Generate palettes for TS.** Extend `build_tokens.py` to emit `palettes` into `wash.generated.ts`, and add
   a palette provider to `ThemeContext`. Store the preference keys under the web's names (`palette`, `theme`,
   `glass`, `decoration`) so a future settings sync is trivial.
2. **Destination accents into tokens.** Move the accent and ambient map from `shell.css:107-112` into
   `tokens.json` (`destination.{chat,work,orchestration,environments,insights,settings}.{accent,ambient}`). The
   web and Android then both read it. `routeAmbient` uses it, fixing today's dead meadow branch.
3. **Glass.**
   - Android can't blur live, as recorded in the 0.5.1 ledger. Expose glass **strength** (the fill opacity,
     floor-clamped as on the web) and keep blur iOS-only.
   - Flat surfaces are the default. The watercolour is the accent only, per §11b.
4. **Rows not cards, no stat tiles.** Retire `StatStrip`/`StatTile` and card stacks in favour of `Row` and
   `Board` (below).
5. **Contrast.** Run `check_contrast.py` for every palette and theme pair on Android surfaces, and close
   the open items in the accessibility audit.

## 5. Component kit (src/ui) parity

New or reworked, each mirroring a web component:

| Kit component | Web counterpart | Notes |
|---|---|---|
| `PageHeader` (title, `StateSentence`, `HeaderTabs`, ⋯) | `.w-pagehead`, `WashHead.set` | Replaces `ModernHeader` and per-screen headers |
| `Row` / `Rows` (sign, title, sub, when, actions, pinned) | `.w-row` (`orchestration/ui.js row()`) | Swipe or ⋯ for secondary actions; 48px minimum (`size.touch`) |
| `Board` (Now sections) | `.w-board` | Needs-you first, with the decisive action visible |
| `PrimaryPill` | §4.4 ink pill | One per screen, floating |
| `Composer` | `.w-composer` | Textarea, attachment chips, slash completion; the foot holds mode, model, effort, plan, auto and the context ring; send circle and stop square |
| `ToolList` | `wash/toolcard.js` | Port `buildToolCards`/`turnSummary` as a pure TS module and check it against the same fixtures as the web, so both fold and group identically |
| `ApprovalCard` (inline; queue n of m; tool, path, diff, command preview, args; choice/choices/input/confirm/quiz/tool_approval; remember; risk) + `Resolved` | `.w-approval`, `.w-prompt__*`, `.w-resolved` | Inline in the transcript, plus a "needs you" banner when off-screen. Replaces the PromptHost sheet as the primary surface |
| `Worked` disclosure | `.w-worked` | Already close; move into the kit |
| `DiffView` (unified, file list, viewed, review mode) | `wash/diff.js`, review mode | Review mode is full-screen with Esc / back to leave |
| `ArtifactViewer` (log, text, JUnit, XML, JSON, CSV, image, pcap, archive, hex; HTML as download) | `work/artifacts.js` | Renders `/checks/{run}/preview`; never executes HTML |
| `ResultsTable` (sortable, columns from schema, CSV share) | `orchestration/results.js` | Horizontal scroll; sort; share CSV |
| `ActionBar` / `ActionSheet` driven by server `actions` (typed confirm, input, danger) | `ui.bindActions`, `runAction` | One implementation for Jobs and Orchestration; replaces the duplicated Prompt/Confirm sheets |
| `Sheet`, `Banner`, `EmptyState`, `Toast` (Undo), `Segmented`, `Field`s | existing | Keep; remove raw RN controls from screens |

Rule: a component used by two destinations lives in the kit. A screen that needs something new adds it to
the kit first.

## 6. Destination backlog

**Chat**

- Conversation:
  - Inline approval cards and resolved breadcrumbs.
  - The composer foot carries mode, model, effort, plan, auto and the context ring. The header carries the
    title only, per §11.
  - The tool list uses the shared fold/group logic.
  - Subagent panel parity: model, context % and the action timeline.
- Sessions:
  - One drawer with Threads and Sessions: unread, ◆ awaiting input, running dot, unload, delete. Thread
    delete never unloads the current or a running thread.
  - Fleet-chat and job sessions stay hidden (the server already filters them).
- Workspace panel sheet: the mode panels plus Context (memory), Files (read/save/create/rename as the web
  does) and Artifacts.
- Composer **mic**: record, transcribe through `POST /api/audio/stt` and put the text in the composer, as the
  web does. The separate Audio screen goes.
- Keep the offline queue, If-Match conflicts and history paging.

**Work**

- Board and lists:
  - The Now board from `/api/jobs/vm/home`.
  - The jobs list with filter.
  - Header tabs: Jobs, Tickets, History. Activity, Settings and Setup go under ⋯.
- Job detail:
  - Tabs: **Notes**, Session, Plan (editor and versions), Diff (+ review mode), Review (findings and
    disposition, reprompt selected), Checks (+ **artifact browser**, evidence), Diagnostics, Cost & audit.
  - The action bar comes from `job.actions`.
  - Push preview and dialog; environments and their approvals.
  - "Open a shell" shows the worktree command to copy, as on the web.
- New job sheet with workspace, environments and "Run in".
- One API client: everything through `jobCore` (VM + `/api/jobs/cmd`). The REST `jobs.ts` stays only for
  routes with no VM equivalent (Jira, tracker import, history export) and shrinks to those.

**Orchestration**

- The Now board: attention, approvals, paused runs, dispatch kill switch, needs reboot, agent updates.
- Runs: list with filters; run detail (grid, batches, decisions, executions log, results table).
- Nodes:
  - Presence (connecting / online / offline) and the busy "Running …" indicator.
  - Node page with maintenance actions (dry run, reboot one at a time), open runs here with inline
    decisions, labels and accept.
  - Policy view. Editing stays operator-only and reuses the web's validate → set flow.
- Catalog: tools, runbooks, versions, results tables.
- **Studio on mobile:**
  - Tool drafts: manifest and files editor, lint, test (check mode on a canary, typed confirmation where
    required), submit for review, publish / request changes for reviewers.
  - The discovery chat via `POST /api/orchestration/studio/session` reuses the Chat kit.
  - Runbooks: the step form (the same `runbooks.js` model functions, ported to TS with a parity test against
    the web's), the YAML view, validate with field-path problems, test and new version.
  - Editors use a monospace multiline field with syntax colours from the generated `syntax` tokens. Long
    files open full-screen.
- Fleet chat as a bottom sheet, reusing the Chat kit (it is the same transport).
- CSRF token handling stays as today (`/api/orchestration/csrf`, refetch on 403).

**Environments**

- List with live meters (`/api/containers/stats`). Detail with sessions, actions (start, stop, restart,
  attach, detach), configuration, snapshot to template, and templates.
- **Terminal**: the existing Shell screen moves into the environment detail. A paired device (section 7a) may
  open a container shell; unpaired non-loopback clients are still refused.

**Insights**

- Runs (traces), Jobs analysis and Costs (pricing view and edit), as tabs of one destination.
- Rename `SessionTraceScreenV2`.

**Settings**

- Appearance: theme cards (palettes), light/dark, glass strength, decoration.
- Areas links into each destination's settings, plus Connection and Providers.
- **Devices**: this phone's pairing, and on the web, the list of paired devices with revoke.
- Remove `AdvancedSettingsSheet` and `SessionSettingsSheet`. Session-level settings move to the composer
  foot and the Chat ⋯ menu.

## 7. Platform and API cleanup

- **Remove dead code:**
  - `History`, `PlaceholderScreen` and the `JobDoctor` route.
  - `api/index.ts`.
  - The `Audio` screen; its job moves to the composer mic.
- **Rename and relocate:** move `RunInPicker` into the kit exports and rename `SessionTraceScreenV2`.
- **Remove duplicates:** merge the two memory clients onto `/api/memory/state` + `/api/v1/memories`. Delete
  the duplicate prompt/confirm sheets once `ActionSheet` exists.
- **Streams:** one SSE manager for the three streams (`/api/events?session=`, `/api/jobs/events`,
  `/api/orchestration/events`). It handles foreground-only reconnect and `Last-Event-ID` resume, refetches
  only the changed parts (`scope/parts`) and shares badges across tabs.
- **Deep links and notifications:** routes map 1:1 to the web hashes. "Needs you" notifications go to the
  exact item.

## 7a. Device pairing (sign-in)

Today the GUI has no authentication: it binds to loopback, and a LAN phone works only when the operator sets
`MUCLI_GUI_ALLOW_REMOTE=1`. Pairing makes a remote device an explicit, revocable grant. It's how the phone
signs in, and it's what allows the container terminal from a phone.

**Pairing flow**

1. On the computer, **Settings → Devices → Pair a device** (or `mucli gui pair` in the terminal) shows a QR
   code and an 8-character code. The code is valid for 10 minutes and for one use.
2. The phone scans the QR, which carries the base URL and the code, or the user types both. It sends
   `POST /api/devices/pair {code, device_name}`.
3. The server returns a device token: 32 random bytes, stored only as a hash in
   `$MUCLI_HOME/devices.json` with the device's name, creation time, last-seen time and address.
4. The phone keeps the token in `expo-secure-store` and sends `Authorization: Bearer <token>` on every
   HTTP, SSE and WebSocket request.

**Rules**

- Every request from a non-loopback client needs a valid device token. That includes reads, SSE and the
  shell WebSocket. The only exceptions are `/healthz` and the pairing endpoint. Loopback behaves exactly as
  today.
- The shell WebSocket accepts loopback or a paired device; anything else is still closed with 1008.
- The container lifecycle routes accept a paired device; the old private-network allowance is replaced.
- Bearer-authenticated requests don't need the CSRF header. Browsers can't attach `Authorization`
  cross-site without a preflight, which the GUI never allows. Browser sessions keep CSRF as today.
- Pairing attempts are rate-limited. Codes are compared in constant time. A wrong code never reveals
  whether a pairing is pending.
- Revoking a device (Settings → Devices, or `mucli gui devices revoke <name>`) takes effect immediately and
  closes that device's SSE and WebSocket connections.
- `MUCLI_GUI_ALLOW_REMOTE=1` is still needed to bind to a non-loopback address. Pairing doesn't widen the
  bind; it gates what a remote client may do once it can reach the server.
- No pairing or device tools are ever exposed to the AI. As with node policy, these are operator-only
  actions.

**Tests**

- Pairing: expiry, single use, rate limiting, wrong code.
- Non-loopback requests: 401 without a token, normal with one.
- Revocation cuts live SSE and WebSocket connections.
- The shell accepts loopback and paired devices and refuses unpaired LAN clients. This extends
  `tests/containers/test_container_access_scope.py`.
- CSRF stays mandatory for browser requests.

## 8. Testing

- **Placement:** behavioural jest tests in `__tests__/<destination>/`, mirroring the Python pillars, and
  `__tests__/ui/` for the kit.
- **Rule:** no source-text assertions; screens are rendered against fixture view models.
- **Shared fixtures:** a few JSON fixtures captured from the real server VMs (`vm/home`, `vm/job`,
  `orchestration/overview`, node and run) live in `mobile/android/__tests__/fixtures/server/`. They are
  refreshed by a script that runs the Python helpers, so the client is tested against today's shapes.
- **Tool-card parity test:** the TS `ToolList` model and the web `toolcard.js` produce the same summary and
  grouping for the same event fixtures.
- **Gates:** `npm run typecheck` and `npm test` in CI (the existing `mobile-typecheck` job gains `npm test`).
- **Acceptance per phase:** an emulator walkthrough at 390×844 in Paper and Dusk, with a screenshot set
  next to the web's `snap.py` mobile captures.

## 9. Decisions (2026-10-20)

1. **Tabs:** Chat · Work · Orchestration · Environments · Insights · More (Settings, Connection, Providers).
2. **Mobile terminal:** kept, for paired devices (section 7a).
3. **Authentication:** device pairing token for every non-loopback client (section 7a).
4. **Orchestration authoring:** Studio (tools and runbooks) on mobile, as well as reading and decisions.
5. **Audio:** the composer mic (speech to text). The Audio screen goes.
6. **Release:** one release (0.6) once every phase is done.

## 10. Phases

Each phase ends green on typecheck and jest, with a walkthrough and screenshots.

| Phase | Scope | Done when |
|---|---|---|
| **P0 Foundations** | Device pairing (server + phone + web Settings → Devices); palettes and destination accents generated; theme provider (palette, glass strength, decoration); six-tab shell with badges and mode fade; kit additions (PageHeader, StateSentence, Row, Board, PrimaryPill, ActionBar/ActionSheet, Composer); one SSE manager with bearer auth; dead code removed | The phone pairs and signs in; unpaired LAN clients get 401; every destination renders through the kit and shows its accent; no screen imports raw RN controls; palettes selectable |
| **P1 Chat** | Inline approvals + resolved; ToolList port with the parity test; composer foot + mic; sessions/threads drawer; workspace panel sheet (mode panels, Context, Files, Artifacts) | Conversation, approvals and panels match the web on the fixtures; offline queue tests still pass |
| **P2 Work** | jobCore-only client; Now board; job detail incl. Notes, diff review mode, artifact browser, push preview, `job.actions` bars; tickets, history, activity, setup | Every job command reachable from the server's actions; artifact kinds render; job screens tested on server fixtures |
| **P3 Orchestration** | Now, runs (detail, results), nodes (presence, busy, maintenance, open runs, policy view), catalog, Studio (tool drafts, runbooks, discovery chat), fleet chat sheet | Decisions (approve/reject/continue/cancel, maintenance) work from the phone with typed confirm; a tool can be drafted, tested on a canary and submitted from the phone; the runbook model passes its parity test with the web; fleet and discovery chats use the Chat kit |
| **P4 Environments, Insights, Settings** | Environments destination with the terminal; Insights tab (Runs, Jobs, Costs); unified Settings incl. Devices | No settings left in sheets elsewhere; the terminal works from a paired phone |
| **P5 Polish and release** | Accessibility audit items (TalkBack, 200% font, contrast per palette), performance (long transcripts, large diffs), notifications/deep links; 0.6 release | Audit items closed; full walkthrough of every destination in Paper and Dusk and one palette; release notes |

Rough size: P0 and P2 are the largest. P1 and P3 reuse most of the kit from P0. Most of the 35k lines
change, but the API layer, offline queue and theme plumbing carry over.
