# Mu-CLI Server Architecture Review

## Status

**Resolved for GUI development:** yes, for a local/trusted GUI client.

The current server stack is sufficient to begin building the GUI:

- headless chat/message execution exists
- slash-command interoperability exists
- direct tool execution exists
- session/runtime/workspace/staged-file APIs exist
- async task tracking exists
- explicit approval workflows for modifying tools exist
- SSE event streaming exists

In other words, the GUI can already:

1. inspect current state
2. send messages
3. poll task progress
4. surface pending approvals and diffs
5. approve/reject tool actions
6. subscribe to live task/approval events
7. update runtime/session/workspace state

## Implemented Server Layers

### 1. WebUI adapter

`WebUI` (`mu/gui/web_ui.py`) is the non-interactive bridge between the
session loop and HTTP/SSE-driven clients. It subclasses the terminal
`BaseUI`, forwards status/tool activity onto the per-session event bus,
and routes approval/confirmation prompts into the `PromptStore` instead
of blocking on stdin. This is the key abstraction that lets GUI clients
reuse the same session logic as the terminal UI.

### 2. Multi-session turn execution

The server runs a **multi-session** model (`app.state.sessions` in
`mu/gui/app.py`): every loaded `Session` is keyed by name, each with its
own `threading.Lock`, busy `threading.Event`, and `WebUI` bridge, so two
sessions can have turns in flight simultaneously. A chat turn is started
by `POST /api/chat/send`, which runs `run_turn` on a worker thread and
streams progress to the browser through the SSE event bus
(`app.state.bus`, `mu/gui/bus.py`). The same session refuses a second
concurrent turn (409). A turn in flight can be cancelled with
`POST /api/chat/interrupt`. Slash commands sent to `/api/chat/send` are
dispatched inline and their result published as a `command_result` event.

### 3. Explicit approval / prompt handling

`PromptStore` (`mu/gui/prompts.py`) stores pending prompts — approvals,
confirmations, and choice requests — as first-class objects keyed by a
`prompt_id`. When a modifying tool call needs a decision, the `WebUI`
opens a prompt (returning a `prompt_id` + an event the worker thread
waits on), the GUI renders the structured payload (including
modifications/diffs), and the user's decision arrives via
`POST /api/prompts/{prompt_id}/answer` (or `/cancel`), which releases
the worker and resumes the turn.

### 4. HTTP API surface

Routers are mounted under `/api/*` (`mu/gui/app.py`):

| Prefix | Router | Surface |
| --- | --- | --- |
| `/api/sessions` | `sessions` | session list/load/create/delete, runtime/workspace/staged-file state |
| `/api/providers` | `providers` | provider + model switching |
| `/api/chat` | `chat` | `/send`, `/interrupt`, `/commands`, `/completions`, `/history/search` |
| `/api/modes` | `modes` | agent-mode switching + mode metadata |
| `/api/prompts` | `prompts` | `/{prompt_id}/answer`, `/{prompt_id}/cancel` |
| `/api` | `inspector` | variable/config inspection + `/set`/`/unset` + layer budgets |
| `/api/teacher` | `teacher` | teacher-mode course CRUD |
| `/api/feature` | `feature` | feature plan state/create/approve/load/archive + task transitions |
| `/api/research` | `research` | research subcommands + citation engine |
| `/api/security` | `security` | security-mode audit surface |
| `/api/loop` | `loop` | loop-mode control |
| `/api/debug` | `debug` | debug-mode surface |
| `/api/skills` | `skills` | skill list/inspect/toggle |
| `/api/audio` | `audio` | audio I/O |
| (events) | `chat.events_router` | SSE stream — live tool execution, headless status, command/runtime/workspace mutations |

The SSE event bus (`mu/gui/bus.py`) is the single subscribe channel the
GUI uses for: token-by-token assistant output, live tool activity,
approval/prompt notifications, command results, and runtime/workspace
state mutations.

## What Is Still Missing?

Nothing here blocks a first GUI implementation, but there are still **recommended next improvements** if the goal is a more production-ready server stack.

### Recommended next improvements

#### A. Streaming transport

Server-Sent Events are now available, which removes polling as a requirement for the GUI. If the project later needs richer bidirectional browser semantics, WebSockets are the next likely step.

- token-by-token assistant output
- live tool activity
- approval notifications
- task state changes

**Recommendation:** SSE is the right default for the current GUI phase; only add WebSockets if interactive client push/input patterns require them.

#### B. Task cancellation — ✅ done

Turns in flight can be cancelled with `POST /api/chat/interrupt`
(`mu/gui/routers/chat.py`), which signals the running turn to stop at
the next iteration boundary and emits an `interrupted` result event. No
further work needed here.

#### C. Upload/download primitives

The current staged-file support is path-based. That works for a local desktop GUI, but browser-based or remote clients usually need:

- binary upload endpoints
- attachment metadata
- generated artifact download endpoints

**Recommendation:** add multipart upload support if the GUI will ever run remotely or in a browser.

#### D. Persistence across server restarts

Tasks and approvals are currently in-memory. If the server restarts, pending approvals and task state are lost.

**Recommendation:** keep this as-is for local GUI development, but persist tasks/approvals if you want robust recovery semantics.

#### E. Authentication and access control

The current server is designed for trusted/local usage and does not implement authentication.

**Recommendation:** optional for local desktop GUI; required before exposing the server beyond localhost.

#### F. Multi-session concurrency model — ✅ done

The server now keeps a **session registry** (`app.state.sessions`) rather
than a single loaded session: every loaded `Session` is keyed by name
with its own lock, busy event, and `WebUI` bridge, so multiple GUI tabs
can hold isolated concurrent conversations at once. This is no longer a
follow-up — it shipped.

## Resolution

For the stated goal — **implement the full server stack first so GUI development can proceed** — the architecture is now in a good enough state to move forward.

### Resolution decision

- **Mark as resolved for local GUI development:** yes
- **Block GUI work pending further server changes:** no

### Follow-up suggestions (non-blocking)

1. add multipart file upload/download if the GUI will not always be local
2. add auth only if the server will be exposed outside localhost
3. persist in-memory prompt/approval state across server restarts if robust recovery is needed
