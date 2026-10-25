# Loop Mode

Loop mode works a long-horizon **mission** in small, verified increments until
the mission is done, paused, stopped or out of budget. The user owns the
mission; the agent works it. State lives in a typed, session-owned record
(`mu/loop/state.py`, engine `mu/loop/engine.py`) that is saved with the session
and survives reloads.

## The mission

A mission (`LoopMission`) has:

- **goal** and a `goal_history` of every change with its actor;
- **done_when** criteria (`c1`, `c2`, ...): stop conditions, each optionally
  with a check command (exit 0 passes by default);
- **budget**: any of `max_increments`, `max_cost_usd`, `max_runtime_s`,
  `max_turns`, and what has been `spent` against each;
- **status**: `running`, `paused`, `blocked`, `stopped` or `done`, with a reason;
- **increments**: the timeline (below).

**The user owns it.** Only a user surface (`/loop`, the GUI panel, mobile) can
start a mission, change its goal or budget, pause, resume, stop it, or attest a
criterion. Agent attempts are refused. Without a mission, loop mode does not
treat the first message as the goal: the agent asks the user to run
`/loop start <goal>` (or use the panel's Start), and nothing continues on its
own. One mission is live at a time; start a new one after stopping (or
finishing) the current one. The last ten finished missions are kept.

## Increments

Each unit of work is recorded with the `loop_increment` tool:
`objective`, `result` (`passed` | `failed` | `partial`), `evidence` (check run
ids `r<n>`), `work_item`, `actions`, `decision`, `next`, and any `delegations`
(sub-agents spawned). This is the timeline the panel renders; it replaces the
old prose "timeline update".

- A `passed` increment needs at least one passing check run as evidence.
- A `failed` increment opens (or links) a `Remediate: ...` work item.
- Every increment counts against `max_increments`.

Check runs come from `run_check` (see the modes evidence model): `bash` test
runs, todo or criterion checks. Each records the command, exit code, an output
excerpt and the full output in the result store.

## Backlog: work items with evidence

The backlog is the session's work items, driven by `todo_write`,
`todo_set_status` and `todo_list`. Items are never evicted, and only one is
`in_progress` at a time (starting one pauses the previous). In loop mode an
item can only be marked `done` with evidence: the id of a passing check run
(`todo_set_status(id, "done", evidence=["r3"])`). A user can complete one
without evidence from a user surface by giving an override reason; that is
recorded as an override event.

## Done when

`loop_check_done` checks the open criteria: it runs each criterion's declared
check, or links passing runs the agent cites as evidence. Running a check is
approval-gated and the approval preview lists the commands that will run. A
criterion without a check can be met by a linked passing run or by a user
attestation (`/loop attest <id> <why>`, or the panel), which is recorded as an
override. When every criterion is met the mission moves to `done`; only the
user can extend it (resume re-opens the criteria).

## Budgets

Increments are counted as they are recorded. Turns, cost and runtime are
charged when each loop-mode turn ends (`Session.send_message` calls
`account_turn`). When any budget is used up, the mission moves to `paused` with
the reason (`cost budget reached`, ...). Resuming is refused until the user
raises the budget (`/loop budget ...`, or the panel's Raise budget).

## Pause, resume, stop

These are real. The loop body checks the mission at every iteration boundary
of a loop-mode turn: when it leaves `running` (paused, stopped, done, or out of
budget) the turn ends at the next boundary. A paused mission tells the agent to
answer the user but not continue mission work until they resume it. A stopped
mission cannot resume; its record stays. Turns never start by themselves:
each turn begins with a user message (there is no auto-continuation across
turns).

## Blockers

`raise_blocker` is a core tool, available in every mode. In loop mode raising
one marks the mission `blocked`, ends the turn and stops the watchdog. The
user's next message (or the GUI's Resolve, or `/blocker resolve`) resolves it,
and when no blockers remain the mission returns to `running`.

## Watchdog

Within a turn, when the model stops without tool calls, the loop watchdog
nudges it to continue, but **only while the mission is `running`** and no
blocker was raised this turn. With no mission, or a paused, stopped, done or
over-budget one, the watchdog stays idle and the turn ends. Its counters reset
at the start of every turn: after 3 ineffective nudges it demands a plan recap,
after 5 it tells the model to call `raise_blocker`, and it stops at 8 nudges
per turn.

## Context

The mission state (goal, status, budget left, done-when, last increment,
backlog) rides in the request-only TURN CONTEXT as the mode capsule; it is never
saved to history, so `session.history` holds only the user's text.
`loop_status` returns the same content plus the last five increments. In
loop/feature mode `session_goal` is sticky across turns unless the user set
`session_goal_sticky` explicitly. The scratchpad is a per-turn notepad (cleared
at turn start unless `scratchpad_persist_across_turns` is set); durable
findings go to `save_memory`, plans to work items.

## `/loop`

```
/loop                                  status: goal, budget left, done-when, last increment, backlog
/loop start <goal> [--done "<text>" [--check "<cmd>"]]... [--max-increments N]
                   [--max-cost USD] [--max-hours H] [--max-minutes M] [--max-turns N]
/loop pause [reason] | resume | stop [reason]
/loop goal <text>
/loop budget --max-cost USD --max-hours H ...
/loop attest <criterion id> <why>
```

`/loop start` also switches the session to loop mode. `--check` attaches a
command (must exit 0) to the preceding `--done` criterion.

## Panel and mobile

The GUI loop panel uses the shared mode shell. The header's state sentence
reads like *Running · increment 12 · $3.40 of $10 · 2h 10m left*; the one
primary action is Start a mission, Pause or Resume; a meter shows the tightest
budget. Tabs:

- **Mission**: the goal, done-when rows with evidence or attestation, and
  *Stop mission…* (confirmed).
- **Timeline**: increments as rows; each opens a sheet with actions, evidence
  runs and the decision.
- **Backlog**: work items with the current one marked; *Add item*.

**Now** lists open blockers, *Budget reached* (Raise budget / Finish) and
*Mission done — extend or finish?*. The routes are `GET /api/loop/state` and
`POST /api/loop/start|control|goal|budget|backlog` and
`/api/loop/criteria/{id}/attest`; every write runs through the engine with the
surface as the actor. The Android Loop screen shows the mission, timeline and
backlog, with Pause/Resume and Stop.

## Long-horizon context freshness

The model chooses useful completion boundaries and relevant evidence.
`context_status` reports projected request size, available context, and a
bounded list of result identifiers. Large relevant context remains available:
the default `auto_compaction_token_limit=0` uses the provider-aware budget.
Automatic cleanup remains a fallback near that budget, clearing eligible old
results without summarizer calls before rolling history into L2. Four new
history entries re-arm cleanup after an attempt, so unchanged retries do not
repeatedly invoke the summarizer. Fixed layers, tools and output reserve also
consume the effective context window.

The active user request and recent tool results remain protected. This
soft cleanup advances the summary anchor without deleting or degrading
the durable transcript: older receipts can still be found with
`search_history` and cached payloads recalled by reference. If protected
content prevents reaching the soft target, it stays intact; the existing
provider-limit recovery remains the final backstop. The first provider
request after compaction includes the new summary and updated token
estimates, with no agent restart.

For repetitive work, maintain a short progress checkpoint containing the
goal, filters, resume cursor, completed counts, confirmed writes,
unresolved exceptions, and next action. Summaries should aggregate completed
batches instead of listing every processed item. This applies to ordinary
long turns as well as `loop` and `feature` modes.

New sessions enable this automatically. Existing sessions retain saved
settings, including the old `auto_compaction_enabled=false` default. Enable
it once in such a session with `/set auto_compaction_enabled true`.
Use `/set auto_compaction_token_limit 0` to remove a saved 64k working threshold;
`/set auto_compaction_enabled false` opts out of proactive cleanup.

### Selective tool-result clearing

The model can call `clear_tool_results` with result identifiers from
`context_status.working_context.candidates`. Only selected results change.
Candidates are paginated oldest first: pass `candidate_offset` from
`next_candidate_offset` to inspect another page (`candidate_limit`: 1–100).
`action="keep"` pins evidence and its call bundle against summarization;
`action="clear"` replaces eligible payloads with recall references;
`action="restore"` releases a previous retention decision. Neither clearing
nor restoring executes the original tool or calls a summarizer.

```json
{
  "result_ids": ["identifier-from-context-status"],
  "action": "clear",
  "checkpoint": {
    "progress": "20 batches confirmed complete",
    "resume": "exact cursor returned by the tool",
    "exceptions": "Two uncertain writes need verification",
    "constraints": "Never send replies",
    "next_action": "Read the next batch"
  }
}
```

The checkpoint is a bounded field update within L2, outside the rolling
summary. It accepts `progress`, `decisions`, `resume`, `exceptions`,
`next_action`, and `constraints`, up to 6,000 JSON characters in total. Invalid
or oversized checkpoints are rejected before edits. Original user requirements
and approval rules remain authoritative. Record confirmed outcomes separately
from attempts; never infer success just because a result was archived.

Clearing refuses recent result bundles, pinned/protected evidence, failures,
pending outcomes and provider-signed content. It verifies durable storage before
changing the projection. Complete signed bundles can still be archived during
compaction; their signatures are never edited individually. Raw history is
never rewritten by selective clearing, and retention/checkpoint
state survives session reload, and `recall` can recover cleared receipts from
saved history after per-run cache eviction. A missing/failed durable store is a
reported skip. Unselected results remain active.

When clearing is insufficient, `compact` accepts the same checkpoint plus
`preserve_result_ids`, `clear_result_ids`, and `through_index` (the last saved
history message eligible for summarization). It keeps later work and protected
call/result bundles intact. Summaries process bounded, newly archived segments;
cleared results contribute small action records, not their raw payloads again.
`context_status` and iteration traces expose clearing savings, latency and
rolling-history summary-call/input/output counters. Traces label the edit `last_context_edit`
with its iteration; summary counters are cumulative within the process.
Fixed-token status uses the last provider
request; the actual pre-provider guard remeasures the assembled request.

A long task that stays under the compaction token budget used to leave L2
(the conversation summary) frozen at its turn-start value while the model
racked up real progress in L5 — so it kept re-reading files it had already
explored and progress halts. Four fixes keep the model oriented on long runs:

- **Per-iteration L2/L3 rebuild** — the conversation summary (L2) and active
  goal (L3) are reassembled from in-memory state *every iteration*, not
  frozen at turn start. L1 (workspace files) and L1B (skills) are cached per
  turn (disk reads once) and reused, so the freshness is cheap.
- **Optional periodic L2 progress checkpoints** — every `progress_checkpoint_every`
  iterations (`0` = off in every mode; set a positive cadence to opt in), recent
  history is folded into the structured summary (Progress / Key decisions /
  Current state / Open items) **without compacting** — the anchor doesn't
  advance and entries stay verbatim in L5, only L2 is enriched. By default the
  model maintains its structured checkpoint without an extra summary request.
- **Auto-recall of cached reads** — a repeat `read_file` / `get_chunk` /
  `list_dir` / `search_*` on an *unchanged* file short-circuits to the
  tool-result sidecar cache instead of re-reading from disk and re-burning
  tokens. Freshness is validated by mtime+size. Loop/feature modes also grow
  the cache (≥256 entries / ≥2 MB) and raise the tool-result floor to ≥8 so
  more recent reads survive compaction.
- **Context-gathering stall detection** — consecutive iterations that
  re-cover already-read paths *without* a concrete change (write/bash/spawn)
  trip a "stop gathering, act" re-orient nudge after
  `recoverage_stall_threshold` (default `4`) iterations, with a cooldown so
  it doesn't spam. Catches the diffuse re-coverage stall that doesn't form a
  clean repeated/periodic tool sequence.

## Reaching the iteration cap

When a turn hits `max_iterations` mid-work, the agent no longer stops
silently. It runs **one final consolidation turn** (tools disabled): a user
message asks the model to state what it accomplished, what remains, and any
blocker, and the response is appended to history and persisted to task
memory (`max_iterations_consolidation`) so the next turn inherits the
handoff. The guard resets each turn.

## Safety guards (loop detection)

Long autonomous runs are bounded by loop-detection guards in
`mu/agent/loop_detection.py` + `mu/agent/loop_body.py`, active by
default across all modes (they bite hardest in loop mode):

- **Tight repeat detection** — the same tool-call sequence repeating
  back-to-back. Trips after `loop_detection_repeat_threshold` (default `5`)
  consecutive repeats.
- **Periodic repeat detection (R7)** — the same tool-name sequence
  repeating with up to `loop_detection_periodic_max_period` (default `6`,
  soft knob) other tool calls in between. Catches slower cycles that
  tight-repeat detection misses.
- **Retryable-failure escalation (R8)** — when a single tool hits the
  same retryable error `retryable_escalation_threshold` (default `3`, soft
  knob) times in one turn with *different* args (which evades pattern
  detection), an escalation message tells the model to change approach.
- **Context-gathering stall detection** — see
  [Long-horizon context freshness](#long-horizon-context-freshness) above.

All are configurable via `/set`; see
[configuration.md](configuration.md#loop-mode) for the defaults.
