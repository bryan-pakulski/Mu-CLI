import van from "../vendor/van-1.6.0.min.js";
import { createStore } from "./state/store.js";
import { createApiClient } from "./services/api.js";
import { createSseClient } from "./services/sse.js";
import { AppShell } from "./components/app_shell.js";

export async function bootVanUi() {
  const store = createStore();
  const api = createApiClient(store.apiBase);

  async function refreshFeaturePlan() {
    const selectedId = store.selectedFeatureId.val;
    const record = store.features.val.find((feature) => String(feature.feature_id || "") === String(selectedId || ""));
    const directory = String(record?.directory || "").trim();
    if (!directory) {
      store.featurePlan.val = null;
      return;
    }
    try {
      const payload = await api.featurePlan(directory);
      store.featurePlan.val = payload?.feature_plan || null;
    } catch {
      store.featurePlan.val = null;
    }
  }

  async function selectFeature(featureId) {
    store.selectedFeatureId.val = String(featureId || "");
    await refreshFeaturePlan();
  }

  async function refresh() {
    store.status.val = "Loading";
    const [sessionsPayload, statePayload, runtimePayload, tasksPayload, approvalsPayload, featuresPayload, workspacePayload, stagedPayload] = await Promise.all([
      api.sessions(),
      api.state(),
      api.runtime(),
      api.tasks(),
      api.approvals(),
      api.features(),
      api.workspaces(),
      api.stagedFiles(),
    ]);
    store.sessions.val = sessionsPayload?.sessions || [];
    store.currentSession.val = store.currentSession.val || sessionsPayload?.current || store.sessions.val[0] || "";
    if (!store.currentSession.val) {
      store.currentSession.val = statePayload?.state?.session_name || "";
    }
    store.runtime.val = runtimePayload || null;
    store.tasks.val = tasksPayload?.tasks || [];
    store.approvals.val = approvalsPayload?.pending_approvals || [];
    store.features.val = featuresPayload?.features || [];
    store.workspaces.val = workspacePayload?.folders || [];
    store.stagedFiles.val = stagedPayload?.staged_files || [];
    if (!store.selectedFeatureId.val && store.features.val.length) {
      store.selectedFeatureId.val = String(store.features.val[0].feature_id || "");
    }
    await refreshFeaturePlan();
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
      if (event === "task.updated" || event === "task.completed" || event === "approval.pending" || event === "approval.resolved") {
        refresh().catch((error) => {
          store.status.val = `Refresh failed: ${error.message}`;
        });
      }
    },
  });

  const app = AppShell(
      store,
      api,
      selectFeature,
      () => refresh().catch((e) => {
        store.status.val = e.message;
      }),
  );

  document.body.innerHTML = "";
  van.add(document.body, app);

  await refresh();
  sse.open();
}
