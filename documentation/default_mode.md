# Default Mode

The baseline workflow. Selected automatically; switch back to it from
any other mode with `/mode default`.

Default mode is for general coding and codebase work — adding a
feature, fixing a small bug, answering a how-does-this-work question,
making a focused change. For long-horizon work use [loop](loop_mode.md);
for staged plans use [feature](feature_plan_engine.md); for "explain
this, don't change it" use [research](research_mode.md).

## Core behavior

1. **Recall before research**
   - `search_memory` for the topic, file paths, or error patterns
     before re-deriving anything that may have been seen before.

2. **Orient with semantic retrieval**
   - For non-trivial requests, start with `retrieve_relevant_context`
     (ranks by lexical + symbol + recency + git-diff). Faster than
     blind `read_file` chains.
   - Use `search_for_string` / `search_references` for exact-text
     follow-ups.

3. **Plan when scope warrants**
   - 3+ tool calls or multi-file changes → publish a `todo_write` plan
     so the user can see the roadmap.
   - Exactly one task `in_progress` at a time via `todo_set_status`.

4. **Parallel context collection**
   - Issue independent reads in a single turn — `read_file`,
     `list_dir`, `search_*`, `retrieve_relevant_context` execute
     concurrently and buffer to the collation queue.
   - `flush` when there's enough to decide.

5. **Surgical edits**
   - `apply_diff` for anchored multi-hunk changes.
   - `search_and_replace_file` for unique-string substitutions.
   - `write_file` only for new files or full rewrites.

6. **Verify with evidence**
   - Don't claim done from inspection — run tests (`pytest`,
     `npm test`, `cargo test`), a linter, or a smoke command via
     `bash`.
   - Re-read the modified file to confirm the change landed.

7. **Save what's reusable**
   - Persist non-obvious findings (root causes, architectural
     invariants, "X actually lives in Y") with `save_memory`.

8. **Final summary**
   - What changed, what was verified, what's still open. Tight — no
     narration of every tool call.

## Delegation

For self-contained side-quests that would bloat context (deep codebase
research, large multi-file refactors), fire
`spawn_agent` calls in parallel. Up to four run concurrently (capped at
`parallel_tool_concurrency`). Children inherit folder context but have
isolated history; they return a focused summary the parent can fold in.

## When NOT to use default mode

- Long-running goals you want the assistant to keep working on
  unattended → `/mode loop`.
- Multi-phase work that benefits from explicit user approval gates
  before implementation → `/mode feature`.
- "Help me understand X" / write-up tasks → `/mode research`.
- Auditing for vulnerabilities → `/mode security`.
- A specific, reproducible bug to chase → `/mode debug`.
