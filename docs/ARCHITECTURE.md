# Architecture Overview

This document describes the current `mu_cli` architecture and practical refactor directions to keep development manageable.

## Current structure

- `agents/mu_cli/cli.py`: interactive CLI and command handling.
- `agents/mu_cli/web.py`: Flask app, API routes, and runtime orchestration.
- `agents/mu_cli/agent.py`: model/tool loop and tool-calling behavior.
- `agents/mu_cli/providers/`: provider adapters (`echo`, `openai`, `gemini`).
- `agents/mu_cli/tools/`: tool implementations and schemas.
- `agents/mu_cli/session.py`: persisted session state storage.
- `agents/mu_cli/workspace.py`: workspace indexing and tool-run memory.

## Manageability assessment

The codebase is functional and test-covered, but maintainability pressure is concentrated in two files:

1. **`web.py` is doing too much**: route handlers, runtime/session lifecycle, background job loop, and policy helpers live together.
2. **Single-file frontend (`templates/index.html`) is large**: it mixes markup, styles, and substantial client-side logic.

This creates high cognitive load for onboarding and raises regression risk when modifying unrelated behavior.

## Recommended architecture decisions (incremental, low-risk)

**Progress**

- ✅ Stage 1 complete: introduced modular backend package `mu_cli/webapp/` with `routes_state.py`, `routes_chat.py`, `routes_session.py`, `runtime.py`, and `jobs.py`; `create_app()` now acts as a thin composition root wiring route registrars and dependencies.
- ✅ Stage 2 complete: centralized key runtime mutations (session new/clear and settings updates) into shared runtime service helpers to reduce ad hoc route-level state mutation.
- ✅ Stage 3 complete: moved frontend JavaScript into `static/app.js` and organized code by boundaries (networking helpers, state/reducers, render functions, event wiring) while keeping server-rendered template deployment unchanged.
- ✅ Stage 4 complete: added explicit typed API contracts for high-churn POST routes (`/api/session`, `/api/settings`, `/api/chat/*`) with predictable 400 error responses and regression tests.
- ✅ Contract Expansion Stage 3 complete: extended contract validation to additional mutable routes (`/api/jobs/<id>/plan`, `/api/jobs/<id>/kill`, `/api/pricing`, and uploads payload checks).
- ✅ Frontend Stage 2 complete: split frontend source into real modules under `static/app/` (`network.js`, `store.js`, `render/core.js`, `main.js`, `events.js`) with a tiny bundle build step.


### 1) Split web backend by responsibility

Create a package such as `mu_cli/webapp/` and gradually move logic:

- `webapp/routes_state.py` (`/api/state`, `/api/settings`, etc.)
- `webapp/routes_chat.py` (`/api/chat`, `/api/chat/stream`, background jobs)
- `webapp/routes_session.py` (`/api/session` actions)
- `webapp/runtime.py` (WebRuntime + session/runtime lifecycle helpers)
- `webapp/jobs.py` (background job state machine)

Keep `create_app()` as a thin composition root wiring shared runtime + blueprints.

### 2) Stabilize runtime mutation through service helpers

Continue centralizing repeated state transitions behind helpers (for example session reset/new/load), and avoid route handlers mutating `runtime` ad hoc.

Status: ✅ Completed for session new/clear and settings update flows via `webapp/services_runtime.py`.

### 3) Make frontend modular without changing deployment model

If keeping a single HTML deliverable, still extract logic into sections with strict boundaries:

- networking helpers
- state store + reducers
- render functions
- event wiring

Status: ✅ Completed — JS moved to `static/app.js` and organized with explicit section boundaries and module facades while preserving server-side template simplicity.

### 4) Adopt explicit API contracts for high-churn routes

Add typed payload validation for `POST` routes (`/api/session`, `/api/settings`, `/api/chat/*`) so malformed payload behavior is predictable and tested.

Status: ✅ Completed via `webapp/contracts.py` validation helpers and route integration with regression tests.

### 5) Add architecture decision records (ADRs)

Record major decisions in `docs/adr/` to reduce future churn and repeated debates.

Status: ✅ Stage 1 complete — ADRs added:
- [`0001-session-model-and-persistence-boundaries.md`](adr/0001-session-model-and-persistence-boundaries.md)
- [`0002-stream-session-switching-behavior.md`](adr/0002-stream-session-switching-behavior.md)
- [`0003-frontend-packaging-strategy.md`](adr/0003-frontend-packaging-strategy.md)

## Near-term refactor plan (suggested sequencing)

1. Move session lifecycle helpers to a dedicated `runtime.py` module. *(done)*
2. Extract `/api/session` action handling into `routes_session.py`. *(done)*
3. Move background job orchestration into `jobs.py` with focused tests. *(done for route/state handling split; deeper loop extraction can be incremental)*
4. Add request schema validation helpers for top 3 mutable endpoints. *(done)*
5. Add initial ADR set for key architecture decisions. *(done)*
6. Expand contracts to remaining mutable routes (`jobs/*`, `pricing`, uploads assumptions). *(done)*

This keeps each PR focused and minimizes behavior change risk.
