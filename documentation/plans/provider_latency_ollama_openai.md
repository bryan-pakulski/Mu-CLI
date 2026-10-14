# Plan: Ollama (cloud + local) and OpenAI latency fixes

Status: implemented (2026-10-14) · Owner: providers + agent loop · Follows `harness_latency_fixes.md`
(implemented), whose loop-level fixes already apply to every provider: inline batched reads,
deferred turn roll, parallel read-only `bash`, the per-iteration overhead fixes, compact tool
results and stepped history trimming.

## 1. The three Ollama routes

Ollama reaches a model in three different ways. They need different policies, and today mucli
treats them the same.

| Route | How it is reached | Example on this machine | Where the model runs | Context | KV cache |
|---|---|---|---|---|---|
| **A. Direct cloud** | host `https://ollama.com` + API key (`ollama_mode=cloud`, or `auto` with `OLLAMA_API_KEY`) | `glm-5.3`, `glm-5.3-flash` (your sessions) | Ollama's servers | model's full window; `num_ctx` is ignored | server-side, not ours to manage |
| **B. Local daemon → cloud model** | local daemon (`localhost:11434`), model tag `:cloud`/`-cloud`; `/api/tags` shows `remote_host` | `glm-5.3-flash:cloud` | Ollama's servers, proxied by the daemon | as A | as A |
| **C. Local model** | local daemon, a pulled model | `qwen3.8:27b` (27.3B, trained 262k context) on an RTX 3060 12 GB | this machine | what the daemon allocates (`num_ctx`), limited by VRAM | llama.cpp prefix reuse; one slot per `OLLAMA_NUM_PARALLEL` |

Findings from the audit (verified in code):

- **Context.** `ollama_num_ctx` defaults to 0 and is dropped (`utils/config.py:198`,
  `providers/ollama.py:672-686`), while `effective_context_window` returns the trained length from
  `/api/show` (`ollama.py:428-454`). On route C that is 262k for qwen3.8. The daemon actually
  allocates its own default (4k–32k depending on version and VRAM), so every budget (compaction,
  inline results, collation) is computed for a window ~10× larger than the real one. The
  prompt is then truncated or rejected. Routes A and B are unaffected.
- **Reloads (route C).** The editor sends `num_ctx` 16384/32768 (`mu/editor/service.py:108-117`)
  while the agent sends none. Alternating between them on the same model reloads it.
- **Size.** The fixed prompt is 15–20k cl100k tokens: system prompt ~5k, the 56-tool core set
  ~8.5k, plus skills and workspace files. The Ollama safety factor seed of 2.5 (`ollama.py:972-992`)
  makes a 32k window an effective 13k, below the fixed prompt. Learned drift for glm is ~1.02.
- **Thinking.** `/effort` is dropped (`ollama.py:684-687`), and `think` is only set by the
  editor, so side calls (summaries) think at the model's default.
- **`keep_alive`** is hard-coded to 600 s on every request (`ollama.py:735`), overriding the
  server's `OLLAMA_KEEP_ALIVE`.
- **KV churn (route C).** Pressure clears rewrite mid-history results almost every iteration on
  small windows. Side calls (summaries, chunking, subagents) evict the main prompt's KV cache
  on a single-slot daemon.
- **Smaller items.** Tool-call ids restart at `ollama_call_0` per response (`ollama.py:834`). The
  subagent wrap-up message is a mid-history `system` message, folded into the system prompt
  (`ollama.py:622-650`), which invalidates the whole prefix. `load_duration`,
  `prompt_eval_count`/`_duration` and `eval_duration` from the final chunk are ignored
  (`ollama.py:848-852`). The 300 s read timeout plus 30 retries restarts a slow cold prompt each time.

## 2. Goals

1. Each route gets correct, stable request settings: the context window mucli plans for equals
   the one the model actually uses, and nothing in the request changes between iterations.
