const api = "";
let currentSession = null;
let currentJob = null;
let streamSocket = null;
let selectedFilter = "all";
let latestAssistantMessage = null;

const el = (id) => document.getElementById(id);

async function req(path, options = {}) {
  const res = await fetch(api + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

function setSessionState(state) {
  const normalized = state || "idle";
  const className =
    normalized === "running" || normalized === "awaiting_approval"
      ? "thinking"
      : normalized === "completed"
      ? "completed"
      : normalized === "blocked" || normalized === "failed"
      ? "blocked"
      : "idle";

  const label =
    normalized === "running"
      ? "thinking"
      : normalized === "awaiting_approval"
      ? "awaiting approval"
      : normalized;

  ["session-state", "active-status"].forEach((id) => {
    const node = el(id);
    node.className = `state-pill ${className}`;
    node.textContent = label;
  });
}

function removeEmptyState() {
  const empty = document.querySelector(".empty-state");
  if (empty) empty.remove();
}

function pushChat(tag, message) {
  removeEmptyState();
  const node = document.createElement("div");
  node.className = `message ${tag}`;
  node.innerHTML = `<div class="tag">${tag}</div><div>${message}</div>`;
  el("chat-window").appendChild(node);
  el("chat-window").scrollTop = el("chat-window").scrollHeight;
  return node;
}

function updateAssistantDraft(text) {
  if (!latestAssistantMessage) {
    latestAssistantMessage = pushChat("assistant", text);
    return;
  }
  latestAssistantMessage.querySelector("div:last-child").textContent = text;
}

function formatTimelineLine(eventType, payload) {
  return `${eventType}: ${JSON.stringify(payload)}`;
}

function isVisibleForFilter(eventType) {
  if (selectedFilter === "all") return true;
  if (selectedFilter === "approval") return eventType.startsWith("approval");
  return eventType === selectedFilter;
}

function addTimeline(eventType, payload) {
  const li = document.createElement("li");
  li.dataset.eventType = eventType;
  li.textContent = `${new Date().toLocaleTimeString()}  ${formatTimelineLine(eventType, payload)}`;
  if (!isVisibleForFilter(eventType)) {
    li.classList.add("hidden");
  }
  el("timeline").prepend(li);
}

function applyTimelineFilter() {
  document.querySelectorAll("#timeline li").forEach((node) => {
    node.classList.toggle("hidden", !isVisibleForFilter(node.dataset.eventType || ""));
  });
}

function updateSessionSummary(session) {
  if (!session) {
    el("session-summary").textContent = "No session selected.";
    el("active-model").textContent = "provider: n/a";
    setSessionState("idle");
    return;
  }

  const providers = (session.provider_preferences?.ordered || []).join(", ");
  el("session-summary").textContent = `mode=${session.mode} | policy=${session.policy_profile} | providers=${providers || "ollama"}`;
  el("mode").value = session.mode || "interactive";
  el("policy").value = session.policy_profile || "default";
  el("providers").value = providers || "ollama";
  el("active-model").textContent = `provider: ${providers || "ollama"}`;
  setSessionState(session.status || "idle");
}

function connectStream() {
  if (!currentSession) return;
  if (streamSocket) streamSocket.close();

  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  streamSocket = new WebSocket(`${scheme}://${window.location.host}/stream/sessions/${currentSession}`);

  streamSocket.onmessage = (raw) => {
    const evt = JSON.parse(raw.data);
    const { event_type: eventType, payload, job_id: jobId } = evt;
    addTimeline(eventType, payload || {});

    if (eventType === "job_state") {
      setSessionState(payload?.state || "idle");
      if (jobId) currentJob = jobId;

      if (["completed", "cancelled", "failed", "blocked"].includes(payload?.state)) {
        latestAssistantMessage = null;
      }
    }

    if (eventType === "loop_step") {
      const draft = `Step ${payload?.index + 1} (${payload?.label}) via ${payload?.provider}\n${payload?.output_preview || ""}`;
      updateAssistantDraft(draft);
    }

    if (eventType === "log" && payload?.message === "job completed") {
      pushChat("assistant", "Completed. Review run metadata for full execution details.");
      latestAssistantMessage = null;
    }

    if (eventType === "approval_requested") {
      pushChat("system", `Approval needed for tool: ${payload?.tool_name}`);
      refreshApprovals().catch(console.error);
    }
  };

  streamSocket.onerror = () => {
    addTimeline("status", { message: "stream disconnected" });
  };
}

async function refreshSessions() {
  const sessions = await req("/sessions");
  const sel = el("session-select");
  sel.innerHTML = "";

  sessions.forEach((s) => {
    const option = document.createElement("option");
    option.value = s.id;
    option.textContent = `${s.id.slice(0, 8)} · ${s.status} · ${s.mode}`;
    sel.appendChild(option);
  });

  if (sessions.length > 0) {
    if (!currentSession || !sessions.some((s) => s.id === currentSession)) {
      currentSession = sessions[0].id;
    }
    sel.value = currentSession;
    updateSessionSummary(sessions.find((s) => s.id === currentSession));
    connectStream();
  } else {
    currentSession = null;
    updateSessionSummary(null);
  }
}

async function refreshApprovals() {
  if (!currentSession) return;

  const approvals = await req(`/sessions/${currentSession}/approvals/pending`);
  const ul = el("approvals");
  ul.innerHTML = "";

  approvals.forEach((a) => {
    const li = document.createElement("li");
    li.textContent = `${a.tool_name}\n${a.reason}`;

    const approve = document.createElement("button");
    approve.className = "btn";
    approve.textContent = "approve";
    approve.onclick = async () => {
      await req(`/jobs/${a.job_id}/approvals/${a.id}`, {
        method: "POST",
        body: JSON.stringify({ decision: "approved" }),
      });
      addTimeline("approval", { id: a.id, status: "approved" });
      pushChat("system", `${a.tool_name} approved`);
      refreshApprovals();
    };

    li.appendChild(document.createElement("br"));
    li.appendChild(approve);
    ul.appendChild(li);
  });
}

el("session-select").onchange = async (event) => {
  currentSession = event.target.value;
  const session = await req(`/sessions/${currentSession}`);
  updateSessionSummary(session);
  connectStream();
  addTimeline("status", { message: `session switched to ${currentSession.slice(0, 8)}` });
};

el("create-session").onclick = async () => {
  const workspace_path = el("workspace").value;
  const created = await req("/sessions", {
    method: "POST",
    body: JSON.stringify({ workspace_path, mode: "interactive" }),
  });
  currentSession = created.id;
  currentJob = null;
  pushChat("system", `Session created: ${created.id}`);
  await refreshSessions();
};

el("refresh-sessions").onclick = refreshSessions;

el("create-job").onclick = async () => {
  if (!currentSession) return;
  const goal = el("goal").value;
  if (!goal.trim()) return;

  pushChat("user", goal);
  latestAssistantMessage = null;
  const job = await req(`/sessions/${currentSession}/jobs`, {
    method: "POST",
    body: JSON.stringify({ goal }),
  });
  currentJob = job.id;
  setSessionState("running");
  el("goal").value = "";
};

el("goal").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el("create-job").click();
  }
});

