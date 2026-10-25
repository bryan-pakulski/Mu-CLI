# Plan: Modes v2 — state and gates in code, short prompts

Status: implemented 2026-10-23 (deferred options in §9) · Follows: `documentation/plans/research_mode_v2.md` (implemented) ·
Covers: default, debug, feature, loop, security, teacher

## 1. Why

Research mode v2 showed the pattern: a mode works when its method lives in **session-owned
data and engine-checked tools**, and the prompt only describes the loop. The other six modes
still carry most of their method as prose, and a survey of each (2026-10-23) found the prose and
the code have drifted apart. Some modes describe machinery that does not run:

| Mode | Most serious finding | Where |
|---|---|---|
| teacher | The lecture watcher and the LEARNER PROFILE injection never run: the session stores a summary record without `current_lesson_id` / `learner_profile`, so `_active_teacher_lesson` returns early every turn | `mu/agent/teacher_watcher.py:22-27`, `mu/tools/teacher/handlers.py:122-135` |
| teacher | The prompt claims gates the engine lacks: diagnostic before curriculum, lecture before assignment, artifacts before assignment, "stays in lecturing" | `propose_curriculum_tool` `handlers.py:653-658` (also wipes an in-progress course), `assign_exercise` `:983-987` |
| security | PoCs run as `bash -lc` on the host, bypass the bash secret guard, and the approval prompt does not show the command | `mu/security/engine.py:406-432`, `mu/agent/secret_guard.py:43` |
| security | The prompt names a tool that does not exist (`refute_finding`) and the wrong PoC working directory; the active report lives on ad-hoc session attributes and is lost on restart | `utils/config.py:1068,1077`; `mu/tools/security/handlers.py:63-65,141-148` |
| debug | Every hypothesis, suspect and repro note is wiped at the next user message (scratchpad cleared outside loop/feature) | `mu/agent/loop_body.py:470-480` |
| loop | `raise_blocker` is `phase="feature"`, so loop mode can never raise the blocker its prompt and watchdog demand; the watchdog counter is never reset | `mu/tools/feature/handlers.py:1534`; `loop_body.py:1930` |
| feature | Three conflicting copies of the workflow (mode prompt says "don't create until approved", the system suffix and per-message directive say "create_feature first"); the directive is saved into history every turn | `config.py:956`, `loop_body.py:626-641`, `session.py:1243-1264`, `loop_body.py:499-520` |
| feature | Exit criteria are self-attested strings; the gates live in the tool handler, so the GUI, mobile and `/feature move` bypass them; `decide_task_diff` is model-callable and records `actor="user"` | `handlers.py:961-993,1342-1359`; `routers/feature.py:265-375`; `commands/feature.py:484-492` |
| default | "Verify with evidence" and the todo rules are prose; three copies of the default prompt exist and the doc has drifted | `config.py:905-926`, `mu/prompts/templates/default.md`, `documentation/default_mode.md` |

One finding affects every mode: on this machine all mode prompts load from
`~/.mucli/prompts/*.md` (user overrides take precedence over the bundled templates and
`AGENTIC_MODES`, `mu/prompts/__init__.py:200-213`). **A prompt rewrite does not reach a user
who has an older override file.** §3.7 handles that.

### Goals

- Each mode's state is a **typed, session-owned record**, persisted with the session, never
  inferred from scratchpad tags or free text.
- **Gates live in engines**, not tool handlers, so every surface (agent, GUI, mobile, slash
  command) goes through the same rules; user overrides are explicit events with an actor.
- **Claims of progress carry evidence**: an engine-run check (command, exit code, output
  excerpt, full output in the result store), not a string the model typed.
- **Decisions that belong to the user are not model-callable** (plan approval, curriculum
  consent, diff decisions).
- **One prompt per mode**, short, describing the loop; volatile mode state rides in the
  request-only TURN CONTEXT, never saved to history.
- Every mode gets **handler-level lifecycle tests**; today most modes test only their engine.

### Non-goals

- Merging feature mode into Jobs, or loop mode into Jobs. They share building blocks (§3.3)
  but stay interactive modes. A "hand this to Jobs" bridge is an open decision (§7).
- New providers, models or UI destinations. Panels stay inside the mode-panel slot.
- Migrations of old mode state beyond a straight load (AGENTS.md): unknown fields are
  ignored, missing ones default; old feature/teacher/security files keep loading.

## 2. What exists (reuse)

| Need | Existing piece |
|---|---|
| Session-owned state, persisted, bound per tool call | `ResearchState` on `SessionManager.research`, dispatch binding (`mu/tools/_dispatcher.py`), `load_research_state` |
| Mode-only tools | `phase="<mode>"` + `AGENT_MODE_METADATA.tool_phases` → `filter_tools_for_mode` (`mu/tools/descriptors.py:137-230`) |
| Approvals YOLO cannot skip | descriptor `approval_policy="always_human"` (`descriptors.py:47`) |
| Engine-run proof with markers | `mu/security/engine.py` `verify_proof` / `verify_remediation` |
| Checks with evidence | `mu/jobs/checks.py` (validation commands, junit parsing, baseline triage), `mu/jobs/evidence.py`, `mu/jobs/receipt.py` |
| Budgets / limits | `mu/jobs/limits.py`, `JobSpec` caps |
| Full output kept out of context | durable result store + `result_*` tools (`mu/session/result_store.py`) |
| Request-only per-turn context | TURN CONTEXT / RUNTIME STATE (`prompt_prefix_stable`, AGENTS.md) |
| Structured checkpoint fields | L2 checkpoint (`progress/decisions/resume/exceptions/next_action/constraints`) on `compact` / `clear_tool_results` |
| Status words and signs | `mu/design/vocabulary.json` + `scripts/design/build_tokens.py` |
| Rows, stepper, sheet | Wash `.w-row`, `.w-stepper`, `WashSheet` (`mu/gui/static/css/wash/components/mu.css`, `wash_shell.js`) |
| Research ledger (read before cite) | `mu/research/` — teacher sources and security references can use it |

## 3. Shared foundation (do first)

Every mode plan below depends on these. They are small individually; together they remove
most of the duplicated half-systems.

### 3.1 Mode state registry (`mu/modes/state.py`)

Generalise what research did:

- `SessionManager.mode_state: dict[str, ModeState]` — one typed state object per mode that
  needs one (`debug`, `loop`, `security`, `feature` pointer, `teacher` pointer), each with
  `to_dict` / `load_dict` and its own lock. Persisted under one session key
  `mode_state: {name: dict}`; `research_sources` stays as is.
- Dispatch binds the session's states for the call (as it binds `ResearchState`), so engines
  never reach for ad-hoc session attributes (`session._active_security_report`) or process
  globals.
- Engines that keep large state on disk (feature plans, courses, security scans) store only a
  **pointer + revision** in session state and load the file through one cached loader
  (mtime-keyed). This replaces the summary-stub copies (`feature_registry`,
  `teacher_registry`) that are rebuilt in two places today and drift.

### 3.2 Evidence: `CheckRun` (`mu/modes/evidence.py`)

One record for "the harness ran something and this is what happened":

```python
@dataclass
class CheckRun:
    id: str                 # "r12"
    purpose: str            # repro | verify | regression | exit_criterion | poc | remediation | grader | suite
    command: str
    cwd: str
    exit_code: int | None
    passed: bool            # per its predicate (exit code / markers present / markers absent)
    predicate: dict         # {"exit": 0} | {"markers": [...], "absent": bool} | {"junit": path}
    excerpt: str            # ≤ 2 KB of the relevant output
    result_ref: str | None  # full output in the result store
    started_at / duration_s
    hashes: dict            # sha256 of files the command depends on (PoC scripts, verifier)
    runner: str             # host | container
```

