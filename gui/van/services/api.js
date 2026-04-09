export function createApiClient(apiBaseState) {
  const baseUrl = () => String(apiBaseState.val || "").replace(/\/$/, "");

  const fetchJson = async (path, options = {}) => {
    const response = await fetch(`${baseUrl()}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data?.ok === false) {
      throw new Error(data?.error || `HTTP ${response.status}`);
    }
    return data;
  };

  return {
    state: () => fetchJson("/api/state"),
    runtime: () => fetchJson("/api/runtime"),
    sessions: () => fetchJson("/api/sessions"),
    tasks: () => fetchJson("/api/tasks"),
    approvals: () => fetchJson("/api/approvals"),
    features: () => fetchJson("/api/features"),
    workspaces: () => fetchJson("/api/workspaces"),
    stagedFiles: () => fetchJson("/api/staged-files"),
    featurePlan: (directory) => fetchJson(`/api/feature-plan?directory=${encodeURIComponent(directory)}`),
    sendMessage: (text, sessionName) =>
      fetchJson("/api/message", {
        method: "POST",
        body: JSON.stringify({ text, session_name: sessionName, async: true }),
      }),
    cancelTask: (taskId) =>
      fetchJson("/api/tasks/cancel", {
        method: "POST",
        body: JSON.stringify({ task_id: taskId }),
      }),
    resolveApproval: (approvalId, decision, reason = null) =>
      fetchJson("/api/approvals/resolve", {
        method: "POST",
        body: JSON.stringify({ approval_id: approvalId, decision, reason }),
      }),
    history: (sessionName) => {
      const query = sessionName ? `?limit=150&session_name=${encodeURIComponent(sessionName)}` : "?limit=150";
      return fetchJson(`/api/history${query}`);
    },
  };
}
