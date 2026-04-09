import van from "../../../vendor/van-1.6.0.min.js";

const { button, code, div, h1, header, input, p } = van.tags;

export function StatusHeader(store, onRefresh) {
  return header({ class: "van-top" },
    h1("Mu-CLI · VanJS Preview"),
    p("Feature-flagged preview. Use ?ui=van to test incremental migration."),
    div({ class: "van-row" },
      input({ value: store.apiBase, oninput: (e) => { store.apiBase.val = e.target.value; } }),
      button({ onclick: onRefresh }, "Refresh"),
      button({ onclick: () => store.runValidation?.() }, "Validate Lists"),
    ),
    div({ class: "van-meta" },
      code(() => `status=${store.status.val}`),
      code(() => `session=${store.currentSession.val || "none"}`),
      code(() => `sse=${store.connected.val ? "connected" : "disconnected"}`),
    ),
    p({ class: "van-event" }, () => `Latest event: ${store.latestEvent.val}`),
    p({ class: "van-event" }, () => `Validation: ${store.validationStatus?.val || "not run"}`),
  );
}
