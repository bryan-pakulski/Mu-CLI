# Mu-CLI Tooling Harness Architecture

## Purpose

This document defines a staged plan for evolving Mu-CLI's tooling harness into a more explicit, testable, and GUI-friendly execution layer. The focus is to keep the current strengths of the project—structured tool metadata, approval-aware execution, batch support, and server-side task orchestration—while making the harness easier to extend, observe, and verify.

## Current Baseline

The repository already contains the core building blocks for a tooling harness:

- `core/tools.py` defines tool metadata, approval requirements, modification previews, and the dispatch layer for tool execution.
- `core/session.py` coordinates tool-call execution inside the conversational loop, including approvals, structured tool results, memory promotion, and collation-aware behavior.
- `core/server.py` exposes headless execution through async tasks, approval workflows, direct tool execution, and server-sent events for external clients.
- `tests/test_batch_tool.py` and `tests/test_server_mode.py` cover important pieces of batch execution and approval-aware orchestration.

That means this work is not a greenfield design. It is a refinement effort: formalize the harness boundaries, clarify responsibilities, and create a roadmap for implementation.

## Architecture Goals

1. Establish a single mental model for how tools are described, validated, executed, approved, and observed.
2. Reduce coupling between raw tool functions and higher-level orchestration paths.
3. Make direct tool execution, conversational tool execution, and server-driven tool execution behave consistently.
4. Improve testability by isolating validation, approval planning, execution, and result formatting.
5. Prepare the tooling harness for richer future integrations such as GUI clients, artifact handling, and more advanced automation tasks.

---

## Stage 1: Baseline Inventory and Contract Definition

**Stage status:** [x] completed

### Objectives

- [x] Document the current tooling harness boundaries and responsibilities.
- [x] Define the canonical lifecycle for a tool call from request to result.
- [x] Identify which behaviors are contractual versus incidental implementation details.

### Action Points

- [x] Inventory the existing tool lifecycle across `core/tools.py`, `core/session.py`, and `core/server.py`.
- [x] Define a canonical execution flow covering:
  1. tool lookup
  2. argument validation
  3. approval detection
  4. modification preview generation
  5. execution
  6. result shaping
  7. event emission
  8. persistence or memory side effects
- [x] Write down the minimum harness contract for every tool:
  - name and schema
  - approval behavior
  - side-effect classification
  - result shape expectations
  - error-handling expectations
- [x] Separate tool implementation concerns from harness orchestration concerns so future changes do not blur those layers.

### Inventory: Current Harness Boundaries and Responsibilities

| Layer | Primary responsibility | What it currently owns |
| --- | --- | --- |
| `core/tools.py` | Tool registry and raw execution primitives | Declares `ToolDefinition` metadata, approval flags, collated-tool membership, modification preview helpers, bounds checks, and the dispatcher that calls the underlying tool functions. |
| `core/session.py` | Conversational harness orchestration | Collects model tool calls, pre-computes approval candidates, renders diffs, requests approval, executes tools through memory-aware helpers, applies collation rules, emits tool traces, shapes structured results, auto-promotes memory, and appends persisted `tool_result` records. |
| `core/server.py` | Headless and API-facing orchestration | Wraps message/tool execution in async tasks, converts approvals into first-class server objects, publishes SSE events, validates API payloads, and exposes direct tool execution through `/api/tool` and chat execution through `/api/message`. |

#### Implementation concerns vs orchestration concerns

**Tool implementation concerns**

- Read or mutate workspace state.
- Enforce local path and file-system boundaries.
- Produce the raw textual result for a tool invocation.
- Detect tool-local failures and return error text.

**Harness orchestration concerns**

- Decide whether a tool can run in the current session.
- Determine whether approval is required and what diff/review payload to show.
- Decide whether output is shown immediately, collated, or serialized into a structured result.
- Publish traces, task updates, approval events, and persisted conversation history.
- Trigger memory/scratchpad side effects that are not inherent to the raw tool implementation.

### Canonical Tool Lifecycle

The current codebase supports one canonical lifecycle, even though pieces of it are split across the dispatcher, session loop, and server entrypoints.

1. **Tool lookup**  
   Resolve tool metadata from `TOOLS` using the incoming tool name.
2. **Argument validation**  
   Validate required API payload fields and reject empty path-like arguments before execution.
3. **Approval detection**  
   Determine whether the tool requires approval based on `ToolDefinition.requires_approval`, `strict_mode`, batch composition, and the `yolo` bypass.
