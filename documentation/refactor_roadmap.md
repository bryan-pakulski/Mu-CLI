# Refactor history

This doc captures the multi-phase refactor that moved mucli from a
two-package layout (`core/` + `mu/`) with overlapping responsibilities
into a single coherent `mu/` package. **The refactor is complete.**
Treat this doc as historical context for grep-archaeology, not as a
current to-do list.

## Current tree (post-refactor)

```
mucli.py                CLI entry point + REPL bootstrap
mu/
  session/              Session, SessionManager, HistoryMixin, helpers,
                        per-turn context/messages/budgets/tools_glue
  agent/                loop_body (run_turn), hooks registry, parallel
                        dispatch, retry/loop-detection, approval, collation,
                        secret guard, plan mode, compactor, usage tracker,
                        AgentLoop wrapper
  tools/                @tool-decorated handlers grouped by domain
                        (file, workspace, research, shell, memory, security,
                        feature, batch, task, agent, skill) + descriptors,
                        dispatcher, bounds/envelope/scrub helpers, hints
  commands/             slash-command registry with @command decorator
  feature/engine.py     feature plan engine
  security/             engine + always-on secret_paths denylist & scrubber
  workspace/            FolderContext (sandbox + gitignore)
  retrieval/index.py    SemanticCodeIndex + RETRIEVAL_INDEX singleton
  memory/stores.py      TaskMemoryStore + ScratchpadStore
  mcp/                  MCP stdio client + auto-registry
  ui/                   RichUI, InputHandler, render, session_picker,
                        SubagentUI, SubagentProgressTracker, stream renderer
  skills/               bundled skills (markdown)

providers/              OpenAI, Gemini, Ollama, base classes
utils/                  config, runtime_metrics, token_estimator,
                        citation_manager, anti_detection, helpers, logger
```

No `core/` directory. No `ui/` (top-level) directory. No `agents/`. No
top-level `commands/`. No `mu/tools/legacy.py`. Every tool body lives in
its `mu/tools/<group>/handlers.py` next to its `@tool` registration.

## Architectural conventions

- **Each `@tool` is a pair**: a positional-signature body (publicly
  callable, easy to test) plus a thin `_<name>_tool(args, context)`
  wrapper carrying the registration metadata. The wrapper unwraps the
  args dict and calls the body.
- **`mu/session/helpers.py`** hosts shared primitives (`_HookAbort`,
  `_shorten_tool_args`, `_hook_abort_envelope`, `_sanitize_for_log`,
  `derive_feature_state_status`, `_slugify_feature_id`,
  `_safe_feature_path_prefix`) with no Session/SessionManager
  dependencies, so every caller — loop_body, manager, history,
  messages, tools_glue, retry — imports them directly without
  circular-import workarounds.
- **Hooks auto-install on `mu.agent` import**: `compactor`, `plan_mode`,
  `secret_guard`, `usage_tracker` register their hooks via
  side-effect imports at the package's `__init__.py` so they fire from
  the first turn.
- **`utils.config.HISTORY_DIR`** is the single source of truth for the
  per-user session directory. `mu.session.manager._history_dir()`
  reads it dynamically so tests can `monkeypatch.setattr("utils.config.HISTORY_DIR", ...)`.
- **`mu.tools` registry**: the `@tool` decorator stores into
  `mu.tools._REGISTRY` / `_HANDLERS` and mirrors into
  `mu.tools.descriptors.TOOLS` / `TOOL_DESCRIPTORS` and
  `mu.tools._dispatcher.TOOL_HANDLERS`. List-iteration callers and
  dict-lookup callers both see registrations.

## Phase log

All phases complete. Test status throughout: 994 passed / 10 failed
(pre-existing baseline) — zero regressions across every phase.

| Phase | Goal | Outcome |
| --- | --- | --- |
| 0 | Delete dead `agents/` + `commands/` packages | done |
| 1 | Migrate tool handlers to `mu/tools/<group>/handlers.py` via `@tool` | done (1.1–1.9) |
| 2 | Migrate dispatcher (`_bounds`, `_envelope`, `_dispatcher`, `_scrub`) | done (2.1–2.4) |
| 3 | Extract Session helpers (budgets, retry, loop-detection, context, messages, tools-glue) | done (3.1–3.6) |
| 4 | Move agent loop body (`Session.send_message`) → `mu/agent/loop_body.py:run_turn` | done |
| 5 | Move `SessionManager` → `mu/session/manager.py` | done |
| 6 | Namespace-rename cohesive `core/*` modules under `mu/` | done (9 modules) |
| 7 | Consolidate `ui/` → `mu/ui/` | done (5 files) |
| 8 | Eliminate `core/` and `ui/` entirely | done (Session + tools.py relocated; both dirs removed) |
| 9 | Decompose `mu/tools/legacy.py` | done — 3,503 LOC bodies redistributed into per-domain handlers; legacy.py deleted |
| 10 | Deep cleanup: auto-install hooks, kill indirections, deduplicate `_shorten_tool_args`, drop stale comments | done |

## Things that look like legacy code but aren't

Some remaining mentions of "legacy" in the codebase describe genuine
backward-compat code paths (not stale migration markers):

- **`mu/session/manager.py`** — `legacy_filepath = os.path.join(_history_dir(), f"{name}.json")` reads the pre-directory session storage format (single `.json` file per session). The variable name is accurate.
- **`mu/ui/input.py`** — same: glob for `*.json` to surface legacy session names in the picker.
- **`mucli.py`** — the "numbered fallback" picker is a real fallback when prompt-toolkit can't drive the terminal.
- **`mu/tools/batch/handlers.py`** — comments about "the legacy path" describe the dispatcher's serial-mode behavior for nested tool calls.
- **`mu/commands/__init__.py`** — comments about the `mucli.handle_command()` dispatch fallback; that path still exists for slash commands not yet ported to the `@command` registry.

These are stable and intentional. Leave them alone.
