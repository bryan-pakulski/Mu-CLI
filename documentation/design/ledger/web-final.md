# Test-migration ledger — web final pass

Branch `wash/web-final` (from `wash/overhaul`). Format per spec §8.3:
`file:line — old → new — intent`. No test was deleted, skipped or xfailed.
Element ids, `data-*` hooks, aria attributes, store names, SSE event kinds,
API calls and `/api/prompts` payloads are unchanged.

Shared helper: `tests/wash_css_helpers.py` reads the Wash stylesheets as
rules (`decls(text, selector)`), so tests pin a property value instead of one
legacy file's source formatting.

## Legacy layer and alias tokens retired (app.css, theme.css, floating_popouts.css, tokens.css, legacy-overrides.css)

- tests/test_artifact_scroll_fix.py:18–122 — `CSS` = `static/css/app.css` and exact-format strings (`.app.has-panel .mode-panel.artifacts-panel { display:flex; }`, `.artifacts-panel { overflow:hidden; }`, `.registry-section-list { flex:1; min-height:0; overflow:auto;`, `flex:1`/`min-height:0` in the body rule, shell/files overrides) → the same rules read from `wash/patterns/panels.css` through `tests/wash_css_helpers.decls()` (property values, not source formatting), plus the override-before-base source order — the artifacts panel stays a bounded flex column whose inner list scrolls, like the shell and files panels.
- tests/test_gui_toasts.py:10 — `CSS_PATH` = `static/css/app.css` → `static/css/wash/components/toast.css` — toast styles exist (the one Wash toast component).
- tests/test_gui_toasts.py:24 — `.toast-container` block with `right/bottom: var(--space-4)` and no `top:` → `.w-toast-region {` block is `position: fixed`, `right/bottom: var(--wash-space-6)`, no `top:` — toasts stack bottom-right, clear of the header.
- tests/test_gui_toasts.py:36 — `.toast` block `background: var(--raised)`, `border-left-width: 4px`, `border-radius: var(--radius-sm)`, `.toast--success/error/info::before` glyph rules, `content: "✓"/"✗"` → `.w-toast` raised surface with hairline and `--wash-radius-md`, tone-coloured `.w-toast__glyph`, template binds `$store.toast.tone()` / `$store.toast.glyph()`, store maps ✓ ✗ ◆ ● — a toned note with a status glyph; the 4px left rule is a superseded planos detail (G§9.16 toast).
- tests/test_gui_toasts.py:55 — `class="toast"`, `toast__msg`, `toast__close`, `toast--enter-start` / `toast--leave-end` in the partial → `w-toast`, `w-toast__msg`, `w-toast__close`, `'toast--' + m.type` and the dismiss handler kept, `x-transition:leave.opacity` — the Alpine fallback renders the shared Wash toast markup.
- tests/test_gui_toasts.py:75 — `.toast--enter…`/`.toast--leave…` transition classes in app.css → `animation: w-toast-bleed`, `@keyframes w-toast-bleed`, `.w-toast[data-leaving]` in toast.css — toasts animate in and out.
- tests/test_artifact_visualizations_subsection.py:18,86 — `APP_CSS` = `static/css/app.css`; `"flex:1 1 auto" in src` → `APP_CSS` = `wash/patterns/panels.css`; `decls(".registry-section")["flex"] == "1 1 auto"` and `flex:1 1 50%` still absent — registry sections size to their content.
- tests/test_container_control_centre.py:180 — app.css `.file-browser-backdrop { … z-index: var(--z-popover) }`, `z-index:calc(var(--z-modal) - 50)` (welcome) and `z-index:var(--z-modal)` (editor) → overlays.css `.file-browser-backdrop.w-modal { z-index: calc(var(--wash-z-modal) + 10) }`, `.w-modal` is `var(--wash-z-modal)`, and the welcome and container-editor backdrops are `.w-modal` — the shared file browser stacks above the guided-creation and editor modals it opens from.
- tests/test_feature_navigation_ui.py:9,54 — `STYLES` = app.css; `var(--bg-subtle)`, `var(--mode-feature)` → `STYLES` = `wash/patterns/panels.css`; `var(--wash-surface)`, `var(--wash-mode-feature-ink)` — the feature library reads real per-theme tokens (legacy aliases retired, P7).
- tests/test_gui_chat_composer.py:11,14 — `APP_CSS` = app.css; `.composer .send-btn:disabled` → `COMPOSER_CSS` (`components/composer.css`) `.w-composer__send:disabled`, markup carries `w-composer__send` — a busy composer shows a disabled Send.
- tests/test_gui_chat_composer.py:29 — `.code-copy-btn` in app.css → in `wash/patterns/conversation.css` — code blocks keep their copy control styling.
- tests/test_gui_files.py:418 — `.files-panel`, `.files-tree`, `var(--bg)` in app.css → same classes in `patterns/panels.css` and `.CodeMirror { background: var(--wash-canvas); color: var(--wash-ink); }` — the files panel and the editor follow the theme.
- tests/test_gui_files.py:438 — side-panel motion strings read from app.css (`flex-basis var(--dur-large)`) → read from `patterns/{conversation,panels,settings}.css` (`flex-basis var(--wash-motion-medium)`, other strings unchanged) — sidebar and stage panels animate; the settings drawer centres.
- tests/test_gui_history_panel.py:298 — `CSS_PATH` = app.css → `wash/patterns/panels.css` — the history panel classes are styled.
- tests/test_gui_loading_indicators.py:31 — `.ui-loading-region::before`, `.work-loading::before`, `animation: container-create-spin` in app.css → the same in `wash/patterns/overlays.css` (loaded on every page) plus `@keyframes container-create-spin` there — loading regions spin on /, /work and /trace.
- tests/test_gui_memory_panel.py:252 — `CSS_PATH` = app.css → `wash/patterns/panels.css` — memory panel and layer-modal classes (and selectable `<pre>`) are styled.
- tests/test_gui_memory_panel.py:277 — tokens.css `:root,\n[data-theme] {` and `--context-*-surface:` definitions used as `var(--context-…)` in app.css; `.context-pressure-pill.hot` uses `var(--error-subtle)` → generated wash/tokens.css has paper/dusk blocks; panels.css has no `--context-` names and no per-theme blocks; the tooltip reads `var(--wash-raised)`; the hot pill `var(--wash-danger-wash)` — the observatory palette resolves per theme from the token layer.
- tests/test_gui_providers.py:32,254 — `CSS_PATH` = app.css with `.ollama-mode-btn` / `.ollama-mode-btn.active` rules → `CSS_PATH` = `wash/patterns/conversation.css` (`.ollama-mode-toggle`, `.ollama-key-input`, also in chat.css); the buttons are `ollama-mode-btn w-segmented__option` with `:aria-pressed` inside `ollama-mode-toggle w-segmented` — the local/cloud toggle is the Wash segmented control.
- tests/test_gui_settings_modal.py:8,11 — app.css `width: min(620px, 48vw);`, `grid-template-columns: minmax(150px, 1fr) minmax(220px, 1.35fr);` → patterns/chat.css `min-width: min(460px, …)`, `max-width: min(590px, …)`, rows `grid-template-columns: minmax(110px, 142px) minmax(0, 1fr);` — the session settings popover is bounded and lays its rows out as a label/value grid.
- tests/test_shell_panel.py:25 — `APP_CSS` = app.css → `wash/patterns/panels.css` — shell panel, output and input are styled.
- tests/test_live_conversation_surfaces.py:68 — css read from app.css → `wash/patterns/conversation.css` — sub-agent cards keep a 10px progress track and an unclipped task line.
- tests/test_session_state_badges.py:12 — `APP_CSS` = app.css → `wash/patterns/welcome.css` — session picker state hooks are styled.
- tests/test_session_state_badges.py:70 — the literal `.welcome-session-state.is-busy{animation:none}` within 200 chars of the reduced-motion media query → `decls(welcome, ".welcome-session-state.is-busy", media="prefers-reduced-motion: reduce") == {"animation": "none"}` — the busy pulse stops under reduced motion.
- tests/test_mode_workspace_ui.py:9,30 — tokens.css `--mode-research: var(--wash-mode-research-ink);` and `var(--mode-X)` used in app.css → wash/tokens.css defines `--wash-mode-X-ink` per theme, base.css maps `[data-mode-tint="X"]` to `--w-mode-*` channels, panels.css redefines no mode colour and has no colour literals — each mode keeps its pigment in both themes.
- tests/test_mode_workspace_ui.py:56 — `.mode-os {` … `.mode-os-topline` / `.mode-os-metrics {` source slices of app.css, `border-radius: var(--radius-pill)` absent → `decls()` of `.mode-os`, `.mode-panel .mode-os`, `.mode-os-metrics` have no background/box-shadow/border-radius and `.mode-os-metric` is flat (`border-radius: 0`, `background: none`) — one plane, no nested cards or pills.
- tests/test_gui_viewport_popouts.py:30 — `@import url("../floating_popouts.css") layer(legacy);` in wash.css → `@import url("components/popout.css") layer(components);`, popout.css has `[data-viewport-floating] {` with `position: fixed;`, and floating_popouts.css is gone — the popout geometry loads on the shell and trace pages.
- tests/test_job_visual_polish.py:48 — `f'{name}: var(--wash-{wash});' in tokens` (legacy tokens.css) → tokens.css absent; generated wash/tokens.css defines `--wash-accent/ok/attention/danger/running-ink` and work.css uses each via `var(--wash-…)` — jobs mode reads the one Wash palette in both themes.
- tests/test_web_product_ui.py:152 — `'body { padding: 0; }' not in legacy-overrides.css` → legacy-overrides.css does not exist — no body padding override.
- tests/test_web_route_shell.py:126,129 — `id="{modal}" role="dialog" aria-modal="true" aria-labelledby="{title}"` on the /containers backdrop, `event.key !== 'Escape'` in containers.js → the backdrop is `id="{modal}" hidden>` and its `.w-modal__dialog` surface carries `role="dialog" aria-modal="true" aria-labelledby`; containers.js opens/closes through `MuDialog.open/close` and closes on the shared `dialog-close` event that dialogs.js dispatches on Escape — container dialogs are labelled and close on Escape (gap: role moved off the backdrop).
- tests/test_wash_css_contract.py:40 — `@layer legacy, tokens, base, …` → `@layer tokens, base, layout, components, patterns, utilities;` — the legacy layer is retired (W4).
- tests/test_wash_css_contract.py:157 — `test_legacy_stylesheets_carry_no_important` (css/*.css) → `test_no_stylesheet_outside_wash_carries_important` (whole css tree outside wash/) — no stylesheet can reverse layer priority.
- tests/test_wash_css_contract.py:168 — `test_legacy_token_file_has_no_hex_and_maps_onto_wash` (alias lines in tokens.css) → `test_legacy_alias_tokens_are_retired` (tokens.css absent; the --wash-* targets exist in the generated file) — spec §7 P7.

## /work legacy emitters moved onto Wash markup

- tests/test_job_web_parity.py:66 — `'<th scope="col" class="num">Duration</th>'` → `'<th scope="col" data-num>Duration</th>'` — the Checks tab's numeric Duration column is right-aligned tabular (`.w-table [data-num]` hook, G§9.19).

## One diff renderer

- tests/test_job_browser.py:76 — harness injects `wash/work_diff.js` before work/view.js → injects `wash/diff.js` (the same order work.html loads them) — the browser round trip renders the Diff tab through the one Wash diff module.
- tests/test_wash_css_contract.py:227 — `test_work_scripts_render_without_inline_styles` checks `wash/work_diff.js` → checks `wash/diff.js` (work_diff.js is deleted) — the diff renderer writes no inline styles.
- (new) tests/test_wash_css_contract.py::test_one_diff_renderer_and_one_diff_stylesheet — one diff module (renderFile / renderTree / statusBadge / findings / tree), loaded by index, work and the gallery; only components/diff.css styles diff rows, marks, findings and the tree.

## One command palette

- tests/test_web_shell_components.py:296 — `INDEX.index("/static/js/wash/palette.js") < INDEX.index("/static/js/wash/chat_palette.js")` → `_layout.html` loads `<script src="/static/js/wash/palette.js"></script>` (not deferred) and index.html no longer links it but still loads chat_palette.js — the palette is on every page, before each page's own registrations.
- (new) tests/test_wash_css_contract.py::test_one_command_palette_on_every_page — one palette module; /, /work, /containers, /trace, /jobs/trace and /models/costs each register their own sources; work/ui.js has no local palette and work.css no `.work-palette` rules.

## Known gaps from the earlier ledgers (fixed)

- Memory and history panel selects were bare `<select class="w-select">`: now `<span class="w-select" data-size="sm"><select class="w-select__control">` (new `.w-select[data-size="sm"]`, touch height on coarse pointers).
- /containers modals had `role="dialog"` on the backdrop: the `.w-modal__dialog` surface carries `role="dialog" aria-modal aria-labelledby`; containers.js opens them through the shared dialog stack (`MuDialog`, dialogs.js) and closes on its `dialog-close` (Esc). The shared file browser got the same fix.
- /trace info popouts did not trap focus: each popout is an `x-dialog` (focus trapped, Esc closes, focus returns to its info button); trace.html loads dialogs.js.
- The pricing reset used `window.confirm`: it opens a Wash destructive confirm (`static/js/wash/confirm.js`, `role="alertdialog"`, Cancel focused). The trace "clear run" confirm moved to it as well.
- `fragments/file_browser.html` emoji: already sprite icons (`wash.svg#i-folder` / `#i-file`) on this branch; a contract test now pins it.
- The /design chat replay frame clipped the composer hint row: the frame is a flex column whose transcript takes the slack.
- The /work New job modal had its own focus trap: it (and every work/ui.js dialog) uses the shared dialog stack (dialogs.js `MuDialog`, the vanilla entry of the `x-dialog` mechanism — /work does not run Alpine).
- "Reduce decoration" (G§6.2 rule 5): Appearance group in the settings drawer (theme select + switch), persisted as localStorage `mucli-decoration` (`reduced` | `full`), applied by the pre-paint bootstrap, following `prefers-reduced-transparency` until set (`window.MuDecoration` in route_theme.js). Hero art, empty-state blots and mode tint bands flatten.
- Canvas charts drew L1A and L4 grey (no server hue): every layer maps through `--wash-layer-<id>`; charts redraw on `mucli-theme-change`.

New contract tests (nothing removed): tests/test_wash_css_contract.py (`test_static_css_holds_only_the_wash_tree`, `test_no_legacy_cascade_layer_or_import_remains`, `test_no_raw_px_font_sizes_anywhere_in_the_css_tree`, `test_no_colour_literals_anywhere_in_the_css_tree`, `test_templates_scripts_and_css_reference_no_legacy_alias_variable`, `test_context_charts_map_every_layer_through_its_wash_pigment`, `test_one_diff_renderer_and_one_diff_stylesheet`, `test_one_command_palette_on_every_page`) and tests/test_web_a11y_contract.py (`test_dialog_semantics_sit_on_the_surface_not_the_backdrop`, `test_trace_info_popouts_trap_focus`, `test_destructive_confirms_are_wash_modals_with_cancel_focused`, `test_memory_and_history_panel_selects_are_wash_selects`, `test_file_browser_uses_sprite_icons_not_emoji`, `test_reduce_decoration_preference_is_persisted_and_applied_before_paint`, `test_gallery_chat_replay_frame_fits_the_composer`).
