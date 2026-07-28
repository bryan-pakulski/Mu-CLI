# Session Guide

Mu-CLI sessions persist conversation history, task memory, and scratchpad
notes across turns. This document covers the memory architecture,
configuration, and the queryable history search feature.

## Memory architecture

The system prompt is composed of seven layers:

- **L0** — Base system prompt (persona + agentic harness + mode workflow)
- **L1** — Workspace context files (AGENTS.md, CLAUDE.md, .mu/CONTEXT.md)
- **L1B** — Installed skills (compact index + auto-expanded bodies)
- **L2** — Conversation summary (rolling LLM-generated compression of older history)
- **L3** — Active goal context (feature/task status + scratchpad snapshot + pinned session goal)
- **L4** — Recent tool activity (compressed, or LLM-summarized, for budget)
- **L5** — Current turn (full history entries from `summary_anchor` onwards)

L1 and L1B are built once per turn (disk reads / skills-tree walk happen
once) and cached; L2 and L3 are reassembled **every iteration** from
in-memory state, so mid-turn updates (auto-compaction rewriting the summary,
tools mutating feature_state / the scratchpad, a freshly-set session goal)
reach the model the same turn — they are never frozen at their turn-start
value. On long turns that stay under the compaction budget, a periodic L2
progress checkpoint (`progress_checkpoint_every`) folds recent history into
the structured summary without compacting, so L2 reflects current progress.

When history exceeds the context budget, the compactor rolls older messages
into the L2 conversation summary and advances `summary_anchor`. Messages
before the anchor are no longer sent to the model — but they remain in
`self.history` and on disk in `session.json`.

Mid-turn compaction (triggered when a provider call would exceed the
budget) keeps a window of trailing messages verbatim and protects the
most recent tool results via `tool_result_floor` (R3), so results just
received are never dropped to make room. If a single message's text
alone exceeds the per-turn history budget (R4), it is replaced in the
runtime slice with a chunk-summarized `[CONTEXT-OVERFLOW …]` envelope
summarized via the provider — the original stays on disk, and the model
is told the verbatim text is not in context.

## Configuration variables

| Variable | Default | Description |
| --- | --- | --- |
| `context_token_limit` | 900000 | Global token cap (sum of all 7 layers + response reserve). |
| `context_trim_threshold` | 0.85 | Fraction of the cap above which compaction kicks in. |
| `response_token_reserve` | 4096 | Tokens reserved for the model's reply. |
| `auto_compaction_enabled` | false | Opt in to proactive automatic compaction. Default is model-directed cleanup; hard provider-overflow recovery remains enabled. |
| `tool_result_floor` | 4 | Trailing tool-result messages in the active turn that compaction (including emergency compaction) must leave verbatim, so mid-turn compaction can't drop results just received. |
| `emergency_keep_recent` | 2 | Trailing messages kept verbatim by emergency (pre-flight) compaction — smaller than the normal keep-recent so budget is reclaimed fast; `tool_result_floor` still protects recent tool results. |
| `compact_history` | false | Remove completed-turn tool metadata after a response. Disabled by default. |
| `conversation_summary_char_limit` | 80000 | Char budget for L2 rolling summary (scales with `context_token_limit`, floor `24000`). |
| `workspace_context_max_chars` | 40000 | Char budget for L1 workspace files (scales with `context_token_limit`, floor `16384`). |
| `skills_max_chars` | 40000 | Char budget for L1B skills block (scales with `context_token_limit`, floor `6144`). |
| `retrieval_context_char_limit` | 40000 | Char budget for L4B semantic-retrieval snippets (scales with `context_token_limit`, floor `10000`). |

