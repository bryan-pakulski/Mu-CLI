# Test-migration ledger — glass web (spec §13)

Branch `wash/glass-web` (from `wash/overhaul`). Format per spec §8.3:
`file:line — old → new — intent`. No test was deleted, skipped or xfailed.
Element ids, `data-*` hooks, aria attributes, store names and
`/api/prompts` payloads are unchanged; approval behaviour is identical (only
the card's look changed).

The glass recipe lives in `base.css` as `--w-glass-*` custom properties built
from the generated `--wash-glass*` tokens. Surfaces read the recipe, so the
solid fallbacks (`@supports not (backdrop-filter)`,
`prefers-reduced-transparency`, `data-decoration="reduced"`, forced colours)
reach every pane from one place.

## Rewritten assertions (superseded visual details)

- tests/test_feature_navigation_ui.py:60 — `"var(--wash-surface)" in panels.css` → `"var(--w-glass-fill)" in panels.css` — the feature library reads real per-theme Wash tokens (now through the glass recipe, G§3.3).
- tests/test_gui_memory_panel.py:295 — `.context-timeline-tooltip` background `var(--wash-raised)` → `var(--w-glass-fill-strong)` — the tooltip is a raised, per-theme surface (level-2 glass).
- tests/test_gui_toasts.py:46–56 — toast block `background: var(--wash-raised)`, `border: 1px solid var(--wash-hairline)`, `"backdrop-filter" not in css` → `background: var(--w-glass-fill-strong)`, `border: 1px solid var(--w-glass-line)`, `backdrop-filter` / `-webkit-backdrop-filter: var(--w-glass-filter-strong)` in the block and no raw `backdrop-filter: blur(` — a raised toned note whose only blur is the shared, fallback-aware glass recipe (G§3.3 level 2, §9.16).
- tests/test_web_conversation_ui.py:52 — `.w-worked__row` `background: var(--wash-sunken)` → `var(--w-glass-fill-subtle)` — interim turns collapse into one sunken row (level 1s).
- tests/test_web_product_ui.py:148 — `'backdrop-filter' not in chat.css` → every `backdrop-filter` in chat.css is `var(--w-glass-filter…)` — no ad hoc frosted effects on the chat page; blur only from the recipe (the <1200px panel overlay).
- tests/test_web_product_ui.py:175 — `'backdrop-filter' not in chat.css` (sunrise atmosphere retired) → same recipe-only check — the retired sunrise layers stay gone; glass is the specified replacement.
- tests/test_web_product_ui.py:222 — composer form `background: var(--wash-surface)` → `var(--w-glass-fill)` — one level-1 composer surface (G§9.6).
- tests/test_web_product_ui.py:296 — `.w-prompt__option:hover { background: var(--wash-sunken); }` → `{ background: var(--w-glass-fill-subtle); }` — option rows get a sunken (glass-subtle) hover (G§9.10).
- tests/test_web_product_ui.py:352 — `'backdrop-filter' not in trace css + bundle` → recipe-only check — trace sections stay flat Wash cards; the sticky iteration bar's blur comes from the recipe.
- tests/test_web_route_shell.py:65–67 — topbar `background: color-mix(in srgb, var(--wash-canvas)…`, `border-bottom: 1px solid var(--wash-hairline)` → `background: var(--w-glass-fill)`, `backdrop-filter: var(--w-glass-filter)`, `border-bottom: 1px solid var(--wash-glass-edge)` — one shared top bar geometry, now level-1 glass with a light edge.
- tests/test_web_route_shell.py:90–92 — `.w-card` `border: 1px solid var(--wash-hairline)`, `background: var(--wash-surface)` → `border: 1px solid var(--w-glass-line)`, `background: var(--w-glass-fill)` — analysis sections are Wash cards (glass, light edge, radius-lg).
- tests/test_web_shell_components.py:97 — `box-shadow: inset 4px 0 var(--w-approval-pigment);` in approval.css → `.w-approval__head` background `color-mix(in srgb, var(--w-approval-wash) 72%, var(--w-glass-fill))` and no `inset 4px 0` / `--w-approval-pigment` — the approval card is a state card that escalates to danger; state now shows in the tinted header strip, not a side stripe (G§3.4, §9.9).

## New tests

- tests/test_wash_css_contract.py `test_no_coloured_side_borders` — every `border-left/right/inline-start/inline-end(-color)` uses only `--wash-hairline(-strong)`, `--wash-glass-*`, `transparent`, `currentColor` or width/style keywords.
- tests/test_wash_css_contract.py `test_no_inset_side_stripes_or_pseudo_element_bars` — no `inset Npx 0` box-shadow with N ≥ 2, and no ≤6px absolutely positioned `::before/::after` bar painted down a side.
- tests/test_wash_css_contract.py `test_glass_recipe_is_built_from_the_glass_tokens`, `test_glass_surfaces_use_the_recipe_with_backdrop_filter`, `test_glass_has_solid_fallbacks_and_a_flat_ambient` — level 1/1s/2/3 surfaces use the recipe; planes and floating panes carry `backdrop-filter` + `-webkit-` twin; every `backdrop-filter` goes through the recipe; all four fallbacks reset to solid surface/raised/sunken and flat canvas.
- tests/test_wash_css_contract.py `test_every_layout_page_declares_an_ambient_wash` — every page extending `_layout.html` sets its ambient (dawn: /, /design; lagoon: /work, /trace, /jobs/trace, /models/costs; meadow: /containers); 960px art on small screens.
- tests/test_web_a11y_contract.py `test_approval_card_keeps_its_tinted_header_and_status_chip` — the approval card keeps a ≥70% tone-wash header strip (danger escalation via the tone channel) and the "◆ Needs you" status chip in its head, on glass, with no inset stripe.
