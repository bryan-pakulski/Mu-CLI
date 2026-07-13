# Mu-CLI Memory System Report

## Overview

Mu-CLI implements a multi-layered memory architecture designed to optimize context management for agentic AI workflows. The system consists of four primary memory stores plus a collation buffer for deferred context delivery.

---

## 1. Context (FolderContext)

**Location:** `mu/workspace/folder_context.py`

**Purpose:** Manages workspace folder tracking and file change detection.

**How it works:**
- Tracks monitored folders (`self.folders`)
- Maintains a snapshot of file states (mtime, size) for change detection
- Generates XML-formatted context diffs showing file modifications
- Provides a tree-map view of the workspace structure

**Why implemented:** Enables the AI to understand the project structure and detect file changes without re-scanning the entire workspace on every turn.

---

## 2. Task Memory (TaskMemoryStore)

**Location:** `mu/memory/stores.py`

**Purpose:** Persistent, durable memory that survives across turns and sessions.

**Key features:**
- **Max entries:** Configurable (effective default 64 via the `memory_max_entries` config variable, applied at turn start in `mu/agent/loop_body.py`; the `TaskMemoryStore` class default is 1024 when constructed directly)
- **Deduplication:** Identical content+tags updates existing entry (hits++, updated_at)
- **LRU eviction:** When limit exceeded, least recently used entries are removed
- **Searchable:** Full-text search across content, tags, and source

**Data structure:**
```python
@dataclass
class MemoryEntry:
    id: int
    content: str
    tags: List[str]
    source: str
    kind: str = "observation"          # decision | finding | observation | goal
    created_at: float
    updated_at: float
    hits: int                          # Access counter for ranking
    status: str = "active"             # active | done | superseded | archived | stale
    superseded_by: Optional[int] = None  # ID of entry that replaced this one
    supersedes: Optional[int] = None     # ID of entry this one replaces
```

**Why implemented:** Prevents the AI from re-reading large files or re-executing expensive searches. Critical findings (file locations, search results, workspace structure) are preserved for quick recall.

