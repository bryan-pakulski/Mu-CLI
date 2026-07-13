# Configuration reference

This document covers every configuration surface mucli exposes:

- Command-line flags
- Environment variables and provider keys
- Configuration files under `.mu/`
- Workspace-context files
- Session variables (runtime knobs)

## Command-line flags

Invoke via `python mucli.py [flags]`.

| Flag | Description |
| --- | --- |
| `--model <name>` | Default model name to start with. |
| `--provider <gemini\|ollama\|openai>` | LLM provider. |
| `--session <name>` | Load a saved session non-interactively. |
| `--workspace <path>` | Attach a workspace folder. Repeatable. |
| `--yolo` | Start in YOLO mode (auto-approve writes). |
| `--debug` | Verbose logging. |
| `--system <prompt>` | Override the initial system instruction. |

## Environment variables

| Variable | Used by | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI provider | API key for OpenAI models. |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini provider | API key for Google Gemini. |
| `OLLAMA_HOST` | Ollama provider | URL of the Ollama server (also settable via `/set ollama_host`). |
| `MUCLI_HOME` | Filesystem layout | Base directory for sessions and logs. Defaults to `~/.mucli/`. |

Sessions and logs live under `MUCLI_HOME`:

- `~/.mucli/sessions/` — saved session JSON files
- `~/.mucli/logs/` — log output

## Configuration files

### `.mu/hooks.json` — lifecycle hooks

Shell-command hooks fired at five lifecycle points:

```json
{
  "hooks": [
    { "name": "log-tool-calls", "point": "post_tool",
      "command": "echo $MU_TOOL_NAME >> /tmp/tools.log" },
    { "name": "deny-rm-rf", "point": "pre_tool",
      "command": "case \"$MU_TOOL_ARGS_JSON\" in *rm\\ -rf*) exit 1;; *) exit 0;; esac",
      "on_failure": "short_circuit",
      "message": "rm -rf detected; refuse" }
  ]
}
```

Hook points: `pre_provider_call`, `post_provider_call`, `pre_tool`,
`post_tool`, `on_stop`. Hooks at the same point run in priority order
(lower first). A non-zero exit at `pre_tool` with
`on_failure: short_circuit` denies the call.

### `.mu/mcp.json` — MCP servers

```json
{
  "servers": {
    "fs":  {"command": ["npx", "@modelcontextprotocol/server-filesystem", "/workspace"]},
    "git": {"command": ["mcp-server-git"], "env": {"GIT_REPO": "/workspace"}}
  }
}
```

Tools register as `mcp__<server>__<tool>` and appear in `/tool list`
alongside built-ins. Manage servers at runtime with the `/mcp` slash
command (`list`, `status`, `reload`, `debug <server>`). Full setup
guide, authentication patterns, and gap list in
[mcp.md](mcp.md).

### `.mu/skills/<name>/SKILL.md` — workspace skills

Workspace-local skills shadow built-ins of the same name. See
[skills.md](skills.md).

### `.mu/CONTEXT.md` — workspace context

