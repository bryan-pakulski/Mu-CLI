# UX Overhaul Roadmap

This roadmap turns the high-level UX overhaul vision into a concrete, checkable plan we can update as work progresses.

## Status legend

- [ ] not started
- [~] in progress
- [x] completed
- [!] blocked

## Guiding outcomes

1. Improve operator confidence with transparent, real-time execution visibility.
2. Reduce time-to-complete for common workflows (session management, repo flow, code review).
3. Improve safety and control for advanced users (rules, tools, context budget, model knobs).
4. Preserve maintainability by shipping incrementally through existing frontend/backend module boundaries.

## Program phases

### Stage 0 — Discovery + UX specification (week 1)

- [~] Define a canonical IA and app-shell map (left rail / center / right rail / bottom drawer).
- [~] Define key user journeys and acceptance criteria.
- [~] Define measurable UX success metrics and baseline capture plan.
- [ ] Build low-fidelity wireframes for major surfaces.
- [ ] Finalize implementation slices for Stage 1.

### Stage 1 — Shell + design system foundations (weeks 2-3)

- [x] Add design tokens (spacing, typography, color roles, radii, shadows, interaction states).
- [x] Implement core UI primitives (cards, tabs, split panes, badges, empty/error/loading states).
- [x] Introduce global layout shell and panel-resize/collapse patterns.
- [x] Add keyboard shortcut infrastructure and focus conventions.

### Stage 2 — Core operational workflows (weeks 4-7)

- [~] Multi-session management v2 (pinned/recent, health states, quick switch).
- [~] Git management v2 (repo/branch always visible, safer actions, status clarity).
- [~] Diff and code management v2 (inline/side-by-side, hunk-level accept/reject).
- [~] Real-time execution timeline (step-by-step model/tool events + metadata).

### Stage 3 — Control plane surfaces (weeks 8-10)

- [ ] Skills management v2 (details, presets, lifecycle visibility).
- [ ] Tools management console (enablement, risk, latency/failure signal, session overrides).
- [ ] Context budget manager (token allocation meter, pin/summarize/exclude controls).
- [ ] System prompt/rules manager (versioning, diff, rollback, conflict checks).
- [ ] Model behavior controls (knobs/sliders + saved profiles).

### Stage 4 — Analytics + hardening + rollout (weeks 11-12)

- [ ] Instrument UX and runtime telemetry dashboard.
- [ ] Add feature flags and phased rollout path.
- [ ] Add regression test expansions for new flows.
- [ ] Run UX sign-off with performance + reliability targets.

## HELP (user quickstart)

Use this section as the baseline in-product help content so users can discover core behavior quickly.

### Shortcuts

- `Ctrl/Cmd + Enter`: send prompt
- `Ctrl/Cmd + B`: toggle left sidebar
- `Ctrl/Cmd + \`: toggle metadata panel
- `Ctrl/Cmd + ,`: open settings

### Tooltips and guidance

- Keep concise tooltip copy for controls with non-obvious side effects (agentic planning, research mode, condense).
- Tooltips should answer: **what this does**, **when to use it**, and **risk/tradeoff** in one sentence.

### Effective workflows

1. **Operate flow**: choose session + workspace, then run prompts from the main composer.
2. **Control flow**: adjust settings/skills/tools before long autonomous runs.
3. **Review flow**: inspect metadata/diffs and validate outputs before commit/PR actions.

### Troubleshooting

- If output looks stale, refresh session state and metadata panel before retrying.
- If context is noisy, condense and re-run with a tighter workspace/path scope.
- If tool calls fail repeatedly, switch to Control surface and review approval/runtime settings.

## Live tracker (update each PR)

| Track | Current stage | Owner | Next milestone | Status |
|---|---|---|---|---|
| Information architecture | Stage 0 | TBD | Wireframe review | [~] |
| Design system | Stage 1 | TBD | Token spec merged | [ ] |
| Session + Git workflows | Stage 2 | TBD | Shell integration | [ ] |
| Diff + review workflows | Stage 2 | TBD | Hunk actions prototype | [ ] |
| Runtime observability | Stage 2 | TBD | Timeline MVP | [ ] |
| Skills/tools/rules controls | Stage 3 | TBD | Control center MVP | [ ] |
| Context budget tooling | Stage 3 | TBD | Token meter MVP | [ ] |
| Metrics and rollout | Stage 4 | TBD | Telemetry dashboard MVP | [ ] |

## Execution rules

- Keep PRs small and scoped to one vertical slice whenever possible.
- Ship behind toggles when risk is medium/high.
- Include explicit UX acceptance criteria in PR descriptions.
- Keep documentation and visuals in sync with behavior changes.

## Stage notes

- Design guideline: [`docs/ux/design-guidelines.md`](docs/ux/design-guidelines.md)
- Stage 1 slice artifact: [`docs/ux/stage1-shell-slice.md`](docs/ux/stage1-shell-slice.md)
- Stage 2 progress artifact: [`docs/ux/stage2-operational-workflows.md`](docs/ux/stage2-operational-workflows.md)

## Immediate next actions

1. Complete Stage 0 artifact set (IA, journeys, metrics definitions).
2. Convert Stage 1 into 3-5 implementation-ready tickets.
3. Begin shell refactor with minimal behavior change.
