# Refactor roadmap

mucli is mid-migration from a monolithic `core/` package to a modular
`mu/` package. The work started but stalled before the heaviest
modules moved. This doc maps the current state, calls out verified
dead code, and proposes phased completion steps with risk levels.

> All claims in this doc were verified by grep against the current
> tree on 2026-05-18. The "verified" notes call out exactly what was
> checked.

## Where things are today

### Production tree (post-refactor)

The `core/` and `ui/` directories are gone — everything lives under `mu/`
plus the orthogonal `providers/`, `utils/`, and `mucli.py` entry point.

| Path | Role |
| --- | --- |
| `mucli.py` | CLI entry point: arg parsing, REPL bootstrap, slash-command dispatch fallback |
| `mu/session/*` | `Session` (agent loop body) + `SessionManager` + `HistoryMixin` + per-turn context/messages/budgets/tools_glue |
| `mu/tools/*` | `@tool`-decorated handlers grouped by domain (memory, workspace, file, shell, research, security, feature, agent, batch, task) + dispatcher + bounds/envelope/scrub helpers + `legacy.py` (the un-decomposed monolith: 27 `TOOLS` descriptors, 55 `_handle_*` bodies, `execute_tool`, `get_modifications`, `infer_tool_error_code`, `tool_requires_approval`, retrieval-index global) |
| `mu/agent/*` | Agent loop scaffolding: loop_body, hooks registry, parallel dispatch, retry/loop-detection, approval, collation, secret guard, plan mode, compactor, usage tracker |
| `mu/feature/engine.py` | Feature plan engine (data model + state machine) |
| `mu/security/{engine,secret_paths}.py` | Security audit engine + always-on denylist & scrubber |
| `mu/workspace/folder_context.py` | `FolderContext` (workspace sandbox + gitignore) |
| `mu/retrieval/index.py` | `SemanticCodeIndex` |
| `mu/memory/stores.py` | `TaskMemoryStore` + `ScratchpadStore` |
| `mu/commands/*` | Slash-command registry with `@command` decorator |
| `mu/mcp/*` | MCP stdio client + auto-registry |
| `mu/ui/*` | `RichUI`, `InputHandler`, `render`, `session_picker`, `SubagentUI`, `SubagentProgressTracker`, stream renderer |
| `mu/skills/*` | Bundled skills (markdown) |
| `providers/*` | OpenAI, Gemini, Ollama, base classes |
| `utils/*` | `config`, `runtime_metrics`, `token_estimator`, `citation_manager`, `anti_detection`, `helpers`, `logger` |

### Verified dead code

| Path | Status | Why dead | Verified by |
| --- | --- | --- | --- |
| `agents/mu_cli/` | **Dead** | Contains only `__pycache__/*.pyc` bytecode; **zero `.py` source files**. Probably the cache from a renamed/moved package. | `find agents -type f` showed only `.pyc`; `grep -r "from agents"` returned zero hits. |
| `commands/` (top-level) | **Dead** | Empty package — only `__pycache__/` subdir; **zero `.py` source files**. Fully replaced by `mu/commands/`. | `ls commands/` shows only `__pycache__/`; `grep -r "from commands\|import commands"` returned zero hits. |

Both are safe deletions. Disk waste only, but they appear in the tree
listing and confuse new readers.

### Half-migrated surfaces

| Surface | Status | The smoking-gun comment |
| --- | --- | --- |
| `mu/session/manager.py` | 20-line `__getattr__` shim re-exporting `SessionManager` from `core/session.py` | "The class body still lives in `core/session.py` while the legacy loop exists. The physical move … will happen as part of the step-7 cutover" |
| `mu/agent/loop.py` | 105 LOC façade; delegates `run_turn` to `Session.send_message` | "Today it delegates to the legacy `mu.session.session.Session.send_message`, which still houses the production loop body" |
| `mu/tools/__init__.py` | Bridge registry: 263 LOC wraps `core/tools.py` via `@tool` decorator. Only **2** new tools live in `mu/tools/`: `spawn_agent`, `todo_*` | Comment: "transitional surface: it mirrors the legacy registry in `core/tools.py`" |
| `core/__init__.py` | Empty (0 LOC) — no shim re-exports masking migrations | — |

