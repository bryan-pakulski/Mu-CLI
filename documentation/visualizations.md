# Inline visualizations

MuCLI can publish interactive HTML documents into a conversation with the
`publish_visualization` tool. The same artifact is presented differently by
each client:

- Web GUI: sandboxed iframe inserted into the chat timeline.
- Mobile: sandboxed server document rendered in a native WebView card.
- Terminal: compact Rich panel containing a clickable browser link.

Visualization descriptors also carry a stable conversation-turn anchor. On a
normal reload the card returns at its exact publish-tool boundary. If history
compaction removes that intermediate tool metadata, the card falls back to the
same stable user turn, before the surviving final response. Page refresh,
session unload/reload, and mobile history pagination therefore preserve the
card in chat as well as in the Artifacts panel.

All new visualizations should follow the built-in `visualization-design` skill
and [MuCLI visualization style guide](visualization_style_guide.md). The skill
contains the shared light/dark tokens and is automatically expanded for chart,
graph, plot, heatmap, timeline, diagram, dashboard, and visualization requests.
Its reusable HTML scaffold lives at
`mu/skills/visualization-design/assets/template.html`.

## Container sessions

Container workers send visualization bytes and metadata to the host over the
existing authenticated artifact control plane. The host writes the artifact to
the authoritative session registry and emits the same `artifact_created` event
used by non-container sessions, so web and mobile clients render it immediately.

The worker protocol is versioned. Applying this change increments that version,
so reopening or reloading a container-backed session rebuilds an older worker
image before it can run visualization tools. The worker health check also
verifies the protocol reported by the image rather than trusting registry state.

Visualization HTML should remain self-contained. A `localhost` URL created
inside the container refers to the container itself and is not exposed to the
browser; publish the generated HTML file or inline HTML instead.

## Tool contract

Provide exactly one of `html` or `file_path`:

```json
{
  "name": "latency.html",
  "title": "Provider latency",
  "height": 520,
  "html": "<!doctype html>..."
}
```

HTML files may use inline JavaScript or charting libraries loaded from a CDN.
Prefer a self-contained document with its data embedded so session playback
does not depend on temporary files or a separate development server.

## Security boundary

Visualization documents are served from the session artifact registry and
rendered with both a response-level CSP sandbox and a client iframe sandbox.
Scripts are allowed, but `allow-same-origin` is deliberately omitted. The
document therefore cannot inspect the parent chat, read MuCLI cookies or local
storage, or call authenticated same-origin APIs as the user.

External network access is allowed for chart libraries and public datasets.
Do not place secrets in visualization HTML or fetch private data directly from
the embedded page.

## Theme contract

Web appends the active `mucli-theme` setting to the artifact URL; mobile does
the same from `useTheme()`. The artifact response installs a small sandbox-local
bootstrap that sets `data-theme` and `window.__MUCLI_THEME__` before the visual
runs and dispatches `mucli-theme-change` on live updates. CSS and SVG should use
the skill's `--mu-*` variables. Canvas/WebGL views should resolve those variables
and redraw on the event while preserving zoom, filters, and selection.

## Mobile dependency

After applying the patch, install the Expo-compatible WebView dependency:

```bash
cd mobile/android
npx expo install react-native-webview
```

Then rebuild the native app because WebView includes native code.

## Example

A minimal interactive graph can be generated without a build step:

```html
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<div id="chart" style="width:100%;height:100vh"></div>
<script>
Plotly.newPlot('chart', [{x:[1,2,3], y:[2,5,3], type:'scatter'}], {
  margin: {t: 24, r: 20, b: 42, l: 48}
}, {responsive: true});
</script>
```