The four char-budget variables scale proportionally with
`context_token_limit` (`utils/config.py:compute_layer_char_budgets`);
the "Default" column is the value at the default `context_token_limit =
900000`, and each never drops below its floor. See
[configuration.md](configuration.md#per-layer-budgets) for the full table.

## Task memory and scratchpad

**Task memory** (`task_memory`) is a durable, session-scoped store for
facts, decisions, and findings the agent wants to reuse across turns.
Saved via `save_memory`, searched via `search_memory`.

**Scratchpad** (`turn_scratchpad`) is a per-turn ephemeral store for
short-lived plans, hypotheses, and checklists. Cleared at the start of
each new turn (unless `scratchpad_persist_across_turns` is set). Saved
via `save_scratchpad`, searched via `search_scratchpad`.

Both stores support lifecycle management (active/done/superseded/archived)
via `update_memory_status`, `retire_memory`, `supersede_memory`, etc.

## Queryable History

The `search_history` tool lets the agent search the full conversation log
— including messages that have been compacted behind the summary anchor
and are no longer in the model's active context window.

### Tool reference: `search_history`

```
search_history(
    query: str,              # Required — keyword/substring to search for
    role: str = None,        # Optional — filter by "user" or "assistant"
    tool_name: str = None,   # Optional — filter by tool name (e.g. "bash")
    include_summarized: bool = True,  # Search pre-anchor (compacted) messages
    context_messages: int = 2,       # Messages before/after each hit for context
    max_results: int = 20   # Maximum hits to return
)
```

**Return format:**

```json
{
  "results": [
    {
      "index": 42,
      "role": "assistant",
      "before_anchor": true,
      "parts_matched": [
        {"type": "text", "snippet": "...matching text...", "match_type": "text"}
      ],
      "context_before": [
        {"index": 40, "role": "user", "preview": "...100-char preview..."}
      ],
      "context_after": [
        {"index": 43, "role": "tool", "preview": "...100-char preview..."}
      ],
      "cache_key": "abc123def456"
    }
  ],
  "total_matches": 3,
  "has_more": false
}
```

### Search semantics

- **Case-insensitive substring matching** across all part types:
  - **text parts**: match on `part["text"]`
  - **tool_call parts**: match on `tool_name` and `json.dumps(tool_args)`
  - **tool_result parts**: match on `str(tool_result)`
  - **file parts**: match on `display_name` and `uri`
  - **image_input parts**: match on `source` and `mime_type`
- **Relevance ranking** (highest to lowest):
  1. Exact text match in text parts
  2. Tool name match
  3. Tool args match
  4. Tool result match
  5. File name match
  - Multiple matches in the same message increase rank
- **Snippet extraction**: 200 chars centered on the match for text;
  tool name + abbreviated args for tool calls; first 200 chars for
  tool results
- **Bounded results**: returns at most `max_results` hits. If more
  matches exist, `total_matches` count and `has_more` flag are included

### Filter options

| Filter | Description |
| --- | --- |
| `role` | Restrict to `"user"` or `"assistant"` messages only |
| `tool_name` | Restrict to messages containing a tool call/result with the given tool name |
| `include_summarized` | `True` (default) searches all history; `False` searches only active (post-anchor) messages |
| `context_messages` | Number of messages before/after each hit to include as context (default 2, clamped to history bounds) |
| `max_results` | Maximum number of hits to return (default 20) |

### Anchor awareness

Each result includes a `before_anchor` boolean:
- `true` — the message is before `summary_anchor` (compacted/summarized,
  no longer in the model's active context window)
- `false` — the message is after `summary_anchor` (still in active context)

This helps the agent understand whether the context is still live or has
been compressed into the conversation summary.

### ToolResultCache integration

When a search hit is a `tool_result` part that has a corresponding entry
in the `ToolResultCache`, the `cache_key` is included in the result. The
agent can immediately call `recall(cache_key)` to retrieve the full
uncompressed tool result.

Cache-key resolution (R10/FM-9) is an **O(1) lookup**: the
`ToolResultCache` maintains a reverse index (`_result_index`, result-content
hash → key) populated whenever a result is stored, so a search hit maps
straight to its key by content hash without scanning cache entries. The
hash matches the linear-scan comparison exactly, so the fast path and the
fallback always agree on the same key. When a cache entry is evicted, its
reverse mapping is dropped at the same time, so a stale hash never
resolves to a reused key. If no cache key is available (the result was
evicted or the tool doesn't support caching), `cache_key` is `null`. The
agent gets a 200-char snippet preview but cannot recall the full result.

### GUI endpoint

```
GET /chat/history/search?query=<query>&role=<role>&tool_name=<tool>&max_results=<n>
```

Read-only endpoint in `mu/gui/routers/chat.py`. Returns the same JSON
shape as the `search_history` tool. Uses the `_resolve_session` pattern
consistent with other chat routes. Returns 400 if `query` is empty/missing.

### TUI command

```
/history search <query> [--role <role>] [--tool <name>] [--limit N]
```

Slash command available in the REPL. Calls `session.search_history()` and
prints results in a readable format:

```
Found 3 match(es) for 'auth':

  [12] assistant:
    (text) ...the auth module lives in session.py...
    ↑ [10] user: What about authentication?
    ↓ [13] tool: [read_file] session.py contents...

  [42] assistant [compacted]:
    (tool_name) bash
    ↑ [40] user: Run the auth tests
    ↓ [43] tool: [bash] pytest output...
```

Lines marked `[compacted]` are pre-anchor messages. Context lines are
indented with `↑` (before) and `↓` (after) arrows.

### Performance characteristics

For short queries (≥ 3 characters), `search_history` builds an
**in-memory trigram inverted index** (trigram → set of message indices)
lazily on first use and reuses it across searches within a turn. Because
every trigram of a query that is a substring of a message's searchable
blob is present in that blob, intersecting the trigram postings produces
a *superset* of matching message indices, so the per-part matcher only
runs against that narrowed candidate set instead of the whole history.
The index is invalidated and rebuilt when the history length changes
(new messages added), keeping it correct without per-message
bookkeeping. Queries too short to form a trigram (< 3 chars) fall back
to a plain linear scan.

Search is in-memory lexical matching with no external dependencies (no
vector DB, no embeddings). The trigram index keeps large sessions
(thousands of messages) fast on the common keyword/substring path while
preserving exact substring semantics — no approximate matches are
introduced.

## Session capability types

MuCLI sessions now have a capability type independent of agent strategy mode:

- **chat** — conversational/research/memory tools only; no host filesystem or shell.
- **workspace** — current host execution with explicit workspace folders and approvals.
- **container** — the full MuCLI worker runs natively in a Docker sandbox with explicit mounts, auto-approved modifying tools, host-enforced egress policy, persistent session state, and downloadable artifacts.

See [Container mode](container_mode.md) for creation, mounts, egress, lifecycle, and artifacts. Strategy modes such as Feature, Research, Debug, or Teacher continue to compose with all three capability types.
