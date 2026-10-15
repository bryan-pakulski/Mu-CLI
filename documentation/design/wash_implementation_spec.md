# Wash overhaul — implementation spec

**Status:** Approved v1 · 2026-10-15
**Design source of truth:** [`wash_design_guide.md`](wash_design_guide.md). This
spec says *how* and *in what order*. The guide says *what*. Section references
like "G§9.9" point into the guide.
**Surfaces:** web GUI (`mu/gui`), Android (`mobile/android`), terminal REPL and
pickers (`mu/ui`, `mu/cli`, `mu/commands`), jobs TUI (`mu/ui/jobs`), container
TUI (`mu/container/tui.py`).

---

## 1. Goals, non-goals, invariants

### Goals

1. Every surface renders from **one token and vocabulary source**, so drift
   such as `ok` being `#9ece6a` in one place and `#73c991` in another, or
   `provisioning` labelled "provisioning" in one place and "preparing" in
   another, becomes impossible: a test fails if it happens.
2. Replace the layered restyles with one CSS architecture, one mobile
   component kit, and one terminal theme:
   - web: `app.css` → `product.css` → `crystal.css` → `clarity.css` →
     `refinement.css` → `conversation.css` → `theme.css`
   - mobile: legacy primitives plus planos
   - terminal: 8 consoles and 5 style dicts
3. Ship the Wash look: Paper and Dusk themes, pigments, watercolour
   atmosphere, Inter and JetBrains Mono, pill actions, and the signature
   components (approval card, tool card, composer, diff, meters).
4. Fix the UX defects the audit found (§2.4) as part of the move.

### Non-goals

- No changes to the agent loop, providers, job engine, or API shapes. The only
  exception is purely additive view-model fields where a component needs data
  the server already has; each such case is called out below.
- No front-end framework migration. Alpine plus vanilla JS stays, and there is
  no build step for the web GUI. The token generator is a dev-time Python
  script whose output is committed.
- No new network dependencies at runtime. Fonts and art are bundled.

### Invariants (from AGENTS.md; every PR must preserve them)

- **Approval gates are not weakened.** The redesigned approval UI must send
  exactly the same `/api/prompts` payloads (`remember` now correctly wired on
  mobile). Plan mode stays read-only, and YOLO stays an explicit opt-in that
  is visibly indicated.
- **The L0–L5 context contract is untouched.** Meters only *display* existing
  data: `context_snapshot` events and `budgets.effective_fill`.
- **Tool capability, secret-path, and result-scrubbing checks stay intact.**
  The UI never displays unscrubbed results that it did not display before.
- **Tests:** never weaken, skip, or delete tests to make a change pass. See §8
  for how source-pinning tests are migrated legitimately.
- The full suite (`python3 -m pytest tests/ -q`) and the mobile jest suite are
  green before every commit.

---

## 2. Current state (audit summary)

### 2.1 Web (`mu/gui`)

- **Tokens:** `static/css/tokens.css` is a good base. It has a Tokyo-Night
  dark palette and a GitHub light palette. Only `tokens.css` (30 hex values)
  and `work.css` (26) contain hex literals; every other CSS file uses `var()`.
  This makes a token-level reskin cheap.
- **Cascade:** `app.css` (6074 lines) → `product.css` → `crystal.css` →
  `clarity.css` → `refinement.css` → `conversation.css` → `theme.css`.
  Problems:
  - 92 selectors are defined in two or more of these files.
  - There are 50+ `!important` declarations.
  - `theme.css` is loaded twice on `/work`.
  - JS injects `popouts.css`, `pricing_settings.css` and `pricing_rows.css`.
- **`/work`:** `work.css` re-declares the whole palette under `.work-app`
  with its own spacing, font and size tokens. `--fs-sm` is 12 there but 13 in
  `tokens.css`.
- **Buttons:** three button systems plus about 15 one-off button classes.
- **Breakpoints:** about 25 ad-hoc values.
- **Fonts:** none bundled. Inter and Geist apply only if they happen to be
  installed.
- **Standalone pages:**
  - `trace.html`, `static/job_trace.html` and `static/model_costs.html`
    duplicate the head boilerplate (theme bootstrap, favicon, CSS list).
  - The latter two load the whole chat CSS stack just to get a header.
- **Unused and unsafe pieces:**
  - htmx is loaded but there are zero `hx-` attributes.
  - `marked` output is not sanitized.
- **Accessibility:**
  - `base.html` modals lack `role="dialog"`.
  - The trace page has 18 click handlers on non-interactive elements.
  - Main-shell toasts are not in a live region.
  - There are about 12 local `outline:none` overrides.

### 2.2 Android (`mobile/android`)

- **Tokens:** `src/theme/tokens.ts` mirrors `work.css`. There are almost no
  raw hex values outside it. About 60 alias keys collapse onto the same values
  (for example `textDim`, `textSoft`, `muted` and `textSecondary`).
- **Two component kits:**
  - legacy `Button`/`Card`/`Badge`/`BottomSheet`/`SafeAreaModal`
  - `planos.tsx` (`PlanosButton`/`Panel`/`Pill`/…)

  Usage is split roughly 60/40.
- **Dead code:**
  - Screens: `ChatScreen.tsx`, `FeatureScreen`, `InspectorScreen`,
    `SessionsScreen`, `SessionDetailScreen`, `TraceScreen`,
    `TraceDetailScreen`, `TracesScreen`.
  - Components: `Header.tsx`, `SessionsDrawer.tsx`, `InspectorDrawer.tsx`,
    `ArtifactsSheet.tsx`, `IconButton`, `Divider`.
- **Chat:** shows **no tool calls**. Only a "Running X" label appears.
- **Theme:**
  - The theme override is not persisted.
  - No `<StatusBar>` is rendered.
  - The splash and icon background `#1A1814` matches neither palette.
- **Touch targets:** nine controls are under 44dp, for example the copy
  button at 32×28 with no label.
- **PromptHost:** the "Always allow" checkbox is ignored (`remember: false`
  is always sent) and has no `accessibilityState`.
- **Docs and tests:**
  - `ACCESSIBILITY_AUDIT.md` and the README palette section are stale.
  - `__tests__/job-views.test.ts:88-93` pins 24 hex values.
  - Its contrast-floor test (lines 81-86) is good and is kept and
    generalised.

### 2.3 Terminal

- **Jobs TUI:** `mu/ui/jobs/theme.py` is the only tokenised theme. It handles
  NO_COLOR and truecolor and is used only by the jobs TUI.
