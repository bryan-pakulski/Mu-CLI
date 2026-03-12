# Mu-CLI: Finalized Design and Implementation Plan

## 1) Vision and Product Goals
Mu-CLI is a provider-agnostic, local-first agentic platform for autonomous (or human-guided) software work.

It delivers three coordinated products:
1. **Mu-CLI Server (Python)** — system of record for sessions, jobs, tools, memory, policy, and telemetry.
2. **Mu-CLI CLI** — fast terminal UX for developers and automation.
3. **Mu-CLI GUI** — visual console for orchestration, observability, and intervention.

### Primary objectives
- Match the practical capabilities expected from Codex/Claude Code/Gemini CLI class tools.
- Be provider-neutral while making **Ollama first-class** from day one.
- Support both autonomous execution and interactive approval-driven operation.
- Be auditable, policy-driven, and production-friendly.

---

## 2) Design Principles
- **Server-centric architecture**: all stateful agent execution and lifecycle management lives in the Python server.
- **Provider abstraction, Ollama optimization**: generic provider contract with advanced Ollama support (streaming, tools, reasoning traces if available).
- **Durable agent runtime**: sessions and jobs are resumable and recoverable.
- **Local-first security**: sandboxing, policy gates, explicit approvals, complete audit trails.
- **Composable extensibility**: pluggable providers, tools, skills, memory backends, and loop strategies.
- **Observable by default**: full tracing for token usage, latency, tool outcomes, failures, reprompts, and loop behavior.

---

## 3) System Context

### Users and operating modes
- **Autonomous mode**: user provides objective + constraints; agent executes end-to-end under policy.
- **Interactive mode**: agent requests approvals or clarifications for gated actions.
- **Hybrid mode**: autonomous until policy or confidence thresholds require intervention.

### High-level boundaries
- **Server**: owns session/job state, execution loop, policies, tool dispatch, memory, telemetry, provider adapters.
- **CLI/GUI**: stateless clients over API + streaming channels.
- **Workspace runtime**: isolated filesystem/process environment where tools execute.

---

## 4) Architecture (Final)

## 4.1 Components
1. **API Gateway (FastAPI)**
   - REST endpoints for session/job/tool management.
   - WebSocket/SSE for live token + event streaming.
2. **Session Manager**
   - Create/resume/terminate sessions.
   - Persist conversation state, context windows, summaries, and memory references.
3. **Job Orchestrator**
   - Manages long-running jobs with lifecycle states.
   - Supports cancellation, retry, checkpointing, and resume.
4. **Agent Runtime Engine**
   - ReAct-style iterative loop with pluggable strategies:
     - Research
     - Interactive
     - Debugging
     - YOLO (high autonomy)
5. **Provider Router**
   - Unified interface for LLM providers.
   - Priority-tuned adapter for Ollama.
6. **Tool Registry + Executor**
   - Dynamic discovery and registration.
   - Policy-aware invocation and structured results.
7. **Skill Registry + Loader**
   - Discovers instruction bundles and templates.
   - Scope/precedence model for instruction application.
8. **Policy Engine**
   - Approval policies (allow/deny/ask/escalate).
   - Context-aware rules by tool, path, risk class, and session mode.
9. **Memory + Context Subsystem**
   - Session memory (short-term) + workspace memory (long-term).
   - Summarization and retrieval with optional embeddings backend.
10. **Telemetry + Audit Pipeline**
    - Structured events, traces, token accounting, latency, errors.
    - Exporters (OpenTelemetry-first).

## 4.2 Data stores
- **PostgreSQL**: source of truth for sessions/jobs/events/policies/config.
- **Redis**: ephemeral queues, locks, stream fanout, cancellation signals.
- **Object store/local disk**: artifacts (logs, patches, snapshots).
- **Optional vector store**: semantic memory retrieval.

## 4.3 Deployment model
- **Single-node local dev** (default): all components in one process + local DB/Redis.
- **Scalable mode**: API and workers split; queue-backed orchestration.