2. **Route C:** no model reloads during a session, and after the first request each iteration
   re-processes only the new tail (KV prefix reuse); measurable through `prompt_eval_count`.
3. **Route C small windows (≤ 32k):** the fixed prompt fits with room for real work.
4. Effort and thinking work on every route and every provider, with no invalid values sent.
5. **OpenAI:** correct effort values, better cache routing, and no repeated reasoning across
   tool-loop turns for reasoning models.
6. Same measurement method as the Anthropic work (`python3 -m mu.trace.compare`), on a
   benchmark matrix covering routes A, B and C and OpenAI (§9).

## 3. Phase 0: spike (½ day, informs Phase 1)

Verify against daemon 0.32.14, with no code changes:

1. Does `/api/ps` report the loaded context (`context_length`) for a running local model, and is
   it the daemon's VRAM-aware default when no `num_ctx` is sent? Load `qwen3.8:27b` once with
   a tiny `/api/generate`.
2. Does sending the *same* `num_ctx` as the loaded value avoid a reload, and does a different one
   force a reload? Check `load_duration`.
3. For a `:cloud` model through the local daemon, does `/api/show` or `/api/tags` reliably mark
   it remote (`remote_host` was set in `/api/tags` and empty in `/api/show` here)? Is `num_ctx`
   ignored?
4. With an identical prefix, does the second request's `prompt_eval_count` drop to the tail?
   That confirms KV prefix reuse for chat requests with tools.
5. Is the `think` field honoured on routes A and B for glm, and on route C for qwen3.8
   (`think: false` / `true`, and `"low"|"medium"|"high"` for gpt-oss-style models)?

Record the answers in this plan (§10) before Phase 1 lands.

### Implementation notes (2026-10-14)

- All phases are implemented. Ollama: `OllamaProvider.route()`, `resolved_num_ctx()`,
  `request_timeout_for()`, `_think_value()`, `generate()` = side call (`think:false`,
  `num_predict` 2048, optional `ollama_side_model`), `keep_alive` only when set, uuid tool-call
  ids, `Retry-After` on 429/503, provider `timings` in usage events and traces. Small windows:
  `mu.session.budgets.small_window`, `mu.tools.descriptors.small_window_tools`. Pressure clears
  free half the history headroom. Subagent wrap-up note is user-role.
- OpenAI: `openai_reasoning_effort()` mapping, `prompt_cache_key` via `CacheHint.cache_key`,
  opt-in `prompt_cache_retention`, SDK `max_retries=0` (Anthropic too), cached model list,
  Responses API path (`providers/openai_responses.py`) with encrypted reasoning replay.
- Found while benchmarking: an OpenAI `insufficient_quota` 429 was retried as transient for
  minutes; quota/billing errors are now permanent.
- The editor no longer sets its own `num_ctx` (it would reload a shared local model).

### Phase 0 results (daemon 0.32.14, qwen3.8:27b, RTX 3060 12 GB)

| Step | Result |
|---|---|
| cold load at `num_ctx` 65536 | `load_duration` 70.7 s; `/api/ps` reports `context_length` 65536; 6.4 of 19.9 GB in VRAM |
| same prefix + new turn, same `num_ctx` | no reload (0.26 s); `prompt_eval_duration` 17.8 → 2.3 s (prefix reuse works; `prompt_eval_count` still reports the whole prompt) |
| no `num_ctx` sent | reload (11.4 s) at the daemon default **4096** |
| `num_ctx` 65536 again | reload (13.6 s) |
| `think` false/true | honoured (local and `:cloud`) |
| `:cloud` model via daemon, `num_ctx` 2048 | ignored; `/api/tags` shows `remote_host` (not `/api/show`) |

Prompt processing on this machine is ~200 tokens/s, so a cold 20k-token prompt costs ~100 s:
prefix reuse matters more than anything else on route C. Telemetry uses `prompt_eval_duration`
(not the count) to measure re-processing.

