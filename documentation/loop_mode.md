# Loop Mode

Loop Mode is designed for long-horizon autonomous tasks where the assistant should keep working until the user stops it.

## Core Behavior

1. **Goal lock**
   - The user provides a long-horizon goal.
   - That goal remains the mission anchor until explicitly changed by the user.

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

## Stop / Resume

- The loop can be stopped at any time.
- Resume should continue from current memory, backlog, and last known loop goal.

## Safety guards (loop detection)

Long autonomous runs are bounded by two loop-detection guards in
`mu/agent/loop_detection.py` + `mu/agent/loop_body.py`, both active by
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

All three are configurable via `/set`; see
[configuration.md](configuration.md#loop-mode) for the defaults.
