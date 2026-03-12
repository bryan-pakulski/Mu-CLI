const api = "";
let currentSession = null;
let currentJob = null;
let streamSocket = null;
let selectedFilter = "all";
let latestAssistantMessage = null;
let currentSettingsSessionId = null;
let openMenuForSession = null;
let sessionsCache = [];
const sessionMessages = new Map();

const panelState = {
  left: { collapsed: false, width: 320, min: 220, max: 560 },
  right: { collapsed: false, width: 420, min: 280, max: 720 },
};
const sessionIndicators = new Map();

const el = (id) => document.getElementById(id);

async function req(path, options = {}) {
  const res = await fetch(api + path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(await res.text());
  if (res.status === 204) return null;
  return res.json();
}

function fillSelect(selectId, values, selected) {
  const node = el(selectId);
  node.innerHTML = "";
  values.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    if (v === selected) opt.selected = true;
    node.appendChild(opt);
  });
}

async function loadModelsForProvider(providerName, selectId, selectedModel = null) {
  if (!providerName) {
    fillSelect(selectId, ["default"], "default");
    return;
  }
  try {
    const models = await req(`/providers/${providerName}/models`);
    const all = models.length > 0 ? models : ["default"];
    const selected = selectedModel && all.includes(selectedModel) ? selectedModel : all[0];
    fillSelect(selectId, all, selected);
  } catch {
    fillSelect(selectId, ["default"], "default");
  }
}

