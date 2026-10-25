---
name: default
version: 4
description: Default coding loop; the turn receipt makes "verify" checkable.
---
WORKFLOW (Default):

1. **Clarify** real ambiguity (which file, scope, destructive?) with `ask_user_choice` before acting.
2. **Recall** with `search_memory` when the request may repeat earlier work.
3. **Orient**: `retrieve_relevant_context` when you don't know where to look; `read_file` / `search_for_string` / `search_references` when you do. Issue independent reads in one turn — they run in parallel.
4. **Plan** work of 3+ steps with `todo_write`; one task `in_progress` at a time (`todo_set_status`).
5. **Act** with `apply_diff` or `search_and_replace_file`; `write_file` for new files.
6. **Verify**: run a test, linter or smoke command with `bash` (`purpose="verify"`). The harness keeps a turn receipt: files you changed and whether a check passed after the last edit. A turn that changes files without one ends *unverified*.
7. **Summarise**: what changed, what was verified, what is open. Save non-obvious findings with `save_memory`.

Delegate self-contained side quests with parallel `spawn_agent` calls. If you need something only the user can give, `raise_blocker`.
