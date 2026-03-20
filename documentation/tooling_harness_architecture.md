# Tooling Harness Architecture Proposal

## Why this proposal exists

μCLI already exposes a useful tool surface and multiple providers, but the current tool loop is still fairly thin:

- provider adapters mostly pass through each vendor's native tool format
- `apply_diff` currently expects a strict unified diff payload
- tool execution success/failure is not normalized into a richer recovery state machine
- provider-specific reasoning/tool-call quirks are handled ad hoc inside each provider implementation
- visibility is strongest in the UI/server trace layer, but the harness does not yet produce a first-class "tool lifecycle" model that can be measured across providers

This document proposes a tool harness architecture aimed at **high-reliability tool usage across OpenAI, Gemini, and local models such as GPT-OSS 120B and Qwen 3.5**, with a specific focus on code modification workflows.

## Objectives

1. **Robust tool calling and fallback handling**, especially for file modifications and diffs.
2. **Flexible diff tools** so weaker local models are not forced to emit one exact patch syntax.
3. **>98% tool-call success rate** on strong hosted models and local 32B-class models.
4. **Provider-specific tuning** for tool and reasoning behavior without fragmenting the core loop.
5. **Much better visibility** into tool planning, execution, fallback, approval, and failure causes.

## Current constraints observed in the codebase

### Provider layer

The current provider abstraction standardizes messages and tool definitions well, but it does not yet describe provider capabilities such as:

- native tool support quality
- structured output reliability
- reasoning/thought-token handling
- partial/streaming tool call behavior
- argument coercion quirks
- whether the provider needs tool-result chain continuity metadata

Gemini already carries special handling for `thought_signature`, while OpenAI and Ollama are simpler pass-through adapters. That is a sign that the system is already paying a provider-specific cost, but without an explicit capability model.

### Tool schema layer

The current tool list exposes a single `apply_diff` mutation primitive that expects standard unified diffs. That is ideal for stronger models, but local models often fail in one of these ways:

- produce malformed hunks
- omit headers
- mix prose with patch content
- output a near-correct patch that could be repaired automatically
- choose `write_file` when a smaller edit primitive would have been safer

### Visibility layer

The server already emits useful trace and approval events, but the harness still lacks a normalized telemetry contract for:

- tool call parse failures
- auto-repair attempts
- fallback path chosen
- schema validation errors
- patch-application failure categories
- per-provider/per-model success rates by tool type

## Proposed architecture

The proposal introduces five new concepts:

1. **Tool Capability Profiles**
2. **A Canonical Tool Invocation IR**
3. **A Mutation Engine with multiple edit strategies**
4. **A Tool Reliability State Machine**
5. **A Tooling Observability pipeline**

---

## 1. Tool Capability Profiles

Add a provider/model capability profile that lives between the session loop and each provider adapter.

### New concept: `ProviderToolProfile`

This profile should be resolved at runtime from:

- provider name
- model name
- optional user overrides
- optional learned reliability history from previous runs

### Suggested shape

```python
@dataclass
class ProviderToolProfile:
    provider_name: str
    model_name: str
    native_tool_calling: bool
    prefers_json_schema_tools: bool
    supports_parallel_tool_calls: bool
    supports_reasoning_tokens: bool
    requires_thought_chain_return: bool
    max_tool_call_arguments_chars: int | None
    tool_call_examples_mode: str  # none | compact | verbose
    patch_strategy: str  # unified_diff | search_replace | write_full_file | hybrid
    strict_schema_validation: bool
    auto_repair_json: bool
    auto_repair_patch: bool
    force_single_tool_per_turn: bool
```

### Why this matters

This lets μCLI encode important provider/model differences explicitly instead of scattering them across prompts and adapters.

### Example defaults

- **OpenAI high-end models**: native tool calling on, strict schema validation on, unified diff preferred.
- **Gemini thinking models**: native tool calling on, thought-chain continuity required, single tool per turn preferred when the model is unstable.
- **Local Qwen / GPT-OSS 32B-120B**: native tool calling may be nominally available, but fallback to JSON-wrapped pseudo-tool blocks and search/replace patching should be enabled by default.

---

## 2. Canonical Tool Invocation IR

Today, each provider response is converted directly into `MessagePart(type="tool_call", ...)`. Keep that external contract, but introduce an internal normalized representation before execution.

### New concept: `ToolInvocation`