Optional plain-text file auto-loaded into LAYER 1 of the system prompt.
Use it for project-specific instructions that should always be in
context. See [Workspace context files](#workspace-context-files).

## Workspace context files

Controlled by the `workspace_context_files` variable. Default value:

```
AGENTS.md,CLAUDE.md,MUCLI.md,.mu/CONTEXT.md
```

For each attached workspace folder, mucli loads the first matching file
(or all of them, in order) into LAYER 1 of the system prompt, capped by
`workspace_context_max_chars` (default `40000` chars at the default
`context_token_limit`; scales with it — see [Per-layer budgets](#per-layer-budgets)).
Set `workspace_context_files` to an empty string to disable.

## Session variables

Every variable below is settable at runtime via `/set <key> <value>` and
inspectable via `/get <key>` or `/variables`. Defaults come from
`utils/config.py:VARIABLE_SCHEMA`.

### Agent behavior

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `agent_mode` | str | `default` | Active agent mode. Same set as `/mode`. |
| `strict_mode` | bool | `false` | Force user approval for every tool call. |
| `yolo` | bool | `false` | Auto-approve write-side tools. |
| `max_iterations` | int | `1000` | Hard cap on iterations per conversation turn. When reached mid-work, the agent runs one final consolidation turn (tools disabled) and writes a handoff summary to memory instead of stopping silently. |
| `session_goal` | str | `""` | The user's pinned top-level task for the current turn, rendered in L3 every iteration so the model keeps direction even when L2 is compacted. Set with `/goal <text>`; clears at end of turn unless `session_goal_sticky` is in effect. |
| `session_goal_sticky` | bool | `false` | When true (or in `loop`/`feature` mode, which default to sticky), `session_goal` is **not** cleared at end of turn — it persists across turns in L3 until cleared (`/goal clear`) or replaced. Long-horizon multi-turn work needs the goal to survive turn boundaries; conversational default-mode use does not. The `session_goal_sticky_explicit` tracker records whether the user set this via `/set`, so the mode-aware default only applies until overridden. |
| `reflective_retry_enabled` | bool | `true` | Show retryable tool failures + remediation hints inline. |
| `streaming_enabled` | bool | `true` | Stream tokens one-by-one instead of one final panel. |
| `structured_tool_results` | bool | `true` | Use structured envelope for tool results (vs. raw text). |

### Context — global cap

The system prompt mucli sends each turn is composed of seven layers
(see [the Memory guide](memory_guide.md)). Each layer has its own soft
budget, and `context_token_limit` is the **hard global cap** the
provider actually sees. The compactor subtracts the active size of all
non-L5 layers from the cap before deciding how much room L5
(conversation history) gets — so a heavy `AGENTS.md` or many expanded
skills will trigger compaction sooner.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `context_token_limit` | int | `900000` | **Global** token cap (sum of all 7 layers + response reserve). Capped further by the provider's real context window if smaller. Changing this reratios the per-layer char budgets proportionally (see [Per-layer budgets](#per-layer-budgets)). |
| `context_trim_threshold` | float | `0.85` | Fraction of the cap above which compaction kicks in. |
| `response_token_reserve` | int | `4096` | Tokens reserved for the model's reply. Tune down for small-context models (Ollama 8k). |
| `tool_context_window` | int | `6` | Recent tool messages kept uncompressed in history. |
| `tool_result_floor` | int | `4` | Trailing tool-result messages in the active turn that compaction (including emergency compaction) must leave verbatim (R3). Prevents mid-turn compaction from dropping results just received. Mode-aware: `loop`/`feature` modes raise this to at least 8 (long-horizon work re-covers more files). |
| `tool_result_cache_entries` | int | `50` | Max entries in the tool-result sidecar cache (`recall()` + auto-recall by locator). Mode-aware: `loop`/`feature` modes raise this to at least 256. |
| `tool_result_cache_bytes` | int | `524288` | Max bytes in the tool-result sidecar cache. Mode-aware: `loop`/`feature` modes raise this to at least 2 MB. |
| `emergency_keep_recent` | int | `2` | Trailing messages kept verbatim by emergency (pre-flight) compaction — smaller than the normal keep-recent so budget is reclaimed fast; `tool_result_floor` still protects recent tool results. |
| `compact_history` | bool | `true` | Auto-compact tooling history after each finished turn. |
| `progress_checkpoint_every` | int | `0` | Periodic L2 progress checkpoint: every N iterations, fold recent history into the structured `conversation_summary` (Progress / Key decisions / Current state / Open items) **without compacting** (the anchor doesn't advance, entries stay in L5). Keeps L2 fresh on long turns that never hit the compaction budget so the model stops re-deriving context it already gathered. `0` disables; when unset, `loop`/`feature` modes default to 12, `default`/`chat` to 0. |
| `recoverage_stall_threshold` | int | `4` | Context-gathering stall detection: number of consecutive iterations that re-read files already read this turn **without** a concrete change (write/bash/spawn) before a "stop gathering, act" re-orient nudge is injected. `0` disables. Catches the diffuse re-coverage stall that doesn't form a clean repeated/periodic tool sequence. |

### Provider retry (transient failures)

When the provider returns a transient error (429, 503, timeout,
connection reset), mucli backs off exponentially. The retry loop is
bounded by a cumulative-wait budget rather than a fixed retry count so
real outages get meaningful backoff without infinite stalling.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `provider_retry_max_total_wait_seconds` | float | `120.0` | Cumulative time budget across all retries for one provider call. Bounds worst-case stall on a flapping endpoint. |
| `provider_retry_base_delay` | float | `0.4` | Initial sleep after the first transient failure. Doubles each attempt (with jitter). |
| `provider_retry_max_delay` | float | `30.0` | Cap on any single sleep — backoff stops doubling here. |
| `provider_max_retries` | int | `30` | Safety belt against pathological 0-delay loops; the budget is normally the binding constraint. |

Inspect live usage with `/memory` — every layer is shown with its
current token count and per-layer cap, plus a **TOTAL** row against
`context_token_limit`.

### Per-layer budgets

Each layer's budget is a soft cap on how much of *that* content gets
included on a turn. All are settable via `/set` and visible in
`/variables`.

The five char-budget variables below **scale proportionally with
`context_token_limit`** (`utils/config.py:compute_layer_char_budgets`):
at the reference limit of `900000` each lands on its target token
budget × 4 chars, and it never drops below an absolute floor (the
historical minimum). So the "default" column is the value at the
default `context_token_limit = 900000`; lowering the global cap
shrinks these toward their floors, and raising it grows them.

| Variable | Type | Default | Floor | Layer | Description |
| --- | --- | --- | --- | --- | --- |
| `workspace_context_max_chars` | int | `40000` | `16384` | **L1** | Workspace files (AGENTS.md, CLAUDE.md, MUCLI.md, .mu/CONTEXT.md per attached folder). |
| `workspace_context_files` | str | `AGENTS.md,CLAUDE.md,MUCLI.md,.mu/CONTEXT.md` | — | **L1** | Comma-separated list of files to auto-load per workspace. Empty disables. |
| `skills_max_chars` | int | `40000` | `6144` | **L1B** | AVAILABLE SKILLS block (compact index + auto-expanded bodies). `0` disables skills entirely. |
| `skills_mode` | str | `compact` | — | **L1B** | `compact` (index + auto-expand on trigger) or `full` (every body inlined up to the budget). |
| `conversation_summary_char_limit` | int | `80000` | `24000` | **L2** | Rolling conversation summary. Clipped from the tail when exceeded. |
| `active_goal_context_char_limit` | int | `16384` | `4000` | **L3** | Feature/task status + scratchpad snapshot. |
| `retrieval_context_char_limit` | int | `40000` | `10000` | **L4B** | Semantic-retrieval snippets injected for the current turn. |
| `retrieval_top_k` | int | `5` | — | **L4B** | Number of semantic-retrieval hits to consider when assembling L4B. |

**L4** (recent tool activity) and **L5** (conversation history) have no
per-layer char budget of their own. L4 is governed by `tool_context_window`
(how many recent tool messages stay uncompressed) plus the compaction
summary; L5 gets whatever the global cap minus the response reserve and
the other layers leaves over. Neither is settable via `/set layer`.

#### Layer-budget shortcuts

Layer IDs are easier to remember than the underlying variable names.
Both `/set` and `/get` accept a `layer` subcommand:

```
/set layer L1 6000         # 6000 tokens; stored as 24000 chars in
                           # workspace_context_max_chars
/get layer L1              # tokens + underlying chars
/get layer                 # table of all five layer budgets
```

The value is in **tokens** — matching the unit shown in `/memory` and
the splash banner — and is converted to chars at a 4:1 ratio for the
underlying `_chars` variable. (Setting the variable directly in chars
via `/set workspace_context_max_chars 16384` still works.)

Layer IDs autocomplete on Tab. Valid IDs are `L1`, `L1B`, `L2`, `L3`,
`L4B`. `L4` and `L5` are rejected — L4 is governed by `tool_context_window`
and the compaction summary, and L5 is the global-cap remainder; adjust
`context_token_limit` instead.

### Notes

- All char-budgets are **soft caps**: the layer is truncated to that
  many chars before being injected into the prompt, then re-counted in
  tokens (via `tiktoken`) for the per-layer fill display and the
  global-cap accounting.
- Tightening any per-layer budget frees room for L5. Loosening one
  steals from L5 — you'll see compaction fire sooner.
- `/set <name> 0` typically disables a layer (subject to `min(1, ...)`
  floors in the renderer). To disable a *file-list*-style layer, set
  the corresponding `*_files` variable to an empty string.

### Memory and scratchpad

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `memory_enabled` | bool | `true` | Enable persistent task memory. |
| `memory_max_entries` | int | `64` | LRU cap on task-memory entries. |
| `memory_summary_limit` | int | `8` | Memory entries shown in the system prompt summary. |
| `scratchpad_enabled` | bool | `true` | Enable turn-local scratchpad. |
| `scratchpad_max_entries` | int | `24` | Max scratchpad entries before eviction. |
| `scratchpad_persist_across_turns` | bool | `false` | Keep the scratchpad across turns. Mode-aware: `loop`/`feature` modes persist regardless; `default`/`teacher` clear at turn start unless this is `true`. |

### Collation

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `collation_enabled` | bool | `true` | Defer read-side tool results into a single collated payload. The model drains the buffer via the `flush` tool. |

### Loop mode

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `loop_active` | bool | `false` | Whether loop mode is currently engaged. |
| `loop_features` | str | `""` | JSON list of features created during the current loop run. |
| `loop_detection_enabled` | bool | `true` | Detect and break tight repeat loops. |
| `loop_detection_repeat_threshold` | int | `5` | Repeat count that trips (tight) pattern-based loop detection. |
| `loop_detection_periodic_max_period` | int | `6` | Soft knob (not in the canonical schema, but settable via `/set` and read with `int()`): max spacing, in tool calls, between two equal tool-name sequences for the *periodic* loop detector (R7) to flag a repeat. Lower = stricter. |
| `retryable_escalation_threshold` | int | `3` | Soft knob (settable via `/set`, read with `int()`): when a tool hits the same retryable error this many times in one turn with different args (which evades pattern detection), inject an escalation telling the model to change approach (R8). |

### Ollama tuning

These map directly to Ollama generation parameters. `0` typically means
"use the server default."

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ollama_host` | str | `""` | Ollama server URL. Falls back to `OLLAMA_HOST`. |
| `ollama_num_ctx` | int | `0` | Context window override. |
| `ollama_num_predict` | int | `0` | Max tokens to predict. |
| `ollama_temperature` | float | `0.0` | Sampling temperature. |
| `ollama_top_p` | float | `0.0` | Nucleus sampling cutoff. |
| `ollama_top_k` | int | `0` | Top-K sampling cutoff. |
| `ollama_repeat_penalty` | float | `0.0` | Repetition penalty. |
| `ollama_seed` | int | `0` | RNG seed (`0` = random). |
| `ollama_mirostat` | int | `0` | Mirostat sampling mode (`0`/`1`/`2`). |

## How configuration is layered

1. **CLI flags** (`--provider`, `--model`, `--workspace`, `--yolo`, ...)
   set initial state for the session.
2. **Environment variables** provide credentials and host URLs.
3. **`.mu/*.json` files** declare durable infrastructure (hooks, MCP
   servers) and are loaded per-workspace.
4. **Session variables** (`/set`) override defaults at runtime and
   persist with the session via `/list` / `/load`.

Variables and saved sessions live under `MUCLI_HOME` (default
`~/.mucli/`).
