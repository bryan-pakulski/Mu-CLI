# Test migration ledger: no overflow stop

Format (spec §8.3): `file:line — old → new — intent`. The specified behaviour
changed by user request (2026-10-16): "Stop issues like this happening during job
mode … add automatic compaction via the harness … We shouldn't just stop dead in
the tracks." An implement stage on the mucli harness ended with "prompt is too
long: about 1,015,872 tokens > 1,000,000 maximum (refused by the job relay before
sending)" although the real request was about 210k tokens.

## Root cause (confirmed)

- Every tool-call part carried an Anthropic thinking signature in
  `thought_signature` (base64 up to about 60k characters), and the agent loop copied
  it onto the matching `tool_result` part too (`mu/agent/loop_body.py`, the
  `tool_result_part` dict). The relay preflight (`mu/jobs/sandbox/model_relay.py`)
  estimated `json.dumps(payload)` under cl100k, so the 81-message transcript counted
  1,024,092 tokens, 932,460 of them signatures. Its visible content is 87,905.
- mucli's own estimators already ignored signatures, so reactive compaction saw a
  prompt well under its budget, changed nothing (1,016,110 → 1,015,872 →
  1,015,872 at the relay), and the breaker re-raised after 3 tries in about 400 ms.
- The copied signature also marked every tool result as provider-signed, which
  blocks folding, clearing and degradation of those results.

## Behaviour changes

- Estimates count visible content only (`utils.token_estimator.estimate_payload_tokens`
  / `estimate_message_tokens`): text, tool arguments, tool results, system prompt and
  tool schemas, plus 1,600 tokens per media item. The relay preflight, the loop's
  request manifest and every pressure gate share it. Drift correction is unchanged:
  gates still compare the cl100k estimate against `drift_corrected_context_limit`.
- The relay refuses up front only when the visible estimate alone reaches the window
  (any request counts at least 1 token); otherwise it forwards and the provider's 400
  is the ground truth.
- Replay data is dropped from requests outside the open tool loop
  (`providers.base.project_replay`): Anthropic keeps thinking on the latest assistant
  message only when it called tools, OpenAI Responses and Gemini keep the current
  turn's reasoning / thought signatures, and the in-sandbox `RelayProvider` applies
  the host provider's scope before the wire. `session.history` is not rewritten.
- Tool results no longer get a copy of the call's signature; loading a session, a
  stage `history.json` or a paused transcript strips copies already saved.
- Overflow recovery is a ladder (`mu/agent/context_guard.py`, `_OVERFLOW_RUNGS`):
  compact → strip replay blobs + degrade large tool results (floor and signed-bundle
  protection lifted) → full compaction into L2 except the latest goal (ledger kind
  `overflow_full_compact`) → truncate a single oversized message with
  "[truncated N tokens to fit the context window]" (ledger kind `overflow_truncate`).
  A rung that shrinks the estimated request by less than 10% escalates before the
  provider is retried. The per-turn breaker counts rungs (4; job stages 16 via
  `overflow_recovery_max_rungs`).
- When every rung fails the turn ends with status `context_overflow` and a clear
  message, not "API Error during agentic loop". A job stage reports
  `error_class: context_overflow` with its L2 summary, and the sandbox runner retries
  the stage once with a fresh conversation seeded from the stage brief and summary
  (event `stage_context_overflow`).
- Claude Code stages write `autoCompactEnabled: true` to their config and drop
  `DISABLE_AUTO_COMPACT` / `DISABLE_COMPACT`; Codex stages pass
  `-c model_auto_compact_token_limit=<85% of the window>` when the window is known.
  A CLI that still exits on overflow is compacted (`/compact` for Claude Code) and
  resumed, up to twice per run. Codex's `context_window_exceeded` is an overflow
  marker.

## Existing tests

- `tests/test_overflow_recovery.py:271` — `len(calls) == _MAX_OVERFLOW_RECOVERIES_PER_TURN + 1`
  → `len(calls) == _MAX_OVERFLOW_RECOVERIES_PER_TURN` — intent: the escalation loop is
  capped per turn and re-raises instead of looping. The cap is now 4 rungs, and the
  non-shrinking `strip_degrade` rung escalates to `full_compact` without its own
  retry, so one fewer provider call is made.
- `tests/test_overflow_recovery.py:497` — `_overflow_recoveries_this_turn == 2` → `== 3`
  — intent: each still-overflowing retry compacts harder, in order (the
  `(4, 0.20, False), (2, 0.30, True)` assertion is unchanged). The second rung did not
  shrink the request, so the third ran before the retry; the breaker counts rungs.
- `tests/test_job_sandbox_w2.py:405` — `"prompt is too long: 300000 tokens"` classified
  `fatal` → `context_overflow` (plus a `context_overflow` status case) — intent: an
  overflow is never a transient backoff retry. It is now its own class, retried once
  with a fresh conversation by the runner.

No other assertion was rewritten, weakened, skipped or deleted.

## New coverage

- `tests/test_job_no_overflow_stop.py` (29 tests), using
  `tests/fixtures/overflow_transcript_shape.json` (the incident transcript's roles,
  part kinds and sizes; content synthesised): the fixture estimates 1,000,472 tokens
  the old way and 58,771 visible; the relay forwards it and still refuses a request
  that cannot fit; budgets, `projected_tokens` and `context_status` ignore signatures;
  replay projection scopes for Anthropic, OpenAI Responses, Gemini and the relay
  provider, with `session.history` unchanged; tool results carry no signature and old
  history is stripped on load; the ladder escalates on a non-shrinking rung, full
  compaction keeps the goal and records `overflow_full_compact`, truncation leaves the
  marker and records `overflow_truncate`; an unrecoverable overflow ends the turn as
  `context_overflow` without "API Error during agentic loop"; the runner retries a
  `context_overflow` stage exactly once with a fresh, summary-seeded prompt; Claude
  Code auto-compact config and env, Codex `model_auto_compact_token_limit`, and the
  harness compact-and-continue path with its bound.