```python
@dataclass
class ToolInvocation:
    call_id: str
    source_provider: str
    source_model: str
    name: str
    raw_arguments: Any
    parsed_arguments: dict[str, Any] | None
    parse_status: str  # parsed | repaired | failed
    confidence: float
    repair_notes: list[str]
    raw_text_span: str | None
    thought_signature: str | None
```

### Responsibilities

The IR layer should:

1. parse provider-native tool calls
2. detect tool-like JSON in plain assistant text when native calling fails
3. repair malformed JSON when the repair is low-risk
4. validate arguments against the tool schema
5. coerce common argument mistakes
6. attach confidence and failure metadata

### Why this matters for local models

Many local models can conceptually select the right tool but fail on the last mile of formatting. The IR layer is where μCLI should recover from:

- trailing commas
- stringified dicts
- wrong field aliases
- prose-wrapped JSON
- single-item arrays emitted as scalars

That recovery path is essential for the >98% success target.

---

## 3. Mutation Engine with multiple edit strategies

This is the most important part of the proposal.

Instead of treating `apply_diff` as one rigid tool, introduce a **mutation engine** that can fulfill a requested modification through several interchangeable edit adapters.

### Key idea

Expose one logical mutation intent to the model, but allow the runtime to choose the safest concrete execution strategy.

### Recommended logical tools

#### A. `propose_file_change`
A higher-level tool for changing an existing file.

Suggested arguments:

- `filename`
- `change_intent`
- `edit_format`
- `payload`
- `expected_occurrences`
- `safety_level`

Supported `edit_format` values:

- `unified_diff`
- `search_replace`
- `replace_block`
- `json_patch`
- `full_file`

#### B. `create_file`
For new files only.

#### C. `delete_file`
For explicit deletion.

#### D. `inspect_file_region`
A mutation-adjacent helper that returns anchored context when the model needs better locality before editing.

### Mutation adapters beneath the tool

#### 1. Unified diff adapter
Best for strong models and human-reviewable edits.

Use when:

- provider profile says diff reliability is high
- hunks validate cleanly
- approval UX benefits from normal diff rendering

#### 2. Search/replace adapter
Best for local models.

Shape:

```json
{
  "filename": "core/tools.py",
  "edit_format": "search_replace",
  "payload": {
    "search": "old text",
    "replace": "new text",
    "all": false
  }
}
```

Advantages:

- far easier for 32B models to emit correctly
- deterministic validation
- easy ambiguity detection
- easy auto-retry with better context

#### 3. Replace-block adapter
Anchored by line ranges or sentinel markers.

Use when:

- a whole function/class block must change
- the model struggles with hunk headers
- search text is too large or too ambiguous

#### 4. Structured patch adapter
For JSON, YAML, TOML, or AST-aware edits where possible.

Examples:

- JSON patch for package/config files
- AST rewrite for Python imports or function signatures

#### 5. Full-file rewrite adapter
Last resort for small/medium files only.

Use when:

- all narrower edit modes fail
- the new file can be validated syntactically before write
- safety checks say the file is small enough

### Mutation planner

Before execution, the runtime should evaluate the proposed change against:

- file size
- number of matching search anchors
- patch parse validity
- syntax validation availability
- provider profile
- recent failure history for this model/tool

Then pick the highest-confidence adapter.

### Example fallback chain

1. try unified diff as emitted
2. if invalid but repairable, repair diff
3. if still invalid, transform into search/replace if anchors are obvious
4. if not possible, ask model for `replace_block`
5. if file is small, request `full_file`
6. if all else fails, return structured mutation failure with guidance

### Why this matters

This separates the **model-facing tool contract** from the **runtime edit mechanism**, which is the core requirement for compatibility with weaker local models.

---

## 4. Tool Reliability State Machine

Every tool call should move through a common lifecycle.

### Suggested states

- `emitted`
- `parsed`
- `schema_repaired`
- `schema_validated`
- `awaiting_approval`
- `executing`
- `auto_retrying`
- `fallback_selected`
- `succeeded`
- `failed_permanent`

### Failure categories

Track failures with stable machine-readable codes:

- `tool_call_not_parseable`
- `schema_validation_failed`
- `missing_required_arg`
- `unsafe_mutation`
- `patch_parse_failed`
- `patch_context_not_found`
- `multiple_match_ambiguity`
- `syntax_validation_failed`
- `provider_transport_error`
- `tool_runtime_exception`
- `approval_rejected`

