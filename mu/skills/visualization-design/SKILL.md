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
- Surfaces: translucent neutral fill, 1px neutral hairline, 14–20px radius, and
  at most one soft elevation shadow. Blur is optional enhancement, never the
  only separation mechanism.
- Colour: Wash paper/dusk neutrals plus lagoon teal as the single default
  accent (the MuCLI Wash design system). Reserve meadow/apricot/rose for
  semantic success/needs-attention/error; use `--mu-series-1..7` in order for
  categorical series. Avoid rainbow
  palettes, neon glows, gradients used as decoration, and coloured card borders.
- Gridlines: only the major lines needed to read values. Axes and labels are
  quieter than data, but must still meet contrast requirements.
- Motion: only to explain a transition or selection. Respect
  `prefers-reduced-motion`; never animate continuously for atmosphere.

Use these shared tokens and provide both themes:

```css
:root, :root[data-theme="dark"], :root[data-theme="dusk"] {
  color-scheme: dark;
  --mu-canvas: #16131B; --mu-glass: rgba(29,25,35,.92);
  --mu-glass-soft: rgba(29,25,35,.64); --mu-text: #F3EEE8;
  --mu-text-soft: #D2C9D1; --mu-text-dim: #A89FAD;
  --mu-accent: #7CC4CB; --mu-accent-strong: #5FA3AB;
  --mu-border: #332C3D; --mu-grid: rgba(243,238,232,.09);
  --mu-success: #A5D68A; --mu-warning: #F5B58A; --mu-error: #F29AAE;
  /* categorical series (Wash pigments, fixed order) */
  --mu-series-1: #5FA3AB; --mu-series-2: #F2A36B; --mu-series-3: #B39DDB;
  --mu-series-4: #8DBF6A; --mu-series-5: #E58A9E; --mu-series-6: #84AEE3;
  --mu-series-7: #F2C94C;
}
:root[data-theme="light"], :root[data-theme="paper"] {
  color-scheme: light;
  --mu-canvas: #F8F6F1; --mu-glass: rgba(255,254,251,.92);
  --mu-glass-soft: rgba(255,254,251,.66); --mu-text: #1E1A17;
  --mu-text-soft: #4E4844; --mu-text-dim: #6B645F;
  --mu-accent: #2A6870; --mu-accent-strong: #1F5359;
  --mu-border: #E6E1D8; --mu-grid: rgba(30,26,23,.09);
  --mu-success: #3D6A2A; --mu-warning: #984812; --mu-error: #A3324B;
  --mu-series-1: #5FA3AB; --mu-series-2: #F2A36B; --mu-series-3: #B39DDB;
  --mu-series-4: #8DBF6A; --mu-series-5: #E58A9E; --mu-series-6: #84AEE3;
  --mu-series-7: #F2C94C;
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