- **Everywhere else:**
  - About 400 inline style literals.
  - 8 `Console()` instances.
  - 5 independent prompt_toolkit `Style.from_dict` dicts. Picker "current" is
    green in two and yellow in two. Hints use `ansiblack bold`, which is
    invisible on dark themes.
  - Monokai is hardcoded.
  - The diff uses hardcoded dark backgrounds (`on #3a0000`, …).
  - The splash is block-letter art.
  - The YOLO indicator blinks.
  - There is no light/dark terminal detection.

### 2.4 Correctness bugs found by the audit (fixed in Phase 0)

| # | Bug | Location |
|---|---|---|
| B1 | Tool args interpolated unescaped into Rich markup in the approval prompt, so brackets can garble the prompt or raise `MarkupError` | `mu/agent/approval.py:147-164` |
| B2 | `RichUI.show_info` wraps strings in `Text(..., style="blue")`, so markup prints literally (plan/skill banners, toggles, loop_body notices). It also stringifies `Table` objects, so the sub-agent progress panel **never renders** (`loop_body.py:2252`, `tools/agent/wait.py:177`, `poll.py:40`). | `mu/ui/rich_ui.py:261-271` |
| B3 | `WebUI` forwards raw Rich markup to web and mobile | `mu/gui/web_ui.py:~173` |
| B4 | Container TUI: exception text unescaped in markup (`tui.py:506,540,599,690`); literal `[dim]` printed with `markup=False` (`:359`) | `mu/container/tui.py` |
| B5 | Splash interpolates unescaped session, model and folder values | `mu/cli/display.py` |
| B6 | Mobile "Always allow this tool" checkbox value ignored; no `accessibilityState.checked` | `mobile/android/src/components/PromptHost.tsx:410-419` |
| B7 | `MODE_PROMPT_STYLES` references `mode-teacher`, which is undefined in the style dict | `mu/ui/input.py:27,658` |
| B8 | Status vocabulary copied into four places and already divergent (`provisioning` label) | `mu/ui/jobs/theme.py:27-52`, `work/view.js:43-53`, `mobile/.../theme/status.ts:9-28`, `api/jobNotifications.ts:12` |

---

## 3. Foundation architecture

### 3.1 New package: `mu/design/`

```
mu/design/
  __init__.py          # load_tokens(), load_vocabulary(), Theme dataclasses (cached)
  tokens.json          # THE token source (G§12)
  vocabulary.json      # status/state/attention/verdict/severity → glyph, semantic, label
  icons/               # source SVGs (24 grid, 1.5 stroke) — one file per icon
  art/                 # wash generator params (one JSON per wash, seeded)
scripts/design/
  build_tokens.py      # tokens.json → CSS + TS outputs (deterministic, sorted)
  build_icons.py       # icons/*.svg → static/icons/wash.svg sprite + mobile TSX
  paint_washes.py      # art/*.json → WebP washes for web + mobile (numpy + Pillow; dev-only)
  check_contrast.py    # WCAG checks over tokens.json (also used by tests)
  snap.py              # Playwright visual snapshots of the component gallery
```

**`tokens.json` shape** (abridged):

```json
{
  "themes": {
    "paper": {
      "color":   { "canvas": "#F8F6F1", "surface": "#FFFEFB", "sunken": "#F1EEE7",
                   "raised": "#FFFFFF", "hairline": "#E6E1D8", "hairline-strong": "#D6CFC3",
                   "ink": "#1E1A17", "ink-2": "#4E4844", "ink-3": "#6B645F",
                   "plum": "#2B1436", "on-ink": "#FFFEFB", "scrim": "rgba(30,26,23,.32)" },
      "pigment": { "lagoon": {"wash": "#DCEDEE", "pigment": "#5FA3AB", "ink": "#2A6870"}, "...": {} }
    },
    "dusk": { "...": {} }
  },
  "semantic": { "accent": "lagoon", "running": "sky", "ok": "meadow", "attention": "apricot",
                "caution": "sun", "danger": "rose", "plan": "lilac", "info": "sky" },
  "mode":   { "default": "lagoon", "feature": "sky", "research": "lilac", "teacher": "sun",
              "security": "rose", "debug": "apricot", "loop": "meadow", "plan": "lilac" },
  "layer":  { "L0": "plum", "L1A": "lagoon", "L1B": "meadow", "L2": "lilac",
              "L3": "sun", "L4": "sky", "L4B": "rose", "L5": "apricot" },
  "series": ["lagoon", "apricot", "lilac", "meadow", "rose", "sky", "sun"],
  "type": { "display": {"size": 48, "line": 52, "weight": 500, "tracking": -0.03}, "...": {} },
  "space": [4, 8, 12, 16, 20, 24, 32, 48, 64, 96],
  "radius": {"xs": 6, "sm": 10, "md": 14, "lg": 20, "xl": 28, "pill": 999},
  "elevation": { "...": {} }, "motion": { "...": {} }, "z": { "...": {} },
  "terminal": { "ansi16": {"accent": "cyan", "running": "blue", "ok": "green", "attention": "yellow",
                           "danger": "red", "plan": "magenta", "muted": "bright_black"},
                "xterm256": { "...": {} } }
}
```

All values are those in G§1–§5. Dusk washes are computed by the generator as
`pigment` mixed at 16% over `surface`, then written out as hex so that every
consumer gets a literal value.

**`vocabulary.json`** has these keys:

- `status`: pending, active, waiting, done, failed, skipped → glyph and
  semantic.
- `job_state`: every `JobState` member → status and canonical label.
- `job_status`: every legacy `JobStatus` member → status and label.
- `attention_reason`: every member → label and short action verb.
- `verdict`
- `severity`
- `check_status`
- `env_status`
- `session_state`: active, working, loaded.

`provisioning` and the legacy `preparing` both use the label **"Preparing"**.

### 3.2 Generated outputs (committed; never hand-edited)

| Output | Consumer |
|---|---|
| `mu/gui/static/css/wash/tokens.css` | Web: `:root` constants plus `[data-theme=paper]` and `[data-theme=dusk]` blocks, `--wash-*` names (G§12) |
| `mu/gui/static/js/wash_vocabulary.js` | Web: `window.WASH_VOCAB` (replaces `work/view.js:43-53` STATUS tables) |
| `mobile/android/src/theme/wash.generated.ts` | Mobile: `paper`, `dusk`, `vocabulary` objects with exact TS types |
| `mu/gui/static/icons/wash.svg` | Web icon sprite |
| `mobile/android/src/components/icons/*.generated.tsx` | Mobile icons (Phase 5) |

Each generated file starts with a `// GENERATED by scripts/design/build_tokens.py
— do not edit` header plus a SHA-256 of the source JSON.