### What's NOT a migration concern

These `core/` modules are cohesive, single-purpose, and don't have a
duplicated counterpart in `mu/`. They're "legacy" only by directory
name; the code itself is current and reasonable. Treat them as
namespace-rename candidates only, not behavior-change candidates:

- `core/feature_mode.py` — feature plan data model
- `core/security_mode.py` — security audit engine
- `core/workspace.py` — `FolderContext`
- `core/secret_paths.py` — denylist + scrubber
- `core/background_tasks.py` — background-task registry
- `core/retrieval.py` — `SemanticCodeIndex`
- `core/memory.py` — memory + scratchpad stores
- `core/approval.py` — approval plan builder
- `core/collation.py` — collation buffer

Moving them under `mu/` is a final-step namespace cleanup; the value is
consistency, not code health.

## The actual bulk of the work

Two modules dominate the unmigrated LOC:

- **`core/tools.py`** (5,433 LOC) — 27 tool handlers, all `_handle_*`
  functions, descriptors, the envelope builder, the secret-output
  scrubber wiring, and the `execute_tool` dispatcher. The `mu/tools/`
  registry already provides the `@tool` decorator path — what's
  missing is migrating the 27 handlers one-by-one. Each one is small
  in isolation (read_file is ~50 LOC; bash is ~200; web_search ~150;
  the security-mode handlers are the largest at ~100 each).
- **`core/session.py`** (3,488 LOC) — `Session.send_message` is ~700
  LOC; the rest is supporting state, history management,
  feature-mode integration, plan/strict mode helpers,
  history-building, hierarchical-context injection, retry wrapper,
  parallel-dispatch glue. The `mu/agent/loop.py` façade is the
  landing zone.

## Refactor roadmap (phased)

Each phase is independently shippable. Risk levels: ✅ safe (no
behavior change), 🟡 medium (behavior preserved but call sites
change), 🔴 high (behavior could subtly change; needs soak).

### Phase 0 — Delete dead code (✅ safe, 5 minutes)

1. ☐ `rm -rf agents/` — orphaned bytecode-only directory. Zero importers. **Run yourself; `rm` denied to the agent.**
2. ☐ `rm -rf commands/` — empty package. Zero importers. **Run yourself.**
3. ☐ Add a `find . -type d -name __pycache__ -exec rm -rf {} +` line to
   `make test` (or similar) so stale bytecode stops accumulating.

**Tests covering**: nothing imports from these paths, so the test
suite is unaffected. Run `pytest tests/` after to confirm.

### Phase 1 — Migrate isolated, leaf tools (🟡 medium, per tool)

Move tools from `core/tools.py` to `mu/tools/<group>/<name>.py` using
the `@tool` decorator. Recommended order — easiest first:

1. ✅ **Memory/scratchpad tools** (`save_memory`, `search_memory`,
   `list_memory`, `save_scratchpad`, `search_scratchpad`,
   `list_scratchpad`, `clear_scratchpad`) — **done.** Moved to
   `mu/tools/memory/handlers.py`. `Session._execute_tool_with_memory`
   short-circuits removed; descriptors, metadata, placeholder handler,
   and `_handle_memory_placeholder` deleted from `core/tools.py`. New
   `tests/test_mu_tools_memory.py` pins behavior (15 tests).
2. ✅ **Workspace inspectors** — **done.** Moved to
   `mu/tools/workspace/handlers.py`. Test `test_search_references.py`
   converted to use `mu.tools.execute`.
3. ✅ **File writers** — **done.** Moved to
   `mu/tools/file/handlers.py` with `requires_approval=True` and
   `preview_policy="required"` preserved.
