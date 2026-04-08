import van from "../vendor/van-1.6.0.min.js";
import { createStore } from "./state/store.js";
import { createApiClient } from "./services/api.js";
import { createSseClient } from "./services/sse.js";
import { AppShell } from "./components/app_shell.js";

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

  const app = AppShell(
    store,
    api,
    () => refresh().catch((e) => {
      store.status.val = e.message;
    }),
  );

  document.body.innerHTML = "";
  van.add(document.body, app);

  await refresh();
  sse.open();
}