Python surfaces read `tokens.json` and `vocabulary.json` directly through
`mu.design` at runtime, with no generated Python files.

### 3.3 Fonts

- Web: `mu/gui/static/fonts/` gets `InterVariable.woff2`,
  `InterVariable-Italic.woff2`, `JetBrainsMono-Variable.woff2` and the
  `OFL.txt` licences. `@font-face` goes in `wash/base.css` with
  `font-display: swap`. Only the shell font is preloaded.
- Mobile: put TTF copies in `mobile/android/assets/fonts/`. `App.tsx` calls
  `useFonts`, and the splash is held via `expo-splash-screen` until the fonts
  load (expo-font is already a dependency).
- Terminal: not applicable.

### 3.4 Contrast verification

`scripts/design/check_contrast.py` asserts the following for both themes:

- every text token (`ink`, `ink-2`, `ink-3`, `plum`, and each semantic `ink`)
  is at least 4.5:1 against `canvas`, `surface`, `sunken` and `raised`
- each semantic `ink` is at least 4.5:1 on its own `wash`
- `on-ink` is at least 4.5:1 on `ink`
- `control-border` is at least 3:1 on `surface` and `sunken`; this is the
  non-text UI boundary used for form controls. `hairline` and
  `hairline-strong` are decorative and exempt.

The script fails with a table of offenders. The values in G§1 already pass.
They were measured during drafting; the lowest is `ink-3` on Paper `sunken`
at 5.02:1.

### 3.5 Watercolour art pipeline

`scripts/design/paint_washes.py <wash.json> --theme paper|dusk --width 1600|800`.
The algorithm was prototyped while drafting this spec. The prototype is
`reference/paint_wash_prototype.py`, and its six outputs
(`reference/wash-{dawn,lagoon,meadow}-{paper,dusk}.webp`) are in the same
folder:

1. Paper base colour from tokens.
2. For each blob in the params: an elliptical mask, edges displaced by fractal
   noise (ragged bleed), a pigment-pooling ring at the edge, and internal
   granulation noise. Composite in multiply mode using the pigment colour.
3. Global paper-grain noise at ±2.5%.
4. A vertical fade to `canvas` (bottom-dissolve, G§6.2.3).
5. Gaussian blur at 1.2px. Write WebP at quality 82.

The result is about 25 KB at 1600×900.

**Determinism:** a fixed seed per wash plus pinned numpy and Pillow
versions, recorded in the output metadata.

**Outputs:**

- web: `mu/gui/static/wash/art/{dawn,lagoon,meadow}-{paper,dusk}-{1600,800}.webp`
- mobile: `mobile/android/assets/wash/*.webp` (800w only)

**Dependencies:** numpy and Pillow are dev-time only. Pillow is already in
`requirements.txt`; add numpy to a new `requirements-dev.txt`. The runtime
never paints.

**Mode tints and spot washes** are CSS-only on web:

- mode tints: layered `radial-gradient`s of `--wash-mode-*` plus an SVG
  `feTurbulence` mask
- spot washes: an `<svg>` symbol with a turbulence-displaced blob, coloured
  via `currentColor`

On mobile, pre-rendered small WebPs serve as spot washes and a solid wash
band serves as the mode tint.

### 3.6 Component gallery

- **Web:** add a `GET /design` route in a new `mu/gui/routers/design.py`.
  - It is enabled only when `MUCLI_DESIGN_GALLERY=1` or with
    `--gui --dev`.
  - It renders `templates/design/gallery.html`: every component from G§9 in
    every state, in both themes side by side, using static fixture data. The
    fixtures replay a canned SSE stream through the real `routeEvent()`, so
    the real render code is exercised.
  - This is the visual review surface for every phase and the input for
    `snap.py`.
- **Mobile:** a hidden `__DesignGallery` screen that can be reached in dev
  builds only (`__DEV__`).
- **Terminal:** `python3 -m mu.ui.theme.gallery` prints every terminal
  component in the active colour mode. Run it with `NO_COLOR=1`,
  `COLORTERM=truecolor`, and `TERM=xterm` (16-colour) to review each mode.

---

## 4. Web implementation

### 4.1 Target CSS architecture

```
mu/gui/static/css/wash/
  tokens.css         # generated
  base.css           # @font-face, reset, element defaults, focus ring, selection, scrollbars, reduced-motion/contrast/forced-colors
  layout.css         # app shell grid, top bar, sidebar, stage, page hero, breakpoints sm/md/lg/xl
  components/
    button.css chip.css card.css nav.css tabs.css form.css composer.css
    message.css toolcard.css approval.css prompt.css diff.css code.css markdown.css
    progress.css meter.css stepper.css toast.css banner.css modal.css drawer.css
    menu.css palette.css table.css empty.css session-list.css terminal.css
  patterns/
    welcome.css work.css containers.css trace.css job-trace.css model-costs.css
    panels.css       # stage mode panels (teacher, feature, research, security, loop, debug, memory, files, …)
    settings.css     # settings drawer
  wash.css           # @layer order + @imports (the single stylesheet each page links)
```

The layer order is declared once in `wash.css`. Later layers win, so
`legacy` comes **first** and has the lowest priority:

```css
@layer legacy, tokens, base, layout, components, patterns, utilities;
```

Rules:

- Each component file owns its selectors exclusively. A test fails if any
  class selector is defined in two component files.
- There is no `!important` outside `utilities`. A test enforces this.
- There are no hex, `rgb()` or raw `px` font-size values outside
  `tokens.css`. A test enforces this, allowing only 0, 1px and 2px.
- Breakpoints are only `600`, `840`, `1200` and `1600`, written as
  `@media (width < 840px)`. A test enforces this.
- **Class naming:** `.w-{component}` and `.w-{component}__{part}`, with
  `[data-variant]`, `[data-state]` and `[data-tone]` attributes. Examples:
  - `.w-btn[data-variant=primary]`
  - `.w-chip[data-tone=attention]`
  - `.w-toolcard[data-state=running]`

  The new prefix lets old and new CSS coexist during migration without
  collisions. Today `.trace-*` collides between the chat and the trace page.

**Migration mechanics.**

- The old CSS files are imported with `@import url(...) layer(legacy)`.
  Because `legacy` is the lowest-priority layer, any Wash rule beats any
  legacy rule regardless of specificity, without `!important`.
  - Legacy rules still apply wherever no Wash rule exists yet, so pages keep
    working throughout the migration.
  - One caveat: `!important` reverses layer priority, so a legacy
    `!important` beats Wash normal rules. W2 therefore strips the 50+ legacy
    `!important` declarations as its first step: `work.css` 20, `app.css` 15,
    `conversation.css` 9, `floating_popouts.css` 8, `refinement.css` 6.