### Retry policy

Not every failure should go back to the model immediately.

#### Safe automatic retries

- JSON fixup
- argument alias coercion
- diff header repair
- whitespace normalization
- re-running search/replace with exact newline normalization

#### Model-guided retries

Ask the model to retry only when the system can provide a precise structured error, e.g.:

- "search text matched 3 regions; provide a more specific anchor"
- "unified diff hunk header did not match file contents"
- "replace_block missing end sentinel"

### Tool retry envelope

When asking the model to retry, do not replay raw failure prose only. Return a structured tool error object such as:

```json
{
  "tool": "propose_file_change",
  "status": "retryable_error",
  "failure_code": "multiple_match_ambiguity",
  "details": {
    "filename": "providers/openai.py",
    "match_count": 3,
    "suggested_format": "replace_block"
  }
}
```

This materially improves local-model correction quality.

---

## 5. Tooling Observability pipeline

To improve visibility, create a first-class telemetry stream for tooling.

### Emit structured events for every stage

Suggested event types:

- `tool.intent_detected`
- `tool.parsed`
- `tool.parse_repaired`
- `tool.schema_validated`
- `tool.approval_requested`
- `tool.execution_started`
- `tool.execution_succeeded`
- `tool.execution_failed`
- `tool.fallback_selected`
- `tool.retry_requested`
- `tool.retry_succeeded`

### Required dimensions

Each event should include:

- timestamp
- session id
- provider
- model
- tool name
- tool kind (`read`, `mutation`, `shell`, `git`, `network`)
- call id
- latency ms
- approval required or not
- fallback count
- failure code if applicable
- file extension / target path class for mutation tools

### Operational dashboards

The server/UI should make it easy to answer:

- which provider/model has the highest tool parse failure rate?
- which tools fail most often on local models?
- how often did a mutation succeed only after fallback?
- what percentage of `apply_diff` calls could have been `search_replace` instead?
- which files or file types are most error-prone?

### Suggested UI additions

1. **Tool timeline panel**: a chronological ladder of tool lifecycle events.
2. **Fallback badge**: visually mark when the runtime repaired JSON or switched mutation adapters.
3. **Mutation confidence indicator**: show why an edit path was chosen.
4. **Per-model reliability summary**: rolling success rates for the active model.
5. **Approval detail expansion**: render the original requested mutation and the final executed adapter side by side.

---

## Provider-specific strategy

The core rule is: **one tool harness, many provider profiles**.

## OpenAI strategy

### Strengths

- strong native function/tool calling
- strong JSON adherence
- good diff generation on larger models

### Recommendations

- keep native tool calls as the default
- support strict JSON schema validation
- use compact tool descriptions to reduce token overhead
- use a provider option for reasoning/thought-token budget rather than embedding chain-of-thought content into prompts
- allow parallel tool calls only for read-only tools until mutation concurrency is proven safe

## Gemini strategy

### Strengths

- strong reasoning and planning
- native tools available
- explicit thought-chain continuity support already exists in the codebase

### Recommendations

- preserve thought signatures across tool turns rigorously
- keep tool retries narrow and structured so the model can continue its reasoning chain
- prefer single mutation tool call per turn when using thinking-heavy models
- add provider-level handling for cases where reasoning output dominates tool output and needs a tighter steering prompt

## Local-model strategy

### Reality to optimize for

Local 32B-class models often understand the task but fail on exact tool syntax. The harness should therefore optimize for **recoverability** rather than purity.

### Recommendations

- allow native tool calling when available, but also parse tool-like JSON from plain text
- bias mutation tools toward `search_replace` and `replace_block`
- keep tool schemas smaller and flatter
- expose a provider profile setting like `force_single_tool_per_turn`
- include 1 compact positive example per mutation tool for local models
- add a local-model mode that asks the model to emit **only one tool call object** and no prose when in execution turns
- maintain model-specific repair rules for common formatting mistakes

### Important principle

For local models, a slightly less elegant tool contract that is highly recoverable is better than a theoretically ideal schema that fails 10-20% of the time.

---

## Recommended concrete refactor plan

## Phase 1: Reliability foundation

1. Add `ProviderToolProfile` and resolve one profile per active session.
2. Introduce `ToolInvocation` IR between provider parsing and tool execution.
3. Add structured schema validation + repair helpers.
4. Start recording normalized tool lifecycle events.

### Expected impact

