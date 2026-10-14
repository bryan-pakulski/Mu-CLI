# Plan: harness latency fixes (Anthropic, interactive and jobs mode)

Status: proposed (2026-10-14) · Owner: agent loop + jobs · Related: `job_build_environments.md`,
`durable_engineering_jobs.md`, AGENTS.md "Context packaging".

## 1. Problem

The same task takes much longer in mucli than in Claude Code on the same Anthropic models. A
read-only investigation of the code, 18 recent Anthropic traces (~1,460 turns) and job
SCRUM-195 (`202c1086…`) found that most of the gap is harness behaviour, not the model:

| # | Cause | Evidence | Cost |
|---|---|---|---|
| 1 | Conversation history is never read from the prompt cache | Cached input pinned at ~31–34k tokens (system + tools) while requests grow to 80–250k; 18% cache-read share; job wrote 12.5M cache-creation tokens in implement and never read them | ~1–3 s per turn (15–25% of model time) and most of the token cost |
| 2 | Extra model round trips: `flush` after batched reads, 256-token inline budget, prompt-mandated steps | `flush`-only turns: 31% of plan, 34% of review requests; 15% of host turns were `result_*`/`recall`/`get_chunk` fetch-backs | ~20 of 52 min of review model time; ~11% of host model time |
| 3 | Blocking end-of-turn summary call (`turn_roll`) | 29–64 calls, 15–32 s each; next turn starts with an empty cache | 15–32 s on most multi-step turns |
| 4 | `bash` always serial; mixed batches fall back to serial | `bash` is 69% of tool calls; 38% of tool turns host / 84% in job implement are single-call | Sum of tool latencies per batch |
| 5 | Jobs: lost work on time limits/resume, review prompt overflow, futile fix loops, an unexplained ~11 s pre-request stall in review, no shared context across stages | See §7 | ~60 of 84 machine minutes in SCRUM-195; most of the 14.4 h of human waits |
| 6 | No way to set effort for the main loop | `mu/agent/retry.py:339-344` passes no `reasoning_effort` and no `cache_hint` | Thinking was 43% of host output, 57–78% in job stages |

Harness overhead between turns (token counting, saves, tracing) is small (median 0.5 s) and is
not a target, apart from the review stall.

## 2. Goals

1. Every Anthropic request reads all of the previous request's prefix from cache, except after
   deliberate history rewrites (fold/roll/compaction), which stay rare and recorded.
2. A read-heavy step costs one model turn, not two or three.
3. No blocking model call happens between a finished turn and the next user turn.
4. Jobs never discard completed stage work because of a limit or a resume, never send a request
   larger than the model's window, and never retry fixes that cannot succeed.
5. Measured: median turn latency and cache-read share before/after on a fixed benchmark task
   (§9), plus job SCRUM-195-style replays.

### Invariants that must hold (AGENTS.md)
- L0–L5 contract and budgets; non-L5 layers consume the global cap before L5 is derived.
- With `prompt_prefix_stable` the system prompt stays byte-stable per turn and volatile blocks
  still ride in the trailing RUNTIME STATE message (never persisted).
- `session.history` is never rewritten by request projection; every history shrink records a
  ledger kind.
- Approval gates, plan-mode read-only, secret-path and result-scrubbing checks unchanged.

## 3. Phase 1: make conversation history cacheable (fixes #1)

**Root cause.** `append_runtime_state` (`mu/session/runtime_state.py:66-96`) puts the RUNTIME
STATE block last. `AnthropicProvider._build_payload` (`providers/anthropic.py:639-664`) sets the
top-level automatic `cache_control`, which marks the *last* block. Each request therefore
writes "history + this turn's runtime state" and the next request never repeats that prefix.
Only the explicit system-block breakpoint hits.

**Change.**
1. `providers/base.py` `CacheHint`: add `volatile_tail_parts: int = 0`: the number of trailing
   message parts (from the end of the last message) that change every request and must sit
   after the last breakpoint.
2. `mu/agent/retry.py` (`provider.stream(...)` at ~L339): build a `CacheHint` and pass it.
   Set `volatile_tail_parts=1` when the wire messages end with the runtime-state part
   (`runtime_state.is_runtime_state_message` or the trailing part check used by
   `strip_runtime_state`). Providers that ignore cache hints are unaffected.
