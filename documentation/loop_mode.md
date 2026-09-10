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
