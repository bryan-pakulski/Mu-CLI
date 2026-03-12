const api = "";
let currentSession = null;
let currentJob = null;
let streamSocket = null;
let selectedFilter = "all";
let latestAssistantMessage = null;
let currentSettingsSessionId = null;
let openMenuForSession = null;
let sessionsCache = [];

const panelState = {
  left: { collapsed: false, width: 320, min: 220, max: 560 },
  right: { collapsed: false, width: 420, min: 280, max: 720 },
};

const sessionIndicators = new Map();

const el = (id) => document.getElementById(id);

async function req(path, options = {}) {
  const res = await fetch(api + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function applyPanelLayout() {
  const shell = el("app-shell");
  shell.style.setProperty("--left-size", panelState.left.collapsed ? "0px" : `${panelState.left.width}px`);
  shell.style.setProperty("--right-size", panelState.right.collapsed ? "0px" : `${panelState.right.width}px`);
  shell.classList.toggle("left-collapsed", panelState.left.collapsed);
  shell.classList.toggle("right-collapsed", panelState.right.collapsed);
}

function togglePanel(side) {
  panelState[side].collapsed = !panelState[side].collapsed;
  applyPanelLayout();
}

function setupResizer(resizerId, side) {
  const resizer = el(resizerId);
  const shell = el("app-shell");

  resizer.addEventListener("pointerdown", (event) => {
    if (panelState[side].collapsed) {
      panelState[side].collapsed = false;
      applyPanelLayout();
    }
    const shellRect = shell.getBoundingClientRect();
    resizer.classList.add("dragging");
    resizer.setPointerCapture(event.pointerId);

    const onMove = (moveEvent) => {
      if (side === "left") {
        panelState.left.width = clamp(moveEvent.clientX - shellRect.left, panelState.left.min, panelState.left.max);
      } else {
        panelState.right.width = clamp(shellRect.right - moveEvent.clientX, panelState.right.min, panelState.right.max);
      }
      applyPanelLayout();
    };

    const onStop = () => {
      resizer.classList.remove("dragging");
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onStop);
      resizer.removeEventListener("pointercancel", onStop);
    };

    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onStop);
    resizer.addEventListener("pointercancel", onStop);
  });
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

  const label = normalized === "running" ? "thinking" : normalized;

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
  if (!isVisibleForFilter(eventType)) li.classList.add("hidden");
  el("timeline").prepend(li);
}

function applyTimelineFilter() {
  document.querySelectorAll("#timeline li").forEach((node) => {
    node.classList.toggle("hidden", !isVisibleForFilter(node.dataset.eventType || ""));
  });
}

function providerHintForError(rawError = "") {
  if (!rawError.includes("/api/generate") || !rawError.includes("404")) return null;
  return [
    "Provider call failed (404 from Ollama).",
    "Check your runtime provider settings and endpoint.",
    "Expected Ollama generate endpoint at: http://localhost:11434/api/generate",
    "If you run Ollama on another host/port, update server provider config accordingly.",
  ].join("\n");
}

