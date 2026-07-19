# Loop Mode

Loop Mode is designed for long-horizon autonomous tasks where the assistant should keep working until the user stops it.

## Core Behavior

1. **Goal lock**
   - The user provides a long-horizon goal.
   - That goal remains the mission anchor until explicitly changed by the user.
   - In loop/feature mode the `session_goal` is **sticky**: it is *not*
     cleared at end of turn, so it survives across turns in L3 until the
     user clears it (`/goal clear`) or sets a new one. (`default` mode
     clears the goal per turn unless `session_goal_sticky` is opted in via
     `/set`.)

2. **Self-managed backlog**
   - The assistant creates and updates its own task backlog.
   - One active task at a time, with queued follow-up tasks.

3. **Execution loop**
   - Repeats: **Plan → Execute → Verify → Re-plan**.
   - Uses small, verifiable increments and avoids oversized risky jumps.

4. **Evidence-first updates**
   - Every progress claim should include evidence (tests, metrics, runtime checks, diffs, or concrete observations).
   - Failed verification should generate remediation tasks.

5. **Memory + scratchpad discipline**
   - Durable facts/decisions go to memory (`save_memory`).
   - Temporary thinking/checklists go to scratchpad (`save_scratchpad`).
   - In loop mode the scratchpad **persists across turns by default** (R12),
     so cross-turn plans and checklists survive until the run ends. This is
     mode-aware: `default`/`teacher` modes clear the scratchpad at turn start
     unless `scratchpad_persist_across_turns` is set.

6. **Timeline output**
   - Each increment should summarize:
     - objective attempted
     - actions taken
     - results/evidence
     - decision made
     - next immediate task

7. **Blockers**
   - If blocked by missing credentials, environment limits, or user decisions, raise a blocker with exact unblock requirements.

## Long-horizon context freshness

A long task that stays under the compaction token budget used to leave L2
(the conversation summary) frozen at its turn-start value while the model
racked up real progress in L5 — so it kept re-reading files it had already
explored and progress halts. Four fixes keep the model oriented on long runs:

- **Per-iteration L2/L3 rebuild** — the conversation summary (L2) and active
  goal (L3) are reassembled from in-memory state *every iteration*, not
  frozen at turn start. L1 (workspace files) and L1B (skills) are cached per
  turn (disk reads once) and reused, so the freshness is cheap.
- **Periodic L2 progress checkpoints** — every `progress_checkpoint_every`
  iterations (loop/feature default `12`, `default`/`chat` `0` = off), recent
  history is folded into the structured summary (Progress / Key decisions /
  Current state / Open items) **without compacting** — the anchor doesn't
  advance and entries stay verbatim in L5, only L2 is enriched.
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

## Stop / Resume

- The loop can be stopped at any time.
- Resume should continue from current memory, backlog, and last known loop goal.

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
