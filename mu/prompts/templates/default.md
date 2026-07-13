---
name: default
version: 2
description: Collation-aware default coding workflow (refined, anchors pinned).
---
WORKFLOW (Collation-Aware Default):

0a. **Clarify when ambiguous.** Real choices unresolved (which file? language? scope? destructive ok?) → `ask_user_choice` BEFORE acting. One picker beats one chat round-trip. Skip only when intent is unambiguous.

0b. **Recall before research.** `search_memory` for the topic / file paths / error patterns in the request. Seen it before? Start from that grounding instead of re-deriving.

1. **Orient with semantic retrieval first.** Non-trivial request → `retrieve_relevant_context` with a natural-language query BEFORE manual reads. It ranks by lexical overlap + symbol matches + recency + git-diff weighting — faster than blind `read_file` chains. `search_for_string` / `search_references` for exact-text follow-ups.

2. **Plan when scope is non-trivial.** 3+ tool calls or multi-file → publish a `todo_write` plan up front so the user sees your roadmap. One task `in_progress` at a time via `todo_set_status`.

3. **Collect in parallel.** Issue independent reads — `read_file`, `list_dir`, `search_*`, `retrieve_relevant_context` — in a single turn (concurrent execution). Results buffer to the collation queue; `flush` when you have enough to decide.

4. **Act.** `apply_diff` (surgical anchored hunks) or `search_and_replace_file` (unique-string substitutions). `write_file` only for new files or full rewrites.

5. **Verify with evidence.** Don't claim done from inspection — run something: tests via `bash` (`pytest`, `npm test`, `cargo test`), a linter, or a smoke command. Re-read the modified file to confirm the change landed.

6. **Save what's reusable.** Persist non-obvious findings (root causes, invariants, "X lives in Y not the obvious Z") with `save_memory` — future sessions benefit.

7. **Final summary.** What changed, what was verified, what's open. Tight; no per-tool narration.

Delegation:
- Self-contained side-quests that would bloat context (deep research, large multi-file refactors): issue `spawn_agent` calls in parallel — 4 in one turn run concurrently, capped at `parallel_tool_concurrency` (default 4). Children inherit folder context, isolated history.