4. **Modification preview generation**  
   Generate `(original, modified, filename)` tuples through `get_modifications(...)` so the caller can review diffs before approval.
5. **Execution**  
   Run the tool through the session memory-aware path or the direct dispatcher path.
6. **Result shaping**  
   Decide whether the result is returned raw, collated into the buffer, or wrapped in a structured tool-result envelope.
7. **Event emission**  
   Publish UI traces, approval events, task state changes, and SSE envelopes for headless clients.
8. **Persistence and memory side effects**  
   Append tool results to history, save session state, and optionally auto-promote high-value structured results into durable memory.

### Tool Contract Model

Every current tool can be mapped to the same minimum contract model:

| Contract field | Meaning |
| --- | --- |
| `name` and `schema` | The public identifier and JSON-schema-like parameter contract from `ToolDefinition`. |
| `approval behavior` | Whether the tool requires review directly, indirectly through composition, or never. |
| `side-effect classification` | Read-only, mutating, composite, git/network, or memory/control-plane behavior. |
| `result shape expectations` | Raw text only, raw text + structured summary, collated delivery, or history/memory side effects. |
| `error-handling expectations` | Return `Error:`-prefixed text or other textual failure output that the harness can pass through or wrap. |

#### Tool-to-contract mapping

| Tool(s) | Approval behavior | Side-effect classification | Result shape expectations | Error-handling expectations |
| --- | --- | --- | --- | --- |
| `get_workspace_details`, `read_file`, `search_for_string`, `get_chunk`, `list_dir`, `list_agent_tasks`, `git_status`, `git_log`, `git_diff`, `git_branch`, `url_grounding`, `read_document` | No approval by default | Read-only discovery / inspection | Eligible for collation; can also be emitted as structured results with previews and parsed metadata | Return textual errors directly to the harness; collate only non-error results |
| `get_current_time` | No approval | Read-only utility | Immediate raw text or structured summary | Return textual errors directly |
| `write_file`, `apply_diff`, `run_agent_task`, `git_checkout`, `git_add`, `git_commit`, `git_push`, `git_pull`, `git_init`, `git_merge_request` | Requires approval unless `yolo` is enabled; may be forced by `strict_mode` | Mutating filesystem, process, or git actions | Immediate execution after approval; structured results may include changed-file/task previews | Preview errors or malformed patch errors must block approval; execution failures return textual errors |
| `batch_job` | Approval depends on nested commands via recursive inspection | Composite orchestration across mixed read/write tools | Aggregates child tool output into a batch result; approval planning and modifications expand over nested commands | Rejects nested `batch_job`; child failures are surfaced in the combined output |
| `flush` | No approval | Control-plane / collation | Emits buffered collated content or a no-op message | Empty buffer is surfaced as a textual no-data result |
| `save_memory`, `search_memory`, `list_memory`, `save_scratchpad`, `search_scratchpad`, `list_scratchpad`, `clear_scratchpad` | No approval | Memory and scratchpad control-plane | Returned through session-managed memory helpers and structured summaries | Return textual status/errors directly |

### Contractual vs Incidental Behaviors

#### Contractual behaviors

- Tools are discovered from the shared `TOOLS` registry.
- Empty path-like args are rejected before dispatch.
- Approval-aware tools can be previewed through `get_modifications(...)` before execution.
- Headless/server clients observe approval and task progress through explicit server-side objects and events.
- Tool results can be serialized into structured envelopes for downstream consumers.
- Read-heavy discovery tools may be collated and deferred until `flush`.

#### Incidental behaviors to avoid depending on long term

- The dispatcher is a long `if/elif` chain rather than a dedicated registry-backed handler object.
- Many failures are identified by string conventions such as `Error:` prefixes instead of typed result objects.
- Approval planning is split between session logic, direct server execution, and tool-local preview helpers.
- Structured result shapes are partly tool-specific and partly inferred from string content.
- Batch execution currently concatenates child results into one textual report instead of a richer nested structure.

### Exit Criteria

- [x] The repository has a documented canonical lifecycle for tool execution.
- [x] Every existing tool can be mapped to the same contract model.
- [x] The team can clearly answer which layer owns validation, approvals, execution, and output shaping.

## Stage 2: Tool Registry and Execution Model Hardening

**Stage status:** [x] completed

### Objectives