## 4. Phase 1: route detection and a stable, truthful context window (A, B, C)

**Files:** `providers/ollama.py`, `utils/config.py`, `mu/editor/service.py`, `mu/tools/agent/spawn.py`.

1. **`OllamaProvider.route(model)`** → `"cloud" | "daemon-cloud" | "local"`, cached per
   (host, model):
   - `cloud`: the host is `ollama.com` (or any https host with an API key and no daemon
     `/api/version`).
   - `daemon-cloud`: local daemon, and the model tag ends with `:cloud`/`-cloud` or
     `/api/tags` lists a `remote_host` for it.
   - `local`: otherwise.
2. **Context window per route:**
   - `cloud`, `daemon-cloud`: `effective_context_window` = trained length from `/api/show`,
     cached. Never send `num_ctx`.
   - `local`, resolved once per (host, model), then constant and **always sent explicitly**
     (decision 2026-10-14: larger is better, never below 64k):
     1. user `ollama_num_ctx` > 0 → use it;
     2. else `num_ctx = min(trained, max(65536, ollama_num_ctx_default))`. New variable
        `ollama_num_ctx_default`, default 65536. The daemon's own smaller default is never adopted.
   - A model whose trained window is below 64k uses its trained window.
   - `effective_context_window` returns exactly the value sent, so all budgets plan against the
     real window.
   - VRAM note: on the 12 GB GPU a 27B model at 64k may spill KV cache to system RAM. That is
     slower per token, but accepted for the larger window. Traces record `load_duration` so this
     is visible.
3. **One `num_ctx` per (host, model)** for every caller. The editor, subagents and side calls
   ask the provider for the resolved value instead of passing their own. On route `local`, a
   different subagent model gets a warning: "loading a second local model swaps VRAM; set
   `subagent_model` to the session model or a cloud model".
4. **Tests** (unit, fake HTTP):
   - route classification for the three cases;
   - window resolution order;
   - `num_ctx` sent only when resolved explicitly, and identical across 5 requests;
   - the editor and the agent produce the same options for the same model;
   - a cloud route never sends `num_ctx`.

## 5. Phase 2: thinking and effort (A, B, C, OpenAI)

1. **Ollama `think`** (top-level request field):
   - If the user or editor set `think` in the constructor, it wins.
   - Else `reasoning_effort` maps to `think`: for models whose `/api/show` capabilities include
     `thinking`, send `"low"/"medium"/"high"` when the model family accepts levels (gpt-oss) and
     booleans otherwise (`low` → `false`, others → `true`; `max` → `"high"` / `true`).
   - With no effort set, omit `think` (model default).
   - Side calls (`generate()` for summaries and chunking) send `think: false` and a
     `num_predict` cap (2048) on every route.
2. **OpenAI effort mapping** (bug):
   - Map mucli levels to the API: `low|medium|high` unchanged, `max` → `xhigh` where the model
     supports it, else `high`.
   - Add `minimal` / `none` to `/effort` for providers that support them.
   - Omit `reasoning_effort` when the user has not set one (today it forces `medium`/`high`).
   - Exclude `*-chat*` models from `_is_reasoning_model` (`providers/openai.py:28, 50-52`).
3. **Anthropic:** `max` is already valid; no change.
4. **`/effort`** lists the values the active provider accepts.
5. **Tests:**
   - the payload for each provider × effort;
   - side calls send `think: false`;
   - no `think` field when effort is unset;
   - `gpt-5-chat-latest` gets no `reasoning_effort`.

## 6. Phase 3: fit small windows (C only)

1. **Safety factor seed:** lower the Ollama `compaction_safety_factor` seed from 2.5 to 1.3
   (`utils/config.py:214`, `ollama.py:972-992`). The learned drift EWMA corrects it per model
   after the first request, and reactive overflow recovery stays as the backstop.