- `run_check(spec, session)` is the single runner. It goes through the same controls as
  `bash`: the secret guard, the approval preview (**the command is shown**), plan mode
  (refused), output scrubbing, timeouts, no login shell (`bash -c`, scrubbed environment).
- `runner="container"` when a container is available (container sessions, or the jobs sandbox
  image for security PoCs, §4.5); `host` otherwise.
- Junit/pytest output is parsed with `mu/jobs/checks.py` helpers when present, so "which tests
  failed" is data.
- Debug repros, feature exit criteria, loop increments, default-mode verification, security
  PoCs/remediations and teacher verifiers all produce `CheckRun`s. Panels render them with one
  shared row + sheet component.

### 3.3 Work items (`mu/modes/work.py`)

Replace todos-in-the-scratchpad (`mu/tools/task/todo.py` stores them as tagged scratchpad
entries; evictable at 64 entries; "one in_progress" not enforced):

- `WorkItem{id, title, status (pending|in_progress|blocked|done|dropped), parent_id, priority,
  evidence: [CheckRun.id], note, created_at, updated_at}` in session state, never evicted.
- The engine enforces **one `in_progress`** (starting one pauses the previous) and `done`
  takes an optional evidence ref (required in loop mode, §4.4).
- `todo_write` / `todo_set_status` / `todo_list` keep their names and arguments (prompts and
  small-window tool lists reference them) and become thin wrappers over the store.
- Default, debug and loop use it directly; feature tasks remain their own richer model but
  share the status vocabulary.

### 3.4 Session blockers (`mu/modes/blockers.py`)

- `raise_blocker` becomes a **core** tool (today `phase="feature"`, hidden in loop, default,
  debug). It writes a `Blocker{id, mode, ref (task/case/finding id), summary, needs, raised_at,
  resolved_at, resolution}` to session state.
- Raising a blocker ends the turn cleanly (the loop watchdog and feature flow already expect
  this) and shows in the attention badge and the mode panel. The next user message, a GUI
  "Resolve" action, or `/blocker resolve` resolves it.
- Feature mode's implicit clearing of `feature_state.blocker` on the next tool call
  (`session.py:450-512`) and `raise_teacher_blocker` (which records nothing,
  `teacher/handlers.py:1559-1568`) go away.

### 3.5 Gates in engines, actors on events

- Every state transition takes an `actor` (`agent`, `user:<surface>`) and writes an event.
  GUI, mobile and slash-command routes call the engine, never the raw transition helper.
- A user **override** (e.g. completing a task without evidence) is allowed only from a user
  surface and is recorded as an `override` event with a reason; the panel marks it.
- Decisions that belong to the user use `approval_policy="always_human"` or are not exposed as
  tools at all: feature plan approval, `decide_task_diff`, teacher curriculum consent.

### 3.6 Turn context, not history

- Remove the per-message directives that are prepended to the user's text and **saved to
  history** (`Session._build_feature_mode_prompt`, `_build_loop_mode_prompt`;
  `loop_body.py:499-502`). They also pollute the durable-memory recall query.
- Each mode contributes a short **mode capsule** to the request-only TURN CONTEXT (current
  task + remaining criteria; open debug case + status; loop mission, budget left and last
  increment; due teacher reviews and learner profile). Never persisted, byte-stable system
  prompt preserved.
- The mode-specific system-prompt suffixes in `loop_body.py:626-656` fold into the single mode
  prompt.

### 3.7 One prompt per mode, and stale overrides

- The bundled `mu/prompts/templates/<mode>.md` is the single source; `AGENTIC_MODES` in
  `utils/config.py` becomes a generated fallback (or is deleted in favour of the templates).
- Templates carry a version header (they already do: `v2`, `v3`). On load, a user override
  whose version is **older than the bundled template** produces a one-time notice in the TUI
  and GUI ("Your debug prompt override is based on v1; the bundled prompt is v3 — keep, diff,
  or reset"), plus `/prompt reset <mode>`. Overrides keep working; nothing is silently
  replaced.
- `mu/prompts/__init__.py` `_CRITICAL` anchors are updated with each rewrite.
- Target sizes: default ≤ 1.5k chars, debug ≤ 2k, feature ≤ 2.5k, loop ≤ 2k, security ≤ 2.5k,
  teacher ≤ 5k (from 2.3k / 3.2k / 9.6k incl. suffix+directive / 4.8k / 4.4k / 15.6k).

### 3.8 Scratchpad semantics

With mode state in typed records, the scratchpad goes back to being a per-turn notepad. The
turn-start wipe stays, but nothing a panel depends on lives there any more. The debug router's
tag parsing (`mu/gui/routers/debug.py:62-90`) is deleted.

## 4. Modes

Each section: problem (verified), design, outputs and surfaces, prompt, phases with tests.

### 4.1 Default mode

**Problem.** No mode state at all. "Verify with evidence" and "final summary: what was
verified" are prose; nothing links edits to a later passing check. Todo rules (plan when
3+ steps, one in_progress) are unenforced. Three copies of the prompt; `default_mode.md` says
"re-read the modified file" and "flush the collation queue", both contradicting the prompt.

**Design — the turn receipt.** Default mode stays light: no new tools beyond the shared ones,
but the harness records what happened.

- `bash` gains an optional `purpose` argument (`verify` | `repro` | `explore`). The harness
  also recognises common check commands (`pytest`, `npm test`, `cargo test`, `go test`,
  `ruff`, `mypy`, `tsc`, project `validation_commands` from `.mucli/` or jobs config) and
  records them as `CheckRun(purpose="verify")`.
- Each turn builds a **turn receipt** from the action records: files changed (with
  `modified_files`), checks run after the last edit (pass/fail), work items touched, open
  blockers.