async function populateRuntimeOptions() {
  const providers = await req("/providers");
  const providerNames = providers.map((p) => p.name);
  fillSelect("providers", providerNames, providerNames[0]);
  fillSelect("modal-providers", providerNames, providerNames[0]);

  const policyProfiles = await req("/policy-profiles");
  fillSelect("policy", policyProfiles, policyProfiles[0]);
  fillSelect("modal-policy", policyProfiles, policyProfiles[0]);

  await loadModelsForProvider(providerNames[0], "model");
  await loadModelsForProvider(providerNames[0], "modal-model");

  el("providers").addEventListener("change", (event) => {
    loadModelsForProvider(event.target.value, "model").catch(() => null);
  });
  el("modal-providers").addEventListener("change", (event) => {
    loadModelsForProvider(event.target.value, "modal-model").catch(() => null);
  });
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

function addSessionMessage(sessionId, role, content) {
  const existing = sessionMessages.get(sessionId) || [];
  existing.push({ role, content });
  sessionMessages.set(sessionId, existing);
}

function renderChatForSession(sessionId) {
  const chatWindow = el("chat-window");
  chatWindow.innerHTML = "";

  const messages = sessionMessages.get(sessionId) || [];
  if (messages.length === 0) {
    chatWindow.innerHTML = '<div class="empty-state">Start by creating/selecting a session, then send a prompt.</div>';
    return;
  }

  messages.forEach((msg) => {
    const node = document.createElement("div");
    node.className = `message ${msg.role}`;
    node.innerHTML = `<div class="tag">${msg.role}</div><div>${msg.content}</div>`;
    chatWindow.appendChild(node);
  });
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function pushChat(tag, message) {
  if (!currentSession) return null;
  addSessionMessage(currentSession, tag, message);
  renderChatForSession(currentSession);
  const messages = el("chat-window").querySelectorAll(".message");
  return messages[messages.length - 1] || null;
}

function updateAssistantDraft(text) {
  if (!latestAssistantMessage) {
    latestAssistantMessage = pushChat("assistant", text);
    return;
  }
  latestAssistantMessage.querySelector("div:last-child").textContent = text;
  const messages = sessionMessages.get(currentSession) || [];
  if (messages.length > 0) messages[messages.length - 1].content = text;
}

function addTimeline(eventType, payload) {
  const li = document.createElement("li");
  li.dataset.eventType = eventType;
  li.textContent = `${new Date().toLocaleTimeString()}  ${eventType}: ${JSON.stringify(payload)}`;
  if (!isVisibleForFilter(eventType)) li.classList.add("hidden");
  el("timeline").prepend(li);
}

function isVisibleForFilter(eventType) {
  if (selectedFilter === "all") return true;
  if (selectedFilter === "approval") return eventType.startsWith("approval");
  return eventType === selectedFilter;
}

function applyTimelineFilter() {
  document.querySelectorAll("#timeline li").forEach((node) => {
    node.classList.toggle("hidden", !isVisibleForFilter(node.dataset.eventType || ""));
  });
}

function setIndicator(sessionId, state) {
  sessionIndicators.set(sessionId, state);
  const dot = document.querySelector(`.session-item[data-session-id='${sessionId}'] .session-dot`);
  if (dot) dot.className = `session-dot ${state}`;
}

function providerHintForError(rawError = "") {
  if (!rawError.includes("/api/generate") || !rawError.includes("404")) return null;
  return "Provider call failed (404 from Ollama generate endpoint). Check selected model and Ollama availability/config for http://localhost:11434/api/generate.";
}

function hydrateSessionMessages(session) {
  const items = (session?.context_state?.messages || [])
    .filter((m) => typeof m.content === "string")
    .map((m) => ({
      role: m.role === "assistant" ? "assistant" : m.role === "system" ? "system" : "user",
      content: m.content,
    }));
  sessionMessages.set(session.id, items);
}

function updateSessionSummary(session) {
  if (!session) {
    el("session-summary").textContent = "No session selected.";
    el("active-model").textContent = "provider: n/a";
    setSessionState("idle");
    el("chat-window").innerHTML = '<div class="empty-state">Start by creating/selecting a session, then send a prompt.</div>';
    return;
  }

  const provider = session.provider_preferences?.ordered?.[0] || "ollama";
  const model = session.provider_preferences?.model || "default";
  const name = session.name || "default";
  el("session-summary").textContent = `session=${name} | mode=${session.mode} | policy=${session.policy_profile} | provider=${provider} | model=${model}`;
  el("mode").value = session.mode || "interactive";
  el("policy").value = session.policy_profile || "default";
  el("providers").value = provider;
  loadModelsForProvider(provider, "model", model).catch(() => null);
  el("active-model").textContent = `${name} · ${provider} · ${model}`;
  setSessionState(session.status || "idle");
  renderChatForSession(session.id);
}

function closeAnySessionMenu() {
  document.querySelectorAll(".session-menu").forEach((menu) => menu.remove());
  openMenuForSession = null;
}

async function openSessionSettings(sessionId) {
  closeAnySessionMenu();
  const session = sessionsCache.find((s) => s.id === sessionId);
  if (!session) return;

  currentSettingsSessionId = sessionId;
  const provider = session.provider_preferences?.ordered?.[0] || "ollama";
  const model = session.provider_preferences?.model || null;
  el("modal-session-name").value = session.name || "default";
  el("modal-providers").value = provider;
  await loadModelsForProvider(provider, "modal-model", model);
  el("modal-mode").value = session.mode || "interactive";
  el("modal-policy").value = session.policy_profile || "default";
  el("session-settings-modal").classList.remove("hidden");
}

function showSessionMenu(anchorButton, session) {
  closeAnySessionMenu();
  const rect = anchorButton.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "session-menu";
  menu.style.position = "fixed";
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.left = `${Math.max(8, rect.right - 140)}px`;

  const settingsBtn = document.createElement("button");
  settingsBtn.textContent = "Settings";
  settingsBtn.onclick = () => openSessionSettings(session.id);

  const clearBtn = document.createElement("button");
  clearBtn.textContent = "Clear Session";
  clearBtn.onclick = async () => {
    closeAnySessionMenu();
    if (!window.confirm("Clear context and messages for this session?")) return;
    await req(`/sessions/${session.id}/clear`, { method: "POST" });
    sessionMessages.set(session.id, []);
    setIndicator(session.id, "dot-idle");
    if (session.id === currentSession) renderChatForSession(session.id);
  };

  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = "Delete";
  deleteBtn.onclick = async () => {
    closeAnySessionMenu();
    if (!window.confirm("Delete this session permanently?")) return;
    await req(`/sessions/${session.id}`, { method: "DELETE" });
    sessionMessages.delete(session.id);
    if (session.id === currentSession) {
      currentSession = null;
      if (streamSocket) streamSocket.close();
      updateSessionSummary(null);
    }
    await refreshSessions();
  };

  menu.append(settingsBtn, clearBtn, deleteBtn);
  document.body.appendChild(menu);
  openMenuForSession = session.id;
}

async function buildSessionIndicator(sessionId) {
  try {
    const jobs = await req(`/sessions/${sessionId}/jobs`);
    const latest = jobs[0];
    if (!latest) return "dot-idle";
    if (["queued", "running", "awaiting_approval"].includes(latest.state)) return "dot-running";
    if (latest.state === "completed") return "dot-success";
    if (["failed", "blocked", "cancelled"].includes(latest.state)) return "dot-error";
  } catch {
    return "dot-idle";
  }
  return "dot-idle";
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
    main.innerHTML = `<div class="session-id">${s.name || "default"}</div><div class="session-meta">${s.mode} · ${s.status}</div>`;
    main.onclick = async () => {
      currentSession = s.id;
      latestAssistantMessage = null;
      const details = await req(`/sessions/${currentSession}`);
      hydrateSessionMessages(details);
      updateSessionSummary(details);
      renderSessionList();
      connectStream();
    };

    const menuBtn = document.createElement("button");
    menuBtn.className = "session-menu-btn";
    menuBtn.textContent = "⋯";
    menuBtn.onclick = (event) => {
      event.stopPropagation();
      if (openMenuForSession === s.id) return closeAnySessionMenu();
      showSessionMenu(menuBtn, s);
    };

    li.append(dot, main, menuBtn);
    ul.appendChild(li);
  });
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
      if (["queued", "running", "awaiting_approval"].includes(payload?.state)) setIndicator(sessionId, "dot-running");
      if (payload?.state === "completed") setIndicator(sessionId, "dot-success");
      if (["failed", "blocked", "cancelled"].includes(payload?.state)) setIndicator(sessionId, "dot-error");
    }

    if (eventType === "loop_step") {
      const draft = `Step ${payload?.index + 1} (${payload?.label}) via ${payload?.provider}\n${payload?.output_preview || ""}`;
      updateAssistantDraft(draft);
    }

    if (eventType === "log" && payload?.message === "job completed") {
      pushChat("assistant", "Job completed successfully");
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

  streamSocket.onerror = () => addTimeline("status", { message: "stream disconnected" });
}