---

## 5) Canonical Data Model

### Session
- `session_id`
- `workspace_path`
- `mode` (autonomous/interactive/hybrid + loop strategy)
- `provider_preferences` (ordered providers, model selection)
- `policy_profile`
- `context_state` (messages, summary pointers, memory refs)
- `status` (active/paused/completed/failed)

### Job
- `job_id`, `session_id`
- `goal`, `constraints`, `acceptance_criteria`
- `state` (`queued`, `running`, `awaiting_approval`, `blocked`, `completed`, `failed`, `cancelled`)
- `checkpoints`
- `result_artifacts`

### Tool call event
- `tool_name`, `input`, `output`, `exit_code`, `duration_ms`
- `approval_state`, `policy_decision`, `risk_level`

### Telemetry event
- `trace_id`, `span_id`, `provider`, `model`
- `token_in`, `token_out`, `latency_ms`
- `retry_count`, `error_type`

---

## 6) Provider Architecture (Ollama First)

## 6.1 Provider interface
Each provider adapter must implement:
- `generate()` (non-streaming)
- `stream()` (token/event streaming)
- `tool_call_support()` (native or emulated)
- `thinking_support()` (native metadata if model supports it)
- `cancel()`
- `health_check()`

## 6.2 Ollama first-class requirements
- Optimized streaming parser.
- Model capability matrix (tools, context length, reasoning tags).
- Local model lifecycle helpers (pull/check/warm-up).
- Sensible defaults for local operation and fallback models.

## 6.3 Provider fallback strategy
- Ordered provider chain per session.
- Failure class aware fallback (timeout vs capability mismatch vs quota).
- Deterministic routing logs for auditability.

---

## 7) Agent Loop and Execution Modes

Core loop (customizable):
1. Ingest goal + current context.
2. Plan next step.
3. If action requires tool: policy check -> approval flow -> execute.
4. Observe result, update memory/context.
5. Evaluate stop condition or continue.
6. Emit structured trace event.

Loop variants:
- **Research mode**: broad exploration, strong citation/logging.
- **Interactive mode**: frequent checkpoints and confirmations.
- **Debugging mode**: tighter iteration, test-first bias.
- **YOLO mode**: maximum autonomy with guardrails.

---

## 8) Tools, Skills, and Approvals

## 8.1 Tools
- Dynamic discovery from configured directories/manifests.
- Tool metadata includes risk class, idempotency, timeout, capability tags.
- Standardized request/response schema for all tool executions.

## 8.2 Skills
- Skill bundles loaded by name/trigger/rules.
- Instruction precedence and scoping supported.
- Optional templates/scripts/assets included in skill package.

## 8.3 Approval and policy model
- Policy decisions: `allow`, `deny`, `ask`, `escalate`.
- Rule inputs:
  - tool type
  - filesystem scope
  - network access
  - command risk heuristics
  - session mode
- Human approval channel exposed consistently in CLI and GUI.

---

## 9) Observability and Analytics

Required telemetry dimensions:
- Token usage by provider/model/session/job.
- Tool usage, success/failure rates, latency, retries.
- Loop metrics (iterations, reprompts, stalls, dead-ends).
- Approval metrics (requests, grants/denials, wait times).

Output channels:
- Real-time stream (CLI/GUI).
- Query API for historical analytics.
- OpenTelemetry export for external stacks.

---

## 9.1 Workspace Discovery and Codebase Indexing

To improve first-pass codebase understanding, Mu-CLI includes a **Workspace Discovery Engine**.

### Goals
- Provide fast, broad awareness of repository structure without full AST parsing on every run.
- Build and maintain a searchable index of workspace files.
- Generate lightweight semantic descriptions to help the agent plan navigation before deep reads.