- [x] Refine the tool registry into a more explicit harness surface.
- [x] Ensure each tool can be reasoned about using structured metadata rather than dispatcher-specific conditionals.
- [x] Reduce ad hoc branching in the execution path.

### Action Points

- [x] Introduce or formalize a richer internal tool descriptor model that can express:
  - whether the tool is read-only, mutating, or composite
  - whether it supports previewable modifications
  - whether it is safe for direct server execution
  - whether its output is eligible for collation or structured summarization
- [x] Review `batch_job` and other composite flows to ensure they delegate through the same harness path as normal tools.
- [x] Standardize error payload semantics so invalid args, missing paths, unsupported nested commands, and runtime failures follow a predictable format.
- [x] Identify opportunities to move execution-specific branching out of the monolithic dispatcher and into descriptor-backed handlers or helper modules.

### Proposed Descriptor Model

The current `ToolDefinition` surface is enough for model-facing tool schema export, but not rich enough to explain harness behavior without following the dispatcher and the session/server orchestration paths. Stage 2 therefore formalizes a richer **internal tool descriptor** that can wrap the existing provider-facing `ToolDefinition`.

| Field | Purpose | Example values |
| --- | --- | --- |
| `definition` | Existing public tool schema used by providers and API state payloads | `ToolDefinition(name=..., parameters=...)` |
| `execution_kind` | High-level execution category used by the harness | `read`, `mutate`, `composite`, `memory`, `control` |
| `preview_policy` | Whether modification previews are available before approval | `none`, `optional`, `required` |
| `server_policy` | Whether `/api/tool` should allow direct execution | `allowed`, `blocked`, `session_only` |
| `result_mode` | Expected harness delivery mode | `raw`, `structured`, `collated`, `structured+collated` |
| `handler_key` | Named implementation entrypoint instead of `if/elif` dispatch branching | `read_file`, `git.diff`, `memory.save`, `composite.batch` |
| `error_mode` | Expected error contract for the tool | `text_error`, `result_envelope` |
| `summary_builder` | Optional structured-result helper used after raw execution | `parse_search_results`, `parse_workspace_details`, `git_preview` |

#### Descriptor classification for the current toolset

| Descriptor group | Tools |
| --- | --- |
| Read / collatable | `get_workspace_details`, `read_file`, `search_for_string`, `get_chunk`, `list_dir`, `list_agent_tasks`, `git_status`, `git_log`, `git_diff`, `git_branch`, `url_grounding`, `read_document` |
| Read / immediate | `get_current_time` |
| Mutating with preview or approval planning | `write_file`, `apply_diff`, `run_agent_task`, `git_checkout`, `git_add`, `git_commit`, `git_push`, `git_pull`, `git_init`, `git_merge_request` |
| Composite | `batch_job` |
| Control-plane | `flush` |
| Memory-plane | `save_memory`, `search_memory`, `list_memory`, `save_scratchpad`, `search_scratchpad`, `list_scratchpad`, `clear_scratchpad` |

### Composite Tool Review

#### `batch_job`

Current behavior shows why Stage 2 is needed:

- `batch_job` is declared in the same `TOOLS` registry as normal tools, but it still branches manually inside the dispatcher.
- Approval and modification inspection recurse into nested commands, which is good, but result aggregation is textual rather than structurally nested.
- Nested `batch_job` is rejected ad hoc in the dispatcher instead of by a reusable descriptor rule.

#### Stage 2 decision

Composite tools should continue to be first-class registry entries, but they should delegate through the same harness stages as single tools:

1. descriptor lookup
2. argument validation
3. nested child planning
4. approval planning
5. child execution via shared handlers
6. structured result aggregation

This means `batch_job` should be treated as a composite descriptor, not as a special-case exception bolted onto the dispatcher.

### Standardized Error Semantics

The current codebase largely communicates failures as text strings such as `Error: ...`. That behavior is useful for backward compatibility, but Stage 2 needs a predictable harness-level model.

#### Proposed harness error categories

| Error code | Meaning | Typical current source |
| --- | --- | --- |
| `invalid_args` | The request shape is wrong or required args are missing/empty | empty path validation, malformed `commands` list |
| `not_found` | The named tool or requested file/resource does not exist | unknown tool, missing file |
| `access_denied` | The request violates workspace or tool enablement boundaries | out-of-bounds file access, disabled tool |
| `approval_required` | Execution is blocked pending review | mutating tools outside `yolo` |
| `preview_failed` | A diff or preview could not be generated safely | malformed patch, missing `patch` utility |
| `execution_failed` | The underlying tool or subprocess failed | git/process failures, runtime exceptions |
| `unsupported` | The harness rejects a structurally unsupported pattern | nested `batch_job`, unsupported direct-server tool |

