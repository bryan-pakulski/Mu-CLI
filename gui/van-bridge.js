import van from "./vendor/van-1.6.0.min.js";

const statusText = van.state("Booting");
const statusKind = van.state("connected");

van.derive(() => {
  const label = statusText.val?.trim() || "Idle";
  document.title = `Mu-CLI — ${label}`;
  document.documentElement.dataset.vanStatusKind = statusKind.val || "";
});

window.addEventListener("mucli:status", (event) => {
  const detail = event?.detail || {};
  statusText.val = String(detail.text || "");
  statusKind.val = String(detail.kind || "");
});