- Each step removes legacy selectors as their Wash replacements land.
- Phase 4 ends with an empty `legacy` layer and the deletion of these files:
  - `app.css`, `product.css`, `crystal.css`, `clarity.css`, `refinement.css`
  - `conversation.css`, `theme.css`, `popouts.css`, `floating_popouts.css`
  - `pricing_*.css`, `route-product.css`, `containers*.css`, `work*.css`
  - the old `tokens.css`

### 4.2 Templates

- **New `templates/_layout.html`:** shared `<head>`, containing:
  - the pre-paint theme script, reading `mucli-theme` with values
    `system`/`paper`/`dusk`; legacy `light`/`dark` values are migrated on
    read
  - favicon (the wash drop mark)
  - font preload
  - `wash.css`
  - the vendor scripts each page declares

  It also holds the app shell: top bar, sidebar, `{% block content %}`,
  global overlays, the toast region, and the command palette.
- `base.html` becomes a thin wrapper over `_layout.html`, with the current
  global modals moved into `templates/partials/`.
- `trace.html` extends `_layout.html`.
- `static/job_trace.html` and `static/model_costs.html` become Jinja
  templates (`templates/job_trace.html`, `templates/model_costs.html`),
  served at `/jobs/trace?job=` and `/models/costs`. The old static URLs return
  a 308 redirect. Links in `work.html` are updated.
- **Shared partials** in `templates/partials/`:
  - `topbar.html`, `sidebar.html`, `toast_region.html`, `palette.html`
  - `approval_card.html` (Alpine template)
  - `tool_card.html`, `empty_state.html`, `icon.html` (a sprite `<use>`
    macro)
- **Jinja macros** in `templates/macros/wash.html`:
  - `btn(label, variant, icon, attrs)`, `chip(...)`, `icon(name, size)`
  - `empty_state(...)`, `status(state)`; the latter reads the vocabulary
    via a Jinja global injected in `app.py`

### 4.3 JavaScript changes

The DOM contract that tests and other code rely on is kept: element ids,
`x-data`/`$store` names, `data-*` hooks, event kinds, and API calls. **Only
class names and markup structure change.**

| Area | Change |
|---|---|
| `app.js` `renderMarkdown` (~6280) | Wrap output in DOMPurify (vendored `purify.min.js`), add `.w-code` wrapper with header and copy, Wash hljs theme (`static/vendor/hljs-wash.css` generated from tokens, replacing github-dark/light). **Security improvement;** reviewed separately in its PR. |
| `app.js` `routeEvent` tool_call/tool_result | Render through one `renderToolCard(ev)` that is shared with the sub-agent panel and history hydration. Group consecutive same-kind calls. The done glyph settles to neutral after 1.2s. |
| Approval queue (`chat.html:392-545`) | Replace with `partials/approval_card.html`. Focus goes to the heading on appear, with no auto-focus on Approve. `⏎` and `a`/`d` shortcuts are scoped to card focus. An assertive live region announces it. Resolved prompts become a breadcrumb, including YOLO auto-approvals (needs the existing `prompt_resolved` payload to carry `auto: true` when auto-approved; **additive field**, from `web_ui.py`). |
| Diff | Replace plain `pre.diff-pane` with `renderDiff(unifiedText, {mode})` in a new `static/js/wash/diff.js`, shared by chat, approvals and `/work` (`work/view.js` diff table moves onto it). |
| Theme toggle | The `theme` store gets `system \| paper \| dusk`. It updates `html[data-theme]` and the `mucli-theme-change` event (the viz contract), and posts to artifact frames as today. |
| Toasts | Move `.toast-container` into `role="status" aria-live="polite"`; errors go in a separate `aria-live="assertive"` region. `/work` `work/ui.js` toasts use the same partial. |
| Command palette | Lift `work/ui.js` palette into `static/js/wash/palette.js`, loaded on every page, with actions registered per page. |
| Dialogs | `dialogs.js` `x-dialog` becomes the only modal mechanism. All `base.html` modals get `role="dialog"`, `aria-modal` and `aria-labelledby`. `work/ui.js` Dialog and `containers.js` `.manager-modal` move onto the same markup. |
| Inline styles | Remove the inline `style=` attributes counted in the audit: 18 in `work/view.js`, 10 in `work/rules.js`, and those in templates. Geometry writes for popover positioning (`floating_popouts.js`) stay. |
| htmx | Remove `htmx.min.js` and its `<script>` (unused). |
| Stage panel selector | Replace the two `<select>`s (`panel_tabs.html`) with a segmented and menu control. Keep `panel-mode-select` and `panel-tool-select` ids as hidden-compatible hooks only if tests or JS depend on them; otherwise update the tests as part of the spec change (§8). |
| Trace page | Convert the 18 click handlers on `div`/`span`/`li` elements to `<button>`s. Charts read colours via `muCssToken('--wash-layer-L0')` etc. |

### 4.4 Web phases

**W1 — Token reskin ("Wash day one").** Smallest diff, largest visual change.

- Rewrite `static/css/tokens.css` so that **every existing name** (`--accent`,
  `--surface`, `--text`, `--viz-*`, the legacy aliases, `--st-*`,
  `--mode-*`, `--context-*`) points at the generated `--wash-*` values. Old
  names map onto Wash semantics, for example `--warn` → `attention`,
  `--running` → `running`, and `--viz-N` → the layer pigments in G§1.4
  order.
- Collapse `work.css:13-96` into the same aliases. Delete its hex values and
  the `body:has(.work-app)` hardcodes.
- Rename the theme values: `[data-theme=dark]` becomes `dusk` and `light`
  becomes `paper`. Keep the old selectors as aliases until W4.
- Bump `--radius-sm` → `--wash-radius-sm` (10) and `--radius-lg` → 20, and
  set buttons to `radius-pill` in `theme.css` and `work.css`.
- Add fonts.
- **Acceptance:** every page renders in Paper and Dusk with the new colours
  and fonts. The suite and visual review in `/design` pass. There are zero hex
  literals outside `wash/tokens.css`.

**W2 — Architecture.**

- Create `wash/` with `base.css`, `layout.css`, `wash.css`, the layer
  import of the legacy files, and `_layout.html`.
- Move the standalone pages onto the shared layout.
- Add the icon sprite. Replace inline SVGs and the "toggle theme" text button
  with a sprite and an icon button.
- Introduce the four breakpoints and delete the ad-hoc ones as layouts move.
- Remove htmx.
- **Acceptance:** one `<link>` per page (`wash.css`). `theme.css` is no
  longer double-loaded. `job_trace` and `model_costs` no longer load the chat
  CSS.