#### Compatibility rule

Until runtime code adopts a typed result envelope everywhere, the harness should preserve existing textual compatibility by keeping the top-level preview human-readable while also attaching a normalized error code whenever the result is structured.

### Execution-Model Refactor Targets

The current dispatcher and orchestration logic can be split into reusable helper layers without changing user-visible behavior.

| Target helper/module | Responsibility currently spread across code |
| --- | --- |
| `tool_registry` | Resolve descriptors, expose provider-facing schemas, and answer classification questions without scanning `TOOLS` manually |
| `tool_preflight` | Validate args, determine disabled-tool policy, and normalize direct-server eligibility |
| `approval_planner` | Centralize `get_modifications(...)`, preview viability checks, and approval payload generation |
| `tool_runner` | Execute descriptor handlers and composite child commands through a shared path |
| `result_shaper` | Apply collation, structured summaries, and normalized error/result envelopes |
| `server_tool_adapter` | Reuse the same preflight/approval/result logic for `/api/tool` instead of duplicating approval behavior |

### Stage 2 Outcome

Stage 2 clarifies that the registry should become the authoritative explanation of harness behavior. The dispatcher may still exist as an implementation detail, but it should eventually be driven by descriptor-backed handlers rather than by an ever-growing `if/elif` chain.

### Exit Criteria

- [x] Tool metadata is sufficient to explain harness behavior without reading multiple execution branches.
- [x] Composite tools follow the same harness model as single tools.
- [x] Tool errors are structurally consistent enough for both CLI and server consumers.

## Stage 3: Approval and Modification Planning Layer

### Objectives

- Make approval logic a first-class harness stage instead of a loosely adjacent behavior.
- Standardize how modification previews are produced and surfaced.
- Ensure all mutating execution paths are predictable for both terminal and server clients.

### Action Points

- Extract or formalize an approval-planning layer that answers:
  - does this request require approval?
  - what files or resources may change?
  - can a preview be generated safely?
  - what should the user or GUI client review?
- Unify how single-tool and batch-tool modifications are expanded into reviewable units.
- Define fallback behavior for tools that mutate state but cannot generate exact previews.
- Tighten the contract between approval planning and execution so approval resolution always maps back to a concrete request payload.
- Verify that server-side approval objects expose everything a GUI needs without depending on terminal-specific rendering logic.

### Exit Criteria

- Approval planning is clearly separated from raw execution.
- Mutating tool requests consistently produce reviewable approval metadata.
- CLI and server approval flows use the same underlying planning rules.

---

## Stage 4: Structured Results, Events, and Artifact Semantics

### Objectives

- Improve downstream usability of tool results for the session loop, server API, and future GUI clients.
- Distinguish raw command output from structured harness-level summaries.
- Define how artifacts and multi-part tool outputs should be represented.

### Action Points

- Formalize a result envelope that can carry:
  - raw output
  - structured summary fields
  - error state
  - modified files
  - produced artifacts
  - telemetry metadata such as duration or execution source
- Align server trace events and session tool-result records around the same result concepts.
- Define how tools that produce large outputs, deferred outputs, or machine-readable payloads should expose them.
- Specify artifact semantics for future additions such as generated reports, uploads, downloadable files, or richer previews.
- Ensure structured results remain compact enough for the conversational loop while still being detailed enough for API consumers.

### Exit Criteria

- Tool results have a documented envelope that works across terminal, agentic, and server contexts.
- Event streams and persisted tool-result records reference the same core semantics.
- The architecture can describe how artifacts and large outputs will be handled without inventing a new model later.

---

## Stage 5: Harness-Oriented Testing Strategy

### Objectives

- Move from feature-specific tests toward a coherent harness verification strategy.
- Ensure that behavior is validated at the right layers.
- Prevent regressions as the harness becomes more modular.

### Action Points

- Define test coverage by layer:
  - registry/metadata tests
  - validation tests
  - approval-planning tests
  - execution tests
  - structured-result tests
  - server orchestration tests
- Expand composite-tool tests so batch flows are verified for mixed read/write operations, error propagation, and approval aggregation.
- Add golden-path and failure-path tests for server-driven direct tool execution and approval round-trips.
- Identify which harness rules should be enforced with unit tests versus integration tests.
- Add regression tests for any extracted helper modules before larger refactors land.

