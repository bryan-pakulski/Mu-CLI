const api = "";
let currentSession = null;
let currentJob = null;
let streamSocket = null;
let selectedFilter = "all";
let latestAssistantMessage = null;
let currentSettingsSessionId = null;
let openMenuForSession = null;
let sessionsCache = [];
let applyingConfig = false;
const sessionMessages = new Map();
let toolsConfigCache = [];
let skillsConfigCache = [];
let skillEditorSessionId = null;
let skillEditorName = null;
const thinkingStatus = new Map();
const assistantDraftBuffers = new Map();

const panelState = {
  left: { collapsed: false, width: 320, min: 220, max: 560 },
  right: { collapsed: false, width: 420, min: 280, max: 720 },
};
const sessionIndicators = new Map();

const el = (id) => document.getElementById(id);
const setOnClick = (id, handler) => {
  const node = el(id);
  if (node) node.onclick = handler;
};

function formatLocalTimestamp(value = null) {
  const dt = value ? new Date(value) : new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)}.${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
}



function applyTheme(theme) {
  document.body.dataset.theme = theme;
  const toggle = el("theme-toggle");
  if (toggle) toggle.textContent = theme === "dark" ? "☀" : "☾";
}

function initTheme() {
  const stored = localStorage.getItem("mucli-theme");
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = stored || (prefersDark ? "dark" : "light");
  applyTheme(theme);
}

function toggleTheme() {
  const next = document.body.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("mucli-theme", next);
  applyTheme(next);
}

function getSessionLimits(session) {
  const maxTimeout = Number(session?.context_state?.max_timeout_s || 300);
  const maxContext = Number(session?.context_state?.max_context_messages || 40);
  return {
    maxTimeout: Number.isFinite(maxTimeout) ? maxTimeout : 300,
    maxContext: Number.isFinite(maxContext) ? maxContext : 40,
  };
}

function buildTimelineNode(eventType, payload, createdAt = null) {
  const li = document.createElement("li");
  li.dataset.eventType = eventType;
  if (!isVisibleForFilter(eventType)) li.classList.add("hidden");

  const details = document.createElement("details");
  details.className = "meta-details";

  const summary = document.createElement("summary");
  const ts = document.createElement("span");
  ts.className = "meta-time";
  ts.textContent = formatLocalTimestamp(createdAt);

  const tag = document.createElement("span");
  tag.className = `meta-tag ${eventType.replace(/[^a-z0-9_-]/gi, "-").toLowerCase()}`;
  tag.textContent = eventType;

  const headline = document.createElement("span");
  headline.className = "meta-headline";
  headline.textContent = typeof payload?.message === "string" ? payload.message : JSON.stringify(payload || {});

  summary.append(ts, tag, headline);
  details.appendChild(summary);

  const pre = document.createElement("pre");
  pre.className = "meta-payload";
  pre.textContent = JSON.stringify(payload || {}, null, 2);
  details.appendChild(pre);

  li.appendChild(details);
  return li;
}

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
    const option = document.createElement("option");
    option.value = v;
    option.textContent = v;
    if (v === selected) option.selected = true;
    node.appendChild(option);
  });
}

async function loadModelsForProvider(providerName, selectId, selectedModel = null) {
  if (!providerName) {
    fillSelect(selectId, ["default"], "default");
    return "default";
  }

  try {
    const models = await req(`/providers/${providerName}/models`);
    const available = models.length > 0 ? models : ["default"];
    const selected = selectedModel && available.includes(selectedModel) ? selectedModel : available[0];
    fillSelect(selectId, available, selected);
    return selected;
  } catch {
    fillSelect(selectId, ["default"], "default");
    return "default";
  }
}

