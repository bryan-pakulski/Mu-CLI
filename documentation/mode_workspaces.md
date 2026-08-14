# Mode workspaces

MuCLI's agent modes share an execution harness, but their outputs do not share
one meaning. A security proof, a research citation, a debug hypothesis, and a
completed feature task are different kinds of evidence. The Mode OS keeps the
interaction model consistent while allowing each mode to present its own
operating surface.

## Architecture

Mode-owned state remains canonical:

- Research: citation ledger and research task memory
- Security: security report, proof, and remediation records
- Debug: investigation scratchpad and durable root-cause memory
- Loop: mission variables, todo scratchpad, workstreams, and checkpoints
- Feature: feature plan engine, task events, exit criteria, and reviews
- Teacher: course engine, learner profile, assignments, grades, and reviews

`mu/gui/mode_workspace.py` adapts those records into a versioned presentation
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
| Research | Brief, Claims, Sources, Citations | source-linked, credibility, evidence gap |
| Security | Risk, Findings, Proof, Fixes | proof verified, fix verified, approved/refuted |
| Debug | Case, Hypotheses, Observations, Root causes | untested, supported, disproved, confirmed |
| Loop | Mission, Queue, Workstreams, Checkpoints | pending, in progress, blocked, reported complete |
| Feature | Plan, Tasks, Criteria, Reviews | exit criterion verified, blocker, review evidence |
| Teacher | Path, Curriculum, Mastery, Reviews | comprehension check, grade, rubric, recall review |

## Interaction rules

- The **Brief/Overview** lens shows the whole workspace. Selecting another
  lens narrows the surface without discarding the user's selection.
- Web lens, search, and evidence-guide state persists in local storage per
  mode. Mobile lens state persists in AsyncStorage per mode.
- Search and browsing do not mutate model memory or evidence state.
- Domain actions stay next to their domain records: security approval,
  feature-task transitions, course switching, and loop pause/resume do not go
  through a generic mode mutation endpoint.
- Loop pause/resume preserves the existing mission, backlog, checkpoints, and
  workstreams. Starting a brand-new mission remains a chat/model operation.

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
