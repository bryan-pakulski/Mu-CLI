import van from "../vendor/van-1.6.0.min.js";
import { createStore } from "./state/store.js";
import { createApiClient } from "./services/api.js";
import { createSseClient } from "./services/sse.js";
import { AppShell } from "./components/app_shell.js";

export async function bootVanUi() {
  const store = createStore();
  const api = createApiClient(store.apiBase);
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  if (!store.validationStatus) {
    store.validationStatus = van.state("not run");
  }

  function assertListCounts() {
    const sessionItems = document.querySelectorAll(".van-session-item").length;
    const messageItems = document.querySelectorAll(".van-message").length;
    const featureItems = document.querySelectorAll(".van-feature-item").length;
    const taskItems = document.querySelectorAll(".van-activity-item").length;
    const expectedFeatures = Math.min(store.features.val.length, 10);
    const expectedTasksAndApprovals = Math.min(store.tasks.val.length, 8) + Math.min(store.approvals.val.length, 5);

    const errors = [];
    if (sessionItems !== store.sessions.val.length) errors.push(`sessions:${sessionItems}/${store.sessions.val.length}`);
    if (messageItems !== store.history.val.length) errors.push(`messages:${messageItems}/${store.history.val.length}`);
    if (featureItems !== expectedFeatures) errors.push(`features:${featureItems}/${expectedFeatures}`);
    if (taskItems !== expectedTasksAndApprovals) errors.push(`activity:${taskItems}/${expectedTasksAndApprovals}`);
    return errors;
  }

  async function runValidation(iterations = 5) {
    store.validationStatus.val = "running";
    for (let i = 0; i < iterations; i += 1) {
      await refresh();
      await delay(25);
      const errors = assertListCounts();
      if (errors.length) {
        store.validationStatus.val = `failed (${errors.join(", ")})`;
        return;
      }
    }
    store.validationStatus.val = `passed (${iterations} refresh cycles)`;
  }

  store.runValidation = () => {
    runValidation().catch((error) => {
      store.validationStatus.val = `error (${error.message})`;
    });
  };

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

  async function sendMessage() {
    const text = String(store.draftMessage?.val || "").trim();
    if (!text) return;
    if (store.sending?.val) return;

    store.sending.val = true;
    store.taskStatus.val = "submitting";
    store.history.val = [...store.history.val, { role: "user", content: text }];
    store.draftMessage.val = "";

    const payload = await api.sendMessage(text, store.currentSession.val || "");
    const taskId = String(payload?.task?.task_id || "");
    store.activeTaskId.val = taskId;
    store.taskStatus.val = taskId ? "running" : "submitted";
    await refresh();
  }

  async function cancelActiveTask() {
    const taskId = String(store.activeTaskId?.val || "");
    if (!taskId) return;
    await api.cancelTask(taskId);
    store.taskStatus.val = "cancelled";
    store.sending.val = false;
    await refresh();
  }

  async function resolveApproval(approvalId, decision) {
    await api.resolveApproval(approvalId, decision);
    await refresh();
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
      try {
        const parsed = JSON.parse(String(data || "{}"));
        const task = parsed?.payload?.task;
        if (task?.task_id && String(task.task_id) === String(store.activeTaskId.val || "")) {
          store.taskStatus.val = String(task.status || "running");
          if (task.status === "completed" || task.status === "error" || task.status === "cancelled") {
            store.sending.val = false;
            if (task.status !== "running") store.activeTaskId.val = "";
          }
        }
      } catch {
        // ignore parsing errors for keep-alive/non-json messages
      }
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
      { sendMessage, cancelActiveTask, resolveApproval },
      () => refresh().catch((e) => {
        store.status.val = e.message;
      }),
  );

  document.body.innerHTML = "";
  van.add(document.body, app);

  await refresh();
  sse.open();
}