**W3 — Signature components** (each is a PR with gallery states):

- button, chip, card, nav, tabs and segmented
- form controls
- composer
- messages and markdown/code (+DOMPurify)
- tool card
- approval card and prompts
- diff
- meters (compact ring plus stacked layer bar)
- stepper
- toast and banner (plan and YOLO banners in the top bar on every page)
- modal, drawer, menu and palette
- empty and error states
- welcome hero with Dawn art

Each component PR deletes the legacy selectors it supersedes.

**W4 — Pages.**

- `/` main shell: the sidebar with Navigate/Sessions/Threads, the stage
  panels (shared panel header with mode tint), and the settings drawer.
- `/work`: dashboard stat strip, needs-you state cards, job rows, job detail,
  diff, history table, setup, settings.
- `/containers`: Meadow hero, environment cards, create flow as a stepped
  modal.
- `/trace`, `/jobs/trace`, `/models/costs`: shared shell, Wash charts per the
  updated viz guide.
- Delete the legacy layer.
- **Acceptance:** the legacy CSS files are gone. The CSS contract tests
  (§8.2) pass. Playwright snapshots exist for every page in both themes at
  1440×900 and 390×844.

---

## 5. Android implementation

### 5.1 Theme

- `src/theme/wash.generated.ts` (generated) is the only colour source.
- `src/theme/tokens.ts` becomes a thin adapter:
  - It exports `colors` in the **new semantic shape**: `canvas`, `surface`,
    `sunken`, `raised`, `hairline`, `ink`, `ink2`, `ink3`, `plum`, `onInk`,
    and `accent`/`ok`/…, each as `{wash, pigment, ink}`, plus `mode`, `layer`
    and `series`.
  - It also exports a `legacyColors` view that keeps the current keys
    (`textDim`, `glassStrong`, …) mapped onto Wash during migration.
  - `legacyColors` is deleted at the end of M3.
- `ThemeContext`:
  - Preference is `system | paper | dusk`, **persisted in AsyncStorage**
    (B-fix).
  - It renders `<StatusBar style>` from the theme.
  - There is a "Reduce decoration" preference.
- `app.json`: set the splash and adaptive-icon background to Paper `canvas`,
  with a dark variant via `expo-splash-screen` config, and a new icon (the wash
  drop mark on paper).
- Add a `tint(color, alpha)` replacement, `washOf(semantic)`. Ad-hoc alpha
  literals (0.08–0.6) are removed in favour of `wash` tokens.

### 5.2 One component kit: `src/ui/`

Create `src/ui/` (re-exported from `src/ui/index.ts`), implementing G§9:

- `Button` (variants primary/secondary/ghost/tonal/danger/icon; sizes md/lg)
- `Chip` (status/meta/eyebrow/filter/removable), `CountBadge`
- `Card` (default/sunken/state), `StatStrip`
- `Tabs`, `Segmented`
- `TextField`, `Select`, `Switch`, `Checkbox`, `Slider`
- `Sheet` (one bottom-sheet implementation; replaces `ModernBottomSheet` and
  `SafeAreaModal`), `Dialog`
- `Toast`, `Banner`
- `ToolCard`, `ApprovalCard`, `ChoicePrompt`
- `DiffView`, `CodeBlock` (Wash syntax theme from tokens)
- `Markdown` (one rules and styles module replacing the inline rules in
  `ChatScreenProduct.tsx` and `planMarkdown.ts`)
- `InkDrop`, `ProgressBar`, `Skeleton`
- `PipelineStepper`, `ContextRing`, `LayerBar`
- `EmptyState`, `ErrorState`
- `Icon` (semantic names, backed by generated SVGs; Ionicons outline as an
  interim mapping)
- `WashBackground` (art image plus bottom fade; replaces
  `AtmosphericBackground`)

Interaction rules:

- All pressables use one `Pressable` wrapper with:
  - pressed scale 0.98
  - a 44dp minimum size via `hitSlop`
  - `accessibilityRole`/`State`
  - optional haptics (`expo-haptics`, new dependency)
- The ~400 `TouchableOpacity` call sites migrate onto it.
- Disabled is always 0.4 opacity plus `accessibilityState.disabled`.
- Icon sizes come only from tokens: 16, 20 and 24.

### 5.3 Mobile phases

**M1 — Token reskin.**

- Generate `wash.generated.ts`. Map `legacyColors` onto it.
- Fonts via `useFonts`. Persist the theme. Add `StatusBar`. Fix the splash
  colours.
- Rewrite `job-views.test.ts:88-93` to assert the palette **equals
  `wash.generated.ts`** rather than literal hex values (see §8). Keep and
  extend the contrast-floor test (lines 81-86) to every semantic `ink` on its
  `wash`.
- **Acceptance:** the app renders in Paper and Dusk. Jest is green.

**M2 — Kit unification.**

- Build `src/ui/`. Migrate every screen off the legacy primitives and
  `planos.tsx`, then delete them.
- Delete the dead screens and components listed in §2.2, after confirming no
  imports with `tsc --noEmit` and jest.
- Fix the sub-44dp targets (copy button gets an `accessibilityLabel`).
- B6: wire the "Always allow" checkbox into `remember`, and remove the
  duplicate "Always allow" button (one control, one meaning).
- **Acceptance:** `grep -r "planos\|TouchableOpacity" src/` returns only
  `src/ui` internals. `testID`s are unchanged.

**M3 — Navigation and signature patterns.**

- Bottom tabs (Chat, Work, Workspace, More) via
  `@react-navigation/bottom-tabs`, which is already installed. The edge-swipe
  drawers stay as shortcuts.
- Settings becomes a real screen under More.
- Chat:
  - `useChatSession.ts:1129-1150` keeps the generating label **and** appends
    tool-call items to the message list.
  - New message item kinds `tool_call`/`tool_result` are rendered by
    `ToolCard`, grouped like web.
  - **Client-only change:** the SSE already carries these events.
- `PromptHost` uses `ApprovalCard` in a non-dismissable `Sheet`.
- `WashBackground` on Connect, Session start and the Work dashboard.
- Rewrite `ACCESSIBILITY_AUDIT.md` from a real audit (TalkBack, font scale
  200%, contrast from `check_contrast.py`). Update the README palette section.
- Bump the version to 0.5.0 (the app is currently 0.4.2) and build the APK.
- **Acceptance:** an Android emulator walkthrough of every tab in both themes
  and with TalkBack. Jest is green.

---

## 6. Terminal implementation

### 6.1 New package: `mu/ui/theme/`

