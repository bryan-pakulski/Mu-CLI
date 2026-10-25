# Debug Mode

For chasing a specific, reproducible bug to root cause. Switch in via
`/mode debug`.

Debug mode optimizes for *finding the actual fault*, not patching the
symptom. One bug is one **debug case**: a typed record the session
owns, persisted with the session, that climbs a status ladder only when
an engine-run check proves the next rung. The prompt
(`mu/prompts/templates/debug.md`) only describes the loop; the method
lives in `mu/debug/` (`state.py`, `engine.py`) and the tools in
`mu/tools/debug/handlers.py`.

For exploratory "is something wrong here?" questions, use
[research](research_mode.md). For long autonomous runs, use
[loop](loop_mode.md).

## The debug case

A case holds the title and symptom, the accepted reproducer, hypotheses
(each with evidence), suspect locations, the fix summary and changed
files, the verification runs, an optional regression test, an optional
bisect result and, on close, the structured root cause.

Status ladder:

```
open → reproduced → root_caused → fixed → verified → closed
                                   (or unreproducible)
```

| Rung | Earned by |
|---|---|
| `reproduced` | `record_repro`: the command fails as described |
| `root_caused` | a hypothesis is `confirmed` with supporting evidence |
| `fixed` | `verify_fix` is called (records the fix summary and files) |
| `verified` | `verify_fix` passes: repro now passes, suite passes, repeats pass |
| `closed` | `close_debug_case` with the root cause |
| `unreproducible` | `close_debug_case` with an `unreproducible_reason` (not allowed once verified) |

A session can hold several cases; one is current. Cases survive turns
and session reloads (they no longer live in the scratchpad).

Every command the engine runs is a `CheckRun` (`mu/modes/evidence.py`,
via `run_check`): command, exit code, pass/fail per its predicate, a
short excerpt and the full output in the result store. Runs are
referenced as `r<n>`.

## Tools

Available only in debug mode (`phase="debug"`).

| Tool | Behaviour |
|---|---|
| `open_debug_case(title, symptom)` | Opens and selects a case. |
| `record_repro(command, expect_marker?)` | Runs the reproducer. Accepted only if it **fails as described**: `expect_marker` appears in the output, or (no marker) the exit is non-zero. A command that passes is rejected. Re-run to narrow. Refused once the case has a fix. |
| `add_hypothesis(statement)` | Adds a hypothesis (`untested`). |
| `update_hypothesis(id, status, evidence)` | Sets `untested` / `supported` / `refuted` / `confirmed`. Evidence items are `{kind: run\|location\|search\|note, ref, stance: supports\|refutes}`; run refs must exist, locations must be real `path:line` in the workspace. `confirmed` needs a supporting run or location; `refuted` needs refuting evidence. |
| `add_suspect(path, line?, symbol?, reason)` | Records a suspect location; the path must exist. |
| `verify_fix(summary, suite_command, files?, repeat?)` | Re-runs the repro (must now pass), then `suite_command` (must exit 0), then the repro `repeat` times (max 50) for flaky bugs. `verified` only if all pass. `files` defaults to `git status`. |
| `record_regression_test(path, name, command?)` | Shows the test fails on the pre-fix tree (a git worktree of HEAD with the new test copied in) and passes on the current tree. Python tests default to `pytest path::name`; other languages need `command`. Optional; a missing one is reported as a gap on close. |
| `close_debug_case(cause, signature?, fix_summary?)` / `close_debug_case(unreproducible_reason)` | Closes a `verified` case with its root cause, or closes an unverified one as unreproducible. |
| `debug_status(all?, select?)` | Shows the current case and the next step, lists every case, or selects one. |
| `bisect_repro(good, bad="HEAD")` | Runs `git bisect run` between the two revisions with the recorded repro as the predicate, in a separate worktree (your tree is never checked out). Returns the first bad commit and the steps. |

`record_repro`, `verify_fix`, `record_regression_test` and
`bisect_repro` run commands, so they need approval (the preview shows
the commands that will run) and go through the `run_check` controls:
no login shell, scrubbed environment, the bash secret guard, timeouts
and output scrubbing.

## Edit gate

While the current case is still `open` (no accepted reproducer), the
first `write_file` / `apply_diff` / `search_and_replace_file` of a turn
is refused with a warning: record a reproducer, or close the case as
unreproducible. Repeating the edit proceeds. With
`debug_require_repro=true` every edit stays blocked until the case is
reproduced. The gate applies only in debug mode with a case open.

## Plan mode

Plan mode refuses the four run tools. Opening a case, hypotheses,
suspects and `debug_status` still work, and the mode capsule says runs
are unavailable until `/plan off`.

## Root causes in memory

`close_debug_case` saves the structured root cause (signature, cause,
fix, files) to the Memory Ledger as a verified finding tagged `debug`,
`root-cause` and the changed file paths. The panel's past root causes
come from a Memory Ledger search over the case's suspect and fix files,
filtered to `root-cause` entries; step 0 of the loop (`search_memory`
on the error text) finds them too.

## Turn context

Each turn the current case rides in the request-only TURN CONTEXT as a
short capsule: title, status, symptom, repro and expected failure, the
latest hypotheses, suspects and the next step. It is never saved to
history.

## Workflow

1. **Recall** — `search_memory` the error text or file, then
   `open_debug_case`.
2. **Reproduce** — `record_repro` with the smallest failing command.
3. **Locate** — `search_for_string` the error text, `search_references`
   the symbol, read full functions in parallel; `add_suspect`.
4. **Hypothesise** — `add_hypothesis`, then `update_hypothesis` with
   evidence. Stuck: `bisect_repro`.
5. **Fix** surgically (`search_and_replace_file` / `apply_diff`); don't
   refactor around it.
6. **Verify** — `verify_fix`; optionally `record_regression_test`.
7. **Close** — `close_debug_case` with the cause and signature.

Debug mode still refuses `try/except` band-aids, fixes claimed without
a repro, and widening the change beyond the cause.

## `/debug`

```
/debug                       current case: status, repro, hypotheses, last runs, next step
/debug cases                 every case (* marks the current one)
/debug select <id>           make a case current
/debug close <root cause>    close the verified current case
/debug unreproducible <why>  close the current case as unreproducible
```

Closes and selections from the command are recorded with the actor
`user:cli`.

## Panel

The debug panel uses the shared mode shell: a state sentence, text tabs
**Case · Hypotheses · Runs · Root causes**, and Now.

- A stepper shows open → reproduced → root caused → fixed → verified.
- **Case** shows the symptom, reproducer, expected failure, fix and
  root cause, with suspects as `path:line` rows that open the file.
- **Hypotheses** are rows with status marks; each opens a sheet with
  its evidence.
- **Runs** are shared `CheckRun` rows (repro before, repro after, suite,
  repeats); each opens the run sheet with the full output.
- **Root causes** lists past root causes from memory for the files
  involved.
- Now shows open blockers and, once verified, *Close case?* (the user
  enters the root cause).

The panel reads `GET /api/debug/state` (`active` is true only in debug
mode); `POST /api/debug/cases/{id}/select` and `POST /api/debug/close`
go through the same engine as the tools. Android's Debug screen shows
the same case, stepper, hypotheses, runs and cases, with *Close case*
when verified.

## When to leave debug mode

- The fault turns out to be design-level / architectural →
  `/mode feature` for a phased plan.
- The "bug" is actually a misunderstanding of behavior →
  `/mode research` to write it up.
- The work is going to take hours of autonomous iteration →
  `/mode loop` with a mission.
