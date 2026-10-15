# Test migration ledger: terminal (Wash T1 + T2)

Format (spec §8.3): `file:line — old → new — intent`. Every rewritten
assertion asserts the Wash equivalent of the same intent; nothing was
deleted without a replacement and nothing was skipped.

## Jobs TUI theme shim

- `tests/test_jobs_tui.py:361` — `"\x1b[1;38;2;122;162;247m mucli jobs" in tc` (planos `#7aa2f7`) → `theme.sgr("accent bold", "truecolor")` equals the SGR computed from `semantic(theme(), "accent")` and prefixes the logo — the jobs logo is bold accent in truecolor.
- `tests/test_jobs_tui.py:366` — `"38;2;224;175;104" in tc` (planos warn `#e0af68`) → `theme.sgr("warn", "truecolor") in tc` and `TOKENS["warn"][0] == semantic(theme(), "attention")` — "needs you" uses the attention colour.
- `tests/test_jobs_tui.py:637` — `pt_style("accent bold", "truecolor") == "fg:#7aa2f7 bold"` → `== f"fg:{semantic(theme(), 'accent')} bold"`; added `color_mode({"TERM": "xterm-256color"}) == "256"` — prompt_toolkit styles come from tokens; colour detection gains 256 (spec §6.1).
- `tests/test_jobs_tui.py:678` — `TICK_S == 0.120` → `TICK_S == vocabulary spinner interval_ms / 1000` (150ms) and `SPINNER == vocabulary spinner frames` (`◌○◎●`) — the spinner and tick come from the shared vocabulary.
- `tests/test_job_status_vocabulary_parity.py:44` — Python tables read from `theme.STATUS`, `theme._STATE_STATUS`, `theme._STATE_LABEL` → read from `mu.design.load_vocabulary()` (`status`, `job_state`) and assert `theme.STATUS` matches it — the terminal reads the shared vocabulary (B8). Web/mobile parsing unchanged.
- `tests/test_job_status_vocabulary_parity.py:81` — waiting states from `theme._STATE_STATUS` → from the vocabulary tables — same intent.
- `tests/test_jobs_tui.py:297-298` — `"⏺ bash(ls src) ..."`, `"⏺ read_file(...) ⎿ ✗ ..."` → `"● bash(ls src) ..."`, `"✗ read_file(...) ⎿ ✗ ..."` — tool rows fold to one line led by a status glyph; `⏺` is replaced by the shared vocabulary (`●` active, `✗` failed), G§4.

## RichUI (thinking, YOLO, live status)

- `tests/test_streaming_unified_live.py:343` — `thinking_prints[0].style == "dim italic"` → `== "wash.thinking"` and `style("wash.thinking").italic` — thinking is visually de-emphasised (italic + muted), spec §8.3 example.
- `tests/test_streaming_ui.py:108` — `"dim italic" in matching[0]` → `"wash.thinking" in matching[0]` plus `_assert_thinking_style_is_muted_italic()` (italic, colour = resolved `ink-3`) — same intent.
- `tests/test_streaming_ui.py:281` — `"dim italic" in line` → `"wash.thinking" in line` — reasoning deltas render in the thinking style.
- `tests/test_streaming_ui.py:390` — `"dim italic" in line` → `"wash.thinking" in line` — Ollama `<think>` content renders in the thinking style.
- `tests/test_rich_ui.py:105` — `startswith("... | ✦ YOLO | ")` → `startswith("... | YOLO:on | ")`, span style `wash.caution.bold`, no `blink` — YOLO-on is visibly flagged in the live status (caution, no emoji, no blink; G§8.4).
- `tests/test_rich_ui.py:248` — `"✦ YOLO" in on` → `"YOLO:on" in on and "YOLO:off" not in on` — the Shift+Tab watcher flips the status text.

## Prompt, plan and YOLO indicators

- `tests/test_input.py:9-20` — `test_prompt_markup_hides_default_mode`: `"[demo]" in markup`, `"mode-feature" not in markup`, `">>>" in markup` → `test_prompt_markup_shows_session_and_default_mode`: `<prompt-session>demo</prompt-session>`, `<mode-default>default</mode-default>`, `"mode-feature" not in markup`, starts with `μ` mark, ends with `›` arrow — the prompt names the session and only the active mode's class; G§8.4 prompt `μ session · mode ›` always names the mode (the old test's "hide default" behaviour is retired by G§8.4).
- `tests/test_input.py:30` — `"[demo]" in markup` → `"<prompt-session>demo</prompt-session>" in markup` — session shown in the prompt.
- `tests/test_input.py:42-44` — `"✦" in markup` → `"<yolo-indicator>[yolo]</yolo-indicator>" in markup` and the `yolo-indicator` rule has no `blink` — YOLO is a visible `[yolo]` chip, no emoji, no blink (G§8.4).
- `tests/test_plan_mode_visibility.py:98-100` — `"PLAN MODE" in markup`, `"🔒" in markup` → `"<plan-indicator>[plan]</plan-indicator>" in markup` and the `plan-indicator` style is the plan (lilac) ink — plan mode is unmissable in the prompt as a `[plan]` chip (G§8.4). The toolbar still says "PLAN MODE ACTIVE — writes blocked" (unchanged assertions).
- `tests/test_plan_mode_visibility.py:150-153` — `"🔒" in banners[0]` → banner starts with the plan notice glyph and contains no `🔒` — the enable banner is a plan notice (`show_notice("plan", …)`); text "PLAN MODE ENABLED" kept.
- `tests/test_plan_mode_visibility.py:57,64,67` — `"🔒 PLAN" in/not in line`, index of `"🔒 PLAN"` → `"PLAN"` (and no `🔒`) — the live status line leads with a plan marker when plan mode is on; no emoji in chrome (G§4).
