---
name: visualization-design
description: Build honest, readable MuCLI visualizations with the shared minimal glass system and live light/dark themes.
trigger: \b(visuali[sz](?:e|ation|ing)|chart|graph|plot|dashboard|heatmap|timeline|diagram|data story)\b
---

# MuCLI visualization design

Use this skill whenever you design, generate, or review a visualization that
will be published through `publish_visualization`. The visual is part of the
MuCLI product, not a microsite: it must feel calm, precise, explainable, and
native beside the conversation.

## 1. Start with the question

Write the one sentence the visual must answer before choosing a chart. Prefer
the smallest form that answers it:

- change over time → line, step, horizon, or small multiples;
- ranked comparison → sorted horizontal bars or dot plot;
- distribution → histogram, strip, box, violin, or ECDF;
- relationship → scatterplot with an honest trend/interval when justified;
- composition → stacked bars/area only when totals matter; use pie/donut only
  for 2–5 parts with obvious differences;
- events and state transitions → annotated timeline;
- flow → Sankey/alluvial only when path volume is the question;
- hierarchy → tree, icicle, or treemap when area encoding is meaningful;
- networks/memory → selected-node ego graph first, not a giant hairball;
- geography → map only when location changes the conclusion;
- dense model/context evolution → heatmap plus an aligned total/delta trace.

If prose, a number, or a small table is clearer, do not create a chart.

## 2. Protect data integrity

- Label units, population, time zone, aggregation, filters, and data freshness.
- Start bar axes at zero. If a line axis is truncated, make the domain and break
  unmistakable. Avoid dual axes; use aligned panels instead.
- Preserve missing values as missing. Never silently turn them into zero.
- Show uncertainty, sample size, targets, and thresholds when they affect the
  conclusion. Distinguish measured, estimated, and inferred values.
- Sort categorically unless sequence is meaningful. Keep time chronological.
- Use position/length before area, angle, saturation, or animation for numeric
  comparison. Never use 3D perspective for quantitative data.
- Give the user the source and a compact "how to read this" note when the
  encoding is not immediately obvious.

## 3. MuCLI visual language

Content leads; chrome recedes. Use one quiet glass surface around the visual,
not a wall of nested cards.

- Spacing: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64`.
- Type: system sans for labels and prose; system mono for timestamps, tokens,
  ids, coordinates, and aligned values. Use tabular numerics.
- Hierarchy: title, one-sentence subtitle, visual, then compact legend/source.
  Use size and weight before colour.
- Surfaces: translucent neutral fill, 1px neutral hairline, 12–16px radius, and
  at most one soft elevation shadow. Blur is optional enhancement, never the
  only separation mechanism.
- Colour: neutral scaffolding plus MuCLI blue as the single default accent.
  Reserve green/amber/red for semantic success/warning/error. Avoid rainbow
  palettes, neon glows, gradients used as decoration, and coloured card borders.
- Gridlines: only the major lines needed to read values. Axes and labels are
  quieter than data, but must still meet contrast requirements.
- Motion: only to explain a transition or selection. Respect
  `prefers-reduced-motion`; never animate continuously for atmosphere.

Use these shared tokens and provide both themes:

```css
:root, :root[data-theme="dark"] {
  color-scheme: dark;
  --mu-canvas: #0d1219; --mu-glass: rgba(18,24,32,.90);
  --mu-glass-soft: rgba(19,25,34,.62); --mu-text: #eef2f6;
  --mu-text-soft: #bbc4cd; --mu-text-dim: #85909c;
  --mu-accent: #8ba9c6; --mu-accent-strong: #7295b5;
  --mu-border: rgba(222,231,240,.085); --mu-grid: rgba(222,231,240,.09);
  --mu-success: #86a897; --mu-warning: #aa987b; --mu-error: #cd7c86;
}
:root[data-theme="light"] {
  color-scheme: light;
  --mu-canvas: #edf3f8; --mu-glass: rgba(252,254,255,.91);
  --mu-glass-soft: rgba(249,252,254,.64); --mu-text: #1c2732;
  --mu-text-soft: #53616e; --mu-text-dim: #5f6e7b;
  --mu-accent: #6286a8; --mu-accent-strong: #527795;
  --mu-border: rgba(39,59,77,.10); --mu-grid: rgba(39,59,77,.10);
  --mu-success: #66877a; --mu-warning: #8f7d60; --mu-error: #b95f6b;
}
```

MuCLI injects the initial theme before visualization code runs. It sets
`document.documentElement.dataset.theme` and `window.__MUCLI_THEME__`, then
emits `mucli-theme-change` whenever the actual web or mobile setting changes.
Theme-aware canvas/WebGL charts must redraw from that event:

```js
const currentTheme = () => document.documentElement.dataset.theme || 'dark';
window.addEventListener('mucli-theme-change', ({ detail }) => {
  redrawForTheme(detail.theme);
});
```

CSS/SVG should use the variables directly. Canvas libraries must resolve the
variables with `getComputedStyle(document.documentElement)` on every redraw;
never bake dark-mode RGB values into the chart.

## 4. Interaction and responsive behaviour

- Design at 320px first, then expand. Avoid horizontal page scrolling.
- Touch targets are at least 44×44px; pointer targets at least 32px.
- Tooltips supplement visible labels; they do not contain the only copy of a
  critical value. Make selection available through keyboard and touch.
- Keep legends adjacent to the marks they explain. Direct-label short series.
- For dense views, use overview → selection → details. Default memory/network
  views to one hop; offer deliberate expansion.
- Preserve zoom/filter/selection when the chart redraws for a theme change.
- Use semantic HTML around SVG/canvas, a concise text summary, and an accessible
  table or list for values that must be exactly recoverable.
- Charts must remain useful without hover and under reduced motion.

## 5. Performance and delivery

- Embed bounded data and keep the HTML self-contained where practical.
- Aggregate before rendering thousands of indistinguishable marks; use canvas
  only when SVG/HTML would be measurably too slow.
- Lazy-load expensive detail, debounce resize, and avoid animation loops when
  nothing changes.
- Never include secrets or private API credentials. The sandbox cannot access
  MuCLI cookies or the parent DOM.
- Publish with a short human title and an intentional height. Include a concise
  prose takeaway beside the artifact.

## Final review

Before publishing, check: question answered; encoding appropriate; axes/units
honest; uncertainty and missingness visible; light and dark both inspected;
320px layout usable; keyboard/focus/touch supported; contrast readable;
reduced-motion honoured; exact values recoverable; no decorative chart junk.

The full developer-facing guide and reusable scaffold live in
`documentation/visualization_style_guide.md` and
`mu/skills/visualization-design/assets/template.html`.
