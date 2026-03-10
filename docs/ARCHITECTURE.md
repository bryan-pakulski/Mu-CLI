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

- ✅ Stage 1 started: introduced a modular web route package and moved `/api/session` route handling into `mu_cli/webapp/routes_session.py`, with `create_app()` now wiring session routes through explicit dependencies.


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

### 3) Make frontend modular without changing deployment model

If keeping a single HTML deliverable, still extract logic into sections with strict boundaries:

- networking helpers
- state store + reducers
- render functions
- event wiring

A medium-term step is moving JS into `static/app.js` while preserving server-side template simplicity.

### 4) Adopt explicit API contracts for high-churn routes

Add typed payload validation for `POST` routes (`/api/session`, `/api/settings`, `/api/chat/*`) so malformed payload behavior is predictable and tested.

### 5) Add architecture decision records (ADRs)

Record major decisions in `docs/adr/` to reduce future churn and repeated debates.

Suggested first ADRs:
- Session model and persistence boundaries
- Why streaming endpoint may switch runtime session
- Frontend packaging strategy (single-file vs static assets)

## Near-term refactor plan (suggested sequencing)

1. Move session lifecycle helpers to a dedicated `runtime.py` module. *(in progress)*
2. Extract `/api/session` action handling into `routes_session.py`. *(done)*
3. Move background job orchestration into `jobs.py` with focused tests.
4. Add request schema validation helpers for top 3 mutable endpoints.

This keeps each PR focused and minimizes behavior change risk.
