const query = new URLSearchParams(window.location.search);
const uiMode = (query.get("ui") || "").trim().toLowerCase();

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.body.appendChild(script);
  });
}

async function startLegacyUi() {
  await loadScript("https://cdnjs.cloudflare.com/ajax/libs/marked/15.0.12/marked.min.js");
  await loadScript("https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.2.6/purify.min.js");
  await loadScript("https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js");
  await import("./van-bridge.js");
  await loadScript("./app.js");
}

async function startVanUi() {
  const { bootVanUi } = await import("./van/main.js");
  await bootVanUi();
}

(async () => {
  if (uiMode === "van") {
    await startVanUi();
    return;
  }
  await startLegacyUi();
})().catch((error) => {
  console.error("UI bootstrap failed", error);
  const fallback = document.createElement("pre");
  fallback.textContent = `UI bootstrap failed: ${error?.message || error}`;
  fallback.style.color = "#f87171";
  fallback.style.padding = "16px";
  document.body.appendChild(fallback);
});