3. `providers/anthropic.py`:
   - When `volatile_tail_parts > 0`, drop the top-level automatic `cache_control`. Instead put an
     explicit `{"type": "ephemeral"}` on the last content block *before* the volatile tail
     (normally the last `tool_result`, or the user's text part on the first iteration).
   - Keep the system-block breakpoint. That uses 2 of the 4 allowed breakpoints.
   - Add a third breakpoint on the second-to-last user turn so a small change at the tail
     (e.g. a retried request) still hits a long prefix. Anthropic reads the longest matching
     prefix up to ~20 blocks back from a breakpoint, so one moving breakpoint is enough when
     turns are short; the extra one covers big multi-tool turns.
   - Never mark a block inside the runtime-state message.
4. **Thinking replay (secondary suspect).** `_convert_messages` replays thinking only for
   `idx == last_tool_turn` (`anthropic.py:458-461, 536-541`). First measure with the Phase 1
   change alone. If cache reads still stop short of the previous turn, keep replaying the
   signed thinking blocks of all assistant turns in the current tool loop (the API ignores
   them for billing outside the latest turn) so earlier turns stay byte-identical.
5. **Secondary cache breakers**, measured after 1–4:
   - `prepare_runtime_history` (`mu/session/messages.py:583`) slides its start index once
     history exceeds the budget, shifting the whole prefix every turn. Change to *stepped*
     trimming: when over budget, drop down to a lower watermark (e.g. 75% of budget) in one go,
     then stay stable until the high watermark is reached again. Record a ledger entry per step.
   - `_fold_turn_context` (`loop_body.py:235`) rewrites retention at turn end; the first request
     of every user turn shows `cached = 0`. Accept one miss per user turn, but make sure the fold
     happens once at turn end (it does) and never mid-turn.

**Tests.**
- Unit, `tests/test_anthropic_cache_breakpoints.py`:
  - with a runtime-state tail, the payload has no top-level `cache_control`; the marker is on
    the last pre-tail block; the system block is still marked;
  - without a tail, behaviour is unchanged;
  - two consecutive iterations produce payloads whose prefix up to the marker is byte-identical
    (serialize and compare).
- Regression: `prompt_prefix_stable` tests and runtime-state tests still pass; the runtime-state
  block is still not persisted.

**Acceptance.** On the §9 benchmark, cache-read share above 85% after the first turn, and
`cached` grows turn over turn instead of staying flat. Expect cost per task to fall
substantially.

## 4. Phase 2: stop forcing extra round trips (fixes #2)

1. **Deliver batched read results inline.**
   - Set `collation_enabled` default to `False` (`utils/config.py:218`).
   - Keep the collation buffer for the cases it exists for: results above a size threshold
     still go to the result store with a reference. Collate only when the *combined* batch
     exceeds `collation_batch_budget_tokens` (new, default 24k), not whenever `len(tool_calls)
     > 1` (`loop_body.py:2297-2301`). Small batches are returned in the same turn.
   - The `flush` tool stays available but is hidden from the schema when collation is off and
     the buffer is empty (fewer tools, fewer tokens).
2. **Raise the inline result budget.**
   - `tool_result_inline_budget`: 256 → 4,000 tokens (`utils/config.py:388`).
   - Per-tool overrides: `read_file` 8,000 (one ~400-line file inline, comparable to Claude
     Code's Read); `bash` follows the 12,000-character cap.
   - The summary-plus-reference path stays for larger results, but never replaces a result
     with a summary larger than the original (seen: `bash_status` 384 → 884 tokens). Return the
     original when the "compressed" form is not smaller.
   - Check against the L4 recent-tool-activity budget in `mu/session/budgets.py`. Larger inline
     results consume L5 faster, but Phase 1 makes re-sending them cheap (cached), and
     `_fold_turn_context` still moves them to the durable store at turn end.
3. **Trim prompt-mandated steps** in the default system prompt (`utils/config.py:776-812`):
   - make `search_memory` and `retrieve_relevant_context` *available* rather than mandatory
     first steps;
   - drop "re-read the file after editing" (the edit tools already return the result);
   - keep `todo_write` for multi-step work only.
   This changes model behaviour, so put it behind a variable (`lean_prompt_rules`, default on)
   and compare with the §9 benchmark.
4. **Compact result envelope.** Serialize the tool-result envelope without indentation and omit
   empty fields (`mu/tools/_envelope.py`). Check every consumer of the envelope (UI, trace
   parser, tests) and keep field names.

**Tests.**
- Batched reads under the budget return full results in the same iteration (no "Stored …
  in collation buffer" text).
- An over-budget batch still collates.
- A `read_file` of a 300-line file is inline.
- The "never inflate" rule holds.
- `flush` is hidden from the schema when there is nothing to flush.
- Run the existing collation and observation tests with the new defaults; tests that pin the
  old defaults change only because the specified default changes.

**Acceptance.**
- `flush`-only turns are under 2% of turns.
- `result_*`/`recall`/`get_chunk` fetch-back turns fall from ~15% to under 5% on the benchmark.

## 5. Phase 3: no blocking work between turns (fixes #3)

`roll_completed_turn` (`mu/session/context_maintenance.py:271-330`) calls
`_generate_llm_summary` (`mu/session/history.py:216-276`) synchronously whenever a finished
turn exceeds `turn_keep_budget_tokens` (6,000).

1. Keep `turn_keep_budget_tokens` at 6,000 (implementation note: a 24,000 budget let unrolled turns push the
   7×40 replay's peak fill past 50%; once the roll is deferred it no longer costs turn latency).
2. Make the roll **deferred**:
   - At turn end, record a pending roll (`turn_roll_pending = {through_index, …}`) and return
     immediately.
   - Run the summary on a background thread with a snapshot of the messages it summarises.
   - Apply it at the *next turn's start*, before context assembly: advance the anchor, write
     `conversation_summary`, ledger kind `turn_roll`. Apply it only if the summarised range is
     unchanged; otherwise drop it and roll again later.
   - If the user's next message arrives before the summary is ready, the request goes out with
     the unrolled history. It fits, because pressure gates still apply.
3. Keep the mechanical fallback: if the summary call fails or the model is unavailable, roll
   with the existing non-LLM summary path (`roll_history_summary` without a provider).
4. `compactor._pressure_clear` and emergency/preflight compaction stay synchronous; they are
   needed to send a request at all.

**Tests.**
- Turn end returns without calling the provider; the pending roll is applied at the next turn
  start; ledger kind `turn_roll` is recorded.
- A stale pending roll (history changed under it) is discarded.
- The pressure paths are unchanged.
- Thread-safety: the background summary never touches `session.history` directly.

**Acceptance.** No model call between `turn_end` and the next `turn_start` in traces; first
request of a turn no longer pays a summary.

## 6. Phase 4: tool parallelism and effort (fixes #4, #6)

1. **Read-only `bash` in parallel.**
   - Add `is_parallel_safe_call(name, args)` in `mu/agent/parallel.py`. A `bash` call is
     parallel-safe when its command parses (shlex, no `;`, `&&`, `|` into writers, redirects,
     or subshells) to an allow-listed read-only executable: `ls cat head tail wc rg grep find
     git status/diff/log/show stat file tree du`.
   - Everything else stays serial.
   - Use it in the batch planner (`loop_body.py:2148-2166`). Group consecutive safe calls and
     run them concurrently; preserve result order; serial calls act as barriers instead of
     forcing the whole batch serial.
2. **Effort.**
   - Session variable `reasoning_effort` (`low|medium|high|max|""`, default `""` = API default).
   - Job setting `jobs.stage_effort.{plan,implement,review}` (default `""`).
   - Passed through `retry.py` to `provider.stream(..., reasoning_effort=...)`; the relay already
     forwards `reasoning_effort` (`model_relay.py`).
   - `/effort` command and a Settings field. No default change; this makes it tunable.

**Tests.**
- Classifier: read-only commands are safe; writes, pipes to `tee`, redirects and `rm` are not.
- Batch planner: order is preserved and barriers are respected.
- Effort reaches the payload's `output_config` (Anthropic) and the relay request.

## 7. Phase 5: jobs mode

1. **Never discard finished stage work.**
   - On the stage time limit, the worker writes its transcript before the container is
     removed (today it only writes on normal exit). On resume, `runner.py:169-172` stops
     appending a 24k-character JSON tail.
   - Instead, the stage session is **restored**: the worker loads
     `logs/<stage>-transcript.json` as the session history (projected through the normal L5
     path) and continues with the human guidance as a new user message. This keeps the cache
     prefix and all prior work.
   - The same mechanism serves approval resumes: after a write approval, the new container
     continues the same conversation.
2. **Cap the review prompt to the model's window.**
   - `build_stage_review_prompt` (`stages.py:186-206`) embeds up to 120k characters of patch.
     Instead embed the diff stat plus per-file patches up to `review_patch_budget_tokens` (new,
     default 30k), and list the remaining files for the agent to read.
   - Add a hard pre-send guard in the relay. If `estimate_tokens(payload) × drift` exceeds the
     model's context window (from `utils/model_pricing` / provider limits), compact first (the
     host-side preflight already exists for interactive sessions; reuse
     `context_guard`'s emergency path inside the stage) and never send a request that will be
     rejected.
   - This would have prevented the 1.41M-token rejection ($35, 11.9 h idle).
3. **Stop futile fix loops** (`verify_worker.py:195-203`). Schedule a fix attempt only if:
   - HEAD or the worktree changed since the previous checks run (compare `head_sha` and the
     dirty status with the last run's summary); and
   - at least one new failure is not environmental. Classify `ModuleNotFoundError`,
     `Cannot find module`, `ENOTFOUND`/DNS errors, `command not found` and missing
     `node_modules` as environmental and park with the missing-dependency message, naming the
     build environment.
4. **Find and remove the ~11 s pre-request stall in review.**
   - It shows between the last tool result / "Collated" event and "Generating" (median 11.3 s
     in review; 0.02 s in plan, 0.31 s in implement).
   - Step 1: add timing spans around context assembly in `loop_body.py` (L5 projection, token
     estimation, runtime-state build, memory recall, preflight compaction) and emit them in the
     trace.
   - Step 2: reproduce with a review-sized prompt (72k-token system + tools + 120k-character
     patch) in a unit benchmark.
   - Suspects scale with prompt size: repeated `estimate_tokens` over the whole history without
     memoization, `prepare_runtime_history` window search, or result-store observation of
     large results.
   - Fix by memoizing per-message token counts (cache keyed by message id + content hash).
5. **Fewer, faster checks containers.**
   - Run all suites that share a build environment in one container. `check_worker` accepts a
     list of suites and reports per suite; per-suite isolation of results is kept.
   - Run baseline triage only for suites whose failures are not environmental.
   - Run independent environments' containers concurrently (bounded by `jobs.max_concurrent`).
6. **Shared context between stages.**
   - Pass a compact *stage brief* from plan to implement and from implement to review: files
     read and why, key findings, commands that worked, from the stage's final summary. Today
     each stage re-explores the repo.
   - Store it as `artifacts/briefs/<stage>.md`, include it in the next stage's prompt under its
     own budget, and show it in the job's Session tab.
7. **Stage start/stop overhead.**
   - Drop the extra end-of-stage model call (~4k in / 1.6k out after the final answer) if it is
     the turn-roll summary. Phase 3 removes it; verify in the stage trace.
   - Keep containers as is; ~3 s start-up is acceptable.

**Tests.**
- Time limit then resume restores the transcript and continues with the cache prefix (unit,
  with a stub relay).
- The review prompt never exceeds its budget; the relay guard refuses or compacts an
  over-window request (no provider call).
- The fix loop is skipped when HEAD is unchanged or the failure is environmental.
- Token-count memoization returns identical counts.
- Multi-suite container returns per-suite results.

**Acceptance.** Replaying SCRUM-195 (same repo, same models) completes review in one run,
issues no rejected request, and schedules no fix attempt for missing dependencies.

## 8. Rollout and order

| Phase | Scope | Risk | Order |
|---|---|---|---|
| 1 | Cache breakpoints (+ stepped trimming) | Low. Payload-only change behind `CacheHint`; easy to verify | First: biggest win, smallest change |
| 2 | Inline batched reads, larger inline budget, lean prompt rules, compact envelope | Medium. Changes context growth and model behaviour; behind variables | Second, after Phase 1 makes larger inline results cheap |
| 3 | Deferred turn roll | Medium. Concurrency; ledger and anchor invariants | Third |
| 5.2, 5.3 | Review prompt cap, relay guard, fix-loop gating | Low | With Phase 1 (independent, high value) |
| 5.1, 5.4–5.7 | Stage restore, stall fix, checks batching, briefs | Medium | After Phase 3 |
| 4 | Parallel read-only bash, effort setting | Low–medium | Any time |

Each phase lands separately with the full suite green. New behaviour sits behind session or job
settings with the new defaults on, so a regression can be switched off without a revert.

## 9. Measurement

- **Benchmark task:** a fixed read–edit–test task on a pinned commit of this repo (e.g. "add a
  field to a job VM and a test"), run 3× per configuration on `claude-opus-5-5`, interactive
  and as a job.
- **Metrics, from traces and job `costs.jsonl`:**
  - wall time; turns; median and p90 turn latency;
  - cache-read share and cache-creation tokens per request;
  - `flush`/fetch-back turn share; model calls between turns;
  - cost.
- **Trace additions needed:**
  - absolute timestamps on iteration records;
  - cache-creation tokens on host iteration records (today only cache reads);
  - retry/backoff events from `mu/agent/retry.py`;
  - the Phase 5.4 context-assembly spans.
- A script under `tools/` (or `mu/trace/`) that prints the comparison table, so before and after
  are measured the same way.
- Optional reference: the same task in Claude Code for a wall-time comparison (manual).

## 10. Decisions (2026-10-14)

1. **Lean prompt rules apply to every mode.** Research, teacher, security, feature and loop modes
   keep their *mode* prompts, which may still ask for memory/scratchpad use where the mode depends
   on it. The verification for Phase 2 includes each mode's existing tests plus a check that each
   mode prompt still names the tools it relies on.
2. **Read collation scales with the model's context window** instead of a flat on/off switch.
   A batch of read results is returned inline unless its combined size exceeds
   `collation_batch_share` (default 10%) of the active model's context window, capped at 24k
   tokens. Large-context models (Anthropic, OpenAI, Gemini) effectively never collate; small
   local models (e.g. an 8k–32k Ollama model) still do.
3. **Thinking replay across the tool loop:** decided from the Phase 1 measurement.