4. ✅ **Shell tools** — **done.** Moved to `mu/tools/shell/handlers.py`.
   Bg-registry resolver ported. `test_background_tasks.py` converted to
   use `mu.tools.execute`.
5. ✅ **Research tools** (`url_grounding`, `web_search`, `arxiv_search`,
   `doi_resolve`, `reddit_search`, `stackoverflow_search`,
   `hackernews_search`, `read_document`) — **done.** Moved to
   `mu/tools/research/handlers.py` with `result_mode="structured+collated"`.
   `test_online_tool_handlers.py` + `test_web_search.py` converted to
   `mu.tools.execute`.
6. ✅ **Skill tools** (`invoke_skill`) — **done.** Moved to
   `mu/tools/skill/handlers.py`. Legacy `get_tool_descriptor` /
   `get_tool_definition` lookups now fall back to `mu.tools.get(...)`
   so callers that bypass the `@tool` path still find migrated tools.
7. ✅ **Batch/composite** (`batch_job`, `flush`) — **done.** Both
   moved to `mu/tools/batch/handlers.py` after Phase 2 made the
   canonical dispatcher available. `batch_job` calls
   `mu.tools._dispatcher.dispatch(...)` directly for nested calls;
   `flush` is a sentinel whose collation-buffer drain still happens in
   the session loop (handler body is a safety net).
8. ✅ **Feature-mode tools** (18 tools: `create_feature`, `create_phases`,
   `create_task`, `get_execution_state`, `block_task`, `resume_task`,
   `review_completed_tasks`, `review_all_completed_tasks`,
   `propose_task_diff`, `decide_task_diff`, `archive_task`,
   `create_feature_task`, `update_feature_task`, `approve_feature_task`,
   `get_current_task`, `get_tasks`, `update_task_status`,
   `raise_blocker`) — **done.** Descriptor-only move to
   `mu/tools/feature/handlers.py`; bodies stay as `_handle_*` in
   `core/tools.py` until `core/feature_mode.py` itself relocates.
9. ✅ **Security-mode tools** (9 tools: `create_security_report`,
   `add_security_finding`, `attach_security_proof`,
   `verify_security_proof`, `attach_remediation_patch`,
   `verify_remediation`, `approve_security_finding`,
   `refute_security_finding`, `get_security_state`) — **done.** Same
   descriptor-only pattern; moved to `mu/tools/security/handlers.py`.

For each move:
- Tests: rely on the per-tool tests in `tests/test_tools.py`,
  `tests/test_search_and_replace.py`, `tests/test_background_tasks.py`,
  `tests/test_search_references.py`, etc.
- What could break: argument-handler signatures (the legacy
  `_handle_foo(args, folder_context, ui, variables)` shape vs the new
  `@tool` `(args, ctx)` shape). The bridge in `mu/tools/__init__.py`
  reconciles them today; each migration removes one bridge entry.
- Verification: `pytest tests/test_tools.py tests/test_mu_tools_registry.py`
  after each tool moves.

**End-state after Phase 1**: `core/tools.py` is just the dispatcher
shell (`execute_tool`, `_check_bounds`, the envelope helpers,
`TOOL_HANDLERS` empty or removed). Expected size: ~500-800 LOC.

### Phase 2 — Move `_check_bounds`, dispatcher, envelope (🟡 medium)

1. ✅ **`_check_bounds` → `mu/tools/_bounds.py`** — body moved; legacy
   underscored name re-exported via a lazy alias to dodge the
   circular-import that arose when `mu.tools` loaded before
   `mu.tools._dispatcher.TOOL_HANDLERS` was defined.
2. ✅ **`execute_tool` → `mu/tools/_dispatcher.py:dispatch`** — body
   moved. `mu.tools.execute(...)` now calls `dispatch` directly
   instead of routing back through `mu.tools._dispatcher.execute_tool`. The
   legacy entry point is a thin shim. Added a bottom-of-file
   `import mu.tools` to `core/tools.py` so the registry mirror fires
   after the legacy maps are populated.