- **Turn-end check:** if files changed and no passing check ran after the last edit, the
  harness gives the model one more iteration with a short nudge ("You changed X, Y without a
  passing check after the edit: run one, or say why it can't be checked"). Configurable
  (`verify_nudge`: on/off), never a hard block; the receipt marks the turn **unverified** if
  the model declines.
- The receipt renders under the assistant message in the GUI as a quiet row ("2 files · tests
  passed" / "2 files · unverified"), opens a `WashSheet` with the diff stats and check runs;
  the TUI prints one line; mobile shows the same row.
- Work items (§3.3) replace scratchpad todos; the default chat gains a small "Plan" disclosure
  above the composer when work items exist (today todos are only visible in the loop panel).

**Prompt.** Clarify-with-picker, recall, orient, act, verify, summarise — the receipt and the
nudge make "verify" checkable, so the prose shrinks to the loop. Fix `default_mode.md`.

**Phases.**
- D1 Turn receipt + check recognition + nudge. Tests (`tests/agent/test_turn_receipt.py`): an
  edit followed by a passing pytest run → verified receipt; an edit with no check → one
  nudge iteration then an unverified receipt; a check before the edit doesn't count.
- D2 GUI/TUI/mobile receipt row; Plan disclosure. Tests: `tests/gui` receipt payload; node test
  for the row model.

### 4.2 Debug mode

**Problem.** Debug is a prompt plus a panel that parses scratchpad tags:
- the state is wiped every turn (§1);
- a hypothesis status change creates a duplicate entry (`ScratchpadStore.save` merges only
  identical content+tags, `mu/memory/stores.py:219-243`);
- `debug_target` is read by the router but written by nothing;
- the router reports `active: true` in every mode and files todo entries under "Notes";
- "reproduce first", "re-run the repro after the fix", "run the wider suite", "10× for flakes"
  are prose;
- no regression-test notion;
- root causes are free-text memories found by a tag filter over the last 20 entries.

Under plan mode, `bash` is blocked, so the workflow cannot reproduce at all and nothing says so.

**Design — the debug case** (`mu/debug/`, `tool_phases: ["debug"]`).

```python
DebugCase{
  id, title, symptom,                       # "pytest tests/x.py::test_y fails with KeyError 'id'"
  status: open → reproduced → root_caused → fixed → verified → closed | unreproducible,
  repro: {command, predicate, runs: [CheckRun.id]},     # must FAIL before the fix
  hypotheses: [{id, statement, status: untested|supported|refuted|confirmed,
                evidence: [{kind: run|location|search|note, ref, stance}]}],
  suspects: [{path, line, symbol, reason}],
  fix: {files, summary, diff_ref},
  verification: {repro_after: CheckRun.id, suite: CheckRun.id, repeats: {n, passed}},
  regression_test: {path, name, failed_before: CheckRun.id, passes_after: CheckRun.id} | None,
  root_cause: {signature, cause, fix, files}            # on close
}
```

Tools:

| Tool | Behaviour |
|---|---|
| `open_debug_case(title, symptom)` | Creates and selects the case (replaces `debug_target`). |
| `record_repro(command, expect)` | Engine runs it (`run_check`, purpose `repro`). `expect` is a failure predicate: a marker in the output, or a non-zero exit. Accepted only if it **fails as described**; status → `reproduced`. Re-runnable to narrow. |
| `add_hypothesis(statement)` / `update_hypothesis(id, status, evidence)` | Typed lifecycle. Evidence links a `CheckRun`, a file location, or a search hit. `confirmed` requires at least one supporting `run` or `location`. |
| `add_suspect(path, line?, symbol?, reason)` | Validates the path exists in the workspace. |
| `verify_fix(suite_command?, repeat?)` | Re-runs the recorded repro (must now pass), then the wider suite (must pass), then optionally the repro `repeat` times for flake suspects. Status → `verified` only if all pass. |
| `record_regression_test(path, name)` | Engine checks the test fails on the pre-fix tree (`git stash`-free: runs against `git worktree` of HEAD, or accepts the earliest recorded failing run) and passes now. Optional but surfaced as a gap when missing. |
| `close_debug_case(root_cause, fix_summary)` | Requires `verified` or an explicit `unreproducible` reason. Writes the structured root cause and promotes it to the Memory Ledger with tags `debug/root-cause` plus file paths (replacing the free-text convention). |

Gates and plan mode:
- **Edit gate:** while a case is open and not yet `reproduced`, the first write-side edit in
  the turn returns a warning envelope ("No reproducer recorded for case d2 — record one, or
  mark the case unreproducible"). It becomes a block when `debug_require_repro=true`.
- `record_repro` / `verify_fix` / `record_regression_test` run commands, so they are
  approval-gated and **blocked in plan mode**. In plan mode the case, hypotheses and suspects
  still work, and the mode capsule says runs are unavailable until plan mode is off.

Optional phase: `bisect_start(good, bad)` / `bisect_step` driving `git bisect run` with the
recorded repro as the predicate, recording each step.

**Surfaces.**
- The panel shows the case header with a `w-stepper` of the status (open → reproduced →
  root-caused → fixed → verified).
- Hypotheses as rows with status signs; a hypothesis opens a sheet with its evidence.
- Suspects as `path:line` links; repro and verification runs as `CheckRun` rows.
- Past root causes come from a Memory Ledger search on the current files, not a tag window.
- The router reads `DebugState` and reports `active` only in debug mode.
- `/debug [status|cases|close]`; Android stays read-only and gets the new shape.
- Vocabulary gains `debug_case`, `hypothesis`.

**Prompt.** Reproduce → locate → hypothesise → fix → verify → close, naming the tools; the
700-character GUI tag contract disappears.

**Phases.**
- G1 Engine + state + tools + persistence. Tests (`tests/modes/test_debug_case.py`): a repro
  that passes is refused; status ladder; `confirmed` without evidence refused; `verify_fix`
  fails when the suite fails; close requires verified/unreproducible; state survives a session
  reload (the bug today).
- G2 Edit gate + plan-mode behaviour. Tests: warning envelope before repro; block with the
  flag; runs refused in plan mode.
- G3 Panel/router/command/mobile; root-cause memory. Tests: `tests/gui/test_debug_state_api.py`,
  terminal `/debug`, node test for the panel model.
- G4 (optional) bisect helper.

### 4.3 Feature mode

**Problem.**
- **Prompt copies conflict.** Three copies of the workflow disagree about when to create the
  feature (§1). The per-message directive (~3.6k chars) is stored in history every turn and
  skews memory recall.
- **Approval is a boolean** (`plan.approved`): no actor, time or plan hash. The model can
  approve its own plan under YOLO. `create_feature_task` (legacy) has no approval gate. There
  is no `/feature approve`, although errors point to it.
- **Exit criteria are self-attested strings.** The completion gate lives in the tool handler
  only; the GUI transition route, the criterion toggle, mobile and `/feature move` (which
  auto-fills every criterion) all bypass it.
- **Task state isn't constrained.** Any number of `in_progress` tasks; pending → completed is
  allowed. `get_current_task` and `get_execution_state` disagree on what is current.
- **Review is a rubber stamp.** `review_all_completed_tasks` creates empty "Review completed"
  records. `approve_feature_task` accepts any `review_status` with no precondition.
  `decide_task_diff` is model-callable and records `actor="user"`. The review-phase diff gate
  covers `apply_diff` only.
- **Two unconnected blocker systems**, and `raise_blocker`'s is silently cleared by the next
  tool call.
- **Small defects:** L3 never shows `next_task` (`session.py:671-695` reads a key that is never
  set). `build_phase_execution_prompt` / `build_review_prompt` are dead. The docs describe
  markdown phase files that don't exist and list teacher tools as feature tools. No handler
  tests.

**Design.**

*Plan lifecycle: draft → approved → executing → review → done.*

- `propose_feature_plan(goal, phases[{title, tasks[{title, objective, actions,
  exit_criteria[{text, check?}]}]}])` persists a **draft** in one call. It replaces
  `create_feature`, `create_phases`, `create_task` and `create_feature_task`.
  `revise_feature_plan(patch)` edits the draft.
- The panel, mobile and `/feature approve` show the draft. **Approval is user-only:** no
  approval tool is exposed to the model. Approval records `{actor, at, plan_hash}`. Editing an
  approved plan creates a new revision that needs re-approval (only the changed tasks are
  reset).
- Until approval, write-side tools in feature mode get a warning envelope; a block when
  `feature_require_approval=true`.

*Execution.*

- **One active task cursor** owned by the engine: `start_task(id)` (one `in_progress`) →
  `complete_task(id)`.
- Each exit criterion is met by **evidence**: either the criterion's declared `check` (a
  command plus predicate, run by `run_check`), or a linked `CheckRun` / file reference with a
  short justification for criteria that aren't commands ("the panel shows X" → a screenshot
  artifact or a reviewer note).
- `complete_task` refuses until every criterion has evidence.
- A user can mark a criterion met from the GUI or mobile; that is recorded as `user`
  attestation and shown differently.
- The engine records the git tree at task start so the task's diff is known (`git diff
  <start>..`), for review.

*Review.*

- `review_task(id, summary, issues[])` requires the task's diff summary (computed by the
  engine) and at least one passing check after the last edit. Issues become follow-up tasks
  or blockers.
- Feature completion requires:
  - every task completed and reviewed;
  - no open blocking issues;
  - a passing full-suite `CheckRun` after the last change.
- That final step is user-confirmed (panel / `/feature complete`). The model can only request
  it.
- Diff proposals: `decide_task_diff` leaves the tool surface; the user decides in the panel.
  The review-phase gate extends to every write-side tool (`write_file`,
  `search_and_replace_file`, `bash`) or is dropped in favour of review records (open decision
  §7.3).

*Blockers* use §3.4 with `ref=task_id`; `block_task` / `resume_task` become blocker
raise/resolve on a task.

*Context.* The mode capsule carries feature, current task, remaining criteria and open
blockers. The per-message directive and the system suffix are deleted.

*Jobs.* Exit-criterion checks reuse `mu/jobs/checks.py` (junit parsing, baseline triage).
Handing an approved plan to Jobs is open decision §7.4.

**Surfaces.**
- The panel keeps its kanban and adds:
  - a draft/approval banner (one primary action: Approve);
  - criterion rows with evidence signs, each opening its evidence sheet;
  - review records;
  - blockers as attention rows;
  - override events marked as such.
- `/feature approve|start|complete|review` added; `/feature move` routes through the engine.
- Mobile gets the same routes (it already has approve/transition).

**Prompt.** Propose a draft, wait for approval, then for each task: start → implement →
evidence for each criterion → complete → review. About 2.5k chars, one copy.

**Phases.**
- F1 Engine-level gates with actor/override events; one cursor; transitions fixed; GUI,
  mobile and command routed through the engine. Tests (`tests/modes/test_feature_gates.py`): a
  GUI transition can't complete a task without evidence; an override is recorded with
  `user`; two in_progress refused.
- F2 Draft/approve lifecycle; user-only approval; plan hash; revision re-approval. Tests:
  model cannot approve (no tool / `always_human`); edits after approval need re-approval.
- F3 Evidence-backed criteria via `run_check`; task start tree; review records; feature
  completion preconditions. Tests: a failing check blocks completion; empty review refused;
  completion needs a passing suite run.
- F4 Prompt consolidation (delete directive + suffix), mode capsule, L3 fix, dead code
  removal, docs rewrite. Tests: history contains the user's text only; capsule present in the
  projected request and absent from `session.history`.
- F5 Panel/mobile/command updates; tool-handler lifecycle tests.

### 4.4 Loop mode

**Problem.**
- `raise_blocker` is unavailable (§1).
- The 4-line "timeline update" is prose and recorded nowhere. The panel's "Checkpoints" are
  the 5 latest memories of any kind.
- The backlog is evictable scratchpad todos with no enforced single `in_progress` and no
  evidence.
- `loop_active` is **cosmetic**: nothing in the loop body reads it, and Pause/Resume changes
  nothing.
- The watchdog's nudge counter is never reset, so after 8 nudges it is off for the life of the
  session object.
- `loop_goal` is not in `VARIABLE_SCHEMA`. The first user message silently becomes the locked
  goal, and the panel tells users to set it with `/goal` (which sets `session_goal`).
- Workstreams (`loop_features`) can never be populated (`start_loop` / `add_loop_feature` have
  no callers, and feature tools are hidden in loop mode).
- No mission budget or stop condition, and no continuation across turns.

**Design — the mission** (`mu/loop/`, `tool_phases: ["loop"]`).

```python
LoopMission{
  goal, goal_history: [{goal, actor, at}],
  done_when: [{text, check?}],          # stop conditions, evidence-backed like feature criteria
  status: running | paused | stopped | done | blocked,
  budget: {max_increments, max_cost_usd, max_runtime_s, max_turns}, spent: {...},
  increments: [{n, objective, work_item, actions, evidence: [CheckRun.id],
                result: passed|failed|partial, decision, next, at}],
  backlog: WorkItems (§3.3)
}
```

- **Start and goal.** `/loop start <goal>` (or the panel's Start) creates the mission, with
  optional `done_when` and budget; the user owns the goal. Without a mission, entering loop
  mode asks for one instead of silently locking the first message.
- **`loop_increment(objective, work_item, evidence, result, decision, next)`** records an
  increment. It replaces the 4-line prose and is what the panel's timeline renders.
  - Marking a work item `done` requires evidence (§3.3).
  - A failed result creates or links a remediation item.
- **`loop_status`** reports the mission, budget left, backlog and the last increments; the
  same content rides in the mode capsule.
- **Done.** `done_when` criteria are checked like feature exit criteria. When all pass, the
  mission moves to `done` and the loop stops; only the user can extend it.
- **Pause and stop are real.**
  - `status=paused` stops the watchdog continuation at the next iteration boundary and makes
    new turns ask before continuing.
  - `stopped` ends the mission.
  - Budgets move the mission to `paused` with a reason when exhausted (reuse
    `mu/jobs/limits.py`).
- **Watchdog.** Counters reset per turn and per mission. Its nudges name `loop_increment` and
  `raise_blocker` (now core). The unused `_watchdog_paused` / `_watchdog_escalated` flags go.
- **Workstreams.** Dropped, unless open decision §7.4 links loops to Jobs; `spawn_agent` calls
  made in loop mode are listed as delegations on the increment that made them.
- **Continuation across turns** (L4, optional): when a turn ends with the mission `running`,
  budget left and no blocker, the harness can start the next turn itself after a short delay.
  Off by default (`loop_autocontinue`); the panel shows a countdown with Stop.

**Surfaces.**
- The panel shows mission, budget meter, `done_when` rows with evidence signs, a timeline of
  increments (rows → sheet with evidence), the backlog and the active blocker.
- Pause/Resume/Stop actually act.
- Fix the empty-state hint; add `/loop start|pause|resume|stop|status|goal`.
- Mobile gets read plus pause/stop.

**Prompt.** Mission → pick the next work item → act → verify → `loop_increment` → repeat;
raise a blocker instead of stalling. About 2k chars, one copy; the directive and the system
suffix are removed.

**Phases.**
- L1 Core `raise_blocker` (§3.4) and watchdog counter reset — **a bug fix, can land first**.
  Tests (`tests/agent/test_loop_watchdog.py`): `raise_blocker` is available in loop mode and
  stops the watchdog; counters reset across turns.
- L2 Mission state, `/loop` command, `loop_increment`, work items with evidence, real
  pause/stop, budgets. Tests (`tests/modes/test_loop_mission.py`): increments persist across
  reload; a paused mission does not continue; an exhausted budget pauses; `done_when` all
  passing → done.
- L3 Panel timeline, mobile, prompt rewrite, mode capsule. Tests: `tests/gui` loop state shape;
  history has no directive.
- L4 (optional) auto-continuation with countdown.

### 4.5 Security mode

**Problem.**
- **Unsafe PoC runs.** PoCs run unsandboxed on the host with a login shell, bypass the secret
  guard, and the user approves `verify_security_proof(finding_id)` without seeing the command.
  A PoC script can change between proof and remediation runs (nothing is hashed).
- **Remediation checks are thin.** "Remediation verified" means only that the markers are
  absent: a patch that breaks the program also passes. Nothing checks the patch is applied to
  the working tree.
- **State isn't reliable.**
  - The active report lives on ad-hoc session attributes: after a restart the agent can't
    reopen it and orphans a new scan.
  - The GUI shows the newest `report.json` by mtime, which can be another session's.
  - An approved finding can be refuted.
  - Re-attaching a proof leaves the old remediation.
  - `report.status` never moves past "scanning".
- **The prompt is wrong in places:** it names `refute_finding` (the tool is
  `refute_security_finding`) and says PoCs run from the scan directory (they run from the
  workspace root).
- **Missing pieces:**
  - no scope or threat model;
  - no record of what was reviewed (coverage);
  - no dedup;
  - severity is a free choice with no vector;
  - no "observation" record for non-exploitable issues;
  - no report generator (`findings/` is created and never written).
- **Plan mode** blocks only the two verify tools, though the others write `report.json`.
- No `/security` command and no router tests.

**Design.**

*State and scope.*
- `SecurityState` in session mode state points at the active scan; `open_security_report(id)`
  and `list_security_reports` reopen scans, and the GUI resolves the scan by session.
- `set_security_scope(in_scope_paths, entry_points, trust_boundaries, assets, attacker)` comes
  first, and the report renders it.

*Coverage ledger* (borrowing research's candidates).
- `scan_attack_surface(patterns?)` runs the known-sink patterns (the prompt's `eval(`,
  `pickle.loads(`, `shell=True`, SQL concatenation, `innerHTML`, …) plus entry-point
  discovery, and registers **surface items** `{id, kind: entry|sink, path, line, snippet,
  status: unreviewed|reviewed_clean|finding|observation}`.
- `review_surface_item(id, verdict, note)` records review. Coverage ("38 of 52 sinks
  reviewed") becomes data, and findings link to surface items.

*Findings.*
- Required: `exploit_path`, `cwe`, and `affected` locations (validated `path:line`).
- A dedup key (`cwe` + sink location) merges duplicates.
- Severity comes from a **CVSS 3.1 vector** (computed score → level) with rationale, and is
  re-confirmed after the proof (the PoC's demonstrated impact is shown beside the vector).
- `add_observation` records code-quality or hardening notes that have no exploit; they never
  enter the findings list.

*Proof and remediation runs* (via `run_check`).
- **Container by default** where Docker is available: the jobs runtime image, network none,
  a copy-on-write copy of the workspace (or the container session itself), and resource and
  time limits. Host fallback only with an explicit per-scan opt-in.
- `bash -c` without the login profile, scrubbed environment, the secret guard applied.
- The approval preview shows the **command and the PoC script contents**.
- A proof bundle hashes the PoC files. `verify_remediation` refuses if they changed since the
  proof (re-proof required).
- `verify_remediation` requires:
  - the patch hunks are present in the working tree (`git diff` of the affected paths
    contains them);
  - the PoC still **executes** (exit code / a "probe ran" marker the PoC must print) and the
    exploit markers are absent;
  - the declared regression checks pass.
- Every run is kept (`proofs/<fid>/<run>/`), not overwritten.

*State machine.*
- Refute only before approval.
- A re-proof clears the remediation.
- `report.status` advances: scanning → verifying → complete.

*Report.* `security_report()` writes `REPORT.md` and `findings/<id>.md` in the scan
directory:
- scope;
- coverage;
- each approved finding with its exploit → fix narrative and links to proofs and patches;
- a refuted-hypotheses appendix;
- observations.

*Plan mode* blocks every tool that writes (create, attach, approve, refute, review, report);
read tools stay available.

**Surfaces.**
- The panel gains scope, a coverage meter, surface-item rows, CVSS shown on each finding, run
  history (`CheckRun` rows) and observations.
- Session-scoped resolution, and a router test.
- `/security [status|scope|coverage|report|open]`.
- Mobile keeps approve/refute through the engine.

**Prompt.** Scope → scan surface → review items → hypothesis → proof → patch → verify →
approve or refute → report. The correct tool names and run semantics; about 2.5k chars.

**Phases.**
- S1 **Safety and correctness first:**
  - prompt fixes (tool name, cwd);
  - `run_check` with no login shell, scrubbed env, secret guard, command shown in the
    approval;
  - PoC hashing;
  - session-owned active report + reopen;
  - GUI session scoping;
  - refute/re-proof/status fixes;
  - plan-mode coverage.

  Tests (`tests/modes/test_security_runs.py`, `tests/gui/test_security_state_api.py`): a PoC
  reading a secret path is refused; a changed PoC script forces re-proof; an approved finding
  can't be refuted; a reload reopens the scan; two sessions' scans don't leak.
- S2 Container runner for PoCs (Docker suite test: a PoC can't reach the network or write
  outside its copy).
- S3 Scope, surface scan, coverage ledger, dedup, CVSS, observations. Tests: duplicate merge;
  coverage counts; CVSS → level.
- S4 Remediation integrity (patch-in-tree, PoC still executes, regression checks) and the
  report generator. Tests: a breaking patch fails verification; the report lists refuted and
  observations.
- S5 Panel/command/mobile.

### 4.6 Teacher mode

**Problem.**
- **The machinery the prompt describes doesn't run.** The lecture watcher (an LLM classifier
  over every chat message) and the LEARNER PROFILE injection are dead (§1).
  `record_lecture_turn` / `conclude_lecture` are reachable only through the watcher. So no
  lecture turns are recorded, the comprehension threshold never fires, and the
  "bulldozing" / inline-multiple-choice feedback never appears.
- **The prompt claims gates the engine lacks.**
  - `propose_curriculum` needs no diagnostic and **wipes an in-progress course's lessons**.
  - `start_lesson` ignores course status and order.
  - `assign_exercise` skips the lecture and the required artifacts.
  - `finalize_course` is unconditional, and `complete_module` is optional.
- **Learner consent and judging are agent-controlled.**
  - `approve_curriculum` (learner consent) is agent-callable and logged as `actor="learner"`,
    and `/teach approve` doesn't exist.
  - Dialog concept coverage matches the agent's own questions.
  - Mastery and rubric scores are self-reported; rubric weights are unused.
- **Verifiers can't be trusted.** A verifier may always pass and runs in `os.getcwd()`.
- **Grading and scheduling are inconsistent.**
  - An "awaiting judgement" rubric grade is stored as a 0% failed grade.
  - Reviews are agent-scheduled lesson counts that are never surfaced, and scores don't feed
    back.
  - Module mastery averages all attempts (including pre-remediation fails) while the report
    card uses the best.
- **Persistence is fragile and duplicated.** Course loading raises on unknown keys. There are
  two transcript systems. The summary record is built in two places.
- **Size and coverage.** The largest prompt (15.6k chars) and 21 tools, with no handler, watcher,
  command or router tests.

**Design.**

*State plumbing (fixes the dead paths).*
- Session mode state holds `{active_course_id, course_path, revision}`. The engine loads
  `Course` from `course.json` through the cached loader (§3.1), with tolerant loading (unknown
  keys ignored).
- The learner profile and due reviews go into the **mode capsule** (TURN CONTEXT), not the
  system prompt.

*Lecture tracking — replace the watcher.* The two-extra-LLM-calls-per-exchange classifier is
replaced by explicit structure:
- `teach_check(question, kind: open|choice, options?)` records a comprehension check. Choice
  checks go through `ask_user_choice`, so the answer is captured exactly.
- For open checks, the learner's **next message is attached** as the response automatically.
- `assess_response(check_id, signal: on_track|partial|confused, note)` lets the tutor grade
  it, one line.
- Lesson comprehension = the recorded signals. `conclude_lecture` fires from the engine when
  the threshold is met, and `assign_exercise` refuses before that unless the diagnostic marks
  the concept known (recorded reason).
- Inline A)/B)/C) options are still detected in the final assistant text (regex, no LLM) and
  produce a one-line nudge.
- (The watcher stays available behind `teacher_watcher=true` for comparison; open decision
  §7.5.)

*Engine gates.*
- `propose_curriculum` requires a recorded diagnostic. On an in-progress course it creates a
  **revision** that keeps graded lessons, assignments and history.
- `approve_curriculum` is **user-only**: GUI button, `/teach approve`, mobile; no model tool.
- `start_lesson` requires an approved curriculum and respects prerequisites/order, unless the
  user overrides.
- `assign_exercise` requires the lecture transcript (or the recorded known-concept skip).
- `complete_module` is required before the next module's lessons start.
- `finalize_course` requires every lesson passed or a user-confirmed early exit (recorded).

*Learning objectives and mastery.*
- Lessons declare 1–4 objectives. Assignments, checks and reviews map to objectives.
- Mastery per objective = best of the recent attempts with decay. Module/course mastery and
  the report card use the same rule.
- An "awaiting judgement" grade is its own status, not a 0% fail.

*Verifier integrity* (from security).
- `assign_exercise` runs the verifier against the **starting artifact** and refuses if it
  already passes.
- It runs in the assignment's `work/` directory via `run_check` (container when available).
- Rubric grading records per-criterion scores and uses the weights. An optional separate
  judge (sub-agent on a cheaper model) is open decision §7.6.

*Spaced repetition.*
- An SM-2-style schedule with **time-based** due dates per objective, set by the engine after
  each grade or review.
- Due reviews appear in the mode capsule and at `start_lesson`.
- A review is a re-graded exercise, and its result updates the schedule and mastery.

*Learner model across courses.*
- The profile is saved to the Memory Ledger (personal scope) at `finalize_course` and on
  `update_learner_profile`, and pre-loaded at `create_course`.
- Re-running the diagnostic merges rather than wipes.

*Sources.* Lesson sources use the research ledger: read before citing; `teach_sources`
records the citation numbers on the lesson.

*Housekeeping.*
- One transcript (the authored `lessons/<id>/lecture.md`; the auto transcript becomes the
  recorded checks).
- One summary builder.
- `raise_teacher_blocker` → core blocker.
- `/teach approve` added.
- `execution_kind` set on the disk-writing tools.

**Surfaces.**
- The panel gains:
  - an objective mastery grid (rows, signs);
  - due reviews with dates;
  - the curriculum approval banner (user action);
  - lesson gate state ("needs transcript", "comprehension 2/3");
  - checks and responses in the lesson sheet.
- Mobile shows mastery and reviews read-only, plus Approve curriculum.

**Prompt.** Diagnostic → curriculum (approval is the learner's) → per lesson: brief → explain +
`teach_check` cadence → transcript/exercises → assignment → grade → advance/remediate → review.
The personalisation guidance stays, shortened. Target ≤ 5k chars, from 15.6k.

**Phases.**
- T1 **Fix the dead paths:**
  - cached course loader;
  - mode capsule with profile and due reviews;
  - tolerant loading;
  - `propose_curriculum` revision instead of wipe;
  - user-only curriculum approval;
  - `/teach approve`.

  Tests (`tests/modes/test_teacher_tools.py`): a full course lifecycle through the tool
  handlers; the profile appears in the projected request after `record_diagnostic`;
  re-proposing keeps graded lessons; the model cannot approve the curriculum.
- T2 `teach_check` / `assess_response` lecture tracking, engine-fired `conclude_lecture`,
  inline-MC nudge; watcher behind a flag. Tests: assign refused before comprehension;
  response auto-attached; known-concept skip recorded.
- T3 Engine gates (diagnostic, order, module, finalize) with user overrides. Tests: each gate
  refuses and each override is recorded.
- T4 Objectives, mastery rule, awaiting-judgement status, verifier pre-check, `run_check` in
  `work/`, rubric weights. Tests: a verifier passing on the starting artifact is refused;
  weighted rubric; consistent mastery numbers across report card and panel.
- T5 Spaced repetition by date, review → mastery, cross-course learner model, research-ledger
  sources.
- T6 Prompt rewrite, panel/mobile, docs.

## 5. GUI: one shell, a different instrument per mode

The engines above give every mode real records; the GUI has to expose them so each mode is
useful on its own terms, not six copies of one generic panel. This section applies the Mu
direction (`documentation/design/mu_style_guide.md`, revision 2) to the mode surfaces,
**including research**, and replaces the current generic Mode OS chrome where it conflicts with
the guide.

### 5.1 What the current mode surfaces get wrong (against the Mu guide)

| Mu rule | Today |
|---|---|
| No metric strips or stat tiles; counts go in the state sentence (§5.5, §6.1) | The shared header (`fragments/mode_workspace_header.html`) renders a metric strip in every mode, including research's new one |
| No chips for status or labels; status is a mark plus a word (§5.4, §12) | Research source-state/type filters, debug/loop/teacher tag chips, feature status chips |
| What needs you lives in **Now**, with its decisive action visible (§5.2) | Approvals and blockers are buried inside sections (feature approve, security approve, loop paused) or absent (curriculum consent) |
| Rows open a detail sheet; at most two lines (§5.1, §7.2) | Several panels expand rows inline into multi-line blocks (sources, findings, lessons) |
| Explanations live in empty states and `?`, not on every visit (§4.6, §13) | "How to read confidence, accuracy, and relevance" disclosure and objective text shown in the panel |
| Heavy things are pages / panes, not sheets (§7.2, §11b) | Feature kanban, security patch diffs, lesson transcripts and the research matrix are squeezed into the 520px aside |
| Every fact in one place (§6) | Counts appear in the metric strip, the tab counts and section heading counts at once |

### 5.2 The shared mode shell (built once, in the foundation step)

Every mode panel keeps the same skeleton so moving between modes feels like one tool, and fills
it with its own records.

- **Header (one bar):** the mode's name; a **state sentence** built from the mode's facts with
  zero parts dropped (e.g. research *4 questions · 1 contested · 7 sources read*; loop
  *Running · increment 12 · $3.40 of $10*); **text tabs** for the lenses (no counts); at most
  **one primary action** that is the mode's decisive verb (Approve plan, Pause, Approve
  curriculum, Generate report). The metric strip and the "how to read" disclosure go; their
  explanations move to the panel's `?` popover, which also holds the evidence vocabulary
  (measured / verified / derived / reported / unassessed, `mode_workspaces.md`).
- **Now:** appears only when something waits for the user — user-only decisions (plan approval,
  curriculum consent, diff decisions, overrides), blockers raised by the agent, approvals for
  commands the mode wants to run (security PoCs: a **decision block**, Mu §7.6, showing the
  command and script), exhausted budgets. Ordered by urgency, each row with its action visible;
  the same items raise the chat destination's rail badge.
- **Rows with marks** for every record (questions, hypotheses, tasks, increments, findings,
  lessons), marks and words from `vocabulary.json` (§5.4 of the guide: success is quiet, colour
  only for what needs eyes). Filters are text tabs or a quiet "Show ▾" menu, never chips.
- **Sheets** for one record's details (`WashSheet`): title, mark + state sentence, decisive
  actions, facts, then *Details ▸* for IDs, paths and raw data in mono.
- **`CheckRun` row and sheet** (§3.2) shared by every mode: *✓ Repro fails as expected · pytest
  tests/x.py::test_y · 2.1s*; the sheet shows the command (mono, copy), cwd, runner
  (host/container), exit code, the predicate, the excerpt and **Full output ▸** (loaded from the
  result store), plus hashes under Details.
- **Expanded view:** an *Expand* action opens the mode in the chat destination's main pane as
  `.w-panes` (list | main) over the conversation, for the heavy surfaces — feature board,
  security report and diffs, lesson reading view, research matrix and report. Esc or *Back to
  chat* returns; state is in the URL (`?mode-view=feature&open=t4`) so it survives reloads and
  can be shared. The aside stays the glance-and-act surface.
- **In the conversation:** mode events show as quiet rows inside the turn's tool-activity line
  (*▸ Recorded 2 claims · read 3 sources*), user-only decisions appear as decision blocks in the
  stream as well as in Now (approving in either place resolves both), and the default-mode turn
  receipt sits under the assistant message.
- **Composer foot:** the mode name gains its one-word state (*Debug · reproduced*, *Loop ·
  paused*, *Feature · t3 of 9*) — the only place the mode's state appears outside the panel
  (guide §6.1: mode lives in the composer foot).
- **Accent:** each agent mode keeps one low-chroma accent for orientation (tab underline, the
  mark of the current item), from tokens; status colours keep their meaning. At most three
  pigments visible (guide §13).
- **Empty state:** one line on what the mode is for and the first step, with the start action
  (Start a mission, Open a case, Ask a research question); nothing else on first run.

### 5.3 Per mode

Each mode gets its own record layout, lenses and actions on top of the shell. "Now" lists only
what the user acts on.

**Research** (revisit of what v2 shipped)
- *State sentence:* questions · contested · claims · sources read (candidates only in the
  Sources tab).
- *Tabs:* Questions · Claims · Sources · Citations.
- *Rows:* the question tree (as built); claims under the selected question; sources with their
  state mark and citation number — the source detail moves from inline expansion to a **sheet**
  (title, origin, credibility with rationale, authors/date, the claims citing it, *Open source ↗*,
  the excerpts quoted from it).
- *Filters:* source state and type become text tabs / a Show menu (no chips).
- *Expanded view:* the claims × origins matrix at full width and the report skeleton
  (`research_report`) as a reading view with *Copy markdown* and, in workspace sessions,
  *Export…* (runs `research_export` through approval).
- *Conversation:* `[^n]` footnotes in assistant text become links that open the source sheet,
  with a hover preview of the quoted excerpt.
- *Now:* usually empty; shows an agent blocker if one is raised.

**Default**
- No dedicated panel: default mode's surface is the **turn receipt** under each assistant
  message (*2 files changed · tests passed* / *2 files changed · unverified*), opening a sheet
  with the diff stats and `CheckRun`s, and a **Plan** disclosure above the composer listing the
  work items (current one marked running) when any exist.
- The workspace panel's Context/Files tabs remain the default aside.

**Debug**
- *State sentence:* the case title and status (*KeyError in sync · reproduced · 3 hypotheses*).
- *Header:* a `w-stepper` for open → reproduced → root-caused → fixed → verified.
- *Tabs:* Case · Hypotheses · Runs · Root causes.
- *Rows:* hypotheses with marks (untested ○, supported, refuted ✕, confirmed ✓) and a one-line
  reason; suspects as `path:line` links that open the file in the Files panel; runs as
  `CheckRun` rows (repro before, repro after, suite, repeats).
- *Sheets:* a hypothesis with its evidence (each run/location/search with its stance).
- *Now:* blocker; "Close case?" when verified (user can close or ask for a regression test).
- *Primary action:* none while investigating; *Close case* when verified.
- *Past root causes:* rows from the Memory Ledger for the files involved, each opening the
  structured record.

**Feature**
- *State sentence:* *Draft · 3 phases · 9 tasks* → *t3 of 9 · 1 blocked* → *Review · 2 to review*.
- *Tabs:* Plan · Tasks · Criteria · Reviews.
- *Now:* **Approve plan** (decision block summarising phases, tasks and criteria, with
  *Approve and start* / *Request changes*), diff proposals awaiting decision (each with the diff
  in the expanded view), blockers, *Mark feature complete?* when preconditions hold.
- *Rows:* tasks grouped by phase with marks; a task sheet shows objective, criteria with their
  evidence marks (✓ verified run, user-attested, missing), the task's diff summary, reviews and
  events; *Override…* is a danger text button that requires a reason and records the override.
- *Expanded view:* the board (phases as columns of rows, not cards), and the task diff.

**Loop**
- *State sentence:* *Running · increment 12 · $3.40 of $10 · 2h 10m left* (or *Paused — budget
  reached*).
- *Primary action:* Pause (running) / Resume (paused); *Stop mission* lives in the `⋯` menu with
  confirmation.
- *Header:* a budget meter (`.w-bar`) for the tightest budget.
- *Tabs:* Mission · Timeline · Backlog.
- *Rows:* `done_when` criteria with evidence marks; the timeline of increments (*✓ 12 Add retry
  to sync · tests passed · next: wire metrics*), each opening a sheet with actions, evidence runs
  and the decision; the backlog as work items with the current one running.
- *Now:* blocker, budget exhausted, *Mission done — extend or finish?*, and with
  auto-continuation on, a countdown row with *Continue now* / *Stop*.

**Security**
- *State sentence:* *Scanning · 38 of 52 sinks reviewed · 2 findings verified*.
- *Tabs:* Risk · Findings · Coverage · Report.
- *Now:* PoC and remediation runs awaiting approval as **decision blocks** (command, script
  contents, runner — container or host — and the effect), findings with both verifications
  awaiting approval (*Approve* / *Refute…*), a host-fallback opt-in request.
- *Rows:* findings by severity with the CVSS score as text (*8.1 high*), mark by lifecycle
  (hypothesis, proof verified, fix verified, approved, refuted); surface items in Coverage with
  review marks and a coverage meter; observations in their own group, quiet.
- *Sheets:* a finding with exploit path, CWE, CVSS vector (and what the proof demonstrated),
  proof and remediation runs (`CheckRun` rows), patch summary; the full diff opens in the
  expanded view.
- *Expanded view:* the generated report (`REPORT.md`) as a reading view, and diffs.
- *Primary action:* *Generate report* once findings are settled.

**Teacher**
- *State sentence:* *Module 2 of 4 · lesson 2.3 · 2 reviews due*.
- *Tabs:* Path · Lesson · Mastery · Reviews.
- *Now:* **Approve curriculum** (decision block listing modules and lessons; the learner's
  consent), due reviews (*Start review*), an assignment waiting for the learner (*Open
  exercise* — a learner action, so it belongs in Now), a grade awaiting judgement.
- *Rows:* the path as modules → lessons with marks and gate state in line 2 (*needs transcript*,
  *comprehension 2 of 3*, *passed 85%*); mastery as objective rows with a mark and percentage
  (a table only when there are 7+ objectives across columns); reviews with due dates.
- *Lesson tab / expanded view:* the lecture transcript as a reading view with its exercise
  files (open in the Files panel), the recorded checks and the learner's responses, the
  assignment and its grade with per-criterion rubric scores.
- *Learner profile:* facts in the Lesson sheet's *Details ▸* and the `?`; not a permanent block.
- *Conversation:* `teach_check` choice checks render through the picker (`ask_user_choice`).

### 5.4 Android and terminal

| Mode | Android | Terminal |
|---|---|---|
| All | Header with state sentence, Now rows with their actions (approve plan/curriculum, resolve blocker, pause/resume), records read-only; `CheckRun` sheet | `/<mode>` status prints the state sentence and Now; blockers and user-only decisions prompt inline |
| Research | Questions, claims, sources (as built), footnote links | `/research` (as built) |
| Default | Turn receipt row under messages | One-line receipt after each turn |
| Debug | Case stepper, hypotheses, runs | `/debug status|cases|close` |
| Feature | Plan approval, tasks with evidence marks | `/feature approve|start|complete|review` |
| Loop | Mission, timeline, Pause/Resume/Stop | `/loop start|pause|resume|stop|status` |
| Security | Findings, approve/refute through the engine; PoC run approval stays desktop-only (command review needs the full view) | `/security status|scope|coverage|report` |
| Teacher | Path, mastery, reviews, Approve curriculum | `/teach` (+ `approve`) |

### 5.5 Verifying the GUI

- Each mode's panel model is a pure module (like `research_ledger.js`) with a node test:
  state-sentence parts (zeros dropped), Now ordering, row marks from the vocabulary, sheet
  markup escaping.
- Router shape and session-isolation tests per mode (`tests/gui/test_<mode>_state_api.py`).
- Every GUI phase ends with Playwright screenshots on a spare port of the panel, a sheet and the
  expanded view in Paper and Dusk, with decoration on and off, checked against the guide's §13
  review checklist; contrast via `scripts/design/check_contrast.py`.
- New status words go through `vocabulary.json` + `build_tokens.py`; no colour literals
  (`tests/design`).

## 6. Order and dependencies

| Step | Contents | Why this order |
|---|---|---|
| 0 | **Bug fixes that need no new design:** core `raise_blocker` (L1), watchdog reset, security prompt tool name/cwd, debug scratchpad persistence as a stop-gap, feature L3 `next_task`, teacher cached loader so the profile reaches the model | Each is small and fixes behaviour users hit today |
| 1 | Foundation §3.1–§3.7 (mode state, `CheckRun` + `run_check`, work items, blockers, engine gates/actors, turn context, prompt versioning) and the shared mode shell §5.2 (header with state sentence, Now, `CheckRun` row/sheet, expanded view, composer-foot state) | Every mode plan builds on these |
| 1b | Research GUI revisit §5.3 (shell, source sheet, text-tab filters, expanded matrix/report, footnote links) | Proves the shell on the one mode whose engine is done |
| 2 | Security S1–S2 | Safety: host-run PoCs |
| 3 | Debug G1–G3 | Smallest mode, exercises `CheckRun`, the case model and the panel pattern end to end |
| 4 | Feature F1–F5 | Largest gate surface; benefits from the debug patterns |
| 5 | Loop L2–L3 | Reuses work items, evidence, budgets |
| 6 | Teacher T1–T6 | Largest rewrite; T1 can move earlier if teaching is in active use |
| 7 | Default D1–D2 | Turn receipt uses everything above; lightest mode, broadest reach |
| 8 | Optional: G4 bisect, L4 auto-continue, S3–S5 extras, research alignment (`research_fetch` approval, research reads as `CheckRun`-style receipts) | |

Each mode's GUI phase (G3, F5, L3, S5, T6, D2) implements its §5.3 layout on the shared shell.
Each step lands green on the full suite (`python3 -m pytest tests/ -q`) and updates the
harness doc (`documentation/testing/harness.md`) with its pillar files.

## 7. Security and invariants

- `run_check` is the only new way to execute commands. It applies the bash secret guard,
  shows the command in the approval prompt, is blocked in plan mode, scrubs output and uses no
  login shell. Container runs use the jobs runtime image with network off (security PoCs,
  teacher verifiers) or the session container.
- User-only decisions (feature plan approval, curriculum consent, diff decisions,
  user overrides) are never model tools; where a tool must exist it uses
  `approval_policy="always_human"`.
- Jobs keep their unattended model (AGENTS.md 2026-10-15). Nothing here changes Jobs stage
  approvals.
- Context budgets: mode capsules are request-only and capped (≤ 1.2k chars each); `CheckRun`
  excerpts ≤ 2 KB with full output in the result store; per-message directives leave history.
- Persistence: mode state saves with the session; large engine files (plans, courses, scans)
  stay on disk with a pointer and revision in the session.

## 8. Open decisions

1. **Edit gates in debug and feature: warn or block by default?** Proposed: warn by default,
   block behind `debug_require_repro` / `feature_require_approval`.
2. **Default-mode verify nudge:** on by default (proposed) or opt-in?
3. **Feature review-phase diff gate:** extend to every write tool, or drop it in favour of
   evidence-backed review records? Proposed: drop.
4. **Jobs bridge:** offer "run this approved feature plan / loop mission as a Job"? Proposed:
   not in this plan; revisit after F3/L2.
5. **Teacher watcher:** delete once `teach_check` lands (proposed), or keep behind a flag
   longer?
6. **Separate grader for teacher rubric judgements** (sub-agent / cheaper model): worth the
   cost?
7. **Security PoC runners — decided 2026-10-23:** allow host and container runs. Docker is the default on host workspaces; host execution requires an explicit operator choice per scan.
8. **Loop auto-continuation across turns:** build L4 at all?
9. **Prompt source of truth:** generate `AGENTIC_MODES` from the templates, or delete it and
   load templates only?
10. **Expanded mode view:** open in the chat's main pane over the conversation (proposed), or
    as a separate keep-alive destination per mode?
11. **User decisions in the conversation:** show plan/curriculum approvals and PoC run
    decisions as decision blocks in the chat stream as well as in Now (proposed), or Now only?

## 9. Status

Implemented 2026-10-23 for default, debug, feature, loop, teacher and the research GUI revisit;
security mode (§4.5 S1–S5) implemented subsequently on 2026-10-23 with both host and container execution.

Not built:

- L4 loop auto-continuation across turns: a turn never starts itself (decision 8).
- The separate teacher grader (open decision 6).

Scope as built:

- The expanded view covers the feature board, the research matrix and report, and the lesson
  reading view, plus the security report and diffs.
- The turn receipt and the Plan disclosure render in the chat.

Decisions taken (§8):

1. Edit gates warn by default (the first edit of a turn is refused, a repeat goes through);
   `debug_require_repro` / `feature_require_approval` turn them into blocks.
2. The verify nudge is on by default (`verify_nudge`).
3. The feature review-phase diff gate was dropped in favour of evidence-backed review records.
4. No Jobs bridge.
5. The LLM lecture watcher is kept behind `teacher_watcher` (default off); `teach_check` /
   `assess_response` replace it.
6. Not built (see above).
7. Both host and container PoCs. `/security runner host` is a session-owned per-scan opt-in; Docker remains the default, with no silent fallback. Existing container sessions use their session container.
8. Not built.
9. The templates in `mu/prompts/templates/` are the single prompt source; `AGENTIC_MODES` is
   generated from them.
10. The expanded view opens over the conversation in the chat's main pane.
11. Now only: no decision blocks in the chat stream.


### Security implementation (2026-10-23)

S1–S5 are implemented in `mu/security/engine.py`, `models.py`, `state.py`, `cvss.py`,
`mu/tools/security/handlers.py`, the security router/panel and `/security` command.

- Session-owned active scan, explicit reopen, session-scoped GUI reads (available while the
  agent waits for approval), scope, coverage ledger, dedup, observations and computed CVSS.
- Shared `run_check`, secret/script guard, scrubbed startup environment, immutable per-run
  bundles, changed-PoC refusal and approval-preview snapshots. Human PoC review also survives
  frontend YOLO toggles; mobile run approval is desktop-only.
- Both runners: disposable networkless Docker checks by default; explicit per-scan host use.
- Remediation gates require real patch hunks, an executing unchanged probe, no exploit markers,
  regression success and stable files. Approval rechecks integrity. Re-proof clears remediation.
- Report generator, browser Risk/Findings/Coverage/Report, sheets, expanded report/diff,
  terminal controls and mobile engine-gated decisions.
- Behaviour is covered by `test_security_mode.py`, `test_security_runs.py`,
  `test_security_state_api.py`, `test_security_sandbox_docker.py`, `test_security_command.py`,
  frontend models/approval tests and Android prompt/panel tests.

Implementation details: custom discovery patterns are literal substrings; scope is frozen once
review starts. PoC dependencies must be declared (direct interpreter scripts are also detected).
Existing container sessions retain their own container policy. Git patch checks use the diff
against HEAD and reverse-apply validation. Host execution is not a sandbox. See
`documentation/security_mode.md` for the operational contract.

Verification recorded 2026-10-23: the complete Python suite passed (1,704 tests, including
Docker); after the final security-only corrections, all 38 security acceptance tests passed,
including real Docker isolation and GUI/terminal coverage. Android typecheck and 36 relevant
mobile tests passed; all nine browser test files passed. Playwright checked the panel, finding
sheet and report in Paper/Dusk with full/reduced decoration, followed finding→run links, and
reported no JavaScript errors. Generated tokens are fresh and all contrast pairs pass.