async function persistCurrentConfig() {
  if (!currentSession || applyingConfig) return;
  const activeSession = sessionsCache.find((s) => s.id === currentSession);
  if (!activeSession) return;

  applyingConfig = true;
  try {
    const updated = await req(`/sessions/${currentSession}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: activeSession.name || "default",
        mode: el("mode").value,
        policy_profile: el("policy").value,
        max_timeout_s: Number(el("max-timeout").value || 300),
        max_context_messages: Number(el("max-context").value || 40),
        provider_preferences: {
          ordered: [el("providers").value],
          model: el("model").value || "default",
        },
      }),
    });

    const idx = sessionsCache.findIndex((s) => s.id === updated.id);
    if (idx >= 0) sessionsCache[idx] = updated;
    updateSessionSummary(updated, { autoPersistIfMissingModel: false });
  } finally {
    applyingConfig = false;
  }
}

async function populateRuntimeOptions() {
  let providerNames = ["ollama"];
  try {
    const providers = await req("/providers");
    providerNames = providers.map((p) => p.name).filter(Boolean);
  } catch {
    // fallback to defaults
  }
  if (providerNames.length === 0) providerNames = ["ollama"];

  fillSelect("providers", providerNames, providerNames[0]);
  fillSelect("modal-providers", providerNames, providerNames[0]);

  let policyProfiles = ["default"];
  try {
    const profiles = await req("/policy-profiles");
    policyProfiles = profiles.filter(Boolean);
  } catch {
    // fallback to defaults
  }
  if (policyProfiles.length === 0) policyProfiles = ["default"];

  fillSelect("policy", policyProfiles, policyProfiles[0]);
  fillSelect("modal-approval-mode", policyProfiles, policyProfiles[0]);

  await loadModelsForProvider(providerNames[0], "model");
  await loadModelsForProvider(providerNames[0], "modal-model");

  el("providers").addEventListener("change", async (event) => {
    await loadModelsForProvider(event.target.value, "model");
    await persistCurrentConfig();
  });

  el("model").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
  });

  el("mode").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
  });

  el("policy").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
  });

  el("max-timeout").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
  });

  el("max-context").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
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

function addSessionMessage(sessionId, role, content, extras = {}) {
  const existing = sessionMessages.get(sessionId) || [];
  existing.push({ role, content, created_at: formatLocalTimestamp(), ...extras });
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
    node.innerHTML = `<div class="tag-row"><div class="tag">${msg.role}</div><div class="timestamp">${msg.created_at || formatLocalTimestamp()}</div></div><div>${msg.content}</div>`;
    chatWindow.appendChild(node);
  });

  if (thinkingStatus.get(sessionId)) {
    const indicator = document.createElement("div");
    indicator.className = "thinking-indicator";
    indicator.innerHTML = '<span class="thinking-dots active"><span>.</span><span>.</span><span>.</span></span>';
    chatWindow.appendChild(indicator);
  }

  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function pushChat(tag, message) {
  if (!currentSession) return null;
  addSessionMessage(currentSession, tag, message);
  renderChatForSession(currentSession);
  const messages = el("chat-window").querySelectorAll(".message");
  return messages[messages.length - 1] || null;
}

function updateAssistantDraft(text, sessionId = currentSession) {
  if (!sessionId) return;
  if (sessionId !== currentSession) return;
  if (!latestAssistantMessage) {
    latestAssistantMessage = pushChat("assistant", text);
    return;
  }
  latestAssistantMessage.querySelector("div:last-child").textContent = text;
  const messages = sessionMessages.get(sessionId) || [];
  if (messages.length > 0) messages[messages.length - 1].content = text;
}

function setThinkingState(sessionId, active) {
  if (!sessionId) return;
  thinkingStatus.set(sessionId, Boolean(active));
  if (sessionId === currentSession) renderChatForSession(sessionId);
}

function isVisibleForFilter(eventType) {
  if (selectedFilter === "all") return true;
  if (selectedFilter === "approval") return eventType.startsWith("approval");
  return eventType === selectedFilter;
}

function addTimeline(eventType, payload) {
  el("timeline").prepend(buildTimelineNode(eventType, payload));
}

function applyTimelineFilter() {
  document.querySelectorAll("#timeline li").forEach((node) => {
    node.classList.toggle("hidden", !isVisibleForFilter(node.dataset.eventType || ""));
  });
}

async function loadSessionTimeline(sessionId) {
  if (!sessionId) return;
  try {
    const events = await req(`/sessions/${sessionId}/events?limit=250`);
    const timeline = el("timeline");
    timeline.innerHTML = "";
    events.forEach((evt) => {
      timeline.appendChild(buildTimelineNode(evt.event_type, evt.payload || {}, evt.created_at));
    });
  } catch {
    // no-op
  }
}

async function refreshCapabilitiesPanel() {
  const toolsList = el("tools-list");
  const skillsList = el("skills-list");
  if (!toolsList || !skillsList) return;

  if (!currentSession) {
    toolsList.innerHTML = '<li class="capability-empty">Select a session to view tools.</li>';
    skillsList.innerHTML = '<li class="capability-empty">Select a session to view skills.</li>';
    return;
  }

  const [tools, skills] = await Promise.all([
    req(`/sessions/${currentSession}/tools-config`),
    req(`/sessions/${currentSession}/skills-config`),
  ]);
  toolsConfigCache = tools;
  skillsConfigCache = skills;
  renderToolsConfig();
  renderSkillsConfig();
}

function renderToolsConfig() {
  const ul = el("tools-list");
  if (!ul) return;
  ul.innerHTML = "";
  if (toolsConfigCache.length === 0) {
    ul.innerHTML = '<li class="capability-empty">No tools discovered.</li>';
    return;
  }

  toolsConfigCache.forEach((tool) => {
    const li = document.createElement("li");
    li.className = "capability-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(tool.enabled);
    checkbox.onchange = async () => {
      const enabled = toolsConfigCache.filter((t) => (t.name === tool.name ? checkbox.checked : t.enabled)).map((t) => t.name);
      toolsConfigCache = await req(`/sessions/${currentSession}/tools-config`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      renderToolsConfig();
    };

    const main = document.createElement("div");
    main.className = "capability-main";
    main.innerHTML = `<div class="capability-name">${tool.name}</div><div class="capability-meta">${tool.risk_level} risk · ${tool.requires_approval ? "requires approval" : "auto"}</div>`;

    const actions = document.createElement("div");
    actions.className = "capability-actions";
    const view = document.createElement("button");
    view.className = "btn";
    view.textContent = "View";
    view.onclick = () => {
      window.alert(`${tool.name}

${tool.description}`);
    };
    actions.appendChild(view);

    li.append(checkbox, main, actions);
    ul.appendChild(li);
  });
}

function openSkillEditor(skill, canEdit) {
  if (!currentSession) return;
  req(`/sessions/${currentSession}/skills/${encodeURIComponent(skill.name)}/content`)
    .then((payload) => {
      skillEditorSessionId = currentSession;
      skillEditorName = skill.name;
      el("skill-editor-title").textContent = canEdit ? `Edit skill: ${skill.name}` : `View skill: ${skill.name}`;
      el("skill-editor-path").textContent = payload.file_path;
      el("skill-editor-content").value = payload.content;
      el("skill-editor-content").readOnly = !canEdit;
      el("skill-editor-save").classList.toggle("hidden", !canEdit);
      el("skill-editor-modal").classList.remove("hidden");
    })
    .catch((err) => window.alert(`Unable to open skill: ${err.message}`));
}

function renderSkillsConfig() {
  const ul = el("skills-list");
  if (!ul) return;
  ul.innerHTML = "";
  if (skillsConfigCache.length === 0) {
    ul.innerHTML = '<li class="capability-empty">No skills found in workspace.</li>';
    return;
  }

  skillsConfigCache.forEach((skill) => {
    const li = document.createElement("li");
    li.className = "capability-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(skill.enabled);
    checkbox.onchange = async () => {
      const enabled = skillsConfigCache.filter((s) => (s.name === skill.name ? checkbox.checked : s.enabled)).map((s) => s.name);
      skillsConfigCache = await req(`/sessions/${currentSession}/skills-config`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      renderSkillsConfig();
    };

    const main = document.createElement("div");
    main.className = "capability-main";
    main.innerHTML = `<div class="capability-name">${skill.name}</div><div class="capability-meta">${skill.file_path}</div>`;

    const actions = document.createElement("div");
    actions.className = "capability-actions";

    const view = document.createElement("button");
    view.className = "btn";
    view.textContent = "View";
    view.onclick = () => openSkillEditor(skill, false);

    const edit = document.createElement("button");
    edit.className = "btn";
    edit.textContent = "Edit";
    edit.onclick = () => openSkillEditor(skill, true);

    actions.append(view, edit);
    li.append(checkbox, main, actions);
    ul.appendChild(li);
  });
}


function renderOverrideList(containerId, items, enabledNames) {
  const node = el(containerId);
  if (!node) return;
  node.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("label");
    row.className = "override-item";
    const checked = enabledNames.has(item.name);
    row.innerHTML = `<input type="checkbox" data-name="${item.name}" ${checked ? "checked" : ""} /> <span>${item.name}</span>`;
    node.appendChild(row);
  });
}

function getCheckedNames(containerId) {
  const node = el(containerId);
  if (!node) return [];
  return [...node.querySelectorAll('input[type="checkbox"]:checked')].map((n) => n.dataset.name);
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
      created_at: m.created_at ? formatLocalTimestamp(m.created_at) : formatLocalTimestamp(),
    }));
  sessionMessages.set(session.id, items);
}

async function updateSessionSummary(session, options = { autoPersistIfMissingModel: true }) {
  if (!session) {
    el("session-summary").textContent = "No session selected.";
    el("active-model").textContent = "provider: n/a";
    setSessionState("idle");
    el("chat-window").innerHTML = '<div class="empty-state">Start by creating/selecting a session, then send a prompt.</div>';
    return;
  }

  const provider = session.provider_preferences?.ordered?.[0] || "ollama";
  let model = session.provider_preferences?.model || null;
  const name = session.name || "default";

  const limits = getSessionLimits(session);
  el("session-summary").textContent = `session=${name} | mode=${session.mode} | policy=${session.policy_profile} | provider=${provider} | model=${model || "default"} | timeout=${limits.maxTimeout}s | context=${limits.maxContext}`;
  el("mode").value = session.mode || "interactive";
  el("policy").value = session.policy_profile || "default";
  el("providers").value = provider;
  el("max-timeout").value = limits.maxTimeout;
  el("max-context").value = limits.maxContext;
  model = await loadModelsForProvider(provider, "model", model);

  if (options.autoPersistIfMissingModel && !session.provider_preferences?.model) {
    const idx = sessionsCache.findIndex((s) => s.id === session.id);
    if (idx >= 0) {
      sessionsCache[idx] = {
        ...sessionsCache[idx],
        provider_preferences: {
          ...(sessionsCache[idx].provider_preferences || {}),
          ordered: [provider],
          model,
        },
      };
    }
    await persistCurrentConfig();
  }

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
  const context = session.context_state || {};

  el("modal-session-name").value = session.name || "default";
  el("modal-workspace").value = session.workspace_path || "";
  el("modal-providers").value = provider;
  await loadModelsForProvider(provider, "modal-model", model);

  const limits = getSessionLimits(session);
  el("modal-mode").value = session.mode || "interactive";
  el("modal-approval-mode").value = session.policy_profile || "default";
  el("modal-max-timeout").value = limits.maxTimeout;
  el("modal-condense-window").value = limits.maxContext;

  el("modal-agentic-planning").checked = Boolean(context.agentic_planning);
  el("modal-research-mode").checked = Boolean(context.research_mode);
  el("modal-auto-condense").checked = Boolean(context.auto_condense);
  el("modal-system-prompt").value = context.system_prompt_override || "";
  el("modal-rules-checklist").value = context.rules_checklist || "";

  const tools = await req(`/sessions/${sessionId}/tools-config`);
  const skills = await req(`/sessions/${sessionId}/skills-config`);
  renderOverrideList("modal-enabled-tools", tools, new Set(tools.filter((t) => t.enabled).map((t) => t.name)));
  renderOverrideList("modal-enabled-skills", skills, new Set(skills.filter((sk) => sk.enabled).map((sk) => sk.name)));

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
      await updateSessionSummary(null);
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
      await updateSessionSummary(details);
      await loadSessionTimeline(currentSession);
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
      if (["queued", "running", "awaiting_approval"].includes(payload?.state)) {
        setIndicator(sessionId, "dot-running");
        setThinkingState(sessionId, true);
      }
      if (payload?.state === "completed") {
        setIndicator(sessionId, "dot-success");
        latestAssistantMessage = null;
        assistantDraftBuffers.delete(sessionId);
        setThinkingState(sessionId, false);
      }
      if (["failed", "blocked", "cancelled"].includes(payload?.state)) {
        setIndicator(sessionId, "dot-error");
        latestAssistantMessage = null;
        assistantDraftBuffers.delete(sessionId);
        setThinkingState(sessionId, false);
      }
    }

    if (eventType === "assistant_chunk") {
      const existing = assistantDraftBuffers.get(sessionId) || "";
      const next = `${existing}${existing ? "\n\n" : ""}${payload?.text || ""}`;
      assistantDraftBuffers.set(sessionId, next);
      updateAssistantDraft(next, sessionId);
      setThinkingState(sessionId, true);
    }

    if (eventType === "loop_step") {
      setThinkingState(sessionId, true);
    }

    if (eventType === "log" && payload?.message === "job completed") {
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
    currentSession = sessionsCache[0].id;
  }

  const statuses = await Promise.all(sessionsCache.map((s) => buildSessionIndicator(s.id)));
  sessionsCache.forEach((s, idx) => {
    sessionIndicators.set(s.id, statuses[idx]);
    thinkingStatus.set(s.id, statuses[idx] === "dot-running");
  });
  renderSessionList();

  if (sessionsCache.length > 0) {
    const active = sessionsCache.find((s) => s.id === currentSession) || sessionsCache[0];
    currentSession = active.id;
    hydrateSessionMessages(active);
    await updateSessionSummary(active);
    await loadSessionTimeline(currentSession);
    await refreshCapabilitiesPanel();
    connectStream();
  } else {
    await updateSessionSummary(null);
    await refreshCapabilitiesPanel();
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

setOnClick("create-session", async () => {
  const workspace_path = el("workspace").value.trim();
  const sessionName = window.prompt("Session name", "new session") || "new session";
  const created = await req("/sessions", {
    method: "POST",
    body: JSON.stringify({
      workspace_path,
      name: sessionName,
      mode: el("mode").value,
      policy_profile: el("policy").value,
      max_timeout_s: Number(el("max-timeout").value || 300),
      max_context_messages: Number(el("max-context").value || 40),
      provider_preferences: { ordered: [el("providers").value], model: el("model").value || "default" },
    }),
  });
  currentSession = created.id;
  currentJob = null;
  await refreshSessions();
});

setOnClick("refresh-sessions", refreshSessions);
const quickSwitch = el("session-quick-switch");
if (quickSwitch) quickSwitch.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") return;
  const prefix = event.target.value.trim().toLowerCase();
  const found = sessionsCache.find((s) => s.id.startsWith(prefix) || (s.name || "").toLowerCase().startsWith(prefix));
  if (!found) return;
  currentSession = found.id;
  const details = await req(`/sessions/${currentSession}`);
  hydrateSessionMessages(details);
  await updateSessionSummary(details);
  await loadSessionTimeline(currentSession);
  connectStream();
  renderSessionList();
  event.target.value = "";
});

setOnClick("browse-workspace", async () => {
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
});

const workspacePicker = el("workspace-picker");
if (workspacePicker) workspacePicker.addEventListener("change", (event) => {
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

setOnClick("create-job", async () => {
  if (!currentSession) return;
  const goal = el("goal").value;
  if (!goal.trim()) return;

  pushChat("user", goal);
  latestAssistantMessage = null;
  assistantDraftBuffers.delete(currentSession);
  const job = await req(`/sessions/${currentSession}/jobs`, { method: "POST", body: JSON.stringify({ goal }) });
  currentJob = job.id;
  setThinkingState(currentSession, true);
  setSessionState("running");
  setIndicator(currentSession, "dot-running");
  el("goal").value = "";
});

const goalInput = el("goal");
if (goalInput) goalInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el("create-job").click();
  }
});

setOnClick("refresh-approvals", refreshApprovals);
setOnClick("toggle-left", () => togglePanel("left"));
setOnClick("toggle-right", () => togglePanel("right"));
setOnClick("theme-toggle", toggleTheme);
applyPanelLayout();

setOnClick("modal-cancel", closeSettingsModal);
setOnClick("modal-save", async () => {
  if (!currentSettingsSessionId) return;

  await req(`/sessions/${currentSettingsSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({
      name: el("modal-session-name").value,
      workspace_path: el("modal-workspace").value,
      mode: el("modal-mode").value,
      policy_profile: el("modal-approval-mode").value,
      max_timeout_s: Number(el("modal-max-timeout").value || 300),
      max_context_messages: Number(el("modal-condense-window").value || 40),
      provider_preferences: { ordered: [el("modal-providers").value], model: el("modal-model").value || "default" },
      agentic_planning: el("modal-agentic-planning").checked,
      research_mode: el("modal-research-mode").checked,
      auto_condense: el("modal-auto-condense").checked,
      system_prompt_override: el("modal-system-prompt").value,
      rules_checklist: el("modal-rules-checklist").value,
    }),
  });

  await req(`/sessions/${currentSettingsSessionId}/tools-config`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: getCheckedNames("modal-enabled-tools") }),
  });

  await req(`/sessions/${currentSettingsSessionId}/skills-config`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: getCheckedNames("modal-enabled-skills") }),
  });

  closeSettingsModal();
  await refreshSessions();
  await refreshCapabilitiesPanel();
});