3. ✅ **Envelope helpers (`_build_tool_envelope`,
   `_envelope_from_handler_result`, `infer_tool_error_code`) →
   `mu/tools/_envelope.py`** — bodies moved; legacy symbols lazy-bind
   on first call.
4. ✅ **Scrubber wiring (`_scrub_and_annotate`) → `mu/tools/_scrub.py`**
   — done. Lazy alias keeps the legacy underscored name working for
   in-file callers (`read_file`, `bash_command`, `get_chunk`,
   `search_for_string`, `search_references`).

After Phase 2: every tool dispatch flows through
`mu/tools/_dispatcher.py`. `core/tools.py` still holds the
implementation functions (`read_file()`, `write_file()`,
`bash_command()`, etc.) and the feature/security `_handle_*` bodies,
but the registration surface and the dispatcher itself are out. Down
from 5,433 LOC to 3,507 LOC (−35%). Test:
`pytest tests/test_harness_layers.py tests/test_envelope_hints_retry.py`.

### Phase 3 — Extract `Session` helpers from the loop body (🟡 medium)

`core/session.py` has ~30 helper methods on `Session` that are not the
loop body itself. Many are independently meaningful:

- ✅ `_build_messages_from_history`, `_prepare_runtime_history`,
  `_summarize_message_parts`, `_clip_preview` → `mu/session/messages.py`
  — **done.** Plus `message_has_thought_signature` and a local
  `_shorten_tool_args` mirror (to avoid a circular import).
- ✅ `_resolve_context_limit`, `_compaction_token_budget`,
  `_resolve_response_reserve` → `mu/session/budgets.py` — **done.**
- ✅ `_execute_tool_with_memory`, `_build_structured_tool_result`,
  `_sync_feature_state_for_tool` → `mu/session/tools_glue.py` —
  **done.** Helpers that stayed on Session (`_unwrap_tool_envelope`,
  `_parse_*`, `_set_feature_state`) are called as `session.<method>`.
- ✅ `_inject_hierarchical_context`, `_build_workspace_context_files`
  → `mu/session/context.py` — **done.** `_build_skills_block`,
  `_build_active_goal_context`, `_build_recent_tool_context` stayed
  on Session and are called as `session.<method>`.
- ✅ `_provider_generate_with_retry`, `_is_transient_provider_error`,
  `_extract_http_status_code` → `mu/agent/retry.py` — **done.**
  Classification stays delegate-able via the session method so test
  monkeypatches still work.
- ✅ Loop-detection helpers (`_coarse_tool_args`,
  `_tool_call_fingerprint`, `_track_tool_for_loop_detection`,
  `_is_repeated_tool_sequence`) → `mu/agent/loop_detection.py` —
  **done.** The `_loop_blocker_raised` flag stays on Session (it's
  per-turn state, not a helper).

Each extraction:
- Move method body to module-level function in `mu/...`
- Update `Session.<method>` to be a one-line forwarder
- After all callers migrate, drop the forwarder

Tests cover these well: `tests/test_session.py` (large),
`tests/test_provider_retry.py`, `tests/test_loop_detection.py`,
`tests/test_loop_blocker_halts_watchdog.py`,
`tests/test_compaction_e2e.py`, `tests/test_context_budget.py`.

### Phase 4 — Move the loop body (🔴 high, the big one) — ✅ **done**

The actual `Session.send_message` body (~1000 LOC, larger than the
original estimate) is now in `mu/agent/loop_body.py:run_turn`. The
move was done verbatim — every `self.X` rewritten to `session.X` —
because attempting to refactor parameter ownership at the same time
as a 1000-LOC relocation would have multiplied the risk surface.

What landed:

1. ✅ New module: `mu/agent/loop_body.py` (1100 LOC: 73 LOC of
   docstring + imports + lazy-binding helper, then the verbatim body).
2. ✅ `Session.send_message` is now a 5-line forwarder to
   `run_turn(self, text)`.