```
mu/ui/theme/
  __init__.py      # public API: get_console(), T (tokens view), style(name), status(state), esc()
  detect.py        # color depth + background detection
  rich_theme.py    # rich.theme.Theme with "wash.*" named styles
  pt_style.py      # prompt_toolkit Style (single dict) for prompt, toolbar, completions, all pickers
  pygments.py      # WashPaperStyle / WashDuskStyle pygments styles (replace monokai)
  glyphs.py        # status glyphs, tool-kind labels, spinner frames (from vocabulary.json)
  gallery.py       # `python -m mu.ui.theme.gallery`
```

**Detection (`detect.py`):**

- Colour depth:
  - `NO_COLOR` gives `none`.
  - `COLORTERM in {truecolor, 24bit}` or `WT_SESSION` gives `truecolor`.
  - `TERM` containing `256color` gives `256`.
  - Otherwise `16`.
  - This keeps today's `color_mode()` semantics for jobs and adds 256.
- Background:
  - `MUCLI_TERM_THEME=paper|dusk` overrides.
  - Else `COLORFGBG` (bg index 7 or 15 means paper).
  - Else an OSC 11 query (only when stdin and stdout are TTYs, with a 100ms
    timeout and non-blocking read; skipped under tmux unless passthrough is
    confirmed).
  - Default: dusk.
  - The result is cached per process.

**Named Rich styles** (`wash.*`) cover:

- `wash.ink`, `wash.ink2`, `wash.muted`, `wash.plum`
- `wash.accent`, `wash.running`, `wash.ok`, `wash.attention`, `wash.caution`,
  `wash.danger`, `wash.plan`
- `wash.mode.{name}`, `wash.layer.{L0..L5}`
- `wash.key` (bold accent), `wash.hint`, `wash.border` (hairline),
  `wash.code`, `wash.path`
- `wash.diff.add`, `wash.diff.del`, `wash.diff.hunk`

In each colour mode a style resolves to the truecolor ink step, the xterm256
nearest, the ANSI-16 name, or `none`/attributes only.

**Single console.** `get_console()` returns a process-wide `Console(theme=…)`.
The 8 module-level `Console()` instances are replaced:

- `mucli.py:39`
- `utils/helpers.py:17`
- `mu/ui/render.py:12`
- `mu/ui/rich_ui.py:31`
- `mu/cli/display.py:28`
- `mu/cli/feature.py:32`
- `mu/commands/container.py:26`
- `mu/container/tui.py:27`

`RichUI.console` *is* `get_console()`, so test doubles that patch
`RichUI.console` keep working.

**Escaping rule.** Any value that is not a literal from our own code goes
through `esc()` (`rich.markup.escape`) before it is interpolated into markup.
This covers model output, tool args, paths, exceptions, session names and
model names. A lint-style test greps for f-strings that contain both `[` and
`{` inside `console.print`/`show_info` calls without `esc(` and flags new
occurrences. Existing ones are fixed in Phase 0.

### 6.2 `RichUI` API changes (B2)

- `show_info(message: str, *, markup: bool = False)`. With the default
  `False` the message is printed literally in `wash.ink2`. Callers that
  intentionally pass markup set `markup=True`.
- New `show_renderable(renderable)` for `Table`/`Panel`. It is used by the
  sub-agent progress callers (`loop_body.py:2252`, `tools/agent/wait.py:177`,
  `tools/agent/poll.py:40`).
- New `show_notice(kind, message)` with kind in
  `info|ok|attention|caution|danger|plan`. It prints `glyph message` in the
  semantic colour and is used for mode banners and toggles. It replaces the
  `[bold black on cyan]` banners in `plan_mode.py:194`, `skills/__init__.py:284`
  and `commands/stats.py:8`.
- `WebUI` (`mu/gui/web_ui.py`) implements the same three methods. Markup
  strings are reduced with `Text.from_markup(msg).plain` (B3) and `kind` is
  forwarded so web and mobile render the matching banner or toast tone. The
  event gains an **additive** optional `kind` field.

### 6.3 Terminal component migration

| Component | Change |
|---|---|
| Splash (`mu/cli/display.py:224`) | One-line wordmark plus gradient rule plus a 3-row info grid (G§8.4). Values are escaped (B5). The context warning becomes `show_notice("caution", …)`. |
| Prompt (`mu/ui/input.py:769`, style dict `:658`) | `μ session · mode ›`, mode in pigment, `[plan]` and `[yolo]` chips, **no blink**. All mode classes are defined, including teacher and loop (B7). Style comes from `pt_style.py`. **Class names are preserved** (`mode-feature`, `plan-indicator`, `yolo-indicator`) because tests and toolbar code use them; only their colours change. |
| Bottom toolbar (`input.py:829,839`) | Key-hint styling (`wash.key` / `wash.hint`). |
| Pickers (4 files) | Delete the 4 local style dicts and use `pt_style.picker_style()`: cursor `❯` accent, current row accent wash (truecolor) or reverse (16), selected `✓` ok, hints `wash.hint` (fixes invisible `ansiblack` hints). |
| Approval (`approval.py:147-164`, `rich_ui.py:224`) | Rounded panel with attention border and the title `◆ Needs you · Permission to …`. Diff preview through the shared diff renderer. Key-hint row. B1 escaping. The `prompt_choice` label stays "Approval choice" (test contract). |
| Tool markers (`rich_ui.py:123,970,1018`) | `● kind  target` live, rewritten as `✓ kind  target  0.4s` in muted on completion (the `_GenerationLive` region already re-renders). No emoji. |
| Thinking | `wash.muted italic`. The test `test_streaming_unified_live.py:343` currently asserts `style == "dim italic"`. Thinking keeps an equivalent: `wash.thinking` resolves to `italic` plus muted colour, and the test is migrated to assert the named style (§8). |
| Diff (`rich_ui.py:557-660`) | Unified by default. Foreground ok/danger. Background tint only in truecolor, from the Paper or Dusk wash per the detected background. Side-by-side stays available above 160 columns. Remove the hardcoded `on #3a0000…`. Pygments: Wash style. |
| Markdown and code (`render.py:73`) | Code panels: `ROUNDED`, `wash.border`, Wash Pygments style. Use the same renderer for the jobs TUI content where possible; otherwise share the style map. |
| Meters (`rich_ui.py:298,331`, `/stats`) | `build_meter` uses `wash.ink2` fill, caution at ≥0.65 and danger at ≥0.85 (aligned with web; today 0.75/0.9). CTX/MEM/SCRATCH… keep their labels (test contract). The stacked L0–L5 bar uses layer styles. |
| Sub-agent panel (`progress.py:289`) | Shared status glyphs (`●◆✓✗○–`) replace `🔨 ⚠ ⏹`. Agent colour from `series`. Now actually renders (B2). |
| Mode banners | `show_notice`. |
| Container TUI (`mu/container/tui.py`) | Section headings `wash.plum bold`. Errors via `show_notice("danger", esc(exc))` (B4). Progress line deduplicated into `mu/container/progress_line.py`, shared with `commands/container.py:112`. |
| Commands (`mu/commands/*.py`, `mucli.py`) | Mechanical replacement of inline colour strings with `wash.*` names (about 300 literals). Tables use `box.SIMPLE_HEAD`. |
| Jobs TUI (`mu/ui/jobs/theme.py`) | Becomes a compatibility shim over `mu.ui.theme`: `TOKENS`, `STATUS`, `SEVERITY`, `pt_style()`, `sgr()` and `color_mode()` keep their signatures. The values come from `tokens.json`. The status tables come from `vocabulary.json` (B8). The spinner becomes `◌○◎●`. |

