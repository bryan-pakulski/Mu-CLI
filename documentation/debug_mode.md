# Debug Mode

For chasing a specific, reproducible bug to root cause. Switch in via
`/mode debug`.

Debug mode optimizes for *finding the actual fault*, not patching the
symptom. It assumes you have a failing repro (or can build one) and
that the answer is "this exact line, for this exact reason" — not "add
a try/except and move on."

For exploratory "is something wrong here?" questions, use
[research](research_mode.md). For multi-day investigations, use
[loop](loop_mode.md).

## Core behavior

1. **Recall**
   - `search_memory` with the error string, file path, or suspect
     symbol. Sibling bugs may already have been fixed; start from that
     fix instead of re-deriving.

2. **Reproduce deterministically**
   - Capture the failing command via `bash` with full stderr.
   - Narrow vague repros to the minimum: single failing test
     (`pytest path::test_x -xvs`, `cargo test name -- --nocapture`,
     `node --inspect-brk ...`).
   - `save_scratchpad` the repro so it survives across iterations.

3. **Locate the emit site**
   - `search_for_string` for the exact error message — lands on the
     emit site fast.
   - `search_references` on the failing function / symbol to map call
     sites.
   - `retrieve_relevant_context` if the symptom is behavioral
     (timeout, wrong result) rather than a literal string.

4. **Read the actual code in parallel**
   - `read_file` on the emit site + direct callers + tests covering
     the symbol, all in one turn (parallel reads).
   - Read full functions, not snippets. The bug is usually three calls
     away from the suspicious line.

5. **Hypothesize the root cause**
   - Distinguish *symptom* from *cause*. The line that raises is
     rarely the bug.
   - Walk the call stack upstream.
   - For library / dependency bugs, `stackoverflow_search` or
     `web_search` with the exact error string + library version.

6. **Bisect when stuck**
   - `git log --oneline` for recent changes.
   - `git bisect start / good / bad` with the repro as the test
     predicate for a binary search.
   - For local changes, comment-out / early-return chunks to isolate.
   - Save the bisect range to scratchpad.

7. **Fix surgically**
   - `search_and_replace_file` with 3–5 lines of anchored context for
     one-off bugs.
   - `apply_diff` for multi-hunk changes.
   - Don't refactor surrounding code — fix the bug, ship.

8. **Verify with evidence**
   - Re-run the exact failing reproducer — must pass.
   - Run the whole test file (or wider suite) — your fix must not have
     broken siblings.
   - For race conditions / flake suspects, run the test 10× via `bash`
     to confirm.

9. **Persist the lesson**
   - `save_memory` with: symptom signature, actual root cause, the fix.
     Tag with the file path / module so step 1 of future sessions can
     hit it.

## What debug mode refuses to do

- **No `try/except` band-aids.** If the underlying state isn't
  genuinely unrecoverable, swallowing the exception hides the bug.
- **No "potential" fixes without a repro.** If you can't trigger the
  bug, you can't claim a fix.
- **No widening scope.** The change should be minimal and bounded to
  the cause.

## When to leave debug mode

- The fault turns out to be design-level / architectural →
  `/mode feature` for a phased plan.
- The "bug" is actually a misunderstanding of behavior →
  `/mode research` to write it up.
- The investigation is going to take hours / days →
  `/mode loop` for a goal-locked autonomous run.