async function refreshSessions() {
  sessionsCache = await req("/sessions");
  sessionsCache.forEach((s) => {
    if (!sessionMessages.has(s.id)) hydrateSessionMessages(s);
  });

  if (sessionsCache.length > 0 && (!currentSession || !sessionsCache.some((s) => s.id === currentSession))) {
    const defaultSession = sessionsCache.find((s) => (s.name || "").toLowerCase() === "default");
    currentSession = (defaultSession || sessionsCache[0]).id;
  }

  const statuses = await Promise.all(sessionsCache.map((s) => buildSessionIndicator(s.id)));
  sessionsCache.forEach((s, idx) => sessionIndicators.set(s.id, statuses[idx]));
  renderSessionList();

  if (sessionsCache.length > 0) {
    const active = sessionsCache.find((s) => s.id === currentSession) || sessionsCache[0];
    currentSession = active.id;
    hydrateSessionMessages(active);
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
      await req(`/jobs/${a.job_id}/approvals/${a.id}`, { method: "POST", body: JSON.stringify({ decision: "approved" }) });
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
  const sessionName = window.prompt("Session name", "new session") || "new session";
  const created = await req("/sessions", {
    method: "POST",
    body: JSON.stringify({
      workspace_path,
      name: sessionName,
      mode: el("mode").value,
      policy_profile: el("policy").value,
      provider_preferences: { ordered: [el("providers").value], model: el("model").value },
    }),
  });
  currentSession = created.id;
  currentJob = null;
  await refreshSessions();
};

el("refresh-sessions").onclick = refreshSessions;
el("session-quick-switch").addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const prefix = event.target.value.trim().toLowerCase();
  const found = sessionsCache.find((s) => s.id.startsWith(prefix) || (s.name || "").toLowerCase().startsWith(prefix));
  if (!found) return;
  currentSession = found.id;
  const details = await req(`/sessions/${currentSession}`);
  hydrateSessionMessages(details);
  updateSessionSummary(details);
  connectStream();
  renderSessionList();
  event.target.value = "";
});

el("browse-workspace").onclick = async () => {
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker();
      el("workspace").value = handle.name;
      return;
    } catch {
      // continue to fallback
    }
  }
  el("workspace-picker").click();
};

el("workspace-picker").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const root = file.webkitRelativePath?.split("/")?.[0];
  if (root) {
    el("workspace").value = root;
  } else {
    const manual = window.prompt("Enter workspace path", el("workspace").value);
    if (manual) el("workspace").value = manual;
  }
});

el("create-job").onclick = async () => {
  if (!currentSession) return;
  const goal = el("goal").value;
  if (!goal.trim()) return;

  pushChat("user", goal);
  latestAssistantMessage = null;
  const job = await req(`/sessions/${currentSession}/jobs`, { method: "POST", body: JSON.stringify({ goal }) });
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

el("save-config").onclick = async () => {
  if (!currentSession) return;
  const activeSession = sessionsCache.find((s) => s.id === currentSession);
  const updated = await req(`/sessions/${currentSession}`, {
    method: "PATCH",
    body: JSON.stringify({
      name: activeSession?.name || "default",
      mode: el("mode").value,
      policy_profile: el("policy").value,
      provider_preferences: { ordered: [el("providers").value], model: el("model").value },
    }),
  });
  updateSessionSummary(updated);
  await refreshSessions();
};

el("refresh-approvals").onclick = refreshApprovals;
el("toggle-left").onclick = () => togglePanel("left");
el("toggle-right").onclick = () => togglePanel("right");
setupResizer("left-resizer", "left");
setupResizer("right-resizer", "right");
applyPanelLayout();

el("modal-cancel").onclick = closeSettingsModal;
el("modal-save").onclick = async () => {
  if (!currentSettingsSessionId) return;
  await req(`/sessions/${currentSettingsSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({
      name: el("modal-session-name").value,
      mode: el("modal-mode").value,
      policy_profile: el("modal-policy").value,
      provider_preferences: { ordered: [el("modal-providers").value], model: el("modal-model").value },
    }),
  });
  closeSettingsModal();
  await refreshSessions();
};

el("session-settings-modal").addEventListener("click", (event) => {
  if (event.target.id === "session-settings-modal") closeSettingsModal();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".session-menu") && !event.target.closest(".session-menu-btn")) closeAnySessionMenu();
});

document.querySelectorAll("#meta-filters .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#meta-filters .chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    selectedFilter = chip.dataset.filter;
    applyTimelineFilter();
  });
});

populateRuntimeOptions().then(refreshSessions).then(refreshApprovals).catch(console.error);
setInterval(() => {
  refreshApprovals().catch(() => null);
}, 2000);