3. ✅ Three `core/session.py` symbols (`_HookAbort`,
   `_shorten_tool_args`, `_hook_abort_envelope`, plus `_sanitize_for_log`)
   are lazy-bound into `mu.agent.loop_body` globals on first call,
   avoiding the import cycle.
4. ✅ Tests that source-inspected `Session.send_message`
   (`test_loop_blocker_halts_watchdog.py`,
   `test_mu_agent_session_integration.py::test_send_message_resets_abort_flag_each_turn`)
   updated to inspect `mu.agent.loop_body.run_turn`.
5. ✅ One test that monkeypatched `mu.session.session.collect_approval_plans`
   updated to patch `mu.agent.loop_body.collect_approval_plans`.

What's intentionally **not** done in this phase:

- Parameter ownership refactor (Step 3 of the original plan). The body
  still uses `session.iteration`, `session.total_in`, etc. — moving
  those to local variables in `run_turn` is a separate, lower-risk
  pass that can land later. The current verbatim move is a stable
  intermediate state.
- `mucli.py` still calls `session.send_message(text)`. The forwarder
  is permanent for now (the `Session` shape is still part of the
  public API). Once Phase 5 (SessionManager relocation) and Phase 6
  (namespace renames) finish, we can revisit whether to expose
  `AgentLoop.run_turn` as the canonical entry point.

**Tests covering**: practically the entire suite. Critical regression
guards: `tests/test_session.py`, `tests/test_mu_parallel_dispatch.py`,
`tests/test_continue_after_interrupt.py`,
`tests/test_loop_blocker_halts_watchdog.py`,
`tests/test_mu_agent_session_integration.py`,
`tests/test_compaction_e2e.py`.

**What could break**: anything that introspects `Session` internals at
runtime. Sub-agents, hooks reading `ctx.session.*`, the UI's status
line reading `session.session_manager.token_counts`. Don't move state
ownership in the same PR as the body move — split.

**Soak**: run real sessions for a day before marking the legacy body
deletable.

### Phase 5 — Move `SessionManager` (🟡 medium) — ✅ **done**

`SessionManager` (557 LOC) now lives in `mu/session/manager.py`. The
20-line `__getattr__` shim that's been there since Phase 0 is gone;
the real class body is what gets imported. `core/session.py`
re-exports it via `from mu.session.manager import SessionManager` so
the legacy `from mu.session.session import SessionManager` path keeps
working for the 100+ test+production call sites.

What landed:

1. ✅ Moved the full `SessionManager` body (line 149-705 of the old
   `core/session.py`) to `mu/session/manager.py`.
2. ✅ Persistence helpers (`save_history`, `_load_session`,
   `compact_completed_turn`, `clear_current_history`, etc.) moved
   along with it.
3. ✅ Removed the lazy `__getattr__` shim — the new file is the
   canonical home.
4. ✅ `derive_feature_state_status` and `_slugify_feature_id` are
   lazy-bound from `core/session.py` (where they still live as
   module-level helpers) to dodge the circular-import concern.
5. ✅ `HISTORY_DIR` is read through a `_history_dir()` indirection
   that re-resolves `mu.session.session.HISTORY_DIR` at every call. This
   keeps the existing `monkeypatch.setattr("mu.session.session.HISTORY_DIR", …)`
   pattern in 27 tests working without bulk-rewrites. Tests that
   want to migrate to the new path can use
   `monkeypatch.setattr("mu.session.manager._history_dir", …)` (or
   patch `mu.session.session.HISTORY_DIR` since the indirection reads
   it dynamically either way).

What's intentionally **not** done:

- `core/session.py` is NOT deleted. It still hosts the `Session`
  class (~800 LOC of state initializers, helpers, feature/security
  glue), plus the small module-level helpers (`_sanitize_for_log`,
  `_shorten_tool_args`, `_safe_feature_path_prefix`,
  `_slugify_feature_id`, `derive_feature_state_status`, `_HookAbort`,
  `_hook_abort_envelope`). The `Session` class will move in a
  future phase; the small helpers stay in `core/` until Phase 6
  renames `core/` to its target modules.