2. **Small-window profile**, applied automatically when the resolved local window ≤ 32k:
   - **Tools:** use a compact allow-list (files, search, `bash`, `todo_write`, `spawn_agent`,
     `flush`) through the existing tool-profile mechanism (`mucli.py:96-115`).
     Variable `small_window_tool_profile` (default `compact`).
   - **Schemas:** trim parameter descriptions to their first sentence for small windows; keep
     names and types.
   - **Prompt layers:** skip the L1B skills index and cap L1A workspace files at 1k tokens.
   - **Prompt:** the system prompt uses the lean base template.
3. **Prompt-mode check:** every mode still validates (`mu/prompts.validate`) with the compact
   tool profile; modes needing tools outside it (research, teacher) re-enable theirs through
   their existing phase lists.
4. **Tests:** with a 16k fake window, the first request (system + tools + one user message)
   fits under 50% of the window; the profile is not applied at 128k; every mode validates.

## 7. Phase 4: keep the local KV cache (C only)

1. **Pressure-clear hysteresis on free space** (`mu/agent/compactor.py` `_pressure_clear`):
   - Trigger and target are expressed as a share of the L5 budget (limit − non-L5 layers −
     reserve), not of the whole window.
   - When clearing, clear down to 50% of L5 in one step, so the next clear is many turns away
     (same idea as stepped trimming).
   - Ledger kind unchanged (`pressure_clear`).
2. **Side calls stay off the main KV slot:**
   - **Default:** on route `local`, deferred turn-roll summaries are applied at the next turn
     start as now, but their model call runs only after the agent turn ends (it already
     does), and is skipped when the user's next message is already waiting (mechanical
     summary instead).
   - **Optional `ollama_side_model`:** a small local or cloud model for summaries and chunking.
     When set, side calls use it and never touch the main slot.
   - **Subagents on route `local`:** not serialized by mucli (decision: trust the daemon's
     `OLLAMA_NUM_PARALLEL`). Documented: each extra slot costs another `num_ctx` of KV memory.
3. **No mid-history system messages:** the subagent wrap-up note (`mu/agent/loop_body.py:1042-1050`)
   becomes a user-role note on all providers, so Ollama doesn't fold it into the system prompt.
4. **Tests:**
   - two consecutive iterations on a small window produce byte-identical request prefixes up
     to the previous tail (no clear in between);
   - a clear removes enough that the next N iterations don't clear;
   - the wrap-up note is user-role.

## 8. Phase 5: reliability and telemetry (A, B, C)

1. **`keep_alive`:**
   - New variable `ollama_keep_alive` (default empty = omit the field, so the server's
     `OLLAMA_KEEP_ALIVE` applies).
   - Never sent on routes A/B.
   - `CacheHint.keep_alive_seconds` is only used when the variable is set.
2. **Timeouts:**
   - `ollama_request_timeout` variable. Defaults: local 900 s (prompt processing streams
     nothing), cloud 300 s.
   - A first-byte timeout on route C is not retried blindly: retry once, then fail with "local
     prompt processing exceeded N s; lower num_ctx or use a smaller model".
3. **Cloud rate limits (A/B):** honour `Retry-After` on 429/503 in `mu/agent/retry.py`'s
   backoff, and report the cause.
4. **Unique tool-call ids:** `ollama_{uuid8}` instead of `ollama_call_{n}` (`ollama.py:834`).
5. **Telemetry:**
   - From the final chunk: `load_duration`, `prompt_eval_count`, `prompt_eval_duration`,
     `eval_count`, `eval_duration`, as `StreamEvent` usage fields and trace `tokens.*`.
   - `mu.trace.compare` gains columns `reloads` (warm iterations with `load_duration` > 0.5 s),
     `prompt_eval_per_turn` (the local equivalent of uncached input) and `tokens_per_s`.
6. **Tests:** fake-daemon streams with those fields reach the trace; `keep_alive` is omitted by
   default; ids are unique across responses.

