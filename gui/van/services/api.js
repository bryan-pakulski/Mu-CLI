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
    sessions: () => fetchJson("/api/sessions"),
    history: (sessionName) => {
      const query = sessionName ? `?limit=150&session_name=${encodeURIComponent(sessionName)}` : "?limit=150";
      return fetchJson(`/api/history${query}`);
    },
  };
}