const settingsModal = el("session-settings-modal");
if (settingsModal) settingsModal.addEventListener("click", (event) => {
  if (event.target.id === "session-settings-modal") closeSettingsModal();
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".session-menu") && !event.target.closest(".session-menu-btn")) closeAnySessionMenu();
});



function closeSkillEditor() {
  skillEditorSessionId = null;
  skillEditorName = null;
  el("skill-editor-modal").classList.add("hidden");
}

setOnClick("refresh-capabilities", () => {
  refreshCapabilitiesPanel().catch(console.error);
});

setOnClick("open-skills-folder", async () => {
  if (!currentSession) return;
  const payload = await req(`/sessions/${currentSession}/skills/open-folder`, { method: "POST" });
  const path = payload.path || "";
  try {
    await navigator.clipboard.writeText(path);
  } catch {
    // clipboard unavailable
  }
  const opened = window.open(`file://${path}`, "_blank");
  if (!opened) window.alert(`Skills folder: ${path}
(Path copied when clipboard access is available)`);
});

setOnClick("skill-editor-cancel", closeSkillEditor);
setOnClick("skill-editor-save", async () => {
  if (!skillEditorSessionId || !skillEditorName) return;
  const content = el("skill-editor-content").value;
  await req(`/sessions/${skillEditorSessionId}/skills/${encodeURIComponent(skillEditorName)}/content`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
  closeSkillEditor();
  await refreshCapabilitiesPanel();
});

const skillEditorModal = el("skill-editor-modal");
if (skillEditorModal) skillEditorModal.addEventListener("click", (event) => {
  if (event.target.id === "skill-editor-modal") closeSkillEditor();
});


document.querySelectorAll("#meta-filters .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#meta-filters .chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    selectedFilter = chip.dataset.filter;
    applyTimelineFilter();
  });
});

initTheme();
populateRuntimeOptions().then(refreshSessions).then(refreshApprovals).catch(console.error);
setInterval(() => {
  refreshApprovals().catch(() => null);
}, 2000);
