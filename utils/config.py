# Constants
import os

# Try importing PIL for image handling
try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Configuration
HISTORY_DIR = os.path.expanduser(os.getenv("MUCLI_HOME", "~/.mucli/"))
SESSION_DIR = os.path.join(HISTORY_DIR, "sessions")
LOG_DIR = os.path.join(HISTORY_DIR, "logs")
DEFAULT_SESSION_NAME = "default"

VALID_SESSION_TYPES = ("chat", "workspace", "container", "orchestration")
SESSION_TYPE_PROMPTS = {
    "chat": (
        "You are in CHAT session type. Host filesystem and shell tools are not "
        "available. Respond conversationally and use research/memory tools when "
        "useful. Do not attempt file, workspace, shell, or process operations. "
        " Use `publish_visualization` proactively whenever a graph, chart, diagram, "
        "timeline, dashboard, map, or interactive view would materially improve "
        "the answer. Create it in the same turn without waiting for a separate "
        "request; prefer self-contained HTML with embedded data and include a "
        "concise textual explanation. Use `upload_artifact` for non-HTML files."
    ),
    "workspace": (
        "You are in WORKSPACE session type. File and shell tools execute on the "
        "MuCLI host and must remain within explicitly attached workspace folders. "
        "Use approval-required behavior for modifying actions unless the user has "
        "explicitly enabled an override. "
        " Use `publish_visualization` proactively whenever a graph, chart, diagram, "
        "timeline, dashboard, map, or interactive view would materially improve "
        "the answer. Create it in the same turn without waiting for a separate "
        "request; prefer self-contained HTML with embedded data and include a "
        "concise textual explanation. Use `upload_artifact` for non-HTML files."
    ),
    "container": (
        "You are in CONTAINER session type. The Docker sandbox itself is the "
        "filesystem and process boundary: freely inspect and modify any non-secret "
        "path inside the container, including /, installed packages, system "
        "configuration, /workspace, and every mounted path. Do not treat attached "
        "workspace folders or gitignore rules as access restrictions. Install and "
        "run software autonomously without approval prompts. Host paths that were "
        "not mounted remain inaccessible; the Docker socket is unavailable; "
        "outbound traffic follows the configured proxy policy; secret paths, "
        "credential dumping, and output leakage remain blocked. "
        " Use `publish_visualization` proactively whenever a graph, chart, diagram, "
        "timeline, dashboard, map, or interactive view would materially improve "
        "the answer. Create it in the same turn without waiting for a separate "
        "request; prefer self-contained HTML with embedded data and include a "
        "concise textual explanation. Use `upload_artifact` for non-HTML files."
    ),
    "orchestration": (
        "You are in ORCHESTRATION session type: you help the operator design, test and "
        "run administrative tools for a fleet of Linux machines. You have no shell and "
        "no file access on the host or on any node, and you never see credentials. "
        "Everything goes through the orch_* tools, mediated by the master. Work as a "
        "guided conversation through these stages: (1) Intent: ask what must happen, "
        "on which machines, how often, what 'done' looks like and what must never "
        "happen; search the catalog first (orch_catalog_search) and reuse or extend an "
        "existing tool instead of duplicating it. (2) Spec: draft the manifest with "
        "orch_tool_draft (typed inputs with patterns/enums, effect, privilege, "
        "idempotency, limits, rollback, check_mode). (3) Discover: look at real "
        "systems only through orch_node_facts and published read-only tools via "
        "orch_run_propose. (4) Implement: write the script; parameters arrive as "
        "$MUCLI_PARAMS (JSON) and quoted MUCLI_P_<NAME> variables, never interpolate "
        "them into code, honour MUCLI_CHECK=1 as a dry run, and write structured "
        "results to $MUCLI_RESULT. Run orch_tool_lint and fix every error. (5) Test: "
        "orch_tool_test proposes a check-mode run on a canary node; the operator "
        "approves it unless their access policy allows such tests. (6) Review: "
        "orch_tool_submit with a one-line headline and the risks. Runbooks compose "
        "published tools: check a spec with orch_runbook_validate before saving it with "
        "orch_runbook_draft (fix every problem it reports, and pass base_digest from your "
        "last save so you never overwrite the operator's edits), then test the draft with "
        "orch_runbook_test. Only the operator publishes tools, approves runs, edits policy or "
        "manages secrets; never claim you did. When a run you proposed waits for approval, say what "
        "it will do and end your turn: you are resumed automatically when it finishes, so never ask "
        "the operator to tell you it is done. Reference tools as id@version. Keep "
        "outputs small: you receive declared outputs and a capped, redacted log tail."
    ),
}

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# --- Layer Char-Budget Ratio System ---
# Layer char budgets scale with context_token_limit. At a reference limit
# each layer has a target token budget; when context_token_limit changes,
# budgets are recomputed proportionally but never drop below absolute floors
# (the historical defaults), so smaller context windows keep usable budgets.
TOKEN_TO_CHAR_RATIO = 4

_REFERENCE_TOKEN_LIMIT = 480000

# Target token budgets at the reference limit (in tokens, not chars).
_LAYER_TOKEN_TARGETS = {
    "context_files_max_chars": 5000,             # L1A
    "skills_max_chars": 10000,                   # L1B
    "conversation_summary_char_limit": 20000,   # L2
    "active_goal_context_char_limit": 4096,     # L3
    "retrieval_context_char_limit": 10000,       # L4B
}

# Absolute floors — historical defaults, never scale below these.
LAYER_CHAR_FLOORS = {
    "context_files_max_chars": 4000,
    "skills_max_chars": 6144,
    "conversation_summary_char_limit": 24000,
    "active_goal_context_char_limit": 4000,
    "retrieval_context_char_limit": 10000,
}


def compute_layer_char_budgets(context_token_limit):
    """Compute char budgets for all layer vars, scaled from reference
    targets but never below floors.

    Returns dict[var_name, chars].
    """
    ratio = context_token_limit / _REFERENCE_TOKEN_LIMIT
    result = {}
    for var_name, target_tokens in _LAYER_TOKEN_TARGETS.items():
        scaled_chars = int(target_tokens * TOKEN_TO_CHAR_RATIO * ratio)
        floor = LAYER_CHAR_FLOORS[var_name]
        result[var_name] = max(scaled_chars, floor)
    return result


def reratio_layer_budgets(session):
    """Recompute and apply layer char budgets when context_token_limit changes.

    Called from /set and /unset when context_token_limit is modified.
    """
    ctx_limit = int(
        session.variables.get("context_token_limit", _DEFAULT_CONTEXT_TOKEN_LIMIT)
    )
    budgets = compute_layer_char_budgets(ctx_limit)
    for var_name, chars in budgets.items():
        session.variables[var_name] = chars