function setIndicator(sessionId, state) {
  if (!sessionId) return;
  sessionIndicators.set(sessionId, state);
  const dot = document.querySelector(`.session-item[data-session-id='${sessionId}'] .session-dot`);
  if (!dot) return;
  dot.className = `session-dot ${state}`;
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

function closeAnySessionMenu() {
  document.querySelectorAll(".session-menu").forEach((menu) => menu.remove());
  openMenuForSession = null;
}

function createSessionMenu(session) {
  const menu = document.createElement("div");
  menu.className = "session-menu";

  const settingsBtn = document.createElement("button");
  settingsBtn.textContent = "Settings";
  settingsBtn.onclick = () => openSessionSettings(session.id);

  const clearBtn = document.createElement("button");
  clearBtn.textContent = "Clear Session";
  clearBtn.onclick = async () => {
    closeAnySessionMenu();
    if (!window.confirm("Clear context and messages for this session?")) return;
    await req(`/sessions/${session.id}/clear`, { method: "POST" });
    setIndicator(session.id, "dot-idle");
    if (session.id === currentSession) {
      pushChat("system", "Session context cleared.");
    }
  };

  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = "Delete";
  deleteBtn.onclick = async () => {
    closeAnySessionMenu();
    if (!window.confirm("Delete this session permanently?")) return;
    await req(`/sessions/${session.id}`, { method: "DELETE" });
    if (session.id === currentSession) {
      currentSession = null;
      currentJob = null;
      updateSessionSummary(null);
      if (streamSocket) streamSocket.close();
    }
    await refreshSessions();
  };

  menu.append(settingsBtn, clearBtn, deleteBtn);
  return menu;
}

async function buildSessionIndicator(sessionId) {
  try {
    const jobs = await req(`/sessions/${sessionId}/jobs`);
    const latest = jobs[0];
    if (!latest) return "dot-idle";
    if (["queued", "running", "awaiting_approval"].includes(latest.state)) return "dot-running";
    if (latest.state === "completed") return "dot-success";
    if (["failed", "blocked", "cancelled"].includes(latest.state)) return "dot-error";
    return "dot-idle";
  } catch {
    return "dot-idle";
  }
}

function renderSessionList() {
  const ul = el("session-list");
  ul.innerHTML = "";

  sessionsCache.forEach((s) => {
    const li = document.createElement("li");
    li.className = `session-item ${s.id === currentSession ? "active" : ""}`;
    li.dataset.sessionId = s.id;

    const dot = document.createElement("span");
    dot.className = `session-dot ${sessionIndicators.get(s.id) || "dot-idle"}`;

    const main = document.createElement("div");
    main.className = "session-main";
    main.innerHTML = `
      <div class="session-id">${s.id.slice(0, 12)}</div>
      <div class="session-meta">${s.mode} · ${s.status}</div>
    `;
    main.onclick = async () => {
      currentSession = s.id;
      const details = await req(`/sessions/${currentSession}`);
      updateSessionSummary(details);
      connectStream();
      renderSessionList();
    };

    const menuBtn = document.createElement("button");
    menuBtn.className = "session-menu-btn";
    menuBtn.textContent = "⋯";
    menuBtn.onclick = (event) => {
      event.stopPropagation();
      if (openMenuForSession === s.id) {
        closeAnySessionMenu();
        return;
      }
      closeAnySessionMenu();
      li.appendChild(createSessionMenu(s));
      openMenuForSession = s.id;
    };

    li.append(dot, main, menuBtn);
    ul.appendChild(li);
  });
}

function openSessionSettings(sessionId) {
  closeAnySessionMenu();
  const session = sessionsCache.find((s) => s.id === sessionId);
  if (!session) return;

  currentSettingsSessionId = sessionId;
  el("modal-mode").value = session.mode || "interactive";
  el("modal-policy").value = session.policy_profile || "default";
  el("modal-providers").value = (session.provider_preferences?.ordered || []).join(", ");
  el("session-settings-modal").classList.remove("hidden");
}

function closeSettingsModal() {
  currentSettingsSessionId = null;
  el("session-settings-modal").classList.add("hidden");
}

function connectStream() {
  if (!currentSession) return;
  if (streamSocket) streamSocket.close();

  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  streamSocket = new WebSocket(`${scheme}://${window.location.host}/stream/sessions/${currentSession}`);

  streamSocket.onmessage = (raw) => {
    const evt = JSON.parse(raw.data);
    const { event_type: eventType, payload, job_id: jobId, session_id: sessionId } = evt;
    addTimeline(eventType, payload || {});

    if (eventType === "job_state") {
      setSessionState(payload?.state || "idle");
      if (jobId) currentJob = jobId;

      if (["queued", "running", "awaiting_approval"].includes(payload?.state)) {
        setIndicator(sessionId, "dot-running");
      } else if (payload?.state === "completed") {
        setIndicator(sessionId, "dot-success");
        latestAssistantMessage = null;
      } else if (["failed", "blocked", "cancelled"].includes(payload?.state)) {
        setIndicator(sessionId, "dot-error");
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

    if (eventType === "log" && payload?.message === "job failed") {
      const hint = providerHintForError(payload?.error || "");
      if (hint) pushChat("system", hint);
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
  sessionsCache = await req("/sessions");
  if (sessionsCache.length > 0 && (!currentSession || !sessionsCache.some((s) => s.id === currentSession))) {
    currentSession = sessionsCache[0].id;
  }

  const statuses = await Promise.all(sessionsCache.map((s) => buildSessionIndicator(s.id)));
  sessionsCache.forEach((s, idx) => sessionIndicators.set(s.id, statuses[idx]));

  renderSessionList();

  if (sessionsCache.length > 0) {
    const active = sessionsCache.find((s) => s.id === currentSession) || sessionsCache[0];
    currentSession = active.id;
    updateSessionSummary(active);
    connectStream();
  } else {
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

el("session-quick-switch").addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const prefix = event.target.value.trim();
  if (!prefix) return;
  const found = sessionsCache.find((s) => s.id.startsWith(prefix));
  if (!found) return;
  currentSession = found.id;
  const details = await req(`/sessions/${currentSession}`);
  updateSessionSummary(details);
  connectStream();
  renderSessionList();
  event.target.value = "";
});

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
  setIndicator(currentSession, "dot-running");
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
  const ordered = el("providers").value.split(",").map((s) => s.trim()).filter(Boolean);

  const updated = await req(`/sessions/${currentSession}`, {
    method: "PATCH",
    body: JSON.stringify({ mode, policy_profile, provider_preferences: { ordered } }),
  });
  updateSessionSummary(updated);
  await refreshSessions();
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

el("toggle-left").onclick = () => togglePanel("left");
el("toggle-right").onclick = () => togglePanel("right");
setupResizer("left-resizer", "left");
setupResizer("right-resizer", "right");
applyPanelLayout();

el("modal-cancel").onclick = closeSettingsModal;
el("modal-save").onclick = async () => {
  if (!currentSettingsSessionId) return;
  const mode = el("modal-mode").value;
  const policy_profile = el("modal-policy").value;
  const ordered = el("modal-providers").value.split(",").map((s) => s.trim()).filter(Boolean);

  await req(`/sessions/${currentSettingsSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ mode, policy_profile, provider_preferences: { ordered } }),
  });
  closeSettingsModal();
  await refreshSessions();
};

el("session-settings-modal").addEventListener("click", (event) => {
  if (event.target.id === "session-settings-modal") closeSettingsModal();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".session-menu") && !event.target.closest(".session-menu-btn")) {
    closeAnySessionMenu();
  }
});

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
