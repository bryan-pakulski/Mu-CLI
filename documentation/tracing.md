# Run tracing & the Trace Analyzer

Mu-CLI writes a **per-run JSONL trace** of every agent-loop iteration and
serves a full-page **Trace Analyzer** dashboard to visualize it. The trace is
the data foundation for harness-performance decisions: it records, per
iteration, the real prompt token count the provider reports versus the
harness's own `cl100k_base` estimate, every compaction pass, every corrective
nudge, every tool call (with latency + cache hits), sub-agent state, and
memory counts.

Everything is defensive — a trace failure can never break the agent loop, and
tracing is disabled by setting `trace_enabled=false`.

## Files and location

Traces live under `MUCLI_HOME/trace/` (default `~/.mucli/trace/`), one file
per **run**:

```
~/.mucli/trace/<session>_run_<hex12>.jsonl
```

A *run* is a single process's worth of turns (a session spans many runs; a
run id is a runtime concept, generated lazily on first emit and not persisted
into `session.json`). Each file is self-contained — iteration records carry
truncated message previews so the dashboard can render the conversation
alongside the metrics without joining `session.json`.

## Enabling / disabling

Tracing is on by default. Disable it with the session variable:

```
/set trace_enabled false
```

When disabled, no trace file is written and no emitter is cached on the
session. Set it back to `true` (or unset it) to resume tracing on the next
turn.

## CLI

| Flag | Description |
| --- | --- |
| `mucli --trace` | Launch the GUI and open the Trace Analyzer dashboard at `/trace`. |
| `mucli --trace-analyze <file>` | Print a terminal summary of a trace JSONL file and exit (headless quick-look — no GUI). |

The headless analyzer is a thin wrapper over the same parser the dashboard
uses, so the numbers match exactly:

```
mucli --trace-analyze ~/.mucli/trace/myrun_run_abc123.jsonl
```

## The dashboard

Open it from the GUI's **tools** dropdown → **Trace Analyzer** (opens
`/trace` in a new tab), or with `mucli --trace`. It renders thirteen sections,
all with custom `<canvas>` (no charting library):

1. **Run picker** — list of runs (session / model / mode / iters / status).
2. **Overview cards** — total iters, tokens & cost, compaction count by type,
   **mechanical-fallback count**, nudge count + how many broke the loop,
   sub-agent iters, peak context, **peak |drift|**, mean drift, outcome
   status.
