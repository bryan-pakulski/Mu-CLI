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