### Exit Criteria

- The harness has a documented test matrix tied to its architectural layers.
- Core behaviors are covered by deterministic tests instead of incidental end-to-end coverage alone.
- Future harness refactors can be made with confidence because expected behavior is explicitly tested.

---

## Stage 6: Incremental Rollout and Migration Plan

### Objectives

- Deliver improvements in small, low-risk increments.
- Preserve current behavior for end users while refactoring internals.
- Create a clear implementation order for follow-on engineering tasks.

### Action Points

- Implement the harness work in the following order:
  1. document contracts and lifecycle
  2. normalize metadata and result semantics
  3. isolate approval planning
  4. extract reusable harness helpers
  5. expand test coverage
  6. update server and session integration points to consume the refined interfaces
- Keep each change set behavior-preserving unless the change intentionally improves a documented inconsistency.
- Use temporary adapter functions where needed so existing tool implementations do not all need to be rewritten at once.
- Update developer-facing docs when harness contracts change so future contributors do not code against outdated assumptions.
- Track migration risks explicitly, especially around approval behavior, structured result formatting, and server event compatibility.

### Exit Criteria

- The implementation order is explicit and practical.
- The harness can be modernized without a disruptive rewrite.
- Session, CLI, and server integrations continue to work against the refined architecture.

---

## Immediate Task Backlog

The following tasks should be treated as the near-term starting point for implementation:

### Task A: Document the canonical harness lifecycle

#### Objectives

- Produce a single reference flow for tool execution.
- Remove ambiguity about where orchestration responsibilities live.

#### Action Points

- Capture the current lifecycle in this document or a follow-up implementation note.
- Map each lifecycle step to the owning module.
- Flag any steps currently duplicated across server and session paths.

#### Exit Criteria

- Contributors can trace a tool call through the system without reverse-engineering multiple files.
- Ownership boundaries are documented and reviewable.

### Task B: Normalize tool metadata

#### Objectives

- Make the registry expressive enough to drive harness decisions.
- Reduce special-case logic.

#### Action Points

- Audit existing tool definitions for missing metadata.
- Propose the additional fields needed for read/write/composite classification, preview support, and result handling.
- Identify which existing branching logic can be converted to metadata-driven behavior.

#### Exit Criteria

- A concrete metadata schema exists for the harness.
- The next refactor can rely on explicit metadata instead of scattered assumptions.

### Task C: Formalize approval planning

#### Objectives

- Treat approvals as a planned harness phase.
- Make modification previews predictable.

#### Action Points

- Define an approval planning API or helper boundary.
- Document how single and batch modifications are expanded into review payloads.
- Identify gaps where current tools cannot produce reliable previews.

#### Exit Criteria

- Approval handling has a dedicated design surface.
- Mutating tool requests can be reviewed consistently across clients.

### Task D: Define the structured result envelope

#### Objectives

- Unify how tool outputs are consumed by session logic and API clients.
- Prepare for richer artifacts and telemetry.

#### Action Points

- Specify the minimum envelope fields.
- Map current structured tool-result behavior onto the proposed envelope.
- Identify compatibility requirements for existing server events and history serialization.

#### Exit Criteria

- A documented result envelope exists.
- Future implementation work can update producers and consumers incrementally.

### Task E: Build the harness test matrix

#### Objectives

- Align tests with architectural responsibilities.
- Prevent regressions during refactoring.

#### Action Points

- List existing tests that already cover harness behavior.
- Identify missing tests by lifecycle stage.
- Prioritize the first wave of unit and integration coverage.

#### Exit Criteria

- There is a concrete test plan tied to the refined architecture.
- Refactor work can proceed with clear verification targets.

---

## Recommended Sequencing

1. Complete Task A and Task B first.
2. Use those outputs to drive Task C and Task D.
3. Finalize Task E before or alongside the first substantial refactor.
4. Only after those pieces are in place should larger code extraction or interface rewiring begin.

## Definition of Success

This architecture effort is successful when Mu-CLI has a tooling harness that is:

- **Explicit** in its contracts and lifecycle
- **Consistent** across conversational, direct, and server-driven execution
- **Approval-aware** without duplicating approval logic in multiple layers
- **Observable** through structured results and event streams
- **Testable** at the metadata, planning, execution, and orchestration levels
- **Incrementally adoptable** without a disruptive rewrite