## 9. Phase 6: OpenAI

1. **Effort mapping:** in Phase 2.
2. **Cache routing:**
   - Send `prompt_cache_key` = the session's trace run id.
   - Send `prompt_cache_retention: "24h"` for models that support extended retention (a
     small allow-list, configurable).
   - The prefix order is already cache-friendly.
3. **No nested retries:** `OpenAI(...)` and `Anthropic(...)` are created with
   `max_retries=0` and an explicit timeout, since `mu/agent/retry.py` owns retries
   (`providers/openai.py:114`, `providers/anthropic.py:292-296`).
4. **Cache the model list** per provider instance (it is fetched per `spawn_agent` today,
   `mu/tools/agent/spawn.py:214`).
5. **Responses API for reasoning models** (largest item):
   - New `OpenAIProvider` path, selected for `_is_reasoning_model` models (variable
     `openai_api = auto|chat|responses`).
   - Uses `responses.create(stream=True, store=False, include=["reasoning.encrypted_content"])`.
   - The returned reasoning items are kept in the assistant message's `thought_signature` (as
     Anthropic thinking is) and replayed in order, so the model continues instead of
     re-reasoning on each tool-loop turn.
   - Tool calls and results map to `function_call` / `function_call_output` items.
   - Chat Completions stays the path for non-reasoning models and OpenAI-compatible base URLs.
6. **Tests:**
   - payloads for both paths;
   - reasoning items round-trip through history;
   - effort and cache-key fields present;
   - `max_retries=0` on both SDK clients.

## 10. Measurement and rollout

**Benchmark matrix**, the same read-edit-test task as the Anthropic plan, 2 runs each, before and
after each phase:

| Run | Route | Model |
|---|---|---|
| 1 | A direct cloud | `glm-5.3` |
| 2 | B daemon → cloud | `glm-5.3-flash:cloud` |
| 3 | C local | `qwen3.8:27b` (12 GB GPU: expect partial offload; also try a ≤ 14B model if available) |
| 4 | OpenAI | a gpt-5.x reasoning model |

**Metrics:** wall time, turns, turn latency, `reloads`, `prompt_eval_per_turn` (local), tokens/s,
cache-read share (OpenAI), effort/think sent, and task correctness.

**Acceptance:**
- **Route C:** zero reloads after the first request; from the 2nd iteration on,
  `prompt_eval_per_turn` within ~2k tokens of the new tail; the first request fits the real
  window (no truncation); no 400s.
- **Routes A/B:** no `num_ctx`/`keep_alive` sent; effort reaches `think`; no regression in turn
  latency.
- **OpenAI:** `/effort max` works; cached-token share ≥ 80% after turn 1 on the benchmark;
  fewer reasoning tokens per turn on the Responses path.

**Order and risk:**

| Phase | Routes | Risk | Order |
|---|---|---|---|
| 0 Spike | C, B | none | first |
| 1 Route + context window | A, B, C | medium (changes budgets for local users) | second; biggest correctness win for C |
| 2 Effort/think (+ OpenAI bug) | all | low | with 1 |
| 5 Reliability + telemetry | all | low | with 1, so later phases are measurable |
| 3 Small-window profile | C | medium (tool surface) | after 1 |
| 4 KV preservation | C | medium | after 3 |
| 6 OpenAI cache/retries/model list | OpenAI | low | any time |
| 6.5 Responses API | OpenAI | high (new request path) | last, behind `openai_api=auto` |

Each phase lands with the full suite green and its new behaviour behind a variable.

## 11. Decisions (2026-10-14)

1. Local context: never below 64k (`ollama_num_ctx_default` = 65536, always sent explicitly;
   the trained window caps it).
2. Trust `OLLAMA_NUM_PARALLEL`: subagents are not serialized by mucli. The variable
   `ollama_num_parallel` only informs side-call scheduling when set.
3. Order as in §10; implement every phase.