_DEFAULT_CONTEXT_TOKEN_LIMIT = 480000
_LAYER_CHAR_DEFAULTS = compute_layer_char_budgets(_DEFAULT_CONTEXT_TOKEN_LIMIT)

# --- Variable Schema & Defaults ---
VARIABLE_SCHEMA = {
    "agent_mode": {
        "type": str,
        "default": "default",
    },  # Agent mode, determines the initial system prompt
    "session_type": {
        "type": str,
        "default": "workspace",
    },  # chat | workspace | container
    "ollama_host": {
        "type": str,
        "default": "",
    },  # Ollama server host
    "strict_mode": {"type": bool, "default": False},  # Forces approval for all tools
    "max_iterations": {
        "type": int,
        "default": 1000,
    },  # Max number of iterations to run for each conversation
    "compact_history": {
        "type": bool,
        "default": False,
    },  # Remove completed-turn tool metadata after a finished conversation.
    "trace_request_full_iters": {
        "type": int,
        "default": 1,
    },  # Round-51 T6: keep full request dumps for the first N iterations of
    # a run; later request trace records are bounded summaries (<2KB).
    "auto_compaction_enabled": {
        "type": bool,
        # Provider-aware fallback if model-led clearing/compaction is late.
        # Saved false values remain explicit opt-outs.
        "default": True,
    },
    "auto_compaction_token_limit": {
        "type": int,
        # Optional smaller threshold for projected L5, after selective result
        # clearing. 0 leaves the full provider-aware budget available. Saved
        # nonzero values remain deliberate working-set limits.
        "default": 0,
    },
    "yolo": {"type": bool, "default": False},  # YOLO mode (no approvals)
    "plan_mode": {"type": bool, "default": False},  # Plan mode. Read-only: write tools are blocked (/plan on|off)
    "verbose": {
        "type": bool,
        "default": False,
    },  # When False (default), hide tool-arg dumps, token lines, result previews, "Compacting turn history" notices, and user-echo panels. The compact inline "→ tool_name" indicator stays so progress is still visible.
    "session_goal": {
        "type": str,
        "default": "",
    },  # The user's pinned top-level task for the CURRENT TURN. Rendered in L3 of the system prompt every iteration so the model retains direction even when the L2 conversation summary gets compacted mid-turn. Set with /goal <text> or the set_session_goal tool; clears automatically at end of turn (`Session._strip_session_goal_after_turn`) so it can't bias an unrelated next request. The durable mirror in task_memory (saved via `_ensure_session_goal_persistence`) stays — only the live variable resets.
    "session_goal_sticky": {
        "type": bool,
        "default": False,
    },  # When True (or in loop/feature mode, which default to sticky), the pinned `session_goal` is NOT cleared at end of turn — it persists across turns in L3 until the user clears it (/goal clear) or sets a new goal. Long-horizon multi-turn work needs the goal to survive turn boundaries; conversational default-mode use does not. See `Session._strip_session_goal_after_turn`.
    "session_goal_sticky_explicit": {
        "type": bool,
        "default": False,
    },  # Tracks whether the user has explicitly set `session_goal_sticky` via /set, so the mode-aware default (loop/feature = sticky) only applies until the user overrides it. Mirrors the `show_thinking_explicit` pattern.
    "show_thinking": {
        "type": bool,
        "default": True,
    },  # When False, suppress streamed reasoning/thinking deltas in the UI. The model still GENERATES thinking when `session.thinking=True` — this only controls whether the dim-italic text is rendered to the terminal. Mode-aware: teacher mode hides thinking by default unless the user has explicitly toggled this var (see `show_thinking_explicit`).
    "show_thinking_explicit": {
        "type": bool,
        "default": False,
    },  # Tracks whether the user has explicitly called `/show-thinking`. When False, modes can apply their own default policy (teacher mode hides; others show). The `/show-thinking` command flips this to True so subsequent mode switches don't override the user's choice.
    "reflective_retry_enabled": {
        "type": bool,
        "default": True,
    },  # Surface retryable tool failures + hints in the live UI
    "streaming_enabled": {
        "type": bool,
        "default": True,
    },  # Render assistant text token-by-token instead of one final panel
    # Ollama provider knobs — set via `/set ollama_<key> <value>`.
    "ollama_num_ctx": {"type": int, "default": 0},  # 0 = automatic: local models get ollama_num_ctx_default (≥64k, capped by the trained window), always sent so the daemon never reloads; cloud routes ignore num_ctx
    "ollama_num_ctx_default": {"type": int, "default": 65536},  # local models only; values below 65536 are raised to 65536
    "ollama_keep_alive": {"type": str, "default": ""},  # empty = omit (the daemon's OLLAMA_KEEP_ALIVE applies); e.g. "30m", "-1"
    "ollama_request_timeout": {"type": float, "default": 0.0},  # 0 = 900 s local (prompt processing streams nothing), 300 s cloud
    "ollama_side_model": {"type": str, "default": ""},  # optional model for summaries/chunking so they never evict the main local KV cache
    "ollama_num_predict": {"type": int, "default": 0},
    "ollama_temperature": {"type": float, "default": 0.0},
    "ollama_top_p": {"type": float, "default": 0.0},
    "ollama_top_k": {"type": int, "default": 0},
    "ollama_repeat_penalty": {"type": float, "default": 0.0},
    "ollama_seed": {"type": int, "default": 0},
    "ollama_mirostat": {"type": int, "default": 0},
    "ollama_mode": {
        "type": str,
        "default": "auto",
    },  # local | cloud | auto(legacy). Selecting ollama in the GUI sets this explicitly. "local" = OLLAMA_HOST env or localhost (never the API-key cloud auto-switch); "cloud" = ollama.com (needs ollama_api_key); "auto" preserves the pre-toggle env-driven resolution for backward compat.
    "ollama_api_key": {
        "type": str,
        "default": "",
    },  # Per-session Ollama cloud bearer token. Empty falls back to env OLLAMA_API_KEY. Ignored by a local daemon.
    "ollama_token_safety_factor": {
        "type": float,
        "default": 1.3,
    },  # Seed divisor for the compaction limit before the learned cl100k→real drift takes over. Measured drift for glm/qwen-class models is ~1.0–1.1; 1.3 leaves headroom. 1.0 disables. Read by OllamaProvider.compaction_safety_factor.
    "collation_enabled": {
        "type": bool,
        "default": True,
    },
    # Batched read results are delivered inline in the same turn unless the batch's
    # combined size exceeds this share of the model's real context window (capped at
    # collation_batch_cap_tokens). Large-context models effectively never collate, so a
    # batch of reads costs one model turn instead of a follow-up `flush` turn.
    "collation_batch_share": {
        "type": float,
        "default": 0.10,
    },
    "collation_batch_cap_tokens": {
        "type": int,
        "default": 24000,
    },
    "memory_enabled": {
        "type": bool,
        "default": True,
    },
    "durable_memory_enabled": {
        "type": bool,
        "default": True,
    },  # Automatic scoped recall from the cross-session SQLite Memory Ledger.
    "durable_memory_auto_capture": {
        "type": bool,
        "default": True,
    },  # Promote model-managed save_memory entries automatically; never prompt.
    "durable_memory_max_items": {
        "type": int,
        "default": 6,
    },
    "durable_memory_token_budget": {
        "type": int,
        "default": 1200,
    },
    "durable_memory_default_scope": {
        "type": str,
        "default": "auto",
    },  # auto resolves repository -> workspace -> personal.
    "durable_memory_show_receipts": {
        "type": bool,
        "default": True,
    },  # Compact visibility only; never an approval step.
    "memory_max_entries": {
        "type": int,
        "default": 64,
    },
    "memory_stale_after_turns": {
        # Auto-decay: at turn start, ACTIVE task-memory entries whose last
        # explicit search/save hit was >= this many turns ago are demoted to
        # STALE. Keeps the active set honest ("active" = recently mattered,
        # not "ever saved") so search_memory (active-only by default) and L3
        # injection stay high-signal instead of accumulating noise. Reversible
        # — a search hit or re-save promotes a STALE entry back to ACTIVE.
        # 0 disables decay. See BaseNoteStore.apply_staleness_decay.
        "type": int,
        "default": 12,
    },
    "progress_checkpoint_every": {
        # Periodic L2 progress checkpoint: every N iterations, fold recent
        # history into the structured conversation_summary (Progress / Key
        # decisions / Current state / Open items) WITHOUT compacting (the
        # anchor doesn't advance, entries stay in L5). This keeps L2 fresh
        # on long turns that never hit the compaction budget, so the model
        # stops re-deriving context it already gathered (the long-horizon
        # stall). 0 disables in every mode; model-maintained checkpoints
        # avoid extra periodic summary calls by default.
        # See HistoryMixin.force_progress_checkpoint.
        "type": int,
        "default": 0,
    },
    "tool_result_floor": {
        # R3/FM-8: number of trailing tool-result messages in the active
        # turn that compaction must leave verbatim, even under emergency
        # compaction with a tiny keep_recent. Prevents mid-turn compaction
        # from dropping tool results just received. Mode-aware (Fix #10):
        # loop/feature modes raise this to at least 8.
        "type": int,
        "default": 4,
    },
    "turn_fold_enabled": {
        # context_packaging_v2 P2: when a turn FINISHES, move its completed
        # tool results into the durable result store (retention action
        # 'clear', recoverable via recall()) so a finished turn no longer
        # rides verbatim into the next one. Zero summarizer cost. Skipped
        # when the result store is unavailable; protected / failed /
        # provider-signed results are never folded. Records a 'turn_fold'
        # entry in the history-shrink ledger.
        "type": bool,
        "default": True,
    },
    "turn_fold_keep_recent_results": {
        # How many of the finished turn's trailing tool results stay
        # verbatim after the fold (the model may still be reasoning about
        # them in the next turn's opening). 0 folds everything clearable.
        "type": int,
        "default": 2,
    },
    "prompt_prefix_stable": {
        # context_packaging_v2 P4: keep the system prompt byte-stable across
        # the iterations of a turn. Volatile per-iteration blocks (memory /
        # scratchpad snapshots, eviction notices, delegated work, peer
        # coordination, wall-clock) render into ONE trailing request-only
        # user message instead of the system prompt, so a saved memory or
        # the clock ticking no longer invalidates the provider prefix cache
        # for system+tools+history. False restores in-prompt rendering.
        "type": bool,
        "default": True,
    },
    "context_pressure_escalate_after": {
        # context_packaging_v2 P3: how many further turn-final iterations
        # at/over context_pressure_nudge_pct (without the summary anchor
        # moving) climb the pressure ladder one rung: L1 nudge -> L2
        # stronger nudge -> L3 harness-forced compaction (at most once per
        # episode). 0 disables escalation (single nudge, legacy behaviour).
        "type": int,
        "default": 3,
    },
    "turn_roll_enabled": {
        # context_packaging_v2 P2: after the turn-boundary fold, if the
        # FINISHED turn still projects to more than turn_keep_budget_tokens
        # (e.g. a 40-iteration turn of assistant/tool traffic), roll it
        # into the L2 conversation summary (summary_anchor advances past
        # the turn; keep_recent=1 keeps the final assistant answer live).
        # Uses the provider summarizer when available, the mechanical
        # summarizer otherwise. Ledger kind 'turn_roll'. Never touches the
        # active turn (only runs at the turn's end).
        "type": bool,
        "default": True,
    },
    "small_window_profile": {
        # auto: compact tools/schemas, no skills index and capped context files when the
        # model's real window is ≤ small_window_threshold; on/off force it.
        "type": str,
        "default": "auto",
    },
    "small_window_threshold": {"type": int, "default": 32768},
    "openai_api": {
        # auto: the Responses API for reasoning models on api.openai.com (encrypted reasoning
        # is replayed between tool-loop turns); chat / responses force one API.
        "type": str,
        "default": "auto",
    },
    "anthropic_cache_ttl": {
        # Lifetime of Anthropic prompt-cache entries: "1h" (default) keeps the cached
        # tools + system + history across pauses between turns longer than 5 minutes
        # (writes cost 2x instead of 1.25x input; reads 0.1x or less either way);
        # "5m" is the API default, cheaper for back-to-back automated work.
        "type": str,
        "default": "1h",
    },
    "openai_prompt_cache_retention": {
        # e.g. "24h" for models that support extended prompt-cache retention (before GPT-5.6;
        # never sent to GPT-5.6+, which use prompt_cache_options); empty = API default.
        "type": str,
        "default": "",
    },
    "openai_xhigh_models": {
        # OpenAI models that accept reasoning_effort "xhigh" (prefix match). /effort max|xhigh
        # sends "high" to every other reasoning model.
        "type": list,
        "default": [],
    },
    "reasoning_effort": {
        # Effort for agent turns: none | minimal | low | medium | high | xhigh | max (empty = provider default).
        # Lower = less thinking, faster turns. Anthropic effort, OpenAI reasoning_effort, Ollama think.
        "type": str,
        "default": "",
    },
    "turn_roll_deferred": {
        # Summarize a finished turn on a background thread and merge it at the next
        # turn start, instead of blocking the end of the turn on a model call.
        "type": bool,
        "default": True,
    },
    "turn_keep_budget_tokens": {
        # Projected-token budget a finished turn may keep verbatim in L5
        # before turn_roll folds it into L2.
        "type": int,
        "default": 6000,
    },
    "tool_call_arg_stub_threshold_chars": {
        # context_packaging_v2 P2: once a tool_call has been answered by a
        # tool_result, string arguments longer than this many chars
        # (write_file content, apply_diff patches, bash heredocs) are
        # replaced in the PROJECTED request by a {__stubbed__, bytes,
        # sha256, preview} stub. The tool already ran; the payload is on
        # disk / in the result store and was ~83k tokens per iteration at
        # peak. session.history keeps the original. 0 disables.
        "type": int,
        "default": 2000,
    },
    "tool_result_cache_entries": {
        # Max entries in the tool-result sidecar cache (recall() + auto-
        # recall by locator, Fix #10). Mode-aware: loop/feature modes raise
        # this to at least 256.
        "type": int,
        "default": 50,
    },
    "tool_result_cache_bytes": {
        # Max bytes in the tool-result sidecar cache (Fix #10). Mode-aware:
        # loop/feature modes raise this to at least 2 MB.
        "type": int,
        "default": 524288,
    },
    "result_store_enabled": {
        # Spec #1/#11: persist full raw tool results to disk under
        # $MUCLI_HOME/results/<run_id>/ so they survive LRU eviction and
        # session restarts and are retrievable via recall()/result_* ops. The
        # in-memory ToolResultCache is the hot layer; this is the durable
        # authoritative raw-output record (the trace stays telemetry-only).
        "type": bool,
        "default": True,
    },
    "result_store_max_bytes": {
        # Per-run byte cap for the durable result store. Oldest stored keys
        # are pruned when exceeded.
        "type": int,
        "default": 16777216,
    },
    "result_store_gc_age_days": {
        # Drop result-store run directories older than this many days on
        # session start. 0 disables GC.
        "type": int,
        "default": 7,
    },
    "tool_result_inline_budget": {
        # Spec #10: tokens of raw tool output kept inline in the model
        # context at delivery. Results at or below this stay verbatim; larger
        # results are replaced by a compact observation + stored_ref + an
        # explicit omission note. Small results cost nothing extra. The effective
        # budget is also capped at tool_result_inline_share of the model's real
        # context window, so small local models stay protected.
        "type": int,
        "default": 4000,
    },
    "tool_result_inline_share": {
        "type": float,
        "default": 0.05,
    },
    "tool_result_failure_budget": {
        # Spec #10: failures may use more space than routine success. Budget
        # applied to error results before they fall back to observation.
        "type": int,
        "default": 1024,
    },
    "tool_inline_budgets": {
        # Per-tool overrides for `tool_result_inline_budget`, keyed by tool
        # name. e.g. {"bash": 400}. Absent tools use the global default.
        # Subagent reports and memory-search hits are model-facing prose the
        # parent must act on, so they get a roomier default than raw logs.
        "type": dict,
        "default": {"read_file": 8000, "get_chunk": 8000, "await_subagent": 4000, "search_memory": 4000},
    },
    "read_dedup_enabled": {
        # Spec #7: content-hash freshness + range memo so re-reading an
        # unchanged file (or an already-supplied range) returns a compact
        # "already supplied, recall key K" note instead of re-injecting.
        "type": bool,
        "default": True,
    },
    "cache_content_hash_enabled": {
        # Spec #7/#8: validate cached read results by content hash in
        # addition to mtime+size, closing the same-size/same-mtime blind spot.
        "type": bool,
        "default": True,
    },
    "lazy_tools_enabled": {
        # Spec #9: phased tool exposure. When True, only tools whose `phase`
        # is in `active_tool_phases` appear in the schema, shrinking per-request
        # schema bytes. Mode-owned phases are always exclusive to their active
        # mode, even when this setting is False.
        "type": bool,
        "default": True,
    },
    "active_tool_phases": {
        # Spec #9: phases always exposed when `lazy_tools_enabled` is True.
        # "core" covers the always-on read/write/memory/session/agent tools.
        # Strategy modes replace any persisted mode-owned phases with their own
        # declared AGENT_MODE_METADATA.tool_phases at request time, so switching
        # modes cannot leak another mode's tools.
        "type": list,
        "default": ["core"],
    },
    "efficiency_metrics_enabled": {
        # Spec #12: collect per-turn efficiency metrics (compression, cache
        # rates, retrieval rate, tool-output share) and emit them in the
        # `turn_end` trace record + the /memory panel.
        "type": bool,
        "default": True,
    },
    "recoverage_stall_threshold": {
        # Context-gathering stall detection (Fix #12): number of consecutive
        # iterations that re-read files already read this turn WITHOUT a
        # concrete change (write/bash/spawn) before a "stop gathering, act"
        # re-orient nudge is injected. 0 disables. See loop_detection.
        "type": int,
        "default": 4,
    },
    "emergency_keep_recent": {
        # Trailing messages kept verbatim by EMERGENCY compaction
        # (pre-flight context check). Smaller than the normal
        # `compactor_keep_recent` so it reclaims budget fast; the
        # `tool_result_floor` still protects recent tool results.
        "type": int,
        "default": 2,
    },
    "memory_summary_limit": {
        "type": int,
        "default": 8,
    },
    "scratchpad_enabled": {
        "type": bool,
        "default": True,
    },
    "scratchpad_max_entries": {
        "type": int,
        "default": 24,
    },
    "scratchpad_persist_across_turns": {
        "type": bool,
        "default": False,
    },
    # Modes v2 gates (documentation/plans/modes_v2.md §8.1-8.2): warn by default.
    "debug_require_repro": {
        "type": bool,
        "default": False,
    },  # Debug: block edits while the current case has no reproducer (default: warn once per turn)
    "feature_require_approval": {
        "type": bool,
        "default": False,
    },  # Feature: block edits until the user approves the plan (default: warn once per turn)
    "verify_nudge": {
        "type": bool,
        "default": True,
    },  # Default mode: one extra iteration when files changed without a passing check after the edit
    "orch_auto_continue": {
        "type": bool,
        "default": True,
    },  # Orchestration sessions: resume the chat when a run it proposed finishes
    "teacher_watcher": {
        "type": bool,
        "default": False,
    },  # Teacher: legacy LLM lecture watcher (teach_check / assess_response replace it)
    "context_token_limit": {
        # Global cap on total prompt tokens (sum of all 7 layers + history).
        # The compactor reserves headroom for non-L5 layers before deciding
        # how much room L5 (conversation history) gets.
        #
        # When this changes via /set, reratio_layer_budgets() recomputes
        # the per-layer char budgets proportionally (see
        # compute_layer_char_budgets above).
        "type": int,
        "default": _DEFAULT_CONTEXT_TOKEN_LIMIT,
    },
    "context_trim_threshold": {
        # Fraction of the global cap above which compaction kicks in.
        "type": float,
        "default": 0.85,
    },
    "response_token_reserve": {
        # Tokens to leave free in the compaction budget for the model's
        # response. With smaller-context providers (Ollama 8k/32k), packing
        # input up to the ceiling means there's no room for output.
        "type": int,
        "default": 4096,
    },
    # ----- Provider retry (transient failures: 429s, timeouts, 5xx) -----
    "provider_retry_max_total_wait_seconds": {
        # Cumulative time budget across ALL retries for a single
        # provider call. Once retry sleeps add up to this, the next
        # transient failure raises instead of retrying — bounds the
        # worst-case time the agent stalls on a flapping endpoint.
        "type": float,
        "default": 120.0,
    },
    "provider_retry_base_delay": {
        # Initial sleep after the first transient failure. Each
        # subsequent attempt doubles this (jittered).
        "type": float,
        "default": 0.4,
    },
    "provider_retry_max_delay": {
        # Cap on any *single* sleep — the backoff stops doubling here.
        "type": float,
        "default": 30.0,
    },
    "provider_max_retries": {
        # Safety belt — even with budget left, abort after this many
        # transient failures. Catches pathological cases (e.g. retry
        # math bug, persistent 429 with 0s suggested wait).
        "type": int,
        "default": 30,
    },
    # ----- LAYER 1A — Workspace context files -----
    "context_files_max_chars": {
        # Total budget for LAYER 1A (AGENTS.md, CLAUDE.md, MUCLI.md, .mu/CONTEXT.md).
        # Files included whole or skipped — no truncation. 0 disables L1A.
        # Scales with context_token_limit via compute_layer_char_budgets.
        "type": int,
        "default": _LAYER_CHAR_DEFAULTS["context_files_max_chars"],
    },
    # ----- LAYER 1B — Installed skills -----
    "skills_max_chars": {
        # Total budget for the AVAILABLE SKILLS block injected as LAYER 1B
        # of the system prompt. 0 disables skills entirely.
        # Scales with context_token_limit via compute_layer_char_budgets.
        "type": int,
        "default": _LAYER_CHAR_DEFAULTS["skills_max_chars"],
    },
    # ----- LAYER 2 — Conversation summary -----
    "conversation_summary_char_limit": {
        # Char budget for LAYER 2 (rolling summary of older history).
        # Clipped from the tail when exceeded so the most recent summary
        # batches survive. Scales with context_token_limit.
        "type": int,
        "default": _LAYER_CHAR_DEFAULTS["conversation_summary_char_limit"],
    },
    # ----- LAYER 3 — Active goal context -----
    "active_goal_context_char_limit": {
        # Char budget for LAYER 3 (feature/task status + scratchpad snapshot).
        # Scales with context_token_limit via compute_layer_char_budgets.
        "type": int,
        "default": _LAYER_CHAR_DEFAULTS["active_goal_context_char_limit"],
    },
    # ----- LAYER 4B — Retrieved snippets -----
    "retrieval_context_char_limit": {
        # Char budget for LAYER 4B (semantic-retrieval snippets injected
        # for the current turn). Scales with context_token_limit.
        "type": int,
        "default": _LAYER_CHAR_DEFAULTS["retrieval_context_char_limit"],
    },
    "retrieval_top_k": {
        # Number of semantic-retrieval hits to include in LAYER 4B.
        "type": int,
        "default": 5,
    },
    "skills_mode": {
        # "compact" (default): name + description only;
        # bodies auto-expand when a skill's regex trigger matches the
        # latest user message, or on `invoke_skill(name)`. "full" reverts
        # to v1 behavior — every skill body is inlined up to the budget.
        "type": str,
        "default": "compact",
    },
    "structured_tool_results": {
        "type": bool,
        "default": True,
    },
    # Loop mode state variables
    "loop_detection_enabled": {
        "type": bool,
        "default": True,
    },
    "loop_detection_repeat_threshold": {
        "type": int,
        "default": 5,
    },
    # ----- Sub-agent async orchestrator -----
    # session_role drives LAYER 3B (Agent Role) injection in
    # inject_hierarchical_context. Default "" => single-agent sessions skip
    # LAYER 3B (backward compatible). Stamped "parent" on a session when it
    # first spawns a child; "child" on every spawned sub-agent session.
    "session_role": {
        "type": str,
        "default": "",
    },
    # Increments per spawn level; cap at MAX_SUBAGENT_DEPTH (2). Children
    # read this to render depth-aware LAYER 3B sub-agent guidance.
    "subagent_depth": {
        "type": int,
        "default": 0,
    },
    # Links a child to its parent's task_id for polling/correlation.
    "subagent_parent_task_id": {
        "type": str,
        "default": "",
    },
    # Consecutive same-tool+same-args calls before the lifecycle manager
    # flags the sub-agent as stuck (surfaced to parent via poll; not an
    # auto-kill).
    "subagent_stuck_threshold": {
        "type": int,
        "default": 3,
    },
    # Consecutive no-novel-output calls before the lifecycle manager flags
    # the sub-agent as stalled (surfaced to parent via poll; not an
    # auto-kill).
    "subagent_stall_threshold": {
        "type": int,
        "default": 5,
    },
    # Hard runtime limit (seconds) before the lifecycle watchdog auto-kills
    # a runaway sub-agent. The ONLY auto-kill path; stuck/stall are advisory.
    "subagent_max_runtime_seconds": {
        "type": int,
        "default": 300,
    },
    # Master switch for lifecycle signal tracking + stuck/stall detection.
    # When False, sub-agents still run async but no heuristics fire.
    "subagent_lifecycle_enabled": {
        "type": bool,
        "default": True,
    },
    # Default model for spawned sub-agents. Empty (default) => the child
    # inherits the parent's model (via clone_for_child). Set to any model
    # installed on the active provider to run children on a different model
    # (e.g. a smaller/faster one for cheap side quests). An uninstalled value
    # falls back to the parent model with a warning rather than crashing the
    # child's first generate() — this is the fix for "Ollama model 'sonnet-3.5'
    # is not installed" errors, which happened when the agent passed a
    # hallucinated model name to spawn_agent. A spawn_agent `model` arg, when
    # valid+installed, overrides this for that one child. Set via /set or the
    # GUI composer settings (dynamic model picker).
    "subagent_model": {
        "type": str,
        "default": "",
    },
    # ----- TTS / STT (audio) -----
    "tts_enabled": {
        "type": bool,
        "default": True,
    },  # Show TTS speak button on assistant messages
    "tts_voice": {
        "type": str,
        "default": "af_heart",
    },  # Kokoro voice name (af_heart, af_sky, etc.)
    "stt_enabled": {
        "type": bool,
        "default": True,
    },  # Show mic button in composer for voice input
    "stt_model": {
        "type": str,
        "default": "base",
    },  # faster-whisper model size: tiny|base|small|medium|large-v3
}