**Lifecycle fields** (`status`, `superseded_by`, `supersedes`) let the agent distinguish active work from completed, superseded, or archived entries. `kind` drives eviction priority (decisions > findings > observations > goals). See the [Memory Lifecycle](#memory-lifecycle) section below.

---

## 3. Scratchpad (ScratchpadStore)

**Location:** `mu/memory/stores.py`

**Purpose:** Turn-local temporary notes that are cleared at the start of each new user turn (subject to mode-aware persistence — see below).

**Key features:**
- Same data structure as Task Memory
- **Max entries:** effective default 24 via the `scratchpad_max_entries` config variable (class default 256)
- **Auto-cleared** at turn start when `scratchpad_enabled` is on, *unless* persistence applies
- **Todo carve-out:** the `todo`-tagged slice (the agent's self-managed task ledger) is preserved by the turn-start clear (`ScratchpadStore.clear_excluding({"todo"})`) so the ledger persists across turns in `default`/`teacher` modes — only ephemeral notes are wiped. See [§8 Agent self-management tools](#8-agent-self-management-tools).
- **Mode-aware persistence (R12/FM-12):** in `loop` and `feature` modes the scratchpad defaults to persisting across turns so cross-turn plans survive; in `default`/`teacher` modes it is cleared at turn start unless the user explicitly set `scratchpad_persist_across_turns=True`. An explicit `True` always persists; `loop`/`feature` always persist (mode wins). Computed once at turn start in `mu/agent/loop_body.py`.
- Used for short-lived plans, observations, and temporary working notes
- Included in system prompt via `render_summary()`

**Why implemented:** Provides a workspace for the AI to jot down temporary thoughts without polluting the persistent memory. Useful for step-by-step reasoning within a single turn.

---

## 4. Collation Buffer (CollationBuffer)

**Location:** `mu/agent/collation.py`

**Purpose:** Defers delivery of read-only tool results to reduce token usage during context gathering phases.

**How it works:**
1. Read-only tools (`read_file`, `search_for_string`, `get_chunk`, etc.) results are stored in buffer
2. Model receives a short status message instead of full payload
3. When ready, model calls `flush` to receive all buffered data at once

**Key features:**
- **Size limit:** 1MB default (configurable)
- **Auto-truncation:** Oldest entries dropped when limit exceeded
- **Persistence:** Saved in session JSON, survives reloads
- **Recall path (R11/FM-4):** when a collated result is deferred, the raw payload is also stored in the `ToolResultCache` (`mu/session/tool_cache.py`) with `force=True` and the placeholder text is stamped with `[cache:KEY]`, so a result dropped by a later buffer overflow remains recoverable via `recall(cache_key)`.
- **Collated tools:** `get_workspace_details`, `read_file`, `search_for_string`, `search_references`, `retrieve_relevant_context`, `get_chunk`, `list_dir`, `url_grounding`, `web_search`, `arxiv_search`, `doi_resolve`, `reddit_search`, `stackoverflow_search`, `hackernews_search`, `read_document`, `get_tasks`, `get_current_task` (the canonical set lives in `mu/tools/descriptors.py:_COLLATED_TOOL_NAMES`).

**Why implemented:** Prevents context window bloat during "exploration" phases. The AI can gather multiple pieces of information before deciding which to process, rather than receiving everything immediately.

---

## 5. Session History

**Location:** `mu/session/session.py` (SessionManager)

**Purpose:** Full conversation history with message compression.

**Key features:**
- **Active context window:** Configurable (default 150 messages)
- **Tool message compression:** Older tool calls/results are summarized when exceeding `tool_context_window`
- **Compact mode:** Option to collapse completed turns (removes intermediate tool metadata)
- **Structured tool results:** Rich metadata about tool execution (file counts, match counts, etc.)

**Why implemented:** Balances between keeping full context and managing token costs. Tool-heavy conversations are compressed while preserving the essential information.

---

## 6. Tool Result Cache (ToolResultCache)

**Location:** `mu/session/tool_cache.py`

**Purpose:** Sidecar cache for full tool results that get compressed out of
history, and for short-circuiting repeat reads of unchanged files.

**Key features:**
- **Content-keyed `recall(key)`** — fetch a cached result by its 12-char key
  after compaction has replaced the original `tool_result` message with a
  one-line summary. LRU-evicted by count (`tool_result_cache_entries`,
  default 50) and bytes (`tool_result_cache_bytes`, default 512 KB); both
  bounds are mode-aware (loop/feature raise to ≥256 / ≥2 MB).
- **Locator-indexed auto-recall** — `store_with_locator` indexes a read-only
  result by `"{tool}:{sorted_args}"` and records the source file's
  `mtime`/`size`. A later repeat `read_file` / `get_chunk` / `list_dir` /
  `search_*` on the *same unchanged* file short-circuits to the cached result
  via `lookup_by_locator` — no disk read, no re-burned tokens. Stale files
  (mtime/size mismatch) miss cleanly and fall back to executing the tool.
- **Collation recall path (R11/FM-4)** — when a collated result is deferred,
  the raw payload is also cached (`force=True`) and the placeholder stamped
  with `[cache:KEY]`, so a result dropped by a later buffer overflow stays
  recoverable via `recall(cache_key)`.
- **Reverse index** — result-content hash → cache key, so
  `HistorySearchMixin._lookup_cache_key` resolves a compressed
  `tool_result` part to its key in O(1).

**Why implemented:** Without auto-recall, the long-horizon stall has the
agent re-reading the same files over and over — every re-read re-burns tokens
and the model re-derives context it already had. The locator index makes the
second-and-later read of an unchanged file a free cache hit.

---

## 7. Max-iterations consolidation

**Location:** `mu/agent/loop_body.py` (tail of `run_turn`)

**Purpose:** When a turn hits `max_iterations` mid-work, the agent no longer
stops silently. It runs **one final consolidation turn** (tools disabled): a
user message asks the model to state what it accomplished, what remains, and
any blocker; the response is appended to history and persisted to task memory
(`kind="consolidation"`, tags `["consolidation","max_iterations"]`) so the
next turn inherits the handoff instead of re-deriving state from scratch. The
`_consolidation_done` guard latches per turn and resets at turn start.

**Why implemented:** A hard iteration cap without a final summary produces an
abrupt cliff — the user sees `max_iterations_reached` with no idea what was
done. The forced consolidation turns the cap into a useful handoff.

---

## 8. Agent self-management tools

**Location:** `mu/tools/session/handlers.py` (`context_status`,
`checkpoint_progress`), `mu/tools/memory/handlers.py` (`retire_thread`),
`mu/tools/task/todo.py` (`todo_delete`, `todo_clear`).

**Purpose:** Let the agent own its context — decide what to keep and what
to prune — instead of the harness shoehorning it with fixed rules. The
harness still enforces hard limits (iteration cap, token budget, tight
repeat detection); *behavioral* self-management is the agent's job,
driven by the SELF-MANAGEMENT block in `AGENTIC_SYSTEM_BASE` (rule 7).

**The tools:**

- **`todo_delete(id)`** / **`todo_clear(status?)`** — prune the task ledger.
  `todo_clear` with no arg (or `status='completed'`) prunes finished items;
  `status='all'` wipes the ledger to start fresh. Without these the agent
  could only re-status tasks, never remove them — "clean up the stale task
  list" was impossible.
- **`context_status`** — read-only per-layer token fill (L0–L5) plus a
  `self_management` block: `uncheckpointed_entries` (history added since the
  last L2 fold), `l2_stale_vs_l5` (True when ≥12 uncheckpointed entries — the
  signal to checkpoint), `todo_count` / `scratchpad_notes` / `memory_entries`,
  and the staleness signals `active_memory`, `stale_memory_count`,
  `stale_todos` (completed todos not yet cleared), `in_progress_todos`, and
  `memory_pressure_pct` (entries vs max — curate before a silent eviction).
  Reuses `collect_context_layers` so the numbers agree with `/memory` and
  the Memory Map panel. Call before big gathers or when a turn feels long.
- **`checkpoint_progress(min_new_entries=6)`** — agent-callable wrapper
  around `HistoryMixin.force_progress_checkpoint`. Folds recent history
  into L2 *without* compacting (the anchor doesn't advance, entries stay
  verbatim in L5) so the next iteration sees a fresh Progress picture.
  No-op if there isn't enough new work since the last checkpoint — no
  wasted provider call. Coexists with the cadenced checkpoint (Fix #9): the
  cadence stays as a fallback safety net, and `force_progress_checkpoint`
  tracks `_checkpoint_anchor` so an agent-driven call after a cadenced one
  is a clean no-op.
- **`retire_thread(topic, reason, clear_scratchpad=True)`** — the "I'm done
  carrying this" lever. Archives every ACTIVE task-memory entry whose
  content/tags/source matches `topic` (so it drops out of the default
  default active+stale search and the system-prompt summary), optionally removes
  matching non-todo scratchpad notes, and writes a single archived audit
  entry recording the drop. Todo-ledger entries are never touched here —
  prune those with `todo_delete`/`todo_clear`.

**Persistent todo ledger:** the `todo`-tagged slice of the scratchpad is
now carved out of the turn-start auto-clear (`ScratchpadStore.clear_excluding`
in `loop_body.py`). In `default`/`teacher` modes ephemeral scratchpad notes
are still wiped at turn start, but the todo ledger survives across turns
for the session — so the agent can reconcile and prune it (the Claude-Code
"clean up the stale task list" move). In `loop`/`feature` modes the whole
scratchpad already persisted; the carve-out changes nothing there.

**Why implemented:** The long-horizon fixes (#9 cadenced checkpoint, #12
stall nudge, #13 forced consolidation) were harness-decided and
agent-ignorant — the harness managed context *for* the agent. These tools
flip that: the agent sees its own fill (`context_status`), folds its own
summary (`checkpoint_progress`), drops its own threads (`retire_thread`),
and prunes its own ledger (`todo_delete`/`todo_clear`). The harness keeps
the hard caps as a safety net but hands the *what to keep* decision to the
agent.

**Staleness decay (anti-rot):** self-management surfaces generate
metadata, and unchecked accumulation is the exact failure mode where the
agent loses track of what matters. To keep the active set honest, the
loop applies automatic decay at turn start
(`BaseNoteStore.apply_staleness_decay`, `loop_body.py`):

- Every save and every `search_memory` hit stamps the entry's
  `last_hit_turn` with the store's monotonic `turn_count` (persisted across
  resume). Passive L3 injection does NOT stamp it — injection is not active
  reliance.
- At turn start, ACTIVE entries whose `last_hit_turn` is older than
  `memory_stale_after_turns` (default 12, `VARIABLE_SCHEMA`; 0 disables)
  are demoted to `stale`. "active" now means *recently mattered*, not *ever
  saved*, so the default search and L3 injection stay high-signal.
- Decay is **reversible through use**: a `search_memory` hit (default
  search is active+stale) or a re-save of identical content promotes a
  `stale` entry back to `active` automatically — the agent never loses what
  it's actually using. Only `done`/`superseded`/`archived` are excluded from
  the default search view.
- The agent's job is to *finish* the job decay starts: when `context_status`
  reports `stale_memory_count > 0`, `archive_memory` / `retire_thread`
  those entries; when `stale_todos > 0`, `todo_clear('completed')`. The
  SELF-MANAGEMENT block frames this as the "reconcile, don't accumulate"
  habit, and the invariant is **net metadata stays flat or shrinks per
  turn** — adding without retiring is the rot to avoid.

---

## Memory Integration Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  User Prompt    │────▶│  Turn Scratchpad │────▶│  [CLEARED]      │
│                 │     │ (temporary notes)│     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Collation      │◄────│  Read-Only Tools │────▶│  Flush on       │
│  Buffer         │     │  (deferred)      │     │  demand         │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────┐
│  Task Memory    │◄────│  Agent saves     │
│  (persistent)   │     │  key results     │
└─────────────────┘     └──────────────────┘
         │
         ▼
┌─────────────────┐
│  System Prompt  │◄──── Includes memory summaries
│  (LLM context)  │
└─────────────────┘
```

---

## Memory Lifecycle

Memory entries carry a `status` field that tracks their lifecycle state. This lets the agent distinguish what is actively being worked on from what has been completed, superseded, or archived — preventing old goals and completed work from resurfacing in search results and system-prompt injection.

### Status states

| Status | Meaning | Search default | Summary injection | Eviction weight |
|--------|---------|----------------|-------------------|-----------------|
| `active` | Current work, ongoing relevance | Included | Included (first priority) | 1.0 (last evicted) |
| `done` | Work described is complete | Excluded unless filtered | Included (capped at 2) | 0.5 |
| `superseded` | A newer entry replaces this one | Excluded unless filtered | Excluded | 0.3 |
| `archived` | No longer relevant; retained for audit | Excluded unless `include_all` | Excluded | 0.1 (first evicted) |
| `stale` | Not hit in N turns (auto-demoted by `apply_staleness_decay`; reactivates on search/save) | Included (reactivates to active on hit) | Excluded (active-first) | 0.8 |

### Transition diagram

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
                    ▼                                              │
  ┌─────────┐  save_memory   ┌─────────┐  retire_memory  ┌──────┐  │
  │ (new)   │ ─────────────▶ │ active  │ ─────────────▶ │ done │  │
  └─────────┘                └─────────┘                └──────┘  │
                               │     │                     │     │
                               │     │ reactivate_memory   │     │
                               │     │ (clears link)        │     │
                               │     ▼                     │     │
                               │  ┌──────────┐             │     │
                               │  │superseded│◀────────────┘     │
                               │  └──────────┘  supersede_memory │
                               │     │                          │
                               │     │ archive_memory           │
                               │     ▼                          │
                               │  ┌──────────┐                  │
                               │  │ archived │                  │
                               │  └──────────┘                  │
                               │     │                          │
                               │     │ reactivate_memory        │
                               │     ▼                          │
                               └─────┘                            │
                                                                  │
  ┌───────┐  update_memory_status(status='stale')                 │
  │ stale │ ◀─────────────────────────────────────────────────────┘
  └───────┘  reactivate_memory returns to active
```

### Tool reference

| Tool | Action | When to use |
|------|--------|-------------|
| `save_memory` | Creates or updates entry with `kind` + `status` | General saves; classify via `kind` (decision/finding/observation/goal) |
| `update_memory_status` | Transitions entry to any valid status | Direct lifecycle control; if `status='superseded'` and reason contains `#N`, sets `superseded_by` |
| `supersede_memory` | Marks old as superseded, links old↔new | Decision reversed, goal changed, finding corrected |
| `retire_memory` | Shorthand for `update_memory_status(entry_id, 'done')` | Work described by the entry is complete |
| `reactivate_memory` | Sets status back to `active`, clears `superseded_by` | Revisiting completed or superseded work |
| `archive_memory` | Sets status to `archived` | No longer relevant but should not be lost (old project context, superseded with no replacement) |
| `search_memory` | Filtered search with `status`, `kind`, `tags_exclude`, `include_all` | Query active work, historical entries, or full audit |

### Goal persistence flow

Session goals pinned via `/goal` or `set_session_goal` are now persisted as memory entries:

1. **Goal set**: `set_session_goal(goal_text)` saves a memory entry with `kind='goal'`, `status='active'`, `tags=['goal', 'session-goal']`. The entry ID is stored as `session._active_goal_memory_id`.
2. **Goal clear**: `/goal clear` or `set_session_goal(clear=True)` marks the goal entry `status='done'` (not deleted — audit trail retained).
3. **Goal shift**: If a new goal is set while one is active, the previous goal entry is marked `status='done'` before the new one is created.
4. **Auto-clear**: At turn end, if `_active_goal_memory_id` is set, the goal entry is marked `status='done'` — **unless the goal is sticky**. In `loop`/`feature` modes (or when `session_goal_sticky` is explicitly set), the live `session_goal` variable and its memory entry persist across turns so long-horizon work keeps its anchor; `/goal clear` still retires it.
5. **Eviction safety**: If the goal entry was evicted before retirement, the clear path handles `None` gracefully — no crash, logs a warning.
6. **Dedup**: Identical goal text triggers dedup in `store.save()` (content+tags match, status match) — increments `hits`, no spurious done→active churn.

### Eviction by status

When `max_entries` is hit, eviction proceeds in this order (lowest score first):

```
archived (0.1) → superseded (0.3) → done (0.5) → stale (0.8) → active (1.0)
```

Within each status tier, the existing kind-weight + LRU scoring applies:

- **Kind weights**: `decision=3.0`, `finding=2.0`, `goal=2.5`, `observation=1.0`
- **Eviction score**: `(hits + 1) * kind_weight * status_weight`
- Lower score = evicted first.

This means an active decision with 0 hits scores `2 * 3.0 * 1.0 = 6.0`, while an archived observation with 5 hits scores `6 * 1.0 * 0.1 = 0.6`. Archived entries are always evicted before active ones, regardless of hit count.

**Eviction events (R12/FM-11):** when `_enforce_limit` evicts entries, it appends a one-line notice per evicted entry to a transient `eviction_log` (capped at the last 20). The agent loop drains this log each turn via `drain_eviction_log()` after rendering L3 memory, so the model is told — exactly once — that a memory it may have relied on is gone (preventing silent re-derivation). The log is not persisted.

### Search default behavior

`search_memory("auth refactor")` with no status filter returns **active + stale entries** matching. A hit on a `stale` entry reactivates it to `active` (decay is reversible through use — see §8), so retrieving decayed-but-relevant knowledge brings it back to the working set automatically. `done`/`superseded`/`archived` stay excluded from the default view, which prevents the core complaint: completed and superseded work resurfacing as if still live.

To see historical entries:
- `search_memory("auth", status="done")` — only completed entries
- `search_memory("auth", status="superseded")` — only superseded entries
- `search_memory("auth", include_all=True)` — all entries regardless of status (full audit)

### render_summary() behavior

The system-prompt memory snapshot (`render_summary(limit=8, include_archived=False, query="")`) now:
1. **Relevance-aware injection (R6/FM-7):** when `query` is non-empty (the agent loop passes the current turn's user text), entries that score against the query are injected FIRST, leading the snapshot. Scoring uses the non-mutating `_rank_by_relevance` helper so this bias does NOT touch `hits`/`updated_at` and cannot perturb eviction scoring.
2. Fills the remaining slots by the **recency partition**: active entries first (up to the limit), then optionally **done entries** (capped at 2), then other non-archived entries.
3. **Excludes archived** entries entirely (unless `include_archived=True`), even among relevance hits.
4. Each line includes the status tag: `- #id [active] [tags] (source): content`

When `query` is empty this reproduces the original recency-only ordering exactly. This ensures the injected memory snapshot reflects **current work**, and surfaces the *relevant* durable decisions/findings for the current turn rather than only the most-recently-touched ones.

---

## Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `memory_enabled` | `True` | Enable task memory system |
| `memory_max_entries` | `64` | Max persistent memory entries |
| `memory_stale_after_turns` | `12` | Auto-demote ACTIVE memory entries not hit in this many turns to `stale` at turn start. `0` disables decay. Reversible — a search hit or re-save reactivates `stale`→`active`. |
| `memory_summary_limit` | `8` | Entries to include in system prompt |
| `scratchpad_enabled` | `True` | Enable turn-local scratchpad |
| `scratchpad_max_entries` | `24` | Max scratchpad entries |
| `scratchpad_persist_across_turns` | `False` | Keep scratchpad across turns. Mode-aware: `loop`/`feature` modes persist regardless; `default`/`teacher` clear at turn start unless this is `True`. |
| `collation_enabled` | `True` | Enable deferred tool results |
| `tool_context_window` | `6` | Recent tool messages to keep uncompressed |
| `tool_result_floor` | `4` | Trailing tool-result messages compaction leaves verbatim. Mode-aware: loop/feature raise to ≥8. |
| `tool_result_cache_entries` | `50` | Max entries in the tool-result sidecar cache. Mode-aware: loop/feature raise to ≥256. |
| `tool_result_cache_bytes` | `524288` | Max bytes in the tool-result sidecar cache. Mode-aware: loop/feature raise to ≥2 MB. |
| `progress_checkpoint_every` | `0` | Fold recent history into the structured L2 summary every N iters without compacting. Loop/feature default 12; `0` disables. |
| `recoverage_stall_threshold` | `4` | Consecutive re-coverage iterations (no concrete change) before a re-orient nudge. `0` disables. |
| `context_token_limit` | `900000` | Token budget used for runtime context/history trimming |
| `context_trim_threshold` | `0.85` | Begin summarizing once runtime context reaches this fraction of the token budget |

---

## Design Philosophy

1. **Token efficiency:** Collation and compression minimize wasted tokens
2. **Persistence:** Task memory survives sessions and prevents re-work
3. **Ephemerality:** Scratchpad provides temporary workspace without pollution
4. **Intelligent eviction:** LRU + hit counting keeps valuable memories accessible
5. **Transparency:** Memory summaries are injected into system prompts for easy access