### Core responsibilities
- Crawl and index files by absolute + workspace-relative path.
- Track metadata (size, extension, modified timestamp, hash/version marker).
- Create per-file descriptions (e.g., purpose, likely subsystem, key symbols, and dependency hints).
- Flag binary/large/generated files for skip or low-priority treatment.
- Maintain incremental updates by detecting changed files only.

### Discovery pipeline
1. Enumerate workspace files with ignore rules (`.gitignore`, config excludes, generated dirs).
2. Classify files (code/config/docs/tests/assets/generated/binary).
3. Extract lightweight features (imports, top-level symbols, headings, test names, entrypoints).
4. Generate/update file description records.
5. Persist index snapshots and expose query APIs to runtime/CLI/GUI.

### Stored index record (minimum)
- `path`
- `file_type`
- `language`
- `last_modified`
- `content_hash`
- `description`
- `key_symbols`
- `tags` (e.g., `entrypoint`, `test`, `config`, `generated`)

### Runtime usage
- Agent loop can request “map workspace” context to ground planning.
- Retrieval can prioritize high-signal files from descriptions/tags before full content reads.
- Resume flows can use cached index state for faster startup.

### Extensibility
- Pluggable analyzers per language/framework.
- Optional deeper parsing and embeddings in later phases.
- Policy hooks can restrict indexing for sensitive paths.

---

## 10) API Surface (v1)

### Session APIs
- `POST /sessions`
- `GET /sessions/{id}`
- `POST /sessions/{id}/resume`
- `POST /sessions/{id}/pause`
- `POST /sessions/{id}/terminate`

### Job APIs
- `POST /sessions/{id}/jobs`
- `GET /jobs/{id}`
- `POST /jobs/{id}/cancel`
- `POST /jobs/{id}/retry`

### Interaction APIs
- `POST /jobs/{id}/input` (human input)
- `POST /jobs/{id}/approvals/{approval_id}`
- `GET /jobs/{id}/events` (or stream)

### Registry APIs
- `GET /providers`
- `GET /tools`
- `GET /skills`
- `GET /policies`

### Streaming
- `WS /stream/sessions/{id}`
- Event types: tokens, tool_calls, approvals, job_state, logs, telemetry.

---

## 11) Deliverables (Three Entities)

## A) Mu-CLI Server (Python, required)
**Tech recommendation**
- FastAPI + Pydantic + SQLAlchemy.
- Celery/RQ or native asyncio worker pool (choose one in Phase 0).
- PostgreSQL + Redis.

**Must-have capabilities**
- Session and job lifecycle with persistence/resume.
- Pluggable providers with Ollama adapter first.
- Streaming outputs + tool calls + approvals.
- Policy engine and audited tool execution.
- Telemetry pipeline + event log API.

## B) Mu-CLI CLI
**Role**
- Primary developer interface for local coding workflows.

**Must-have capabilities**
- Create/resume sessions.
- Start jobs with mode selection.
- Real-time stream rendering (tokens, tool events, approvals).
- Interactive approval prompts and inline user guidance.
- Artifact/log retrieval and replay.

## C) Mu-CLI GUI
**Role**
- Visual operational cockpit for visibility, debugging, and team workflows.

**Must-have capabilities**
- Session/job dashboards.
- Live timeline of model/tool/approval events.
- Manual intervention panel (approve/deny/input/cancel/retry).
- Policy and provider configuration views.
- Analytics panels for usage, latency, and failure trends.

---

## 12) Implementation Plan

Status legend: `[ ]` not yet started, `[~]` in progress, `[x]` completed.

## [x] Phase 0 — Foundation (1–2 weeks)
- [x] Finalize architecture decisions and schemas.
- [x] Stand up repo structure, CI, formatting, linting, typing.
- [x] Define provider/tool/skill/policy interfaces.

**Exit criteria**
- [x] Architecture decision record complete.
- [x] Baseline server boots with health endpoint.

## [x] Phase 1 — Core Server Runtime (2–4 weeks)
- [x] Build sessions/jobs persistence and baseline lifecycle.
- [x] Implement agent loop engine with pluggable modes.
- [x] Implement event stream and job state transitions.
- [x] Harden end-to-end runtime behavior (idempotency, retries, failure handling).