el("cancel-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/cancel`, { method: "POST" });
  pushChat("control", "Cancel requested");
};

el("resume-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/resume`, { method: "POST" });
  pushChat("control", "Resume requested");
};

el("save-config").onclick = async () => {
  if (!currentSession) return;
  const mode = el("mode").value;
  const policy_profile = el("policy").value;
  const ordered = el("providers")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const updated = await req(`/sessions/${currentSession}`, {
    method: "PATCH",
    body: JSON.stringify({
      mode,
      policy_profile,
      provider_preferences: { ordered },
    }),
  });
  updateSessionSummary(updated);
  pushChat("system", "Runtime settings updated");
};

el("refresh-approvals").onclick = refreshApprovals;
el("clear-chat").onclick = () => {
  el("chat-window").innerHTML = '<div class="empty-state">Chat history cleared for this view.</div>';
  latestAssistantMessage = null;
};
el("condense-chat").onclick = () => {
  pushChat("system", "Condense requested (UI placeholder). Hook this to a backend summarization endpoint.");
};

document.querySelectorAll("#meta-filters .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#meta-filters .chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    selectedFilter = chip.dataset.filter;
    applyTimelineFilter();
  });
});

refreshSessions().then(refreshApprovals).catch(console.error);
setInterval(() => {
  refreshApprovals().catch(() => null);
}, 2000);