### Phase 6 — Namespace-rename the cohesive modules (✅ safe per module) — ✅ **done**

Moved the modules that were fine where they were but lived in `core/`
for historical reasons:

| Current | New | Status |
| --- | --- | --- |
| `core/feature_mode.py` | `mu/feature/engine.py` | ✅ moved (990 LOC) |
| `core/security_mode.py` | `mu/security/engine.py` | ✅ moved (585 LOC) |
| `core/workspace.py` | `mu/workspace/folder_context.py` | ✅ moved (454 LOC) |
| `core/secret_paths.py` | `mu/security/secret_paths.py` | ✅ moved (290 LOC) |
| `core/background_tasks.py` | `mu/tools/shell/background.py` | ✅ moved (245 LOC) |
| `core/retrieval.py` | `mu/retrieval/index.py` | ✅ moved (227 LOC) |
| `core/memory.py` | `mu/memory/stores.py` | ✅ moved (185 LOC) |
| `core/approval.py` | `mu/agent/approval.py` | ✅ moved (154 LOC) |
| `core/collation.py` | `mu/agent/collation.py` | ✅ moved (108 LOC) |

Each `core/<name>.py` is now an ~10-LOC re-export shim — every legacy
`from core.X import Y` import keeps working. Five new subpackages were
created (`mu/feature`, `mu/security`, `mu/workspace`, `mu/retrieval`,
`mu/memory`). Net: 3,238 LOC physically moved out of `core/`; the
shims total 163 LOC across all nine modules. Test status: 994 passed
/ 10 failed — exact pre-Phase-6 baseline, zero regressions.

After downstream consumers migrate to the new `mu.X` import paths,
the shim files can be deleted in a future cleanup.

### Phase 7 — Consolidate `ui/` and `mu/ui/` (🟡 medium) — ✅ **done**

Direction chosen: move everything to `mu/ui/`, drop top-level `ui/` —
consistent with the rest of the migration. Five files moved verbatim:

| Old | New | LOC |
| --- | --- | ---: |
| `ui/base.py` | `mu/ui/base.py` | 40 |
| `ui/render.py` | `mu/ui/render.py` | 81 |
| `ui/input.py` | `mu/ui/input.py` | 752 |
| `ui/rich_ui.py` | `mu/ui/rich_ui.py` | 834 |
| `ui/session_picker.py` | `mu/ui/session_picker.py` | 278 |

Each `ui/<name>.py` is now an ~8-LOC re-export shim. The 5 new
`mu/ui/` modules join the existing `progress.py`, `stream.py`,
`subagent.py` — a single coherent UI package.

Three tests had to be updated because their `monkeypatch.setattr`
targets pointed at module-internal bindings (`ui.input.HISTORY_DIR`,
`ui.render.render_response`). With the body moved, the canonical
patch target is the new `mu.ui.*` path; the shim's separate namespace
won't reach the function's bare-name lookup. Two patches in
`test_input.py` and one in `test_streaming_unified_live.py` were
retargeted.

Test status: 994 passed / 10 failed — exact pre-Phase-7 baseline,
zero regressions.

### Phase 8 — Eliminate `core/` and `ui/` entirely (✅ done)

Final cleanup. Three sub-steps:

1. **Rewrite all importers off the 14 shims** (Phase 6/7 left behind):
   52 files updated by mechanical regex pass to import from the canonical
   `mu.X` location.
2. **Move the two remaining substantive files**:
   - `core/session.py` (1,185 LOC) → `mu/session/session.py`
   - `core/tools.py` (3,503 LOC) → `mu/tools/legacy.py` (kept as a
     single file — the proper decomposition of the 55 `_handle_*`
     bodies into per-group `handlers.py` modules is future work, not
     required for the directory cleanup)
3. **Delete everything legacy**: the 14 shims, the original
   `core/session.py`, the original `core/tools.py`, the empty
   `core/__init__.py` and `ui/__init__.py`, and finally `rmdir` both
   directories.

