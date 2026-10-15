# Wash — the Mucli design consistency guide

**Status:** Approved v1 · 2026-10-15
**Applies to:** browser GUI (`/`, `/work`, `/containers`, `/trace`, job trace,
model costs), Android client, terminal REPL, jobs TUI, and container TUI.
**Companion:** [`wash_implementation_spec.md`](wash_implementation_spec.md)
describes how each surface moves to this guide.
**Visual reference:** [`reference/wash_reference.html`](reference/wash_reference.html)
(open it in a browser; screenshots are in `reference/`).

---

## 0. Why "Wash"

Mucli started as an internal dev tool: a Tokyo-Night dark palette, GitHub-style
light mode, and many layers of restyling ("planos", "crystal", "clarity",
"refinement") on top of one another. Each surface drifted. The terminal
`ok` green is `#9ece6a`, web is `#73c991`. Mobile has two component kits. The
job status table is copied into four places, and the copies already disagree.

Wash replaces all of that with one system, inspired by
[duna.com](https://duna.com/). It borrows these qualities from Duna:

- **Painted atmosphere.** Soft watercolour landscapes and skies sit behind
  calm, near-monochrome UI. The art supplies the emotion and the UI stays quiet.
- **Warm paper, warm ink.** Off-white backgrounds (`#F7F7F5`, `#EDEDE8`) and
  brown-black text (`#1A1816`, `#292421`) instead of cold greys. Display
  headings are a deep plum (`#1B0624`).
- **Pill-shaped actions.** One dark ink pill is the primary call to action.
  Secondary actions are outlined pills. Small rounded "eyebrow" chips label
  sections.
- **Big, tight, confident type.** Large headings with negative letter-spacing
  (−0.02 to −0.03em) and short benefit-led copy.
- **Generous space and hairlines.** Sections breathe, and 1px warm hairlines
  separate things instead of heavy boxes.
- **Soft cards.** Radius about 20px, sunken warm fills, and almost no shadow.

On top of that we add **happy colour**. Mucli is a tool people sit in all
day, and we want it to feel light and optimistic. Every status, layer, mode and
agent gets a watercolour pigment. Pigments are used as soft washes, never as
neon.

### Design principles

1. **Paper first, paint second.** Content sits on calm paper. Colour is a wash
   that tells you something: state, mode, layer, or a moment of delight.
   Decoration never sits behind text you have to read.
2. **One answer per question.** Each concept has exactly one look on every
   surface: one "needs you", one primary button, one approval card, one diff.
   If two components look different, they must mean different things.
3. **The human is in charge, visibly.** Approvals, plan mode, YOLO, and
   anything that mutates are always the most legible things on screen. Good
   looks never weaken a safety signal.
4. **Calm while working, loud only when it matters.** Streaming, tool calls and
   background jobs stay soft and peripheral. Only "needs you", errors and
   destructive confirmations get strong ink.
5. **Same tokens, native idioms.** Web, Android and terminal share token
   values and vocabulary, but each follows its own platform's interaction
   conventions.
6. **Accessible by construction.** All text meets WCAG AA (4.5:1) against its
   real background. Colour is never the only signal. Everything works with a
   keyboard and with reduced motion.

---

## 1. Colour

### 1.1 Paper and ink (neutrals)

Two themes: **Paper** (light, the default and the hero) and **Dusk** (dark).
The default follows the OS, and the user's choice is remembered on every
surface.

| Token | Role | Paper | Dusk |
|---|---|---|---|
| `canvas` | App background | `#F8F6F1` | `#16131B` |
| `surface` | Cards, panels, composer | `#FFFEFB` | `#1D1923` |
| `sunken` | Inset areas, code, selected rows, inactive tabs | `#F1EEE7` | `#120F16` |
| `raised` | Popovers, menus, sheets | `#FFFFFF` | `#262030` |
| `hairline` | Default 1px divider/border | `#E6E1D8` | `#332C3D` |
| `hairline-strong` | Strong decorative dividers, secondary-button outlines | `#D6CFC3` | `#463D52` |
| `control-border` | Form-control boundaries (inputs, checkboxes, switch-off tracks), ≥ 3:1 | `#8F877F` | `#776C81` |
| `ink` | Primary text, primary button fill | `#1E1A17` | `#F3EEE8` |
| `ink-2` | Secondary text | `#4E4844` | `#D2C9D1` |
| `ink-3` | Tertiary/muted text, placeholders, metadata | `#6B645F` | `#A89FAD` |
| `plum` | Display headings, wordmark | `#2B1436` | `#F6E9F2` |
| `on-ink` | Text on an ink fill | `#FFFEFB` | `#16131B` |
| `scrim` | Modal backdrop | `rgba(30,26,23,.32)` | `rgba(8,6,10,.6)` |

Measured contrast: `ink-3` is 5.0:1 on Paper `sunken` (the worst case) and
6.2:1 on Dusk `raised`. Every text token clears AA on every surface token.

### 1.2 Pigments

There are seven watercolour pigments. Each has three steps:

- **wash:** a pale fill for chip backgrounds, card tints and selected rows.
- **pigment:** the mid tone for paint, graphics, dots, bars and chart marks.
  **Never use it for text.**
- **ink:** the text-safe step. On Paper it is darker than the pigment. On Dusk
  it is a lighter glow.

| Pigment | Paper wash | Pigment | Paper ink | Dusk ink | Dusk wash |
|---|---|---|---|---|---|
| **Lagoon** (teal) | `#DCEDEE` | `#5FA3AB` | `#2A6870` | `#7CC4CB` | lagoon 16% over surface |
| **Meadow** (green) | `#E3EFD8` | `#8DBF6A` | `#3D6A2A` | `#A5D68A` | 16% mix |
| **Sun** (yellow) | `#FBEFC8` | `#F2C94C` | `#755600` | `#F2D27A` | 16% mix |
| **Apricot** (orange) | `#FCE3CF` | `#F2A36B` | `#984812` | `#F5B58A` | 16% mix |
| **Rose** (pink-red) | `#F9DCE2` | `#E58A9E` | `#A3324B` | `#F29AAE` | 16% mix |
| **Lilac** (violet) | `#EAE1F4` | `#B39DDB` | `#5E3F94` | `#C7B3EE` | 16% mix |
| **Sky** (blue) | `#DCE8F7` | `#84AEE3` | `#2D5C9C` | `#9CC2F2` | 16% mix |

Measured contrast:

- Paper ink on its own wash: lowest is 5.2:1 (apricot).
- Paper ink on `sunken`: lowest is 5.5:1.
- Dusk ink on Dusk `raised`: lowest is 7.5:1.

The verification script is in the implementation spec §3.4.

### 1.3 Semantic mapping

Components use **semantic tokens only**, never pigments directly. The only
exception is categorical graphics (§1.4).

| Semantic | Pigment | Meaning |
|---|---|---|
| `accent` | Lagoon | Links, focus ring, selected tab, active nav, sliders and toggles in the on state |
| `running` | Sky | Work in progress: streaming, tool executing, job stage active |
| `ok` | Meadow | Done, passed, approved, connected |
| `attention` | Apricot | **Needs you**: approvals, questions, plan review, limits |
| `caution` | Sun | Soft warning: pressure near the limit, stale data, YOLO on |
| `danger` | Rose | Failed, denied, destructive, disconnected |
| `plan` | Lilac | Plan mode (read-only) |
| `info` | Sky | Neutral notices |
| `neutral` | ink-3 on sunken | Pending, skipped, cancelled, idle |

`attention` (Apricot) and `danger` (Rose) are deliberately far apart in hue,
so "needs you" never reads as "broken".

### 1.4 Categorical colours: modes, layers, agents

**Modes.** Each mode keeps its pigment everywhere: prompt marker, mode chip,
panel header wash, and the mobile mode drawer.

| Mode | Pigment |
|---|---|
| default | Lagoon |
| feature | Sky |
| research | Lilac |
| teacher | Sun |
| security | Rose |
| debug | Apricot |
| loop | Meadow |
| plan (overlay state) | Lilac, with a dashed border so it is distinct from research |

**Context layers L0–L5.** Used in the meters, the Context Observatory, and
trace heat strips. They are ordered from "deep and stable" (cool, darker) to
"live" (warm, brighter), and that order holds in every chart.

| Layer | Name | Pigment |
|---|---|---|
| L0 | System | Plum `#9C6B98` (graphic step) |
| L1A | Workspace context files | Lagoon |
| L1B | Skills | Meadow |
| L2 | Conversation summary | Lilac |
| L3 | Goals and memory | Sun |
| L4 | Recent tool activity | Sky |
| L4B | Retrieved context | Rose |
| L5 | Live history | Apricot |

Every layer chart also shows direct labels (`L0`, `L1A`, …), so colour is
never the only key.

**Sub-agents and parallel workers** cycle through pigments in this order:
Lagoon, Apricot, Lilac, Meadow, Rose, Sky, Sun.

### 1.5 Usage ratios

On any screen, roughly 85% is paper and ink, at most 10% is washes, and at
most 5% is pigment. An exception is atmosphere art in hero areas (§6). If a
working screen looks like a rainbow, too many things are claiming attention.

---

## 2. Typography

### 2.1 Typefaces

| Role | Family | Fallback stack | Notes |
|---|---|---|---|
| UI and display | **Inter** (variable, OFL 1.1, self-hosted) | `"Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` | Display sizes use Inter's `opsz` axis (Display cut) with tight tracking. This is the closest free match to Duna's GT America. |
| Code, data, terminal-like UI | **JetBrains Mono** (OFL, self-hosted) | `"JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace` | Ligatures off by default. |

The fonts are bundled locally: woff2 for web, TTF via expo-font for Android.
There are no network font requests. Mucli runs offline and inside containers.

### 2.2 Scale

| Token | Size / line height | Weight | Tracking | Use |
|---|---|---|---|---|
| `display` | 48 / 52 | 500 | −0.03em | Welcome hero only |
| `title-1` | 32 / 38 | 500 | −0.025em | Page titles (Dashboard, Environments) |
| `title-2` | 24 / 30 | 500 | −0.02em | Section titles, modal titles |
| `title-3` | 18 / 24 | 600 | −0.01em | Card titles, panel headings |
| `body-lg` | 16 / 24 | 400 | 0 | Chat messages, long-form reading |
| `body` | 14 / 21 | 400 | 0 | Default UI text |
| `label` | 13 / 18 | 500 | 0 | Buttons, chips, form labels, tabs |
| `caption` | 12 / 16 | 400 | +0.01em | Metadata, timestamps, hints |
| `eyebrow` | 12 / 16 | 500 | +0.02em | Section eyebrow chip text (sentence case, **not** all caps) |
| `mono` | 13 / 20 | 400 | 0 | Code, tool args, paths, IDs |
| `mono-sm` | 12 / 18 | 400 | 0 | Diff gutters, log lines |

Rules:

- Display and title text use `plum`. Everything else uses `ink`, `ink-2` or
  `ink-3`.
- Use **sentence case everywhere.** No ALL-CAPS labels, no `// TERMINAL //`
  framing, no `_UNDERSCORED_SHOUTING_`.
- Numbers use `font-variant-numeric: tabular-nums` (and a mono font in the TUI).
- Chat messages are 16px `body-lg` with a reading width of at most 72ch.
- Mobile uses the same scale. The OS font-size setting may scale it up but
  never down (minimum `caption` is 12).

---

## 3. Space, shape, elevation

### 3.1 Spacing

There is a 4px base unit. The tokens are `space-1` through `space-10`:
`4, 8, 12, 16, 20, 24, 32, 48, 64, 96`.

- Padding inside a component: 8–16.
- Gap between related items: 8–12. Between groups: 24–32. Between page
  sections: 48–64.
- Page gutters: 24 on desktop, 16 on tablets and mobile.
- Content width: 1200 maximum for dashboards, 760 maximum for the reading
  column.

### 3.2 Radius

| Token | Value | Use |
|---|---|---|
| `radius-xs` | 6 | Inline code, keycaps, tiny tags |
| `radius-sm` | 10 | Inputs, selects, menu items, code blocks |
| `radius-md` | 14 | Tool cards, list rows with fills, toasts |
| `radius-lg` | 20 | Cards, panels, message bubbles, approval card |
| `radius-xl` | 28 | Modals, bottom sheets (top corners), hero art frames |
| `radius-pill` | 999 | Buttons, chips, segmented controls, composer on mobile |

A child's radius equals its parent's radius minus the gap between them. Do
not nest a 20 inside a 20.

### 3.3 Elevation and glass

Wash surfaces are **frosted glass laid over a watercolour**. Every page has a
fixed, full-bleed *ambient wash* behind it (§6). Surfaces are translucent and
blurred, so the painting glows softly through them. Elevation comes from
opacity and blur, not from heavy shadows.

| Level | Token | Treatment | Use |
|---|---|---|---|
| 0 | none | Ambient wash on `canvas` | Page background |
| 1 | `glass` | 62% surface, `blur(24px) saturate(1.5)`, 1px `glass-border` highlight edge plus `glass-edge` outline, `glass-highlight` top sheen, `glass-shadow` | Cards, panels, sidebar, top bar, stage, composer, tables |
| 1s | `glass-subtle` | Tinted translucent inset; no blur of its own | Inputs, tool-card rows, code, chips on glass, selected rows |
| 2 | `glass-strong` | 82–84% surface, `blur(32px)`, highlight edge, `elevation-2` | Menus, popovers, toasts, the active nav item |
| 3 | `glass-strong` | As level 2 plus `elevation-3` | Modals, sheets, command palette |

Rules:

- **Opacity floor.** Text-bearing glass is never more transparent than 62%.
  `check_contrast.py` composites `glass` over the blurred ambient art and
  requires every text token to stay at 4.5:1 or better on the worst pixel.
- **Edges are light, not colour.** A glass surface is outlined by a white
  highlight (Paper) or faint white line (Dusk), plus a very low-alpha dark
  edge. Edges are never pigment-coloured (§3.4).
- **Nesting.** Glass on glass is allowed; each layer compounds opacity, so
  nested surfaces only become more legible. Avoid more than two levels.
- **Fallbacks.** Without `backdrop-filter` support, or with
  `prefers-reduced-transparency`, "Reduce decoration" or forced colours, glass
  becomes the solid `surface` / `raised` colour and the ambient wash becomes
  flat `canvas`.

### 3.4 Borders and focus

- 1px neutral edges only (`hairline`, or the glass edge tokens on glass).
- **No coloured side accents.** Surfaces, rows and list items never carry a
  coloured left or right bar, rule or inset stripe, not even for state. State
  is shown by a tinted header strip, the status chip (glyph plus label), and
  the semantic ink of the text. Structural indentation lines (trees, nested
  replies) use a neutral `hairline`.
- Coloured outlines are reserved for focus and for plan mode's dashed
  composer outline.
- **Focus ring:** a 2px `accent` outline with a 2px offset. Inputs use offset
  0 plus a 3px `accent` wash halo. It is visible on every interactive element,
  and never `outline: none` without a replacement.

---

## 4. Iconography and glyphs

- **Web and mobile icons:** one line-icon set at a 1.5px stroke, with rounded
  caps and joins, on a 24 grid. Sizes are 16 (inline), 20 (buttons) and 24
  (navigation). Web uses inline SVG sprites from a single
  `static/icons/wash.svg`. Mobile uses the same SVG paths via
  `react-native-svg`, or Ionicons (outline variants only) during the
  transition. Do not mix filled and outline icons.
- **Status glyph vocabulary.** Glyphs are shared on every surface, including
  the terminal, and always paired with a text label:

| Status | Glyph | Semantic | Label examples |
|---|---|---|---|
| pending | `○` | neutral | queued, not started |
| active | `●` (animated on GUI) | running | planning, implementing, checking |
| waiting | `◆` | attention | **needs you** |
| done | `✓` | ok | done, passed, pushed |
| failed | `✗` | danger | failed |
| skipped | `–` | neutral | cancelled, skipped |

- **Tool kinds** have one icon each, the same everywhere: read, write/edit,
  shell, search, web, sub-agent, memory, plan, container. The TUI uses short
  mono labels (`read`, `edit`, `sh`, …) instead of emoji. **No emoji in
  product chrome on any surface.** The current `🔨 ⚠ ⏹ 🔒 ✦` usages all go
  away.
- **Wordmark:** `μcli` in Inter Display 500, `plum`, −0.03em, with a small
  watercolour "drop" mark (a painted dot that cycles pigments slowly when the
  agent is working). It replaces the three-dot logo and the ASCII block art.

---

## 5. Motion

Motion should feel like pigment settling into wet paper: soft, short, and
never bouncy.

| Token | Duration | Easing | Use |
|---|---|---|---|
| `motion-micro` | 120ms | `cubic-bezier(.2,0,0,1)` | Hover, press, toggle |
| `motion-small` | 200ms | `cubic-bezier(.16,1,.3,1)` | Chips, popovers, row expand |
| `motion-medium` | 320ms | `cubic-bezier(.16,1,.3,1)` | Sheets, modals, drawers |
| `motion-bleed` | 600ms | `cubic-bezier(.3,0,.2,1)` | Washes fading in (opacity plus 8px→0 blur), hero art |

Signature motions:

- **Bleed-in.** New status washes and toasts fade from a soft blur to sharp.
  Nothing slides in from off-screen except sheets and drawers.
- **Ink drop.** The "working" indicator is a single pigment dot that slowly
  swells and settles (1.6s loop). It replaces bouncing dots and the braille
  spinner on GUI surfaces. The TUI keeps a calm 4-frame `◌ ○ ◎ ●` cycle at
  0.15s.
- **Streaming caret.** A soft pigment bar at the end of streaming text. Its
  colour is the active mode's pigment.
- **Press.** Buttons scale to 0.98 and darken one step. Mobile adds light
  haptic feedback on primary and approval actions.

- **Page transitions.** Navigating between web pages never flashes. Cross-document
  View Transitions cross-fade the pages; the top bar and sidebar hold still, and
  the stage content fades out and rises in (`motion-small` out, `motion-medium`
  in). `/work`'s in-page sections use the same animation. Same-origin links are
  prefetched on hover (speculation rules, prefetch only). Pages close their live
  streams on `pagehide` rather than `beforeunload`, so Back and Forward restore
  from the back/forward cache instantly.

`prefers-reduced-motion` (and the Android reduce-motion setting) turns all of
this into instant state changes. The ink drop becomes a static `●` with a
"working" label.

---

## 6. Atmosphere: watercolour washes

The paintings are what make the product feel like Duna. Mucli generates them
procedurally with one deterministic script (spec §4), so they are free,
reproducible, themable, and small (about 25 KB WebP).

### 6.1 Wash kinds

| Wash | Composition | Where |
|---|---|---|
| **Dawn** | Apricot/rose sky over lilac hills, lagoon water, meadow foreground, fading to paper at the bottom | Welcome/launcher hero, mobile connection screen, empty chat |
| **Lagoon** | Sky and lagoon blues with lilac | `/work` dashboard header band, Jobs on mobile |
| **Meadow** | Meadow, sun, lagoon | `/containers` and environment creation |
| **Dusk** | Lilac, plum, rose on a dark ground (for the Dusk theme) | The dark versions of all of the above |
| **Mode tints** | A single-pigment band, 120px high, fading out | The top of a mode panel and of the chat column when a mode is active |
| **Spot washes** | A small irregular blot, about 64–160px | Behind empty-state illustrations, onboarding steps, success moments |

### 6.2 Rules

1. **Text never sits directly on art.** Body text and data sit on glass
   (§3.3) at or above the opacity floor. Hero headings may sit on hero art
   where it fades to at least 80% paper. Contrast is always checked against
   the composited pixels.
2. **Ambient wash behind everything; vivid art at the edges.** Every page has
   a soft, fixed, full-bleed ambient wash: Dawn for chat and welcome, Lagoon
   for Work and Trace, Meadow for Environments. The ambient wash is the glass
   backdrop. The stronger hero paintings still appear only in hero areas,
   headers, empty states and onboarding.
3. **Bottom edges dissolve into canvas.** Art never has a hard bottom edge,
   just as Duna's hero dissolves into the page.
4. **Dusk gets its own paintings.** Do not darken a Paper wash with a filter.
5. **Art can be turned off.** A "Reduce decoration" preference (on all
   surfaces) and `prefers-reduced-transparency` replace the art with a flat
   wash token.
6. **Paper grain.** An optional 3% noise texture on `canvas` (CSS only) gives
   the paper some tooth. It is disabled in compact density.

---

## 7. Voice and copy

- Warm, plain and short, like a capable colleague. Use "You're all caught up",
  not "NO_PENDING_ITEMS".
- Lead with the benefit or the action: "Approve and run", not "Execute tool
  call with approval".
- Sentence case. Avoid exclamation marks, with one allowed per success moment.
- Name things consistently across surfaces. Use the **canonical labels** from
  the shared status vocabulary (`provisioning` is labelled **"Preparing"**
  everywhere).
- Errors should say what happened, why if known, and what to do next:
  "Couldn't reach Ollama at localhost:11434. Is it running? · Retry".
- Do not anthropomorphise past what is useful. The agent "is reading 3 files",
  not "is thinking hard 🤔".

---

## 8. Layout and shells

### 8.1 Web app shell

```
┌──────────────────────────────────────────────────────────────────────┐
│ Top bar 56px  μcli ▸ session      [mode chip] [model chip] [ctx ◔]  ⚙ │  sticky, canvas / glass over art
├────────────┬───────────────────────────────────────┬─────────────────┤
│ Sidebar    │  Chat column (max 760, centred)        │  Stage (panels) │
│ 264px      │                                        │  360–560px,     │
│ sessions   │  messages / tool cards / approvals     │  resizable,     │
│ threads    │                                        │  collapsible    │
│            │  ┌──────────── composer ─────────────┐ │                 │
│            │  └───────────────────────────────────┘ │                 │
└────────────┴───────────────────────────────────────┴─────────────────┘
```

- **Top bar:** wordmark, breadcrumb (`μcli › session › thread`), then status
  chips on the right: mode, provider·model, context meter, plan, YOLO,
  connection. Links to Chat, Work, Environments and Trace are **primary
  navigation** in the sidebar, not icons in the top bar.
- **Sidebar:** on `surface` with a hairline on the right. It has sections for
  Navigate (Chat, Work, Environments, Trace), Sessions and Threads. It
  collapses to a 64px icon rail below 1200px and becomes an off-canvas drawer
  below 840px.
- **Stage:** the right panel for mode panels and tools. It uses one header
  pattern: title, mode-tint wash band, and a close button. Panels are chosen
  from a **segmented list or menu**, not two `<select>`s.
- **Breakpoints (the only ones allowed):** `sm` 600, `md` 840, `lg` 1200,
  `xl` 1600. Replace the 25 ad-hoc breakpoints with these.

### 8.2 Standalone pages

`/work`, `/containers`, `/trace`, job trace and model costs all use **the same
app shell**: the same top bar, sidebar and tokens, through a single shared
Jinja head and layout. A page may add a hero band (art plus title-1) above its
content.

### 8.3 Mobile shell

- A native stack plus **bottom tab bar** with Chat, Work, Workspace and More
  (Settings). `@react-navigation/bottom-tabs` is already installed. Swipe-edge
  drawers stay as shortcuts, but they are no longer the only way to reach
  sessions and modes.
- **Header:** session name (title-3, plum), mode chip, and a context meter
  ring. It is transparent with glass over art and solid `canvas` on scroll.
- **Sheets:** every secondary flow is a bottom sheet with `radius-xl` top
  corners, a grab handle, and drag-to-dismiss. Full-screen modals are only for
  approval when the screen reader is on, and for editors.
- **Settings:** a real screen under More, not a long sheet.

### 8.4 Terminal shell

Terminals cannot paint watercolour. We translate the system instead of
copying it:

- **Palette:** truecolor uses the Dusk or Paper *ink* steps, chosen by the
  detected terminal background. 256-colour uses the nearest xterm-256 values.
  16-colour uses semantic ANSI names (accent = cyan, running = blue,
  ok = green, attention = yellow, danger = red, plan = magenta, muted = bright
  black). With `NO_COLOR`, use glyphs and text only.
- **Rhythm:** one blank line between turns. Panels use `ROUNDED` borders in a
  neutral border colour only, never a semantic colour; state lives in the
  panel title (glyph plus semantic ink). There are no HEAVY or DOUBLE boxes.
  Terminals cannot blur, so there is no glass: panels stay borderless-fill.
- **Splash:** a single line, `μcli` in plum or bold, followed by a
  pigment-gradient rule (`━━━━` that cycles apricot → rose → lilac → lagoon in
  truecolor). Then a 3-line info grid: session, model, workspace. No block
  letters and no fake system-boot copy.
- **Prompt:** `μ session · mode ›`, where the mode name is in the mode's
  pigment. Plan and YOLO are bracketed chips: `[plan]` in lilac, `[yolo]` in
  sun. **No blinking.**
- **Bottom toolbar:** muted text with key hints; the key name is in `ink` and
  the description in `ink-3`.

---

## 9. Components

Each component lists: **anatomy**, **variants**, **states**, **behaviour**,
and **per-surface notes** (W = web, M = mobile, T = terminal). Every component
on every surface uses only semantic tokens.

### 9.1 Button

| Variant | Look | Use |
|---|---|---|
| `primary` | `ink` pill, `on-ink` label | **One per view**: Send, Approve, New job, Create |
| `secondary` | `surface` pill, 1px `hairline-strong`, `ink` label | Alternative actions: Cancel, Open setup |
| `ghost` | No fill. On hover, a `sunken` pill appears | Toolbars, tertiary actions |
| `tonal` | `{semantic}-wash` fill, `{semantic}-ink` label | Contextual actions inside a state, e.g. "Review plan" in an attention card |
| `danger` | `danger-ink` fill, `on-ink` label | Destructive confirmation only (delete, deny, force-push) |
| `icon` | 36px circle (44 on touch), ghost by default | Toolbars. **Always** has an `aria-label` and a tooltip |

- **Sizes:** `sm` 28px high (web dense tables only), `md` 36px (default),
  `lg` 44px (hero, mobile default). Horizontal padding is 16 for md, 20 for
  lg. The label is `label` type.
- **Icon placement:** leading icon at 16px. A trailing chevron `›` means
  "navigates" (Duna's "Explore ›").
- **States:**
  - hover: one step darker (primary) or sunken fill (others)
  - pressed: scale 0.98
  - focus: ring
  - disabled: 40% opacity and `cursor: not-allowed`; still focusable when it
    has a tooltip explaining why
  - loading: label replaced by an ink drop and width kept, with
    `aria-busy="true"`
- **Keyboard shortcut** hints show as a trailing keycap (`radius-xs`, `sunken`,
  `mono-sm`) on desktop only.
- **T:** buttons appear as key hints, e.g. `[a] approve  [d] deny  [e] edit`.
  The key letter is `accent` and bold, the label is `ink-2`.

### 9.2 Chip, pill, badge

- **Status chip:** a pill 24px high with a glyph and a label. The fill is the
  semantic wash and the text is the semantic ink. Example: `◆ Needs you`.
- **Meta chip:** a `surface` pill with a hairline and `ink-2` text, for
  things like model, provider and branch. Clickable meta chips show a trailing
  `▾`.
- **Eyebrow chip (Duna):** a `sunken` pill with an optional 6px pigment
  square, in `eyebrow` type. It sits above section titles ("Plan", "Review",
  "Context").
- **Filter chip:** like a meta chip. When selected it becomes `ink` fill with
  `on-ink` text and a leading `✓`.
- **Count badge:** an 18px-min circle or pill in `mono-sm` tabular numbers,
  `sunken` and `ink-2`. With attention it becomes the `attention` wash and ink.
- **Removable chip** (attachments): a trailing ✕ icon-button with a 24px hit
  area (44 on touch via hit slop).
- **T:** `◆ needs you` printed in attention ink with no background. Meta chips
  are `ink-3` text separated by ` · `.

### 9.3 Card and panel

- **Card:** `glass`, `radius-lg`, glass edge, 20–24 padding. Title in
  `title-3`, optional eyebrow, body, and a footer with actions aligned right.
- **Feature/sunken card (Duna "Automated tasks"):** `glass-subtle` fill, no border.
  Used for the selected item in a list of explanations, settings groups, and
  onboarding steps.
- **State card:** a glass card whose header strip is tinted with the
  semantic wash (about 70% wash mixed into the glass) and carries the status
  chip. There is no side rule. Only `attention`, `danger`, `plan` and `ok`
  use this. It is how approvals, failures and plan prompts stand out.
- **Stat strip:** do not use a card per metric. Show stats as one row of big
  numbers (`title-1`, tabular) with captions underneath and hairline dividers
  between them (Duna's "10.6x / 4.8x / 37%"). This replaces the five-tile row
  on the Work dashboard.
- **Panel (stage):** full-height `glass`, with a header of title,
  mode-tint band and actions. Sections inside are separated by hairlines, not
  nested cards.

### 9.4 Top bar and navigation

- **Nav item:** 36px high, `radius-sm`, icon plus label. The active item is a
  `glass-strong` pill with `ink` text and an `accent`-ink icon, plus
  `aria-current`. There is no side bar. Hover gives a `glass-subtle` fill.
  The count badge is aligned right.
- **Breadcrumb:** `ink-3` separators `›`, with the current crumb in `ink`
  using `label` type.
- **Tabs:** text tabs with a 2px `accent` underline on the active tab, `label`
  type, and arrow-key navigation (`role=tablist`).
- **Segmented control:** a `sunken` pill track with a `surface` thumb (level 1
  shadow) that slides on `motion-small`.
- **M:** bottom tab bar items are a 24 icon with a 12 label. The active item
  gets an `accent` icon, `ink` label, and a wash pill behind the icon.
- **T:** the jobs TUI tabs show `Dashboard  Jobs  Activity` with the active
  tab in `ink` bold and an `accent` underline character row (`━━━━`).

### 9.5 Inputs and forms

- **Text field:** 40px high (44 on touch), `surface`, `radius-sm`, 1px
  `control-border`. The label is above (`label` type, `ink-2`), the hint
  below (`caption`, `ink-3`). An error turns the border to `danger-ink` and
  shows a `✗` plus message.
- **Textarea and code input:** same as a text field, using a `mono` face for
  code, paths and Dockerfiles.
- **Select:** looks like a text field with a trailing chevron. It opens a
  level-2 menu (custom listbox on web with typeahead; native picker sheet on
  mobile).
- **Switch:** a 36×20 track. Off is `control-border`, on is `accent`, and the
  thumb is `surface`. The label is on the left and the switch on the right of
  the row.
- **Checkbox and radio:** 18px, `radius-xs` for checkbox, `accent` fill when
  on, with a 44px hit row on touch.
- **Slider** (effort, budgets): `sunken` track, `accent` fill, 20px thumb, and
  labelled stops.
- **Form layout:** one column, at most 560 wide. Group related fields with
  `title-3` headings. Primary action at the bottom right; on mobile, a sticky
  bottom action bar.
- **Validation:** inline, after blur or submit, never on every keystroke.

### 9.6 Composer

This is the most-used control. Anatomy:

```
┌─────────────────────────────────────────────────────────────┐
│ [attachment chips…]                                         │
│ Ask μcli anything…                                          │  ← body-lg, auto-grow 1→12 lines
│                                                             │
│ (+) [◉ Feature ▾] [Effort ▾] [plan]           🎤  ( ↑ Send ) │
└─────────────────────────────────────────────────────────────┘
```

- `surface`, `radius-lg` (web) or `radius-pill` while it is a single line
  (mobile), level 1. On focus-within it gets the accent focus halo.
- The mode chip carries the mode pigment's dot. A 3px wash glow along the top
  edge of the composer takes the mode pigment.
- Send is a 36px `primary` circle (44 on mobile). While generating it becomes
  a **Stop** square (`secondary` with a danger glyph). Enter sends and
  Shift+Enter adds a newline (configurable).
- The slash-command menu is a level-2 popover anchored above the caret, with
  keyboard navigation. Each item has a name in `mono`, a description in
  `ink-3`, and a match highlight as an `accent` wash.
- Plan mode adds a lilac dashed outline and a placeholder: "Plan mode —
  read-only. Ask for a plan…".
- **T:** the prompt line is as in §8.4. Completions are a prompt_toolkit menu
  styled `raised` (truecolor bg) with an `accent` selected row.

### 9.7 Messages

- **User message:** right-aligned bubble with a lagoon wash
  (`accent-wash`), `ink` text, `radius-lg` with a 6px bottom-right corner, at
  most 80% of the column width.
- **Assistant message:** no bubble. Full-width prose on canvas, in
  `body-lg`. A small header row shows the wordmark drop, model in `caption`,
  and elapsed time. Hover actions (copy, retry, fork) are ghost icon buttons
  that fade in.
- **Thinking:** a collapsible "Thinking" row (`ink-3`, italic, with the ink
  drop while streaming). It is collapsed by default once the answer starts.
- **Interim turns:** consecutive tool-heavy intermediate turns collapse into
  one "Worked for 2m · 14 steps ›" row (`sunken`, `radius-md`).
- **Markdown:**
  - Headings use the title scale, not oversized.
  - Lists have 24px indents.
  - Blockquotes are a `glass-subtle` inset with italic `ink-2` text; no
    coloured side rule.
  - Tables are hairline tables with a `sunken` header.
  - Links are `accent` ink, underlined on hover.
  - Inline code is `sunken`, `radius-xs`, `mono`.
- **T:**
  - User turns get a `›` gutter mark in `accent` with plain text, not a blue
    panel.
  - Assistant text renders through the shared Rich theme with no panel.
  - Thinking renders `ink-3` italic and collapses to one summary line after
    completion.

### 9.8 Tool call card

This is the single representation of a tool invocation. It replaces
`.trace-event`, `→ name`, the "Running X" label, and the `TranscriptItem`
copies.

```
╭ ● read  src/app/main.py  · lines 1–120                    0.4s  ›╮
╰──────────────────────────────────────────────────────────────────╯
```

- **Collapsed row:** a status glyph or ink drop, a tool-kind icon/label, the
  primary argument (path, command, query) in `mono` truncated in the middle,
  secondary meta in `ink-3`, duration, and a disclosure chevron. It uses
  `sunken` with `radius-md` and is 36px high.
- **Expanded:** arguments as a key/value grid (`mono-sm`), then a result
  preview (first 20 lines, with "Show all" and a cache link). Errors show a
  `✗` in danger ink and a danger-wash header tint, with no side rule.
- **Grouping:** three or more consecutive calls of the same kind collapse into
  "Read 6 files ›".
- **Running:** a sky ink drop with a live elapsed timer. **Done:** `✓` in ok
  ink, shown for 1.2s, then the glyph settles to neutral `ink-3`, so a long
  transcript does not turn into a field of green ticks.
- **Sub-agent call:** a card with the agent's pigment dot, name, progress
  meter and a "View" link that opens the sub-agent panel.
- **M:** the same card, which means **mobile chat shows tool calls for the
  first time**. Tap to expand, using a bottom sheet for long results.
- **T:** `● read  src/app/main.py  1–120` while running; on completion it is
  rewritten in place as `✓ read  src/app/main.py  1–120  0.4s` in `ink-3`.
  Verbose mode adds an indented, dimmed result preview.

### 9.9 Approval card (safety-critical)

This is the most important component in the system. It must be unmistakable,
calm, and impossible to approve by accident.

```
┌───────────────────────────────────────────────────────────────────┐
│░░ ◆ Needs you · Permission to edit 2 files ░░░░░░░ (tinted strip) │
│  write_file  src/server/routes.py   +18 −4                        │
│  ┌ diff preview (unified, 12 lines, "Open full diff ›") ───────┐  │
│  └─────────────────────────────────────────────────────────────┘  │
│  Risk: modifies tracked files                    [ ] Always allow │
│                          ( Deny )  ( Edit )  (  ✓ Approve  ⏎ )    │
└───────────────────────────────────────────────────────────────────┘
```

- It is a **state card** in `attention`: an apricot-wash header strip and
  the `◆ Needs you` status chip, on glass, with no side rule. A high-risk
  tool (shell with network, delete, push) escalates the header tint and risk
  chip to `danger`.
  Denial uses the `danger` button only when the action itself is destructive;
  otherwise Deny is `secondary`.
- **Content:**
  - the tool and a human sentence ("Edit 2 files", "Run `pytest -q`")
  - the target in `mono`
  - a diff or command preview
  - risk reasons
  - the "Always allow this tool" checkbox, wired to the request's `remember`
    flag. **This fixes the mobile bug where it was ignored.**
- **Actions:** Approve is `primary`, Deny is `secondary`, and Edit/Reply is
  `ghost` where supported. Approve is never auto-focused when the card
  appears. Focus goes to the card heading, and the `⏎` shortcut works only
  once the card itself has focus. This prevents an approval being triggered by
  typing that was meant for the composer.
- **Queue:** with several pending prompts, they stack with "1 of 3" and
  next/previous controls. The top bar shows a `◆ 3` attention chip that jumps
  to the queue.
- **Resolved:** the card collapses to a one-line breadcrumb such as
  "✓ Approved · write_file routes.py · 14:02" or "✗ Denied", and keeps its
  place in the transcript.
- **Live region:** a new approval announces "Mucli needs your approval: edit 2
  files" through an assertive live region (web) or `AccessibilityInfo`
  (Android).
- **YOLO:** when YOLO auto-approves, a resolved breadcrumb still renders,
  tagged `auto` in sun. Auto-approval is always visible after the fact.
- **M:** a bottom sheet that cannot be swiped away and needs an explicit
  choice. The floating "Input required" banner keeps the same attention
  styling.
- **T:** a rounded panel with a neutral border and an attention-ink title
  `◆ Needs you · Permission to edit 2 files`, the diff preview, and the key
  hint row `[a] approve  [A] always  [d] deny  [e] edit`. **All
  model-supplied text is escaped before it goes into markup** (this fixes the
  current `MarkupError` risk).

### 9.10 Choice, quiz, confirm, input prompts

These share the approval card's frame, using `accent` (questions) instead of
`attention` unless they block work. Options are full-width selectable rows
(`radius-md`, `sunken` hover, `accent` wash plus `✓` when selected) with
number shortcuts `1–9`. Quiz feedback uses `ok` and `danger` washes with the
explanation shown below. **T:** pickers share one prompt_toolkit style: cursor
`❯` in accent, selected `✓` in ok, hints in `ink-3`. They all use the same
colours (today some pickers highlight the current row in green and others in
yellow).

### 9.11 Diff view

There is one diff component (web, mobile and TUI), used in approvals, chat
`diff` events, and the job Diff tab.

- **Unified by default,** with split view on wide web screens (≥1200) as a
  toggle.
- **Lines:**
  - additions: `ok` wash background with a `+` gutter in ok ink
  - deletions: `danger` wash background with a `−` gutter in danger ink
  - context: canvas
  - hunk headers: `sunken` with `ink-3` text
  - intra-line changes: one step stronger in the wash
- **Gutters:** line numbers in `mono-sm` `ink-3`.
- **Syntax highlighting:** the Wash syntax theme (§9.12) on top of the diff
  washes.
- **File header:** path (`mono`), `+N −M` in ok/danger ink, collapse toggle,
  and "Open file".
- **File tree** (jobs): a list with status letters (`A`/`M`/`D` in
  ok/accent/danger ink).
- **T:** uses ok/danger **foreground** colours by default. A truecolor
  terminal with a dark background additionally gets a subtle background tint
  derived from the Dusk washes, and a light background gets Paper washes. This
  replaces the hardcoded `on #3a0000` values.

### 9.12 Code block and syntax theme

- `sunken`, `radius-sm`, `mono`, 16 padding. A header row shows the language
  (`caption`, `ink-3`) and a copy button (ghost icon that becomes `✓ Copied`).
- **Wash syntax theme**, one theme per mode:

  | Token class | Paper | Dusk |
  |---|---|---|
  | keywords | lilac ink | lilac ink |
  | strings | meadow ink | meadow ink |
  | numbers and constants | apricot ink | apricot ink |
  | functions | sky ink | sky ink |
  | types | lagoon ink | lagoon ink |
  | comments | `ink-3` italic | `ink-3` italic |
  | punctuation | `ink-2` | `ink-2` |

  It replaces github-dark/light (web), the custom tokenizer colours (mobile),
  and monokai (TUI, via a generated Pygments style).

### 9.13 Status, progress and loading

- **Ink drop:** the universal "working" indicator (§5), 8–12px.
- **Linear progress:** a 4px `sunken` track with a `running` pigment fill. An
  indeterminate bar is a soft travelling wash, not stripes.
- **Skeletons:** `sunken` blocks with a slow (1.8s) lighter wash sweep;
  static under reduced motion.
- **Pipeline stepper** (job lifecycle): a horizontal row of steps, each a
  status glyph plus label, joined by hairlines. The connector turns ok-pigment
  once that step is passed. The current step is a `running` wash pill.
  Waiting states show `◆` with an attention wash. Labels come from the shared
  vocabulary. On mobile the stepper is compact (dots plus the current label).
- **Connection state:** a chip in the top bar showing Live (ok),
  Reconnecting… (caution with ink drop) or Offline (danger) plus Retry.
  Mobile's offline queue shows "3 changes waiting to sync".

### 9.14 Context and token meters (L0–L5)

- **Compact meter (top bar and mobile header):**
  - A ring 20px across. The fill shows effective context fill in the real
    frame (`budgets.effective_fill`), coloured `ink-2`, turning caution at
    ≥65% and danger at ≥85%. These thresholds match the existing `.warm`/`.hot`
    rules.
  - The tooltip or tap opens the breakdown.
- **Stacked layer bar:** one horizontal bar split into L0–L5 segments in their
  layer pigments, with direct labels above segments of 6% or more and a legend
  for the rest. The budget cap is a dashed `ink` tick. The drift-corrected
  limit is a solid tick with a "real" label.
- **Observatory** (the memory panel and the trace): timeline area charts use
  the same layer pigments with 60% fill opacity. The charts follow
  `documentation/visualization_style_guide.md`, whose colour and glass
  sections are rewritten to reference Wash tokens.
- **T:** `ctx ◔ 42%` in the status line. `/stats` draws the stacked bar in
  block characters coloured per layer, with labels.

### 9.15 Job components

- **Job row:** status glyph, title (`label`, `ink`), repo · branch in `mono`
  `ink-3`, a mini stepper, the attention reason chip if waiting, and relative
  time. Hover shows a `sunken` fill. Rows are 56px high.
- **Job detail header:** title-1 with a status chip, the pipeline stepper, and
  an action bar. The primary action is the one the state asks for (Review
  plan, Approve push, Respond), and other actions sit in an overflow menu.
- **Needs-you list:** attention state cards, sorted by wait time. Each shows
  the attention reason (canonical label) and its primary action inline.
- **Review findings:** severity chips with fixed colours: blocker uses
  danger, major uses attention, minor uses caution, nit uses neutral.
- **Verdict chips:** ready (ok), ready with nits (ok outline), needs work
  (attention), do not merge (danger).

### 9.16 Toast and banner

- **Toast:** level 2, `radius-md`, 360px max, bottom-right on web and above
  the tab bar on mobile. It has a glyph in semantic ink, a message, and an
  optional action. Info and success toasts auto-dismiss after 5s; errors
  persist. It bleeds in, and lives in an `aria-live="polite"` region (assertive
  for errors).
- **Banner:** a full-width state strip at the top of a page or panel, for
  setup incomplete or plan mode. It uses the semantic wash with a glyph, a
  sentence, one tonal action, and an optional dismiss.
  - Plan mode banner: lilac.
  - YOLO has **no banner**. It is shown by the top-bar status chip
    (`◆ Auto-approve` in sun while on), which is always visible and toggles it.
    Auto-approved tool calls still leave an `auto` breadcrumb in the
    transcript (§9.9).

### 9.17 Modal, drawer, sheet, popover, command palette

- **Modal:** level 3, `radius-xl`, maximum width 560 (sm), 760 (md) or 960
  (lg). It has a title-2 header with a close icon, a scrolling body, and a
  sticky footer with actions aligned right (primary last). It traps focus,
  closes on Esc, and restores focus on close. It always uses
  `role="dialog"`, `aria-modal`, and `aria-labelledby`.
- **Destructive confirm:** a small modal. The title states the consequence
  ("Delete environment 'gpu-dev'?"). The `danger` button repeats the verb
  ("Delete"). Cancel gets default focus.
- **Drawer:** a side panel (settings, file browser) at `motion-medium`, 420px
  wide (full width below `md`).
- **Sheet (M):** see §8.3.
- **Popover and menu:** level 2, `radius-sm`, 6 padding, 32px items with a
  `sunken` hover. Shortcuts are aligned right in `ink-3`.
- **Command palette (⌘K, every web page):** level 3, centred, 640 wide. It
  has a search field and grouped results (Navigate, Sessions, Actions,
  Commands). It is the same palette on `/`, `/work` and `/containers`.

### 9.18 Empty, error and onboarding states

- **Empty state:** a spot wash in the context's pigment with a simple line
  icon, a title-3 line ("No jobs yet"), one sentence, and one primary or
  secondary action. Centred, with at most 360 text width.
- **Error state:** the same layout with a rose spot wash, plus "what
  happened" and "what to do", Retry, and "Copy details".
- **Welcome/launcher:**
  - The Dawn wash hero dissolves into paper.
  - Below it are the display headline "What are we building today?", a body-lg
    subline, and three large choice cards: Continue (recent sessions list),
    Start new, and Environments.
  - A footer line reads "Your models, workspace and environments stay under
    your control."
- **Launcher layout** (`components/launcher.css`): every landing page uses the
  welcome screen's shape: tall centred hero, three choice cards, a centred
  960px column for anything below, and a footnote. The Work dashboard uses it
  with the Lagoon hero, "What should we ship next?", and live cards for Needs
  you, Running now and Done this week, replacing the stat strip.
- **Mobile connect screen:** the Dawn wash with the wordmark, a server URL
  field, a "Connect" primary button, and a QR-scan alternative if present.

### 9.19 Tables

Hairline rows, no zebra striping. The header uses `sunken`, `label` type and
`ink-2`, and stays sticky. Numbers are aligned right and tabular. The selected
row gets an `accent` wash. Row actions appear on hover or focus. On mobile,
tables become stacked lists. **T:** Rich tables use `box.SIMPLE_HEAD`, a
header in `ink-2` bold, and no borders between columns.

### 9.20 Sessions and threads list

Each row has a session-type icon, the name (`label`), last activity in
`caption`, and status: a busy ink drop, an unread dot in the accent pigment,
or attention `◆`. The active row is a `glass-strong` pill (no side bar). Rows use
`role=button`/`tabindex=0` (sessions *and* threads) and have an overflow menu
on hover or long-press.

### 9.21 Terminal panel and logs

A `sunken` (Paper) or `#0F0C12` (Dusk) surface, `mono` text, ANSI colours
remapped to the Wash palette (a 16-colour table derived from the pigment ink
steps), and `radius-sm`. Log lines have the timestamp in `ink-3` and the level
glyph in its semantic colour.

---

## 10. Behaviour and UX rules

1. **Never lose the user's place.** Streaming auto-scroll pauses as soon as
   the user scrolls up, and a "Jump to latest ↓" pill appears. Panels remember
   their scroll position and open state per session.
2. **Optimistic but honest.** Show the user's message immediately. Mark it
   "Sending…" in `ink-3` until it is acknowledged, then "Not sent · Retry" in
   danger on failure.
3. **Interrupt is always one action away.** Esc (web/TUI) or the Stop button
   stops generation. It is never hidden in a menu.
4. **Safety states are global and persistent.** Plan mode, YOLO, and pending
   approvals show in the top bar on every page (chat, work, containers) and in
   the mobile header. Switching pages never hides them.
5. **Keyboard parity (web).**

   | Key | Action |
   |---|---|
   | ⌘/Ctrl+K | Palette |
   | ⌘/Ctrl+Enter | Send |
   | Esc | Stop or close the topmost layer |
   | `g c` / `g w` / `g e` | Go to Chat / Work / Environments |
   | `?` | Shortcut help |
   | `j` / `k` | Move through lists |

   The current Work shortcuts are folded into this set.
6. **Touch targets** are at least 44×44 dp on mobile and coarse pointers,
   using hit slop where the visual is smaller.
7. **Destructive actions** need confirmation unless they can be undone. Where
   undo exists (archive, dismiss), act immediately and show a toast with Undo.
8. **Loading:** under 300ms, show nothing. From 300ms to 2s, show a skeleton
   or ink drop. Over 2s, show a text status ("Building image · step 3/7").
9. **Errors** appear where they happened (inline). Toasts only report
   background failures.
10. **Density:** Comfortable (default) and Compact (web, a 0.875 multiplier on
    spacing, art off). It is per device and remembered.
11. **Theme:** System, Paper or Dusk, persisted per device on every surface
    (mobile currently forgets it on restart).

---

## 11. Accessibility checklist (per component and per release)

- Text contrast is at least 4.5:1 (3:1 for title-2 and larger, and for UI
  boundaries), measured on the actual composited background.
- Every status shows a glyph and a label, never colour alone.
- Every interactive element has a role, a name and keyboard access.
  `div`/`span`/`li` with click handlers must be buttons or links (the trace
  page currently has 18 violations).
- Focus is always visible, and the focus order follows the visual order.
- Live regions:
  - approvals: assertive
  - toasts and job status changes: polite
  - streaming: a polite summary announcement when the reply finishes, not
    every delta
- `prefers-reduced-motion`, `prefers-reduced-transparency`,
  `prefers-contrast: more` (hairlines become `ink-3`, washes become stronger)
  and `forced-colors` are honoured.
- Android: `accessibilityRole`, `accessibilityState` (checked, disabled,
  busy), and labels on every icon button. The OS font scale is respected up
  to 200% without clipping.
- Terminal: `NO_COLOR` gives glyphs and text with full meaning. There is no
  blink, and nothing important is conveyed only by colour or bold.

---

## 12. Token naming (contract)

There is one token namespace on every surface:

| Category | Pattern | Examples |
|---|---|---|
| Neutrals | `color.{canvas, surface, sunken, raised, hairline, hairline-strong, ink, ink-2, ink-3, plum, on-ink, scrim}` | |
| Pigments | `pigment.{lagoon, meadow, sun, apricot, rose, lilac, sky, plum}.{wash, pigment, ink}` | |
| Semantics | `{accent, running, ok, attention, caution, danger, plan, info, neutral}.{wash, pigment, ink}` | |
| Categorical | `mode.{name}`, `layer.{L0..L5}`, `series.{1..7}` | point at pigments |
| Type | `type.{display, title-1, …}` (size, line height, weight, tracking) | |
| Space | `space.{1..10}` | |
| Radius | `radius.{xs, sm, md, lg, xl, pill}` | |
| Elevation | `elevation.{0..3}` | |
| Motion | `motion.{micro, small, medium, bleed}` (duration plus easing) | |
| Z-index | `z.{base, raised, dropdown, sticky, overlay, modal, popover, toast}` | |

How each platform spells the tokens:

| Platform | Form | Example |
|---|---|---|
| CSS | `--wash-{path}` | `--wash-attention-ink` |
| TypeScript | `tokens.attention.ink` | |
| Python | `T.attention.ink` and Rich style names `wash.attention` | |

Status and state vocabulary (glyph, semantic, canonical label, per
`JobState` / attention reason / verdict / severity) lives in the **same
source file** as the tokens. Surfaces import it and never copy it.
