# Inline visualizations

MuCLI can publish interactive HTML documents into a conversation with the
`publish_visualization` tool. The same artifact is presented differently by
each client:

- Web GUI: sandboxed iframe inserted into the chat timeline.
- Mobile: sandboxed server document rendered in a native WebView card.
- Terminal: compact Rich panel containing a clickable browser link.

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
