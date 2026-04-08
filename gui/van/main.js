import van from "../vendor/van-1.6.0.min.js";
import { createStore } from "./state/store.js";
import { createApiClient } from "./services/api.js";
import { createSseClient } from "./services/sse.js";

const { button, code, div, h1, h2, header, input, li, main, p, section, textarea, ul } = van.tags;

function renderMessage(message) {
  const role = String(message?.role || "assistant");
  const content = String(message?.content || message?.text || "");
  return div({ class: `van-message ${role === "user" ? "user" : "assistant"}` },
    div({ class: "van-message-role" }, role),
    div({ class: "van-message-content" }, content || "…"),
  );
}

export async function bootVanUi() {
  const store = createStore();
  const api = createApiClient(store.apiBase);

  async function refresh() {
    store.status.val = "Loading";
    const [sessionsPayload, statePayload] = await Promise.all([api.sessions(), api.state()]);
    store.sessions.val = sessionsPayload?.sessions || [];
    store.currentSession.val = store.currentSession.val || sessionsPayload?.current || store.sessions.val[0] || "";
    if (!store.currentSession.val) {
      store.currentSession.val = statePayload?.state?.session_name || "";
    }
    const historyPayload = await api.history(store.currentSession.val || "");
    store.history.val = historyPayload?.history || [];
    store.status.val = "Ready";
  }

  const sse = createSseClient({
    apiBaseState: store.apiBase,
    onOpen: () => {
      store.connected.val = true;
      store.latestEvent.val = "SSE connected";
    },
    onError: () => {
      store.connected.val = false;
      store.latestEvent.val = "SSE disconnected";
    },
    onEvent: ({ event, data }) => {
      store.latestEvent.val = `${event}: ${String(data || "").slice(0, 100)}`;
      if (event === "task.updated" || event === "task.completed") {
        refresh().catch((error) => {
          store.status.val = `Refresh failed: ${error.message}`;
        });
      }
    },
  });

  const app = main({ class: "van-shell" },
    header({ class: "van-top" },
      h1("Mu-CLI · VanJS Preview"),
      p("Feature-flagged preview. Use ?ui=van to test incremental migration."),
      div({ class: "van-row" },
        input({ value: store.apiBase, oninput: (e) => { store.apiBase.val = e.target.value; } }),
        button({ onclick: () => refresh().catch((e) => { store.status.val = e.message; }) }, "Refresh"),
      ),
      div({ class: "van-meta" },
        code(() => `status=${store.status.val}`),
        code(() => `session=${store.currentSession.val || "none"}`),
        code(() => `sse=${store.connected.val ? "connected" : "disconnected"}`),
      ),
      p({ class: "van-event" }, () => `Latest event: ${store.latestEvent.val}`),
    ),
    div({ class: "van-layout" },
      section({ class: "van-panel" },
        h2("Sessions"),
        ul(() => store.sessions.val.map((name) => li(
          button({
            class: () => (name === store.currentSession.val ? "van-active" : ""),
            onclick: async () => {
              store.currentSession.val = name;
              const history = await api.history(name);
              store.history.val = history?.history || [];
            },
          }, name),
        ))),
      ),
      section({ class: "van-panel van-chat" },
        h2("Chat (Read-Only Preview)"),
        div({ class: "van-feed" }, () => store.history.val.map(renderMessage)),
        textarea({ disabled: true, rows: 4, placeholder: "Composer migration starts in Phase 3." }),
      ),
    ),
  );

  document.body.innerHTML = "";
  van.add(document.body, app);

  await refresh();
  sse.open();
}
