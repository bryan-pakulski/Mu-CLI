# Default Mode

The baseline workflow. Selected automatically; switch back to it from
any other mode with `/mode default`.

Default mode is for general coding and codebase work — adding a
feature, fixing a small bug, answering a how-does-this-work question,
making a focused change. For long-horizon work use [loop](loop_mode.md);
for staged plans use [feature](feature_plan_engine.md); for "explain
this, don't change it" use [research](research_mode.md).

## The loop

The prompt (`mu/prompts/templates/default.md`) describes one loop; the
harness makes the "verify" step checkable with the turn receipt below.

1. **Clarify** real ambiguity (which file, scope, destructive?) with
   `ask_user_choice` before acting.
2. **Recall** with `search_memory` when the request may repeat earlier
   work.
3. **Orient** with `retrieve_relevant_context` when the location is
   unknown; `read_file` / `search_for_string` / `search_references`
   when it is known. Independent reads issued in one turn run in
   parallel.
4. **Plan** work of 3+ steps with `todo_write`; one item `in_progress`
   at a time (`todo_set_status`).
5. **Act** with `apply_diff` or `search_and_replace_file`; `write_file`
   for new files.
6. **Verify** with a test, linter or smoke command through `bash`
   (`purpose="verify"`).
7. **Summarise** what changed, what was verified and what is open; save
   non-obvious findings with `save_memory`.

If the model needs something only the user can give, it calls
`raise_blocker` (see [Blockers](#blockers)).

## Turn receipt

The harness records what each default-mode turn did
(`mu/modes/receipt.py`):

- **Files changed**, from the edit tracker (`mu/modes/edits.py`), which
  notes every successful write-side edit.
- **Checks run.** `bash` takes an optional `purpose`
  (`verify` | `repro` | `explore`). A run with purpose `verify` or
  `repro`, or one the harness recognises as a check (`pytest`,
  `npm test`, `cargo test`, `go test`, `ruff`, `mypy`, `tsc`, `eslint`,
  … and the suite commands in the project's `.mucli/jobs.toml`
  `[checks]`), is recorded as a `CheckRun` with its exit code.
  `purpose="explore"` is never recorded.
- **Work items touched** and **open blockers**.

The receipt's status is `verified` when a passing check ran after the
last edit, `unverified` when files changed without one, and
`no_changes` otherwise. A check that ran before the last edit does not
count.

Receipts are stored with the session (last 60). The terminal prints one
line after the turn (`Receipt · 2 files changed · checks passed`); the
GUI shows a quiet row under the assistant message that opens a sheet
with the files and check runs; Android shows the same row. When the
last turn ended unverified, the next turn's mode capsule says so.

## Verify nudge

At the end of a turn that changed files without a passing check after
the last edit, the harness gives the model one more iteration with a
short nudge: run a check, or say plainly why it can't be checked. It is
never a block, and fires at most once per turn; if the model still
doesn't verify, the receipt says *unverified*. Turn it off with
`/set verify_nudge false`.

## Work items and the Plan disclosure

`todo_write` / `todo_set_status` / `todo_list` / `todo_delete` /
`todo_clear` are thin wrappers over the session's typed work items
(`mu/modes/work.py`). Items are saved with the session and never
evicted. The engine keeps exactly one item `in_progress`: starting one
puts the previous back to `pending`. `done` can cite passing check runs
(`evidence=["r3"]`); a failing run is refused as evidence.

When work items exist, the GUI chat shows a **Plan** disclosure above
the composer listing them with the current one marked. The mode capsule
in the request-only TURN CONTEXT carries the open count and the current
item.

## Blockers

`raise_blocker(summary, needs, …)` is a core tool in every mode. It
records a blocker in session state and ends autonomous continuation.
The user resolves it by sending their next message, with the panel's
Resolve action, or with `/blocker resolve <id> [answer]`.

## Delegation

For self-contained side-quests that would bloat context (deep codebase
research, large multi-file refactors), fire
`spawn_agent` calls in parallel. Up to four run concurrently (capped at
`parallel_tool_concurrency`). Children inherit folder context but have
isolated history; they return a focused summary the parent can fold in.

## Prompt overrides

The bundled template is the single source of the default prompt. A
user override in `~/.mucli/prompts/default.md` still wins; when its
`version` is older than the bundled template, the terminal and GUI show
a one-time notice. `/prompts diff default` compares them,
`/prompts keep default` silences the notice, and
`/prompts reset default` moves the override aside.

## When NOT to use default mode

- Long-running goals you want the assistant to keep working on
  across turns → `/mode loop`.
- Multi-phase work that benefits from explicit user approval gates
  before implementation → `/mode feature`.
- "Help me understand X" / write-up tasks → `/mode research`.
- Auditing for vulnerabilities → `/mode security`.
- A specific, reproducible bug to chase → `/mode debug`.