**Exit criteria**
- [x] Run/resume/cancel job flow works end-to-end locally.

## [x] Phase 2 — Ollama First-Class + Provider Layer (2–3 weeks)
- [x] Implement initial Ollama adapter with generate/stream/health.
- [x] Add generic provider interface.
- [x] Add second provider adapter (for validation).
- [x] Add provider fallback routing.

**Exit criteria**
- [x] Ollama-backed jobs run reliably with streaming and tool interactions.

## [x] Phase 3 — Tools, Skills, Policy, Approvals (3–4 weeks)
- [x] Dynamic tool and skill discovery (bootstrap registry + API exposed).
- [x] Policy engine with allow/deny/ask/escalate.
- [x] CLI/GUI approval flows (server APIs + streaming lifecycle hooks implemented).

**Exit criteria**
- [x] Risky actions block for approval; audit trail complete.

## [x] Phase 3.5 — Workspace Discovery and Codebase Indexing (pre-CLI)
- [x] Implement workspace discovery/indexing engine with filepath-centric records.
- [x] Generate lightweight file descriptions for rapid codebase orientation.
- [x] Expose index build/query APIs for session workflows.
- [x] Add incremental refresh + scheduling + prioritization heuristics.

**Exit criteria**
- [x] Agents can map a workspace quickly without full-file parsing.

## [x] Phase 4 — CLI MVP (2–3 weeks)
- [x] Build CLI commands for session/job lifecycle.
- [x] Add live rendering for tokens/events (event polling stream view in CLI).
- [x] Add interactive prompts for approvals and user input.

**Exit criteria**
- [x] CLI supports full local coding loop without GUI (interactive `loop` command).

## [ ] Phase 5 — GUI MVP (3–5 weeks)
- [ ] Build dashboard, job timeline, intervention controls.
- [ ] Add provider/policy configuration pages.

**Exit criteria**
- [ ] GUI provides parity for core lifecycle + approvals.

## [ ] Phase 6 — Observability, Hardening, and GA (2–4 weeks)
- [ ] OpenTelemetry exporter, metrics dashboards, error budget tracking.
- [ ] Load tests, failure injection, persistence recovery tests.
- [ ] Security review of sandboxing and policy bypass resistance.

**Exit criteria**
- [ ] Production-readiness checklist met.

---

## 13) Suggested Repository Layout

```text
mu-cli/
  server/
    app/
      api/
      core/
      providers/
        ollama/
      runtime/
      tools/
      skills/
      policies/
      telemetry/
      persistence/
    tests/
  cli/
    src/
    tests/
  gui/
    src/
    tests/
  docs/
    architecture/
    api/
    operations/
```

---

## 14) Risks and Mitigations
- **Provider capability drift**: maintain capability matrix + adapter conformance tests.
- **Runaway agent loops**: max-iteration/time/token budgets + dead-loop detectors.
- **Unsafe tool execution**: strict sandbox, policy gates, default-deny high-risk classes.
- **State corruption/resume bugs**: checkpointing + replayable event sourcing patterns.
- **Local resource limits (Ollama models)**: adaptive model selection and graceful degradation.

---

## 15) Success Metrics
- Time-to-first-successful-job (local) under 10 minutes.
- >=95% successful session resume after forced restart in test harness.
- Full auditability of tool calls and approvals (100% trace coverage).
- Median token stream latency acceptable for interactive UX.
- Ollama workflows pass conformance suite for streaming + tool-calls.

---

## 16) Immediate Next Actions
1. Approve this finalized design baseline.
2. Create ADRs for worker model, DB schema, and event transport choice.
3. Implement Phase 0 scaffolding and CI.
4. Start Phase 1 server runtime with persistence + streaming.
5. Begin Ollama adapter in parallel behind provider interface.
