# Mode workspaces

MuCLI's agent modes share an execution harness, but their outputs do not share
one meaning. A security proof, a research citation, a debug hypothesis, and a
completed feature task are different kinds of evidence. The Mode OS keeps the
interaction model consistent while allowing each mode to present its own
operating surface.

## Architecture

Mode-owned state remains canonical:

- Research: the research ledger (questions, claims, sources)
- Security: security report, proof, and remediation records
- Debug: debug cases (`mu/debug`) with their check runs; root causes in the
  Memory Ledger
- Loop: the loop mission (`mu/loop`): goal, done-when criteria, budgets,
  increments, and the work-item backlog
- Feature: the feature plan file (approval, tasks, criteria evidence,
  reviews, events)
- Teacher: the course file (curriculum, lessons, checks, grades, mastery,
  dated reviews) and the learner profile

Debug, loop, work items, blockers, check runs and receipts are typed mode
state saved with the session (`mu/modes/state.py`); feature plans and
courses stay on disk with a pointer in the session.

## The mode shell

Research, debug, feature, loop and teacher panels share one shell
(`fragments/mode_shell.html`, `static/js/mode_shell.js`; models in
`mode_models.js`, Alpine stores in `mode_stores.js`):

- one header bar: the mode's name, a **state sentence** built from its
  facts with zero parts dropped, text tabs (no counts) and at most one
  primary action;
- **Now**, shown only when something waits for the user — user-only
  decisions (plan approval, curriculum consent, diff decisions),
  blockers, failed runs, exhausted budgets, due reviews — each with its
  action visible;
- rows with marks from `vocabulary.json`; one record opens in a
  `WashSheet`; check runs share one row and sheet (command, cwd, runner,
  exit code, predicate, excerpt, full output from the result store);
- an **expanded view** (`fragments/mode_expand.html`) over the
  conversation for the heavy surfaces — the feature board, the research
  matrix and report, the lesson reading view — with its state in the URL
  (`?mode-view=feature&open=t4`);
- a one-field dialog (`fragments/mode_ask.html`) for blocker answers,
  override reasons, root causes and mission goals.

User actions go through the mode engines with the surface as the actor
(`user:gui`, `user:mobile`, `user:cli`); overrides are recorded as
`override` events. Shared routes live under `/api/mode-state/` (check
runs and their output, blockers and resolve, work items, receipts).
Android has the same pieces in `mobile/android/src/components/modes/`.

Default mode has no panel: its surface is the turn receipt under each
assistant message and the Plan disclosure above the composer
([default_mode.md](default_mode.md)).

Security still uses the earlier workspace header. `mu/gui/mode_workspace.py`
adapts the research and security records into a versioned presentation
contract. It does not copy the records into a second database and it does not
change agent context. Every existing mode state endpoint includes a
`workspace` object alongside its native payload.

The shared contract contains:

- identity, objective, and live status;
- mode-specific view/lens definitions;
- a small set of factual metrics with explanations;
- explicit accuracy, relevance, and evidence semantics;
- a search hint and provenance description.

The native payload supplies the explorable records and actions. This prevents
the shared shell from collapsing all modes back into generic memory cards.

### Workspace and container parity

`session_type` is an execution boundary, while `agent_mode` is the strategy
harness. A Docker-backed session therefore supports every Mode OS workspace;
it is not a separate or reduced set of modes. The host securely proxies mode
state and control requests to the attached worker, which runs the same router
and presentation adapter against the container's actual `/workspace` and
in-memory session. The worker also emits the same context snapshots,
sub-agent timelines, tool progress, and artifact descriptors to web and mobile.

Every client request carries `session_name`. This matters when a mobile-selected
container is not the web daemon's currently focused session: mode reads and
mutations must never fall through to another session's state.

## Evidence semantics

Mode workspaces must never manufacture precision. Use these terms deliberately:

- **Measured**: a recorded measurement exists, such as a grade, source
  credibility grade, or verified exit-criterion count.
- **Verified**: a recorded verification action exists, such as a reproducible
  exploit proof or remediation check.
- **Derived**: the UI calculated a value from canonical state, such as
  completed tasks over total tasks.
- **Reported**: the model or user changed a lifecycle state, such as a todo
  becoming complete. Reported completion is not correctness.
- **Unassessed**: the mode has no defensible measurement. Show this label
  instead of displaying a default score or treating missing data as zero.

Source credibility is not claim accuracy. A confirmed debug hypothesis is not
an independent correctness score. Completed work is not acceptance evidence.
Lesson completion is not mastery. The evidence guide in every workspace makes
these boundaries visible.

## Mode lenses

| Mode | Primary lenses | Verification language |
| --- | --- | --- |
| Research | Questions, Claims, Sources, Citations | source-linked, credibility, evidence gap |
| Security | Risk, Findings, Proof, Fixes | proof verified, fix verified, approved/refuted |
| Debug | Case, Hypotheses, Runs, Root causes | untested, supported, refuted, confirmed |
| Loop | Mission, Timeline, Backlog | running, paused, blocked, done; increment passed/failed |
| Feature | Plan, Tasks, Criteria, Reviews | draft, approved, criterion evidence (run, user-attested, missing), override |
| Teacher | Path, Lesson, Mastery, Reviews | comprehension check, grade, awaiting judgement, rubric, review due |

## Interaction rules

- The **Brief/Overview** lens shows the whole workspace. Selecting another
  lens narrows the surface without discarding the user's selection.
- Web lens, search, and evidence-guide state persists in local storage per
  mode. Mobile lens state persists in AsyncStorage per mode.
- Search and browsing do not mutate model memory or evidence state.
- Domain actions stay next to their domain records: security approval,
  feature-task transitions, course switching, and loop pause/resume do not go
  through a generic mode mutation endpoint.
- Loop pause/resume preserves the existing mission, backlog and increments.
  Starting a mission is a user action (panel Start or `/loop start`).

## Visual system

The workspace shell uses the MuCLI glass hierarchy, typography, spacing, and
theme tokens. Each mode receives a low-chroma accent for orientation; status
and risk colours still retain their semantic meaning. All surfaces derive from
the active MuCLI light/dark theme. Mobile uses `ThemeContext`; web uses
`html[data-theme]` variables. Reduced-motion preferences remain respected.

The shared visual system is intentionally limited to shell chrome, metrics,
lenses, search, provenance, and evidence explanation. Mode record layouts
should be designed for the domain rather than forced into one universal card.

## Adding a mode

1. Keep the mode engine or existing canonical store as the source of truth.
2. Add a builder in `mu/gui/mode_workspace.py` with unique lenses and honest
   evidence semantics.
3. Return the contract as `workspace` from the mode state endpoint.
4. Add the shared header to the web surface and a native
   `ModeWorkspaceHeader` to mobile.
5. Build domain-specific records and actions below the header.
6. Persist exploration state, not domain state, in the client.
7. Test that no missing measurement is rendered as a score and that both
   light and dark theme tokens are used.
