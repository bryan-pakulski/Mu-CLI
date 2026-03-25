# Mu-CLI Memory System Report

## Overview

Mu-CLI implements a multi-layered memory architecture designed to optimize context management for agentic AI workflows. The system consists of four primary memory stores plus a collation buffer for deferred context delivery.

---

## 1. Context (FolderContext)

**Location:** `core/workspace.py`

**Purpose:** Manages workspace folder tracking and file change detection.

**How it works:**
- Tracks monitored folders (`self.folders`)
- Maintains a snapshot of file states (mtime, size) for change detection
- Generates XML-formatted context diffs showing file modifications
- Provides a tree-map view of the workspace structure

**Why implemented:** Enables the AI to understand the project structure and detect file changes without re-scanning the entire workspace on every turn.

---

## 2. Task Memory (TaskMemoryStore)

**Location:** `core/memory.py`

**Purpose:** Persistent, durable memory that survives across turns and sessions.

**Key features:**
- **Max entries:** Configurable (default 64)
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
    created_at: float
    updated_at: float
    hits: int  # Access counter for ranking
```

**Why implemented:** Prevents the AI from re-reading large files or re-executing expensive searches. Critical findings (file locations, search results, workspace structure) are preserved for quick recall.

---

## 3. Scratchpad (ScratchpadStore)

**Location:** `core/memory.py`

**Purpose:** Turn-local temporary notes that are cleared at the start of each new user turn.

**Key features:**
- Same data structure as Task Memory
- **Auto-cleared** when `send_message()` is called (if `scratchpad_enabled`)
- Used for short-lived plans, observations, and temporary working notes
- Included in system prompt via `render_summary()`

**Why implemented:** Provides a workspace for the AI to jot down temporary thoughts without polluting the persistent memory. Useful for step-by-step reasoning within a single turn.

---

## 4. Collation Buffer (CollationBuffer)

**Location:** `core/collation.py`

**Purpose:** Defers delivery of read-only tool results to reduce token usage during context gathering phases.

**How it works:**
1. Read-only tools (`read_file`, `search_for_string`, `get_chunk`, etc.) results are stored in buffer
2. Model receives a short status message instead of full payload
3. When ready, model calls `flush` to receive all buffered data at once

**Key features:**
- **Size limit:** 1MB default (configurable)
- **Auto-truncation:** Oldest entries dropped when limit exceeded
- **Persistence:** Saved in session JSON, survives reloads
- **Collated tools:** `read_file`, `search_for_string`, `get_chunk`, `list_dir`, `get_workspace_details`, `git_status`, `git_diff`, `git_log`, `git_branch`, memory/scratchpad tools

**Why implemented:** Prevents context window bloat during "exploration" phases. The AI can gather multiple pieces of information before deciding which to process, rather than receiving everything immediately.

---

## 5. Session History

**Location:** `core/session.py` (SessionManager)

**Purpose:** Full conversation history with message compression.

**Key features:**
- **Active context window:** Configurable (default 150 messages)
- **Tool message compression:** Older tool calls/results are summarized when exceeding `tool_context_window`
- **Compact mode:** Option to collapse completed turns (removes intermediate tool metadata)
- **Structured tool results:** Rich metadata about tool execution (file counts, match counts, etc.)

**Why implemented:** Balances between keeping full context and managing token costs. Tool-heavy conversations are compressed while preserving the essential information.

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

## Configuration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `memory_enabled` | `True` | Enable task memory system |
| `memory_max_entries` | `64` | Max persistent memory entries |
| `memory_summary_limit` | `8` | Entries to include in system prompt |
| `scratchpad_enabled` | `True` | Enable turn-local scratchpad |
| `scratchpad_max_entries` | `64` | Max scratchpad entries |
| `collation_enabled` | `True` | Enable deferred tool results |
| `tool_context_window` | `6` | Recent tool messages to keep uncompressed |
| `active_context_window` | `150` | Messages in LLM context window |

---

## Design Philosophy

1. **Token efficiency:** Collation and compression minimize wasted tokens
2. **Persistence:** Task memory survives sessions and prevents re-work
3. **Ephemerality:** Scratchpad provides temporary workspace without pollution
4. **Intelligent eviction:** LRU + hit counting keeps valuable memories accessible
5. **Transparency:** Memory summaries are injected into system prompts for easy access
