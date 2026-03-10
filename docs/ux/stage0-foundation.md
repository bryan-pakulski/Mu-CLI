# Stage 0 Foundation: IA, Journeys, and UX Metrics

This document starts Stage 0 and defines the concrete artifacts needed before broader UI implementation.

## 1) Canonical information architecture (IA)

### App shell zones

- **Left rail (Navigate + scope)**
  - Sessions (active/recent/pinned)
  - Repository/workspace selector
  - Context sources overview (files, summaries, tool memory)
- **Center (Primary work area)**
  - Conversation stream
  - Prompt/input composer
  - Run timeline anchor for active execution
- **Right rail (Inspect + debug)**
  - Tool call details
  - Step metadata (latency, token/cost signal, errors)
  - Runtime/debug paneling
- **Bottom drawer (Review + approve)**
  - Diff/code review workspace
  - Tool output details and approvals
  - Commit/PR preparation helpers

### Navigation model

- Top-level surfaces:
  1. **Operate** (sessions, repo context, execution)
  2. **Control** (skills, tools, rules, model settings)
  3. **Review** (diffs, approvals, change management)

## 2) Priority user journeys and acceptance criteria

### Journey A: Resume and continue a session

- User can identify active session state within 2 seconds.
- User can switch sessions in at most 2 interactions.
- Session switch preserves/clarifies workspace and model context.

### Journey B: Inspect repo and produce safe changes

- Repo + branch context is always visible in shell.
- User can open staged/unstaged diffs quickly and understand change scope.
- Commit preparation flow surfaces risk warnings before mutating actions.

### Journey C: Track real-time execution

- User can see every step in model/tool lifecycle while a run is active.
- User can expand an event to inspect input/output metadata.
- Error states include enough context to determine retry vs manual intervention.

### Journey D: Tune behavior and guardrails

- User can adjust model controls with clear “impact” explanations.
- Prompt/rules changes are versioned and reversible.
- Tool/skill toggles reflect current effective state per session.

### Journey E: Manage context budget

- User can view token-budget allocation across context components.
- User can pin/exclude/summarize context entries with immediate feedback.
- User can see when content is omitted due to budget constraints.

## 3) UX success metrics (for Stage 4 instrumentation)

### Efficiency

- Time-to-first-action (session load -> first successful action)
- Time-to-first-tool-call
- Median time from prompt to reviewed diff

### Reliability

- Tool success rate
- Retry rate after tool failure
- Session interruption/error frequency

### Operability

- Diff acceptance rate
- Percentage of runs where timeline was expanded/inspected
- Frequency of context budget overflow

### Safety/control

- Number of risky actions intercepted by guardrails
- Rate of rules/prompt rollback actions
- Frequency of per-session tool override use

## 4) Stage 0 deliverables checklist

- [x] IA shell and navigation model documented.
- [x] Five core user journeys with acceptance criteria documented.
- [x] UX metrics taxonomy drafted.
- [ ] Wireframe draft links added.
- [ ] Stage 1 implementation tickets linked.

## 5) Hand-off into Stage 1

The first Stage 1 coding slice should implement a shell layout abstraction (zones + panel controls) without changing API contracts or runtime behavior. This keeps risk low while enabling iterative replacement of each panel surface.
