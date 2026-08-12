# MuCLI visualization style guide

MuCLI visualizations are explanatory product surfaces embedded in a working
conversation. They should look native beside chat, context observability, and
memory—not like separately branded dashboards.

The built-in `visualization-design` skill applies this contract automatically
for visualization, chart, graph, plot, dashboard, heatmap, timeline, diagram,
and data-story requests. This document is the implementation reference for
people adding or reviewing visualization code.

## Design principles

1. **Answer one question.** A visual begins with a decision or comparison, not
   a chart type. Split unrelated questions into aligned views.
2. **Data earns every pixel.** Remove decoration that does not improve reading,
   interaction, orientation, or provenance.
3. **Overview before detail.** Give the shape first, then selection, tooltip,
   drill-down, or exact-value table. Dense graphs begin with a relevant
   neighbourhood rather than every node.
4. **Honesty over drama.** Scales, missingness, uncertainty, and denominators
   must be explicit. Visual intensity must reflect data intensity.
5. **One product, two themes.** Light and dark are equal targets. A successful
   dark chart that becomes low-contrast or muddy in light mode is unfinished.
6. **Interaction is optional enhancement.** The main conclusion and important
   values remain available without hover, animation, or a precision pointer.

## Choose the visual from the task

| User question | Preferred forms | Avoid |
| --- | --- | --- |
| How did it change? | line, step, horizon, small multiples | unordered bars, smoothed curves that imply false data |
| Which is larger? | sorted horizontal bars, dot plot | 3D bars, radial bars |
| How is it distributed? | histogram, strip, box/violin, ECDF | mean-only displays |
| Are variables related? | scatter, hexbin, faceted scatter | dual axes that imply correlation |
| What makes up the total? | stacked bars/area, compact table | many-slice pie/donut |
| When did state change? | annotated timeline, state band | unlabelled event dots |
| Where does volume flow? | Sankey/alluvial, flow map | Sankey for simple sequences |
| What is connected? | one-hop ego graph, adjacency matrix | default full-network hairball |
| Where is it happening? | proportional-symbol/choropleth map | map when location is incidental |
| How is context evolving? | aligned layer heatmap + total/delta trace | a single current-state square |

Prefer a number, sentence, or table whenever it is clearer than a chart.

## Data integrity rules

- State units, time zone, population, filters, aggregation, and freshness.
- Bars start at zero. Truncated continuous axes must show their domain clearly.
- Do not use two quantitative y-axes. Use aligned panels or normalized indices.
- Missing, unavailable, and zero are distinct states.
- Include sample size and uncertainty intervals where interpretation depends on
  them. Label forecasts, model outputs, interpolation, and inferred values.
- Sort by value for comparison and chronologically for time. Preserve meaningful
  domain ordering such as severity or lifecycle.
- Use position and length for the comparisons that matter most. Area, angle,
  colour saturation, and animation are weaker encodings.
- Never use perspective or 3D volume to encode ordinary values.
- Provide an exact-value route: labels, accessible table, download, or details.

## Visual system

### Composition

Use this order unless the visualization is intentionally immersive:

1. short title;
2. one-sentence subtitle stating measure and scope;
3. primary visual;
4. adjacent legend or direct labels;
5. compact source/method/freshness note;
6. optional details or exact-value table.

Use a single glass frame. Nested panes are reserved for genuinely independent
views, not decoration. Whitespace and alignment should establish most grouping;
hairlines only clarify boundaries that spacing cannot.

### Scale

- Spacing: `4, 8, 12, 16, 24, 32, 48, 64px`.
- Type: `12, 14, 16, 20, 24, 32px`; body line-height `1.45–1.6`.
- Radius: 12px for controls, 16px for the outer frame, pill only for compact
  status/filter chips.
- Numerics: `font-variant-numeric: tabular-nums`.
- Borders: one neutral 1px hairline. Do not colour borders by series.
- Shadows: at most one soft neutral shadow for elevation.

### Colour

Neutral scaffolding plus one MuCLI blue accent is the default. Semantic colours
are limited to success, warning, and error. Multi-series categorical palettes
must remain distinguishable under common colour-vision deficiencies and should
use direct labels or secondary shape/dash encoding.

Do not use rainbow scales for ordered data. Sequential data uses a monotonic
lightness scale; diverging data has a meaningful centre; categorical data uses
separable hues without implying order.

The canonical tokens and theme event are embedded in the built-in skill and its
reusable template. Do not copy colours from screenshots.

## Theme contract

The artifact view injects a bootstrap before visualization code runs:

- the current client setting is passed as `?mucli_theme=light|dark`;
- `<html data-theme="…">` is set before content executes;
- `window.__MUCLI_THEME__` exposes the current value;
- `mucli-theme-change` is dispatched with `{detail: {theme}}`;
- web also posts live theme messages into already mounted frames;
- mobile reloads the isolated WebView with its current `useTheme()` value.

CSS and SVG use `[data-theme]` plus `--mu-*` variables. Canvas/WebGL resolves
CSS variables again on `mucli-theme-change` and redraws without losing filters,
zoom, or selection.

## Glass aesthetic

The glass treatment is quiet environmental depth:

- translucent neutral surface with a solid-colour fallback;
- subtle backdrop blur where supported;
- one hairline that remains visible in both themes;
- low-amplitude background atmosphere, never behind fine labels;
- no neon outlines, excessive glows, glossy bevels, or decorative gradients;
- no card-per-metric layout when one aligned strip or table is clearer.

Glass must not lower contrast. Text and data marks are evaluated against the
actual composited surface, not just their nominal colour values.

## Accessibility and input

- Body text and essential labels meet 4.5:1 contrast; large marks/text meet 3:1.
- Focus indicators meet 3:1 against adjacent colours.
- Every pointer action has keyboard parity. Prefer semantic buttons and inputs.
- Touch targets are at least 44×44px; mouse targets at least 32px.
- Tooltips are reachable by focus/tap and never contain the only critical value.
- Provide a text takeaway and an exact-value alternative for canvas/SVG content.
- Do not rely on colour alone for status or selection.
- Honour `prefers-reduced-motion`; stop nonessential transitions completely.

## Responsive and performance

Design at 320px first. Titles wrap; controls collapse or scroll intentionally;
legends move below the plot; labels thin by explicit priority; the document
never creates accidental horizontal page scroll.

SVG is preferred for modest, interactive mark counts. Canvas is appropriate for
large dense fields. Aggregate or bin before drawing thousands of overlapping
marks that communicate no additional information. Debounce resize, lazy-load
details, and do not run a permanent animation loop on static data.

## Review checklist

- [ ] The title and subtitle state the question, measure, scope, and unit.
- [ ] The encoding matches the analytical task.
- [ ] Axes, baselines, aggregation, missingness, and uncertainty are honest.
- [ ] Important values remain available without hover.
- [ ] Light and dark modes were inspected, not assumed.
- [ ] The layout works at 320px and with large text.
- [ ] Keyboard, touch, focus, contrast, and reduced motion are supported.
- [ ] The visual uses one restrained glass frame and one primary accent.
- [ ] Data source, freshness, and transformations are traceable.
- [ ] The HTML is bounded, self-contained where practical, and secret-free.

Start new artifacts from
`mu/skills/visualization-design/assets/template.html` and replace its example
content without removing the theme, accessibility, or reduced-motion hooks.
