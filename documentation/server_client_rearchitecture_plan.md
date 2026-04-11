# μCLI Server/Client Rearchitecture Plan

## Goal
Make the **μCLI server** the single runtime authority (the "magic") while GUI and TUI are thin clients that only interact through server APIs.

## Stage Tracker

### Stage 1 — Server as runtime authority
- [x] Add reusable CLI HTTP client abstraction (`core/server_client.py`).
- [x] Ensure `mucli.py --server` is the authoritative runtime entry.
- [x] Remove direct local-runtime TUI loop and route TUI interactions over HTTP only.
- [x] Default TUI mode can launch and attach to a managed local server when `--connect` is not provided.

### Stage 2 — Client transport and reconnect UX
- [x] Add client capability discovery handshake (`/api/capabilities` + TUI probe on connect).
- [x] Move TUI loop into a dedicated `tui/` client package to separate transport/render/bootstrap concerns.
- [x] Persist and restore last-connected server/session profile in user config for reconnect-first UX.
- [x] Add async task watcher mode in TUI (poll `/api/tasks` + `/api/tasks/<id>`).

### Stage 3 — Streaming and approvals parity
- [x] Introduce SSE-powered live stream consumption in TUI (`/api/events`).
- [x] Surface approval prompts/results in TUI via approval endpoints (`/api/approvals*`).
- [x] Expose live tool trace events in client UI (trace.tool / trace.tool_result).

### Stage 4 — Contract hardening and packaging
- [~] Publish explicit API capability/version payload (`api_version`, runtime mode, feature flags).
- [~] Add integration tests with spawned server fixture (reconnect, async tasks, approvals).
- [~] Split entrypoints into `mucli-server` and `mucli-tui` executables.
- [x] Add multi-client arbitration controls (session locks and read-only observer mode).

## Architectural Direction
- **Single runtime owner**: provider lifecycle, tool execution, approvals, task queue, feature loops, memory, and session persistence run only inside server mode.
- **Client-only frontends**: terminal and browser clients send commands/messages and consume state/events.
- **Reconnect-first UX**: both clients can reconnect to existing server sessions and inspect task progress via `/api/tasks` + `/api/events`.

## Risks and Mitigations
- **Risk**: startup race between TUI and managed server.
  - **Mitigation**: health polling with bounded timeout before entering input loop.
- **Risk**: legacy assumptions in command handlers about direct UI ownership.
  - **Mitigation**: preserve server-side `HeadlessUI` traces and continue lifting interactive pieces into API workflows.
- **Risk**: feature parity gaps during migration.
  - **Mitigation**: staged rollout with compatibility checks and targeted integration tests per endpoint family.

## Definition of Done (Final state)
- No user-facing flow executes model/tool runtime outside server process.
- GUI and TUI both function as API clients with reconnect support.
- Task, approval, and stream progress are observable from either client.
- API contracts are versioned and integration-tested.