3. **Context growth** — `total_est` (the harness's `cl100k_base` estimate) vs
   `prompt_tokens_actual` (the provider's real prompt size) per iteration,
   with a context-limit reference line and compaction markers. Toggle the
   stacked L0–L5 layer breakdown.
4. **Request context attribution** — stacked system, user, assistant,
   tool-call, tool-result, file/image, other, and tool-schema token estimates.
   A ranked table identifies the component and individual message part behind
   each large request-to-request jump.
5. **Tokenizer drift** — `drift_pct` per iteration (the headline diagnostic,
   see below). A ±15% warn band is shaded.
6. **Compaction timeline** — kind, tokens saved, summarizer mode
   (`llm` vs `mechanical`), `keep_recent`, budget. Click a row to jump to
   that iteration.
7. **Tool histogram** — per tool: count, success/error split, average
   latency, cache-hit rate. Click a tool for its per-iteration latency series.
8. **Read-state / redundant reads** — a heatmap of read paths × iterations;
   re-reads of a path with no intervening write are flagged red.
9. **Nudge timeline + efficacy** — each nudge on the iteration axis by kind,
   with whether a materially different action (a write, or a novel tool call)
   followed within 3 iterations.
10. **Subagent timeline** — active children per iteration with stuck/stall
   flags.
11. **Memory & scratchpad counts** — task-memory and scratchpad counts per
    iteration.
12. **Token breakdown** — in / out / cached / reasoning per iteration.
13. **Conversation view** — iter-aligned message previews (assistant text,
    tool calls + args, result previews). Click any iteration in any
    chart/table to scroll here and highlight it.

The dashboard is post-hoc analysis of completed runs. Live mid-run updates
(publishing trace events on the SSE bus) is a follow-up, not yet implemented.

## JSONL record schema

Line 1 is a header; subsequent lines are events keyed by `type`.

### `run_start` (one per run)
```jsonc
{ "type": "run_start", "run_id": "run_…",
  "session": "…", "model": "…", "provider": "…", "mode": "…",
  "context_limit": 128000, "max_iterations": 1000 }
```

### `iter` (one per agent-loop iteration, at the post-response seam)
```jsonc
{ "type": "iter", "iter": 0, "max_iter": 1000, "wall_ms": 120,
  "context": { "l0":…,"l1":…,"l1c":…,"l1b":…,"l2":…,"l3":…,"l4b":…,"l5":…,
               "total_est":…, "prompt_tokens_actual":…, "prompt_tokens_real_est":…,
               "drift_ratio":…, "drift_pct":…, "drift_pct_reliable":…,
               "estimate_source":… },
  "tokens": { "in":…,"out":…,"cached":…,"reasoning":…,"cost_delta":… },
  "has_text": true, "has_tool_call": true,
  "assistant_preview": "…",
  "subagents": { "active":…,"stuck":…,"stall":…,"children":[…] },
  "memory": { "task_memory_count":…,"by_status":{…},"scratchpad_count":… },
  "compaction": null | { …latest compaction this iter… },
  "status": "running" }
```

### `request` (one privacy-preserving manifest per provider request)

Records token and byte counts—but not raw prompt content—for the system
prompt, every message part, and tool schemas. `component_tokens` drives request
attribution, while hashes make repeated payloads detectable without creating a
second store of repository content.

```jsonc
{ "type": "request", "iter": 0, "token_estimate": 42000,
  "component_tokens": {
    "system": 8000, "user": 500, "assistant": 2500,
    "tool_calls": 1000, "tool_results": 25000,
    "files_images": 0, "other": 0, "tool_schemas": 5000
  },
  "messages": [
    { "index": 4, "role": "tool", "bytes": 100000, "tokens": 25000,
      "parts": 1, "part_details": [
        { "index": 0, "type": "tool_result", "tool_name": "bash",
          "bytes": 100000, "tokens": 25000 }
      ] }
  ] }
```

The headline field is **`context.drift_pct`**:

```
if prompt_tokens_actual is a reliable full-prompt signal (actual*4 >= total_est, or total_est == 0):
    drift_pct = (prompt_tokens_actual - total_est) / max(1, prompt_tokens_actual) * 100
else:
    drift_pct = 0.0     # actual is a cached delta, not a full prompt — see drift_pct_reliable
```

`prompt_tokens_actual` is the provider-reported input count. For providers
that expose cache deltas (notably Ollama), `prompt_tokens_actual` is the
non-cached prompt **delta** — near-zero in a warm loop, NOT the prompt size.
Normalising by that near-zero value used to blow `drift_pct` up to ±2000%;
it is now gated: when `actual` is a tiny fraction of the estimate the field
is zeroed and `drift_pct_reliable` is `false` (the UI renders it as
"drift unknown" rather than "0% drift = perfect estimate"). The
representative real-prompt size in that case is
`prompt_tokens_real_est = total_est * drift_ratio` (the drift-corrected
cl100k estimate). `total_est` is the harness's tiktoken `cl100k_base`
estimate captured immediately before that exact provider request
(`estimate_source: "pre_request"`); it does not include the response that was
archived afterward. On a model whose tokenizer is not `cl100k_base` (e.g.
glm), this drift is
systematic and is the primary signal for diagnosing long-horizon compaction
failures: if the estimate is wrong, the compaction budget is wrong.

### `tool` (one per tool call; joined to iters by `iter`)
```jsonc
{ "type": "tool", "iter": 0, "name": "read_file", "arg_fp": "read_file:ab12",
  "ok": true, "error_code": null, "latency_ms": 30, "cache_hit": false,
  "result_bytes": 4000, "path": "a.py", "preview": "…" }
```

### `nudge` (one per corrective nudge injection)
```jsonc
{ "type": "nudge", "kind": "recoverage_stall", "iteration": 1, …extra… }
```
Kinds: `empty_response`, `loop_watchdog`, `recoverage_stall`, `loop_detect`,
`loop_detect_retryable`, `retryable_escalation`.

### `compaction` (one per compaction pass)
```jsonc
{ "type": "compaction", "iter": 1, "kind": "auto_hook",
  "tokens_before": 5000, "tokens_after": 1500, "tokens_saved": 3500,
  "msgs_before": 40, "msgs_after": 12,
  "anchor_before":…, "anchor_after":…, "anchor_delta": 1,
  "summarizer": "llm" | "mechanical" | "none",
  "keep_recent": 4, "budget": 3000 }
```
`kind` is one of `turn_start`, `auto_hook`, `emergency_preflight` — the three
trigger sites. `summarizer` is `mechanical` when the LLM summarizer fell back
to the lossy per-part truncation path (a silent quality loss worth tracking).

### `turn_end` (one per turn; flushes + closes the file)
```jsonc
{ "type": "turn_end", "status": "completed",
  "total_in":…, "total_out":…, "total_cost":…,
  "tool_calls":…, "tool_results":…, "error": null,
  "session_totals": {…}, "iters": 3 }
```

## What the trace answers

The trace was built to confirm or refute the harness-side suspects for why
mucli underperforms on long-horizon tasks (with the model held constant):

- **Tokenizer drift (suspect #1)** — read the **drift curve**. If `total_est`
  diverges from `prompt_tokens_actual`, the compaction budget (computed in
  `cl100k_base` tokens) is wrong for the real model, and compaction fires at
  the wrong point.
- **Reasoning tokens unbudgeted (suspect #2)** — read the **token breakdown**.
  Reasoning tokens that balloon mid-turn force `keep_recent=2` emergency
  compaction (visible in the compaction timeline as `emergency_preflight`).
- **Lossy mechanical fallback (suspect #3)** — read the **mechanical-fallback
  count** in the overview. Each mechanical compaction silently lost
  per-part content.

Beyond the original suspects, the dashboard also quantifies whether the
**nudges** actually work (the efficacy column) and how much **redundant
reading** happens (and whether it correlates with compaction — re-reading
caused by state loss vs aimlessness).

## Architecture

```
mu/trace/
  __init__.py    Public exports
  emitter.py     TraceEmitter (writes JSONL), get_emitter, build_iter_record,
                 drain_compactions, emit_nudge, emit_tool — hooked into
                 mu/agent/loop_body.py, mu/agent/compactor.py,
                 mu/session/history.py, mu/session/session.py
  parser.py      parse_trace, build_series, build_summary (shared by router
                 + CLI — pure functions over a parsed run)
  snapshot.py    build_trace_snapshot (canvas-ready context-growth grid +
                 diverging drift strip)
```

- **Emitter** (`mu/trace/emitter.py`) — lazy-opens the JSONL file on first
  write, thread-safe, every path wrapped in `try/except` so telemetry never
  breaks the loop. `get_emitter(session)` returns `None` when disabled.
- **Hook points** — `run_start` once per run; the per-`iter` record at the
  post-response seam (where `response.input_tokens` aligns with the context
  estimate); per-`tool` capture at the post-execution site with real latency;
  `nudge` at all six injection sites; `compaction` logged in one place
  (`history.py:roll_history_summary_to_token_budget`) with the trigger kind
  stamped by each caller; `turn_end` + flush + close in
  `session.py:send_message`'s `finally` block (runs on every exit path).
- **Read side** — `mu/gui/routers/traces.py` (`GET /api/traces`,
  `/api/traces/<run_id>`, `/raw`, `/summary`) and `mucli --trace-analyze`
  both call the shared parser/snapshot, so the GUI and CLI show identical
  numbers.

## See also

- [configuration.md](configuration.md) — `trace_enabled` variable, `--trace`
  / `--trace-analyze` flags, `MUCLI_HOME` layout.
- [session_guide.md](session_guide.md) — the compaction system the trace
  instrumented (three triggers, `keep_recent`, `tool_result_floor`).
- [memory_guide.md](memory_guide.md) — the L0–L5 context layers the trace
  records per iteration.