DEFAULT_VARIABLES = {k: v["default"] for k, v in VARIABLE_SCHEMA.items()}


def validate_and_cast(key, value):
    """Validates and casts a value based on the schema."""
    if key not in VARIABLE_SCHEMA:
        # For unknown variables, we default to string
        return value

    target_type = VARIABLE_SCHEMA[key]["type"]

    if target_type == bool:
        if isinstance(value, bool):
            return value
        v = str(value).lower()
        if v in ["true", "1", "t", "y", "yes", "on"]:
            return True
        if v in ["false", "0", "f", "n", "no", "off"]:
            return False
        raise ValueError(f"Invalid boolean value for {key}: {value}")

    if target_type == int:
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid integer value for {key}: {value}")

    if target_type == float:
        try:
            return float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid float value for {key}: {value}")

    if target_type == list:
        # GUI/mobile/`/set` send text: a JSON list or comma-separated items.
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value or "").strip()
        if text.startswith("["):
            import json as _json
            try:
                parsed = _json.loads(text)
            except ValueError:
                raise ValueError(f"Invalid list value for {key}: {value}")
            return [str(v).strip() for v in parsed if str(v).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    return str(value)


# --- System Prompts & Nudges ---
AGENTIC_SYSTEM_BASE = """You are an autonomous AI Software Engineer.

Reasoning: high

## Grammar
Respond like smart caveman. Cut articles, filler, pleasantries. Keep all technical substance.
- Drop articles (a, an, the)
- Drop filler (just, really, basically, actually, simply)
- Drop pleasantries (sure, certainly, of course, happy to)
- Short synonyms (big not extensive, fix not "implement a solution for")
- No hedging (skip "it might be worth considering")
- Fragments fine. No need full sentences.
- Technical terms stay exactly. "Polymorphism" stays "polymorphism"
- Code blocks unchanged. Caveman speak around code, not in code.
- Error messages quoted exact. Caveman only for explanation.
- **Thinking/reasoning tokens: caveman mode too.** Internal thinking must be terse. Drop filler, hedging, narration. "Need check X" not "I should probably investigate X to understand whether..." Keep technical substance — symbols, file paths, function names, logic. Compress reasoning to bullet points or fragments. Saves thinking token budget significantly.

## Pattern
```
[thing] [action] [reason]. [next step]
```

TOOL SURFACE:
- Filesystem: `read_file`, `write_file`, `apply_diff`, `search_and_replace_file`, `list_dir`, `get_chunk`.
- Search: `search_for_string` (exact substring, line numbers), `search_references` (context lines), `retrieve_relevant_context` (semantic index, lexical+symbol+recency).
- Shell: `bash` covers everything else — git ops, make, grep, find, curl, anything not surfaced as a dedicated tool.
- Research: `web_search`, `arxiv_search`, `doi_resolve`, `reddit_search`, `stackoverflow_search`, `hackernews_search` (results are unread candidates), `url_grounding`, `read_document` (PDFs); research mode adds `research_fetch` and the evidence ledger (`research_question`, `record_claim`, `research_status`, `research_report`).
- Memory: `save_memory` / `search_memory` / `list_memory` manage working memory and the scoped cross-session Memory Ledger; `manage_durable_memory` pins, archives, restores or marks durable knowledge for review without an approval round-trip; `save_scratchpad` / `search_scratchpad` / `list_scratchpad` / `clear_scratchpad` are per-turn.
- Self-tracking: `todo_write(content, status)`, `todo_set_status(id, status)`, `todo_list(status?)`, `todo_delete(id)`, `todo_clear(status?)` for per-session task plans the user can see and you can prune.
- Context self-management: `context_status` reports projected request usage, result_ids and clearing restrictions. Use `clear_tool_results(result_ids, action='keep')` for evidence that must remain verbatim, and action='clear' to remove selected completed payloads with zero summarizer calls. Originals stay recallable. action='restore' releases a prior decision. Save a structured checkpoint (progress, decisions, resume, exceptions, next_action, constraints) as part of clearing or `compact`. Only use `compact(focus, through_index, preserve_result_ids, checkpoint)` when completed history itself needs summarizing; through_index bounds eligible history and later work stays active. The harness is a provider-window fallback, not a substitute for your task-aware decisions.
- Sub-agents: `spawn_agent(task, tools?, max_iterations?, model?)` for focused side-quests (research, large refactors) so the parent context stays clean. Sub-agents inherit folder context and run YOLO; depth-capped to 2 levels.
- Workflow: `batch_job` to bundle related calls, `flush` to drain the collation buffer when a result was parked there, `raise_blocker` to pause for user input.
- Visual output: `publish_visualization(name, html|file_path, title?, height?)` publishes a persistent interactive HTML view into web/mobile chat and a browser link in TUI. Use it proactively when a visual explains data or structure better than prose; do not wait for the user to nudge a tool call.
- Goal pinning: `set_session_goal(goal, clear=False)` pins the user's top-level task into L3 of the system prompt for the CURRENT turn. Keeps you on track through long multi-iteration runs where L2 (conversation summary) gets compacted. **Auto-clears at end of turn** — each new user message starts fresh; re-pin at the top of the next turn if it's also multi-step. Don't carry stale goals into unrelated requests. The user can also `/goal <text>` manually. If the pinned goal mid-turn diverges from the user's current ask, pause and confirm before overwriting.
- **Clarification** — `ask_user_choice(question, options, multi_select=False, allow_other=False, description="")` — multiple-choice picker. 2-8 options. `multi_select=true` for select-all. `allow_other=true` for free-form fallback. Result: `{selected, other_text, cancelled}`.

WHEN TO USE SUBAGENTS:
- When a complex task can be broken into independent, smaller tasks.
- When parallel processing (running tasks simultaneously) is necessary.
- When you need to contain errors from one specific task from impacting the whole workflow.

GENERAL RULES:
0. **Clarify before you act.** For non-trivial requests where intent isn't clear, use `ask_user_choice` to lock down choices before writing code or running shell.
0a. **Tag your claims by confidence.** Every claim about the system or its behavior gets one of: `[verified]` (you ran it, observed the result), `[inferred]` (you read code, concluded by analysis), `[guess]` (extrapolation, not certain). Self-evident descriptions of code you just wrote don't need tags. Untagged claims read as `[verified]` — false confidence corrodes the working relationship. When in doubt, downgrade.
0d. **Explain surprising moves inline.** When you touch a file, run a command, or change a system the user did NOT explicitly name in their request, prefix the action with one short line: `(why: <reason>)`. Surprise without explanation is bad collaboration. This includes: editing files adjacent to the named target, running shell beyond the obvious next command, installing dependencies, modifying config.
0e. **Flag disagreement, don't silently overwrite.** If your observation diverges from the user's description (they say "this function does X" but reading shows Y; they say "this is slow" but profiling shows no hotspot), surface it in one line: `I see X. You said Y. Which matches reality?` Then wait. Don't paper over either model — the divergence itself is the signal.
0f. **No dead code or speculative compatibility.** Remove obsolete branches, flags, helpers, tests, and docs when replacing behavior. Do not retain backward-compatibility shims unless the user explicitly requires a compatibility contract.
1. Never guess file paths. If a tool returns "File not found", use `list_dir` or `search_for_string` to find the correct path.
2. Always provide the full 'filename' argument for tools.
3. When using `apply_diff`, you MUST provide a standard unified diff.
   - File headers: `--- filename` and `+++ filename`.
   - Hunk headers with line numbers: `@@ -start,len +start,len @@`.
   - Context lines start with a space. Deletions start with `-`. Additions start with `+`.
   - DO NOT use markers like `*** Begin Patch` or `@@` without line numbers.
   - If unsure of line numbers, use `read_file` first or `write_file` to overwrite the whole file.
4. PREFER `search_and_replace_file` for targeted code modifications. Use `apply_diff` only for complex multi-file changes or when search-replace is insufficient.
   - Include 3-5 lines of context in your search string to ensure uniqueness.
   - For multiple matches, use `expected_count` or provide more context.
   - Use `dry_run=True` to preview changes before applying.
5. Multiple tool calls in a single turn execute concurrently. Issue them together when the calls are independent reads (e.g. read 3 files at once). Use `batch_job` only when you need an atomic bundle with shared approval.
6. Read-only tool results come back in the same turn. Only a very large batch is parked in the collation buffer; when a result says so, call `flush` for what you need. Do not repeatedly ask for information already in active context.
7. YOU OWN YOUR CONTEXT. When the context grows large (long tasks, big gathers), use `context_status` to inspect projected usage and result_ids. Keep relevant evidence with `clear_tool_results(action='keep')`; clear only selected completed results with action='clear' before paying for a summary. Preserve goal/filter rules, exact resume cursor, confirmed counts and side effects, unresolved exceptions, constraints and next action in its checkpoint. `compact` accepts that checkpoint, preserve_result_ids and a through_index boundary when older conversation also needs summarizing. Large relevant context may remain active. The harness clears eligible old results and then summarizes only as a fallback near the effective provider budget. Retrieve archived results; never repeat writes merely because their old output is out of context.

SELF-MANAGEMENT:
- **Todo ledger is persistent and yours — keep it honest.** The `todo` ledger survives across turns (it is NOT cleared at turn start like ephemeral scratchpad notes). At the start of a non-trivial task, `todo_write` the plan. When a task is done → `todo_set_status(completed)`. When abandoned or no longer relevant → `todo_delete(id)` (or `todo_clear('completed')` to prune all finished items in one call). When the user's ask shifts mid-task, RECONCILE the ledger BEFORE starting new work: drop what no longer applies, repromote what does. Do not leave stale `in_progress` items lying around — that is the "clean up the stale task list" move, do it proactively.
- **Memory plane is model-controlled and non-blocking.** When a reusable non-secret fact, decision, preference, convention, procedure, or lesson emerges, call `save_memory` autonomously. Do not ask permission for routine memory management. Mu-CLI surfaces write/recall receipts and lets the user inspect, edit, archive, pin, or forget afterward. Never store credentials or secret material.
- **Promote durable, drop ephemeral.** When a fact/decision/finding will matter beyond this turn, `save_memory` it AND clear the scratchpad note it came from. The call automatically promotes eligible content into the scoped cross-session Memory Ledger; scratchpad is scratch. Use the memory lifecycle: `supersede_memory` when a finding changes, `retire_memory` when done, `archive_memory` when no longer relevant, `retire_thread(topic, reason)` to drop a whole abandoned thread in one call.
- **Supersede, don't sibling.** When a finding/decision is UPDATED, `supersede_memory(old_id, new_id)` the old entry — do NOT save a sibling. Five progressively-better versions of the same fact, all `active`, is the fastest way to drown signal in noise. One source of truth per fact; the old one goes `superseded` (off the active set) the moment the new one lands.
- **Decay keeps the active set honest — reconcile, don't accumulate.** Memory entries not searched or re-saved in the last `memory_stale_after_turns` turns auto-decay from `active` to `stale` at turn start, so "active" means recently mattered, not ever saved. A search hit or re-save promotes a stale entry back to `active` automatically — decay is reversible through use, it never loses what you're actually using. Your job is to FINISH the job decay starts: when `context_status` shows `stale_memory_count > 0`, `archive_memory` or `retire_thread` those entries before adding new state; when `stale_todos > 0`, `todo_clear('completed')`. Net metadata should stay flat or shrink over a turn — if you only ever add and never retire, you are accumulating the exact rot that confuses you about what's important. Watch `memory_pressure_pct`: curate BEFORE the cap forces a silent eviction that drops a high-value entry to make room for trivia.
- **Watch your own context fill.** Call `context_status` before big gathers and when a turn feels long. It returns per-layer token fill plus `l2_stale_vs_l5`, `uncheckpointed_entries`, `stale_memory_count`, `stale_todos`, and `memory_pressure_pct`. If L2 is stale relative to L5 progress, call `checkpoint_progress` to fold recent work into the summary yourself — don't wait for the budget to force it.
- **Recognize your own stall.** If you've re-read the same files two iterations running without a concrete change (no write/bash/spawn between), STOP gathering: reconcile the ledger, decide the next concrete action, and act. Re-reading is not progress.
- **You write the handoffs.** When you near the iteration cap or the user ends the turn mid-work, leave a consolidation in memory (`kind=consolidation` via `save_memory`, or just `save_memory` with the what's-done / what-remains / blocker): the next turn starts from your handoff, not from re-derivation.
8. Use the scratchpad for observations and short-term plans on long tasks, and task memory for durable facts, decisions and verified findings — concise and high-value. Call `search_memory` when the task may repeat earlier work; skip it for self-contained requests.
9. For long-horizon work, maintain `todo_*` as a visible progress ledger so the user can see what you're doing — and prune it (rule 7) so it reflects current reality, not history.
10. For focused side-quests that would consume large parent context (deep research, multi-file refactors), call `spawn_agent` with a tight `tools` whitelist. The child returns a clean summary; parent stays uncluttered.
11. Tool results may include structured summaries. Prefer the structured fields and summaries over raw blobs.
12. If plan mode is active, write-side tools (`write_file`, `apply_diff`, `bash`, `spawn_agent`, feature mutators) are blocked. Gather context, propose a plan, and tell the user to `/plan off` when they're ready for execution.
"""

# Mode prompts have one source: the bundled templates under
# mu/prompts/templates/<mode>.md (frontmatter + body). This mapping is
# generated from them as the last-resort fallback for the prompt library.
AGENT_MODE_NAMES = ("default", "debug", "feature", "research", "loop", "security", "teacher")


def _load_mode_templates() -> dict:
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "mu", "prompts", "templates")
    out = {}
    for name in AGENT_MODE_NAMES:
        try:
            with open(os.path.join(root, f"{name}.md"), "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            out[name] = ""
            continue
        body = raw
        if raw.lstrip().startswith("---"):
            rest = raw.lstrip()[3:]
            end = rest.find("\n---")
            if end != -1:
                body = rest[end + 4:]
        out[name] = body.strip()
    return out


AGENTIC_MODES = _load_mode_templates()

AGENT_MODE_METADATA = {
    "default": {
        "description": "General coding and codebase assistance.",
        "documentation": "documentation/default_mode.md",
        "display_name": "Default Mode",
    },
    "debug": {
        "description": "Root-cause analysis and targeted debugging workflow.",
        "documentation": "documentation/debug_mode.md",
        "display_name": "Debug Mode",
        "tool_phases": ["debug"],
    },
    "feature": {
        "description": "Phased Feature Plan Engine with approval, blockers, and review.",
        "documentation": "documentation/feature_plan_engine.md",
        "display_name": "Feature Mode",
        "tool_phases": ["feature"],
    },
    "research": {
        "description": "Exploration and explanation mode for understanding systems.",
        "documentation": "documentation/research_mode.md",
        "display_name": "Research Mode",
        "tool_phases": ["research"],
    },
    "loop": {
        "description": "Long-horizon autonomous loop with ongoing timeline updates.",
        "documentation": "documentation/loop_mode.md",
        "display_name": "Loop Mode",
        "tool_phases": ["loop"],
    },
    "security": {
        "description": (
            "Security audit engine: every claim is gated on a verified PoC + a "
            "verified patch — no unverified findings."
        ),
        "documentation": "documentation/security_mode.md",
        "display_name": "Security Mode",
        "tool_phases": ["security"],
    },
    "teacher": {
        "description": (
            "Structured course engine — diagnostic, curriculum, per-lesson "
            "assignment/grade loop with verifiable exit criteria. Supports "
            "code, quiz, and socratic-dialog assignment kinds."
        ),
        "documentation": "documentation/teacher_mode.md",
        "display_name": "Teacher Mode",
        # research: lessons read and cite sources through the research ledger.
        "tool_phases": ["teacher", "research"],
    },
}

# GUI-only view panels — read-only visualization surfaces exposed through
# the GUI "tools" menu. These are NOT agent modes: they never appear in the
# composer mode picker, `/mode`, the splash banner, or `/set agent_mode`
# autocomplete, and `POST /api/modes/{name}` rejects them. They drive no
# system-prompt workflow text. Each entry: name, display_name, description.
GUI_VIEW_PANELS = [
    {
        "name": "threads",
        "display_name": "Coordination",
        "description": (
            "Live peer-thread roster, path ownership, messages, conflicts, "
            "and the durable coordination audit."
        ),
    },
    {
        "name": "history",
        "display_name": "History",
        "description": (
            "Searchable conversation history (keyword, role, and "
            "tool-name filters)."
        ),
    },
    {
        "name": "memory",
        "display_name": "Memory Center",
        "description": (
            "Auditable cross-session knowledge, recall receipts and the live "
            "Context Map showing exactly what reaches the model."
        ),
    },
    {
        "name": "systemPrompts",
        "display_name": "System Prompts",
        "description": (
            "Edit the base + per-mode system-prompt templates "
            "(view, edit, save, reload, init, reset)."
        ),
    },
    {
        "name": "files",
        "display_name": "Files",
        "description": (
            "Navigate and edit the session's workspace files — a tree, a "
            "code editor (CodeMirror), and create / rename / delete. "
            "Requires an attached workspace."
        ),
        # Unlike the other view panels, Files only makes sense with a
        # workspace attached. modes.py honors this to disable the entry
        # (and the tools button) when no folder is attached.
        "needs_workspace": True,
    },
    {
        "name": "artifacts",
        "display_name": "Artifacts",
        "description": (
            "Download, inspect, refresh, and remove deliverables published "
            "by the agent for the active session."
        ),
    },
    {
        "name": "shell",
        "display_name": "Shell",
        "description": (
            "Interactive shell into the session's attached container. "
            "Send commands and see output in real time via WebSocket. "
            "Requires a container session."
        ),
        # Unlike the other view panels, Shell only makes sense with a
        # container-backed session. modes.py honors this to disable the
        # entry (and the tools button) when no container is attached.
        "needs_container": True,
    },
    {
        "name": "trace",
        "display_name": "Trace Analyzer",
        "description": (
            "Per-run visualization of context growth, tokenizer drift, "
            "compaction/nudge/tool timelines, redundant reads, subagents, "
            "and memory — the data for harness-performance decisions. "
            "Opens in a new tab."
        ),
        # External full-page route (not an in-page panel). The tools
        # dropdown renders external views as a new-tab link to `route`
        # instead of calling setView.
        "external": True,
        "route": "/trace",
        # Session-scoped: the tools dropdown appends ?session=<current session>
        # so the analyzer opens on this session's combined run view, not a
        # global run picker.
        "route_session": True,
    },
]

NUDGE_EMPTY_RESPONSE = "You have completed your tool executions but provided no textual response. Please provide a clear, textual summary of your findings or a final answer to the user."

NUDGE_EMPTY_RESPONSE_CHILD = (
    "You have completed your tool executions but provided no textual response. "
    "Return a concise summary of your findings to the parent orchestrator now — "
    "partial results are valuable. Do not wait for further input."
)