**Acceptance:**

- `grep -rnE "\[(bold |dim |italic )?(red|green|yellow|blue|cyan|magenta|white|black)" mu/ mucli.py utils/`
  returns nothing outside `mu/ui/theme/`.
- There is one `Console(` constructor in the codebase.
- The gallery renders correctly in the none, 16, 256 and truecolor modes, on
  both light and dark terminal backgrounds. Check with screenshots from
  `kitty` or `alacritty` with paper and dusk themes, plus a `NO_COLOR` text
  capture.

---

## 7. Phase plan and sequencing

```
P0 Foundations ──► P1 Reskin (W1 ∥ M1 ∥ T1) ──► P2 Web architecture (W2) ──► P3 Web components (W3) ──► P4 Web pages (W4)
                                          ├──► P5 Android (M2 → M3)
                                          └──► P6 Terminal (T2)
                                                                   all ──► P7 Cleanup & docs
```

| Phase | Contents | Size (PRs) | Depends on |
|---|---|---|---|
| **P0** | `mu/design/` (tokens, vocabulary, icons source), `scripts/design/*`, contract tests (§8.1), fonts vendored, art generated, `/design` gallery skeleton, **bug fixes B1–B8** (shippable on their own, first PR) | 3 | none |
| **P1** | W1 web token reskin; M1 mobile reskin; T1 `mu/ui/theme` package, single console, jobs theme shim, pickers and prompt style unified | 3 (parallel) | P0 |
| **P2** | W2 CSS layers, `_layout.html`, standalone pages onto the shell, sprite, breakpoints, htmx removal | 2 | P1 |
| **P3** | W3 signature components (about 10 small PRs, gallery-driven) | ~10 | P2 |
| **P4** | W4 pages; legacy CSS deleted | ~5 | P3 |
| **P5** | M2 kit unification, then M3 navigation and patterns; APK 0.5.0 | ~4 | P1 |
| **P6** | T2 terminal component migration (§6.3) | ~3 | P1 |
| **P7** | Remove legacy alias tokens (web `--accent` etc., mobile `legacyColors`, jobs `TOKENS` aliases if unused); rewrite the colour, glass and theme-contract sections of `documentation/visualization_style_guide.md` to use Wash (`paper`/`dusk`, `--wash-*`; keep the `mucli-theme-change` event); update the built-in visualization skill template tokens; update mobile README; add a "Design system" paragraph to AGENTS.md pointing at the guide | 1–2 | all |

Each PR:

- runs the full Python suite and mobile jest
- attaches before and after `snap.py` screenshots (Paper and Dusk, desktop
  and mobile viewport) of the gallery and affected pages
- states its user-visible changes and the test-migration ledger entries
  (§8.3)

---

## 8. Testing strategy

### 8.1 New contract tests (P0)

| Test file | Asserts |
|---|---|
| `tests/test_wash_tokens.py` | `tokens.json` schema valid; contrast floors (§3.4) for both themes; generated CSS/TS/JS are byte-identical to a fresh `build_tokens.py` run (no hand edits / stale output) |
| `tests/test_wash_vocabulary.py` | `vocabulary.json` covers **every** member of `JobState`, `JobStatus`, attention reasons, verdicts, severities, check and env statuses (imported from `mu/jobs`); no surface defines its own status table. It greps `work/view.js`, `mobile/.../status.ts` and `jobNotifications.ts` for the old literal tables. |
| `tests/test_wash_css_contract.py` | (from W2 onward, scoped to `static/css/wash/`): no hex/rgb outside `tokens.css`; no `!important` outside utilities; only the 4 breakpoints; no class selector defined in two component files; every `var(--wash-*)` used exists in `tokens.css` |
| `tests/test_wash_terminal_theme.py` | every `wash.*` style resolves in none/16/256/truecolor; `esc()` applied (approval with `"[bold]x[/]"` args renders literally; B1); `show_info` with markup default prints literally and `show_renderable(Table)` renders a table (B2); `NO_COLOR` output contains glyphs and labels |
| `tests/test_wash_markup_lint.py` | AST scan: f-strings interpolating non-literal values into Rich markup inside `print`/`show_*` calls must use `esc()` (allowlist file for justified exceptions, initially empty) |
| `mobile/android/__tests__/wash-theme.test.ts` | palette equals generated tokens; contrast floors; theme preference persists; `remember` sent when "Always allow" checked (B6) |
| `tests/test_web_a11y_contract.py` | every `templates/**` dialog has `role="dialog"` + `aria-labelledby`; no `onclick`/`@click` on `div\|span\|li` in `trace.html`; toast regions have `aria-live` |

### 8.2 Visual verification

- `scripts/design/snap.py`:
  1. Boot `create_app(...)` on a spare port. Never use 30311, where the
     user's own GUI runs.
  2. Use a stub `build_session_fn` and `MUCLI_DESIGN_GALLERY=1`.
  3. Capture `/design`, `/`, `/work`, `/containers`, `/trace`,
     `/jobs/trace` and `/models/costs` in `paper` and `dusk`, at 1440×900 and
     390×844, into `build/snaps/<phase>/`.
- Reviewers compare the screenshots against the guide. There is no
  pixel-diff gating, because fonts and antialiasing vary. A
  console-error-free page load is asserted.
- The chat view needs a session. The gallery's canned SSE replay covers the
  chat components, so a real provider is not required.

### 8.3 Migrating source-pinning tests (rules)

About 77 test files read templates, CSS or TSX as text. Many of them pin the
*old* visual implementation, for example:

- `test_web_product_ui.py:95` asserts `--panel-tabs-h: 64px`
- `test_web_route_shell.py:44` asserts `height:48px`
- `test_job_visual_polish.py` asserts that `work.css` is linked
- `job-views.test.ts` pins hex values

