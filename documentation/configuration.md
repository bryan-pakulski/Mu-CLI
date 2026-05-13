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
`workspace_context_max_chars` (default `8192` chars). Set the variable
to an empty string to disable.

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
| `max_iterations` | int | `1000` | Hard cap on iterations per conversation turn. |
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
| `context_token_limit` | int | `256000` | **Global** token cap (sum of all 7 layers + response reserve). Capped further by the provider's real context window if smaller. |
| `context_trim_threshold` | float | `0.85` | Fraction of the cap above which compaction kicks in. |
| `response_token_reserve` | int | `4096` | Tokens reserved for the model's reply. Tune down for small-context models (Ollama 8k). |
| `tool_context_window` | int | `6` | Recent tool messages kept uncompressed in history. |
| `compact_history` | bool | `true` | Auto-compact tooling history after each finished turn. |

Inspect live usage with `/memory` — every layer is shown with its
current token count and per-layer cap, plus a **TOTAL** row against
`context_token_limit`.

### Per-layer budgets

Each layer's budget is a soft cap on how much of *that* content gets
included on a turn. All are settable via `/set` and visible in
`/variables`.

| Variable | Type | Default | Layer | Description |
| --- | --- | --- | --- | --- |
| `workspace_context_max_chars` | int | `8192` | **L1** | Workspace files (AGENTS.md, CLAUDE.md, MUCLI.md, .mu/CONTEXT.md per attached folder). |
| `workspace_context_files` | str | `AGENTS.md,CLAUDE.md,MUCLI.md,.mu/CONTEXT.md` | **L1** | Comma-separated list of files to auto-load per workspace. Empty disables. |
| `skills_max_chars` | int | `6144` | **L1B** | AVAILABLE SKILLS block (compact index + auto-expanded bodies). `0` disables skills entirely. |
| `skills_mode` | str | `compact` | **L1B** | `compact` (index + auto-expand on trigger) or `full` (every body inlined up to the budget). |
| `conversation_summary_char_limit` | int | `8000` | **L2** | Rolling conversation summary. Clipped from the tail when exceeded. |
| `active_goal_context_char_limit` | int | `4000` | **L3** | Feature/task status + scratchpad snapshot. |
| `recent_tool_context_char_limit` | int | `12000` | **L4** | Compressed recent tool calls/results. |
| `retrieval_context_char_limit` | int | `5000` | **L4B** | Semantic-retrieval snippets injected for the current turn. |
| `retrieval_top_k` | int | `5` | **L4B** | Number of semantic-retrieval hits to consider when assembling L4B. |

L5 (conversation history) has no per-layer budget of its own — it
gets whatever the global cap minus the response reserve and the
non-L5 layers leaves over.

#### Layer-budget shortcuts

Layer IDs are easier to remember than the underlying variable names.
Both `/set` and `/get` accept a `layer` subcommand:

```
/set layer L4 6000         # 6000 tokens; stored as 24000 chars in
                           # recent_tool_context_char_limit
/get layer L1              # tokens + underlying chars
/get layer                 # table of all six layer budgets
```

The value is in **tokens** — matching the unit shown in `/memory` and
the splash banner — and is converted to chars at a 4:1 ratio for the
underlying `_chars` variable. (Setting the variable directly in chars
via `/set workspace_context_max_chars 16384` still works.)

Layer IDs autocomplete on Tab. L5 is rejected — adjust
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
| `loop_detection_repeat_threshold` | int | `3` | Repeat count that trips loop detection. |

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
