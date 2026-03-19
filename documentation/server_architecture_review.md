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

### 1. Headless UI adapter

`HeadlessUI` provides the non-interactive bridge between the core session loop and HTTP-driven clients. It logs status/tool activity and now forwards approval requests into the approval manager rather than silently auto-prompting. This is the key abstraction that lets GUI clients reuse the same session logic as the terminal UI.

### 2. Async task execution

`TaskManager` wraps long-running message execution into tracked tasks. This matters for GUI work because chat turns can now:

- start asynchronously
- move through `pending`, `running`, `awaiting_approval`, `completed`, or `error`
- be polled independently of the original request

### 3. Explicit approval handling

`ApprovalManager` stores pending approvals as first-class server objects. A modifying tool call can pause execution, emit a structured approval payload (including modifications/diffs), and resume once the GUI submits a decision.

### 4. HTTP API surface

The current API surface covers:

- health/state/history
- sessions
- runtime config
- workspaces
- staged files
- tools
- async tasks
- approvals
- message turns
- slash commands
- SSE-driven state mutation notifications for commands/tools/runtime/workspaces/staged files
- live SSE trace events for tool execution and headless status output

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

#### B. Task cancellation

Tasks can be started and polled, but there is no explicit cancellation endpoint yet.

**Recommendation:** add task cancellation before introducing richer multi-panel GUI workflows.

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

#### F. Multi-session concurrency model

The server works with a single in-process `Session` object at a time. This is fine for a single-user GUI, but if you later want multiple active GUI tabs with isolated concurrent conversations, you may want a session registry instead of one loaded session plus saved session switching.

**Recommendation:** not a blocker for the current GUI effort.

## Resolution

For the stated goal — **implement the full server stack first so GUI development can proceed** — the architecture is now in a good enough state to move forward.

### Resolution decision

- **Mark as resolved for local GUI development:** yes
- **Block GUI work pending further server changes:** no

### Follow-up suggestions (non-blocking)

1. add task cancellation
2. add multipart file upload/download if the GUI will not always be local
3. add auth only if the server will be exposed outside localhost
