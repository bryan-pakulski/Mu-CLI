---
name: ui-ux
description: Design or review UI for a modern, content-first, low-chrome aesthetic.
trigger: \b(ui|ux|design|frontend|css|tailwind|component|layout|typography|spacing|palette|theme|wireframe|mock-?up|accessibility|a11y)\b
---

When designing or reviewing a UI, hold to these principles. They are
biased toward the aesthetic of Claude, Linear, Vercel, and Anthropic's
own surfaces: content-first, restrained, intentional.

## First principles

1. **Content is the product.** Chrome (borders, headers, sidebars,
   toolbars) exists to serve content, not the other way around. If a
   line, divider, or panel doesn't earn its place, remove it.
2. **Whitespace is structure.** Generous, consistent spacing groups
   related content and separates unrelated content. Pack only when
   information density is the actual user need (tables, lists).
3. **One accent, neutral everything else.** A single accent color for
   primary actions and brand identity. Everything else is on a neutral
   gray ramp. Reserve red/yellow/green strictly for semantic state
   (error / warning / success).
4. **Hierarchy through size and weight, not color.** Two or three type
   sizes max in a typical view. Bold or larger = more important. Avoid
   colored text for emphasis except for semantic state or links.
5. **Functional motion only.** Transitions clarify state changes
   (fade-in on mount, slide on drawer open). No decorative animations,
   no parallax, no auto-playing video.
6. **Native conventions over invention.** Don't reinvent checkboxes,
   selects, or scrollbars unless you have a specific reason. Users
   know how the platform's controls work.

## Visual specifics

- **Spacing scale.** Use a single scale (typically `4 / 8 / 12 / 16 / 24
  / 32 / 48 / 64`px). Never ad-hoc values. Tailwind's default scale is
  fine.
- **Type scale.** A small set of sizes (e.g. `12 / 14 / 16 / 20 / 24 /
  32`). Body copy is `15-16px`. Line-height `1.5` for prose,
  `1.2-1.3` for headings.
- **Borders and dividers.** Default to **no border**. Add one only
  where grouping cannot be communicated by spacing alone. Single
  hairline (`1px`, neutral-200/800), never colored.
- **Shadows.** Used sparingly for elevation (popovers, modals,
  dropdowns). Subtle, soft, single shadow. Never stacked, never
  colored.
- **Corner radius.** Consistent across the system. One small radius
  (`4-6px`) and one larger (`8-12px`) for cards/modals. Pick one
  language; don't mix sharp and round.
- **Icons.** Stroke-style, consistent stroke width, single size system
  (`16` and `20`px is typical). Label icons unless they're universally
  understood (close, search, plus). Never decorative.
- **Tabular numerics** (`font-variant-numeric: tabular-nums`) for any
  column of numbers — prices, counts, percentages, timestamps.
- **Truncate with intent.** Single-line text gets ellipsis on overflow
  and a tooltip if the full text isn't recoverable elsewhere. Multi-
  line content gets `line-clamp` with a "show more" affordance.

## Accessibility (non-negotiable)

- Contrast: body text ≥ `4.5:1`, large text ≥ `3:1`, focus rings ≥
  `3:1` against adjacent colors.
- Every interactive element has a visible focus state. Don't remove
  the default focus ring without replacing it.
- Touch targets ≥ `44×44px`. Mouse targets ≥ `32px` tall with a
  hover state.
- Keyboard parity: every mouse interaction has a keyboard equivalent
  (Tab, Enter, Space, Arrow keys, Escape).
- Semantic HTML first (`<button>`, `<a href>`, `<label for>`). ARIA
  only when semantics don't exist.
- Respect `prefers-reduced-motion` and `prefers-color-scheme`.

## States are first-class

For every component or view, design these explicitly — they are not
afterthoughts:

- **Empty.** What does this look like with zero data? Provide a clear
  next action.
- **Loading.** Skeletons (matched to the eventual layout) preferred
  over spinners. No spinner-on-blank-page.
- **Error.** Actionable message ("Retry", "Reload"), not just "Something
  went wrong." Show what failed and what to do.
- **Partial / paginated.** "Loading more...", "End of list", or page
  controls — never silent truncation.
- **Disabled.** Visibly disabled (reduced opacity, no hover state). If
  the user can't tell why it's disabled, add a tooltip.

## What to remove

When reviewing an existing UI, look first for things to delete:

- Section headers that label the obvious ("Actions", "Details").
- Icons paired with their own text label that adds nothing
  (a trash icon next to the word "Delete" — pick one).
- Dividers between rows that already have adequate spacing.
- Borders around cards that already sit on a contrasting background.
- Drop shadows on flat surfaces.
- Emoji in functional UI (they're decoration, and they break visual
  hierarchy).
- "Helpful" tooltips on self-explanatory elements.
- Loading spinners that flash for <300ms (just don't show them).

## Review checklist

When asked to review a UI design or implementation, walk through:

1. What can I remove without losing meaning?
2. Is the spacing on a consistent scale?
3. Is there exactly one accent color in active use?
4. Is type hierarchy clear from size/weight alone (cover the colors —
   does it still read)?
5. Do all states exist (empty / loading / error / disabled)?
6. Tab through it — is the focus order sensible, is the ring visible?
7. Resize to 320px wide — does it still work?
8. Check contrast on the lightest gray text against its background.
9. Are touch targets large enough?
10. Does anything animate that doesn't need to?

Output as a numbered list with severity tags (`blocker | warning |
nit`) and concrete suggestions, not vague complaints. Skip praise; go
straight to findings.