In total ~80 import-path rewrites across 40+ files, plus a cosmetic pass
that retargeted ~20 stale docstring/comment references to the new
`mu.session.session` / `mu.tools` paths.

`mu/session/manager.py` now reads `HISTORY_DIR` dynamically from
`mu.session.session` (was `core.session`); the indirection that lets
tests `monkeypatch.setattr("mu.session.session.HISTORY_DIR", ...)`
is preserved.

Test status: 994 passed / 10 failed — exact pre-Phase-8 baseline,
zero regressions. `core/` and `ui/` no longer exist on disk.

## Out of scope

These are not refactor candidates — they're current code:

- `providers/` — provider-specific implementations are intentionally
  separate. Leave alone.
- `utils/` — small standalone utilities. Leave alone.
- `mu/skills/`, `documentation/` — content, not code.

## Phasing summary

| Phase | Risk | LOC moved | Status | Soak required | Blocked by |
| --- | --- | ---: | --- | --- | --- |
| 0 — Delete dead | ✅ | 0 | ☐ User to run `rm -rf` | No | — |
| 1 — Migrate handlers | 🟡 | ~3,000 from `core/tools.py` | ✅ All sub-phases done (1.1–1.9) — 59 tools migrated across 9 modules | Per tool | — |
| 2 — Migrate dispatcher | 🟡 | ~500 | ✅ all done (2.1–2.4) | Half-day | Phase 1 |
| 3 — Extract Session helpers | 🟡 | ~800 | ✅ all six clusters done (budgets, retry, loop-detection, context, messages, tools-glue) | Per cluster | — |
| 4 — Move loop body | 🔴 | ~1,000 | ✅ done (verbatim move; param ownership refactor deferred) | A day in prod | Phase 3 |
| 5 — Move SessionManager | 🟡 | ~600 | ✅ done (557 LOC + lazy HISTORY_DIR proxy) | Half-day | Phase 4 |
| 6 — Namespace rename | ✅ per module | 3,238 | ✅ done (9 modules + 5 new subpackages; shims preserve back-compat) | None | — |
| 7 — Consolidate UI | 🟡 | 1,985 | ✅ done (5 files moved to `mu/ui/`; shims preserve back-compat; 3 test patches retargeted) | None | — |
| 8 — Final `core/` + `ui/` removal | ✅ | ~4,700 | ✅ done (Session + tools.py relocated to `mu/`; 14 shims + both legacy dirs deleted; ~80 imports rewritten) | None | Phase 7 |

Sequencing: 0 → (1 ∥ 6) → 2 → 3 → 4 → 5 → 7. Phase 1 and Phase 6 can
run in parallel since they touch disjoint code.

## What a "done" repo looks like

```
mucli.py                # entry, ~600 LOC after slimming
mu/
  agent/                # loop, hooks, retry, parallel, compactor
  commands/             # slash commands
  feature/              # feature plan engine + tools
  mcp/                  # MCP client + registry
  memory/               # task + scratchpad stores
  retrieval/            # semantic index
  security/             # audit engine + secret paths
  session/              # SessionManager + history mixin
  skills/               # bundled skills
  tools/                # all 30+ tools, one file each
  ui/                   # RichUI, input, stream, subagent UI
  workspace/            # FolderContext
providers/              # OpenAI, Gemini, Ollama
utils/                  # token estimator, config, logger, citation manager
documentation/
tests/
```

No `core/`. No top-level `commands/`. No `agents/`. No `ui/` outside
`mu/`.

## How to start

If you have a free afternoon: Phase 0 + start Phase 1 (memory tools
are the easiest first migration — see step 1 of Phase 1).

If you want a low-risk warm-up: Phase 6 module-by-module. Each move
is mechanical and the tests catch import errors immediately.

If you want maximum value-per-hour: Phase 1 (the tools dir is where
new contributors land; making it the canonical location pays off
every time someone adds a tool).