This alone should materially improve parse success and make failures measurable.

## Phase 2: Mutation engine

1. Replace direct `apply_diff` execution with a mutation engine.
2. Keep `apply_diff` as a compatibility alias, but internally convert it into `propose_file_change` with `edit_format="unified_diff"`.
3. Add `search_replace` and `replace_block` adapters.
4. Add syntax-validation hooks for Python/JSON/Markdown-sensitive flows.

### Expected impact

This is the biggest contributor toward the >98% success target.

## Phase 3: Provider tuning

1. Add default profiles for OpenAI, Gemini, Ollama/local.
2. Add model overrides for known-good and known-bad tool users.
3. Tune prompts/examples/tool descriptions per provider profile.
4. Add a "strict hosted" mode and a "recoverable local" mode.

## Phase 4: UX + analytics

1. Surface tool timeline and fallback badges in terminal + server events.
2. Add per-model reliability counters.
3. Add approval views showing requested vs executed mutation adapter.
4. Add regression tests for malformed tool calls and patch recovery.

---

## Suggested success metrics

The >98% target should be defined precisely.

### Primary metrics

1. **Tool parse success rate**
   - percentage of model-emitted tool intents that become valid `ToolInvocation`s

2. **Tool execution success rate**
   - percentage of valid tool invocations that complete successfully

3. **Mutation success rate**
   - percentage of requested code modifications that produce the intended file change and pass validation

4. **First-pass mutation success rate**
   - same as above, but without runtime fallback or retry

5. **Recovered success rate**
   - percentage of successes that required repair or fallback

### Model certification bar

A model should only be labeled "tool-stable" if, over a representative benchmark suite:

- total tool success is >= 98%
- mutation success is >= 95% on first pass
- mutation success is >= 98% with fallback enabled
- permanent malformed-tool failures stay below 1%

---

## Benchmark plan

Create a tooling benchmark suite with at least these categories:

1. read-only tool selection
2. multi-step read -> mutate -> verify loops
3. malformed JSON repair
4. malformed diff repair
5. ambiguous search/replace handling
6. config-file structured edits
7. provider-specific thought-chain continuity
8. mixed prose + tool-call extraction from local models

### Benchmark outputs

For each run, record:

- provider/model
- tool chosen
- raw output
- repaired output if any
- adapter selected
- retries
- final outcome
- latency

This benchmark should become the gating mechanism for prompt/profile changes.

---

## Minimal data-model additions

A practical first implementation could add a new module such as:

- `core/tool_profiles.py`
- `core/tool_invocations.py`
- `core/mutation_engine.py`
- `core/tool_telemetry.py`

### Suggested responsibilities

#### `core/tool_profiles.py`
- provider/model capability resolution
- user overrides
- default profiles for OpenAI/Gemini/Ollama/local

#### `core/tool_invocations.py`
- raw response parsing
- schema validation
- argument repair/coercion
- retryable error formatting

#### `core/mutation_engine.py`
- adapter selection
- diff/search-replace/full-file execution
- syntax validation
- approval display payload generation

#### `core/tool_telemetry.py`
- structured event sink
- in-memory counters
- server/UI export helpers

---

## Practical guidance for prompts and tool descriptions

To make this architecture work well on local models, change the prompting contract for execution turns.

### Hosted-model prompt style

Use concise tool instructions and rely on schema enforcement.

### Local-model prompt style

Use a short execution rule block like:

- emit at most one tool call
- emit no prose before or after the tool object
- prefer `search_replace` for small edits
- if patch syntax is uncertain, ask for file context instead of guessing

### Why this helps

Local models usually fail from output formatting instability, not from inability to understand the task.

---

## Recommendation summary

If only three things are implemented, they should be these:

1. **Introduce a canonical tool invocation IR with repair/validation.**
2. **Replace single-mode `apply_diff` execution with a mutation engine that supports unified diff, search/replace, and full-file fallback.**
3. **Add structured tool telemetry so reliability can be measured by provider and model.**

Those three changes will do the most to improve local-model compatibility while also benefiting Gemini and OpenAI integrations.

## Proposed decision

Proceed with a phased refactor that keeps the current public tool surface compatible, but internally moves μCLI to:

- capability-driven provider behavior
- repairable tool invocation parsing
- multi-adapter mutation execution
- measurable tool lifecycle telemetry

That is the most realistic path to a robust, provider-flexible tooling harness with strong local-model support.
