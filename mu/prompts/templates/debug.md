---
name: debug
version: 2
description: Debug case loop — reproduce, locate, hypothesise, fix, verify, close.
---
WORKFLOW (Debugging) — one bug is one debug case; its status climbs open → reproduced → root caused → fixed → verified → closed.

0. **Recall**: `search_memory` the error text / file — a past root cause may apply. `open_debug_case(title, symptom)`.
1. **Reproduce**: `record_repro(command, expect_marker)` with the smallest failing command. The engine runs it and accepts it only if it fails as described. Without a reproducer your first edit is refused.
2. **Locate**: `search_for_string` the error text, `search_references` the symbol, read full functions in parallel. `add_suspect(path, line, reason)`.
3. **Hypothesise**: `add_hypothesis`, then `update_hypothesis(id, status, evidence)` — `confirmed` needs a supporting run (r<n>) or code location. Distinguish symptom from cause. Stuck? `bisect_repro(good)` finds the breaking commit in a separate worktree.
4. **Fix** surgically (`search_and_replace_file` / `apply_diff`); don't refactor around it.
5. **Verify**: `verify_fix(summary, suite_command, repeat)` re-runs the repro (must pass), the wider suite, and repeats for flaky bugs. Optionally `record_regression_test(path, name)` — it must fail on the pre-fix tree.
6. **Close**: `close_debug_case(cause, signature)` saves the root cause to memory; `unreproducible_reason` if it never reproduced.

`debug_status` shows the case. In plan mode, runs are unavailable; hypotheses and suspects still work.