The redesign is a **specified behaviour change**, which AGENTS.md allows.
These rules keep it honest:

1. **Preserve hooks, change looks.**
   - These are kept unless the guide explicitly removes the element: element
     `id`s, `data-*` hooks, `testID`s, `accessibilityLabel`s, `x-data`/store
     names, SSE event kinds, API paths, prompt_toolkit style *class* names,
     and user-facing labels.
   - Most of those 77 files assert these hooks and should pass unchanged.
2. **A test that pins a superseded visual detail is rewritten, in the same
   PR, to assert the Wash equivalent of the same intent.** The table below
   gives examples.

   | Old assertion | Intent | New assertion |
   |---|---|---|
   | `height:48px` route bar | Standalone pages share a top bar | `_layout.html` used by all pages; top bar height token `--wash-topbar-h` |
   | `work.css` linked, not `product.css` | `/work` doesn't load chat CSS | `/work` links only `wash.css`; no legacy files exist |
   | `'#1A1814' not in text` | No stale brand colours | Palette equals generated tokens |
   | `style == "dim italic"` | Thinking is visually de-emphasised | Thinking uses `wash.thinking` (italic + muted) |
   | exact SGR `38;2;122;162;247` | Jobs logo uses accent truecolor | SGR equals `sgr("accent")` computed from tokens |

3. Every rewritten assertion is listed in the PR description's **test
   migration ledger**, in the form `file:line — old → new — intent`.
4. **Never** delete a test without a replacement that covers the same intent.
   Never mark tests skip or xfail. If an intent is genuinely retired (for
   example the two-`<select>` stage picker), the ledger says so and names the
   guide section that retires it.

The highest-churn files to expect:

- `test_web_product_ui.py`
- `test_job_visual_polish.py`
- `test_web_route_shell.py`
- `test_jobs_tui.py` (SGR values)
- `test_input.py` (only if prompt text changes; class names are preserved)
- `test_plan_mode_visibility.py` (banner text stays "PLAN MODE ENABLED")
- `test_streaming_unified_live.py`
- `test_rich_ui.py`
- `test_mobile_product_ui.py`
- `test_session_type_icons.py`
- `test_gui_memory_panel.py` (layer colour mapping)
- `test_visualization_style_guide.py`
- mobile `job-views.test.ts` and `plan-mode.test.tsx` (`plan` ≠ `attention`
  ≠ `accent`, which still holds: lilac, apricot, lagoon)

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The 7,600-line `app.js` builds DOM with legacy class names, so a CSS deletion silently unstyles something | The legacy CSS layer stays until W4. Each component PR greps `app.js`/`product.js`/`web_shell.js` for the classes it retires and updates the emitters. The gallery covers all states. |
| Monkey-patch layers (`product.js`, `web_shell.js`, `history_hydration.js`) inject CSS and DOM | W2 inventories their injections. Injected stylesheets are folded into `wash/` and the injections removed in W4. |
| Washes hurt readability or performance | Art only appears in hero, header and empty-state zones (G§6.2). Images are WebP of 25 KB or less, lazy-loaded below the fold. There is a "Reduce decoration" toggle. Contrast is checked on composited pixels in `snap.py` with axe-core run in Playwright. |
| OSC 11 background query hangs or garbles some terminals | 100ms timeout, TTY-only, skipped under tmux/screen without passthrough, `MUCLI_TERM_THEME` override, cached. |
| Inter's variable font adds about 340 KB | Subset to Latin plus Latin-ext plus Greek (for μ) with `pyftsubset` in `build_tokens.py --fonts`. Target ≤ 120 KB per file. |
| Mobile kit migration regresses behaviour | Keep `testID`s. Migrate one screen per commit. Jest plus the emulator walkthrough. Dead-code deletion goes in its own commit. |
| Theme name change (`light`/`dark` → `paper`/`dusk`) breaks stored preferences and the viz artifact contract | Read-time migration in the pre-paint script and mobile storage. Artifact bootstrap accepts both `?mucli_theme=light\|dark\|paper\|dusk` and maps old names. `mucli-theme-change` `detail.theme` keeps emitting `light`/`dark` *and* a new `detail.wash` field until the viz skill template is updated in P7. |
| DOMPurify changes rendered markdown (for example strips inline SVG in answers) | Configure the allowlist to match current rendering (MathJax SVG is typeset *after* sanitising). Separate PR with focused tests on markdown fixtures. |

---

## 10. Decisions (all confirmed 2026-10-15)

1. **Name:** "Wash". The CSS prefix is `--wash-*` and the classes are `.w-*`.
2. **Default theme:** follow the OS, with Paper as the designed-first hero.
   Dusk is a full peer, not an afterthought.
3. **Fonts:** Inter and JetBrains Mono (OFL), self-hosted. This avoids
   licensing a GT America–style face.
4. **Art:** procedurally generated washes (§3.5), not commissioned
   illustration. The generator can later be swapped for hand-painted assets
   without touching components.
5. **Icons:** a Lucide subset (ISC licence) redrawn at a 1.5 stroke, as
   source SVGs in `mu/design/icons/`. Mobile adds `react-native-svg` in M3;
   until then it uses Ionicons outline via the `Icon` wrapper.
6. **Mobile navigation:** move to bottom tabs (Chat, Work, Workspace, More).
7. **New dependencies:**
   - web: vendored DOMPurify
   - mobile: `expo-haptics`, `react-native-svg`, `expo-splash-screen` (if
     not already transitively present)
   - dev: `numpy` (art), `fonttools` (subsetting)
8. **Terminal thresholds:** context caution and danger move to 65% and 85%
   to match web.

---

## 11. Definition of done (whole overhaul)

- One token source and one vocabulary source. The contract tests guard both.
- Web: one stylesheet (`wash.css`), legacy CSS deleted, all pages on
  `_layout.html`, one button, chip, modal and toast system, and a palette on
  every page.
- Android: one component kit, bottom tabs, tool cards in chat, a persisted
  theme, and every target at least 44dp. The `ACCESSIBILITY_AUDIT.md` is
  accurate.
- Terminal: one console, one Rich theme, one prompt_toolkit style, works in
  none/16/256/truecolor on light and dark backgrounds, no emoji, no blink,
  and all dynamic markup escaped.
- B1–B8 are fixed, with regression tests.
- Full Python suite and mobile jest green. `snap.py` screenshot sets are
  archived for the final review.
- The docs are updated: this spec, the guide, the viz guide, the mobile
  README, the accessibility audit, and AGENTS.md.
