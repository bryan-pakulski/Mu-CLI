const el = (id) => document.getElementById(id);

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  events: [],
  eventSource: null,
  tools: [],
  runtime: null,
  sessions: [],
  currentSession: "",
  selectedSessionAction: "",
};

const VARIABLE_DEFS = [
  { key: "yolo", label: "YOLO mode", type: "bool", group: "variables", description: "Auto-approve tool actions without prompting." },
  { key: "strict_mode", label: "Strict approvals", type: "bool", group: "variables", description: "Request approval for more tool operations." },
  { key: "agent_mode", label: "Agent mode", type: "select", options: ["default", "debug", "feature", "research"], group: "variables", description: "Controls system behavior and prompting strategy." },
  { key: "memory_enabled", label: "Memory enabled", type: "bool", group: "memory", description: "Enables persistent memory summarization in long loops." },
  { key: "compact_history", label: "Compact history", type: "bool", group: "memory", description: "Reduces tool metadata in saved history to save tokens." },
  { key: "collation_enabled", label: "Collation enabled", type: "bool", group: "memory", description: "Batches read-heavy tool outputs into delayed context flushes." },
  { key: "max_iterations", label: "Max iterations", type: "number", group: "memory", description: "Upper bound for agentic tool-call loop iterations." },
  { key: "make_timeout", label: "Make timeout", type: "number", group: "memory", description: "Timeout (seconds) for long-running run_agent_task commands." },
  { key: "make_max_output", label: "Make max output", type: "number", group: "memory", description: "Maximum output characters returned from run_agent_task." },
  { key: "ollama_host", label: "Ollama host", type: "text", group: "memory", description: "Base URL for Ollama provider endpoint." },
];

const ui = {
  apiBaseInput: el("apiBaseInput"),
  messageInput: el("messageInput"),
  sendBtn: el("sendBtn"),
  refreshBtn: el("refreshBtn"),
  clearLogBtn: el("clearLogBtn"),
  eventStream: el("eventStream"),
  chatStream: el("chatStream"),
  stateBadge: el("stateBadge"),
  runtimeSummary: el("runtimeSummary"),
  statsSummary: el("statsSummary"),
  modelInput: el("modelInput"),
  agenticToggle: el("agenticToggle"),
  thinkingToggle: el("thinkingToggle"),
  applyRuntimeBtn: el("applyRuntimeBtn"),
  collapseBtn: el("collapseBtn"),
  expandBtn: el("expandBtn"),
  configPanel: el("configPanel"),
  appShell: document.querySelector(".app-shell"),
  openSettingsBtn: el("openSettingsBtn"),
  closeSettingsBtn: el("closeSettingsBtn"),
  saveSettingsBtn: el("saveSettingsBtn"),
  settingsModal: el("settingsModal"),
  settingsTabs: el("settingsTabs"),
  toolsList: el("toolsList"),
  variablesList: el("variablesList"),
  memoryList: el("memoryList"),
  systemPromptInput: el("systemPromptInput"),
  settingsModelInput: el("settingsModelInput"),
  themeModeSelect: el("themeModeSelect"),
  accentSelect: el("accentSelect"),
  sessionTabs: el("sessionTabs"),
  newSessionBtn: el("newSessionBtn"),
  newSessionModal: el("newSessionModal"),
  closeNewSessionBtn: el("closeNewSessionBtn"),
  createSessionConfirmBtn: el("createSessionConfirmBtn"),
  newSessionNameInput: el("newSessionNameInput"),
  newSessionProviderSelect: el("newSessionProviderSelect"),
  newSessionModelInput: el("newSessionModelInput"),
  toggleMetaBtn: el("toggleMetaBtn"),
  metaPanel: el("metaPanel"),
  sessionActionModal: el("sessionActionModal"),
  closeSessionActionBtn: el("closeSessionActionBtn"),
  sessionActionNameInput: el("sessionActionNameInput"),
  saveSessionActionBtn: el("saveSessionActionBtn"),
  deleteSessionActionBtn: el("deleteSessionActionBtn"),
};

ui.apiBaseInput.value = state.apiBase;

function api(path) {
  return `${state.apiBase}${path}`;
}

function clip(text, max = 1800) {
  const value = typeof text === "string" ? text : JSON.stringify(text, null, 2);
  return value.length > max ? `${value.slice(0, max)}\n…(truncated)` : value;
}

function pushEvent(kind, payload) {
  state.events.unshift({ ts: new Date().toLocaleTimeString(), kind, payload: clip(payload) });
  state.events = state.events.slice(0, 300);
  renderEvents();
}

function renderEvents() {
  ui.eventStream.innerHTML = "";
  for (const event of state.events) {
    const card = document.createElement("div");
    card.className = "event-card";
    const tag = inferMetaTag(event.kind);
    card.innerHTML = `
      <details class="meta-entry">
        <summary>${event.ts} · ${event.kind}<span class="meta-tag">${tag}</span></summary>
        <div class="event-body"></div>
      </details>
    `;
    card.querySelector(".event-body").textContent = event.payload;
    ui.eventStream.appendChild(card);
  }
}

function inferMetaTag(kind) {
  const lower = (kind || "").toLowerCase();
  if (lower.includes("tool")) return "tool";
  if (lower.includes("error")) return "error";
  if (lower.includes("debug")) return "debug";
  if (lower.includes("info")) return "info";
  if (lower.includes("session")) return "session";
  return "event";
}

function renderConversation(history) {
  ui.chatStream.innerHTML = "";
  for (const message of history || []) {
    if (!["user", "assistant"].includes(message.role)) continue;
    const textParts = (message.parts || [])
      .filter((part) => part.type === "text")
      .map((part) => part.text || "")
      .filter(Boolean);
    if (!textParts.length) continue;

    const card = document.createElement("div");
    card.className = "event-card";
    card.innerHTML = `<div class="event-head">${message.role}</div><div class="event-body"></div>`;
    card.querySelector(".event-body").textContent = textParts.join("\n\n");
    ui.chatStream.appendChild(card);
  }
}

async function refreshHistory() {
  try {
    const payload = await fetchJson("/api/history?limit=120");
    renderConversation(payload.history || []);
  } catch (err) {
    pushEvent("history.error", String(err));
  }
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(api(path), { headers: { "Content-Type": "application/json" }, ...options });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function setConnected(connected, text = "Connected") {
  ui.stateBadge.classList.remove("connected", "error");
  ui.stateBadge.classList.add(connected ? "connected" : "error");
  ui.stateBadge.textContent = text;
}

async function refreshRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime");
    state.runtime = runtime;
    ui.modelInput.value = runtime.model || "";
    ui.agenticToggle.checked = !!runtime.agentic;
    ui.thinkingToggle.checked = !!runtime.thinking;
    ui.settingsModelInput.value = runtime.model || "";
    ui.systemPromptInput.value = runtime.system_instruction || "";

    ui.runtimeSummary.textContent = [
      `session: ${runtime.session_name}`,
      `provider: ${runtime.provider}`,
      `model: ${runtime.model}`,
      `agentic: ${runtime.agentic}`,
      `thinking: ${runtime.thinking}`,
      `disabled_tools: ${(runtime.disabled_tools || []).length}`,
    ].join("\n");
    setConnected(true, "Connected");
  } catch (err) {
    setConnected(false, `Error: ${err.message}`);
    pushEvent("runtime.error", String(err));
  }
}

async function refreshStats() {
  try {
    const statePayload = await fetchJson("/api/state");
    const token = statePayload.state?.token_counts || {};
    const historyLength = statePayload.state?.history_length ?? 0;
    const disabledTools = (statePayload.state?.disabled_tools || []).length;
    const memoryInfo = performance?.memory
      ? `${(performance.memory.usedJSHeapSize / 1024 / 1024).toFixed(1)}MB / ${(performance.memory.totalJSHeapSize / 1024 / 1024).toFixed(1)}MB`
      : "n/a";
    ui.statsSummary.textContent = [
      `history_length: ${historyLength}`,
      `tokens_total: ${token.total ?? 0}`,
      `tokens_in/out: ${token.input ?? 0} / ${token.output ?? 0}`,
      `est_cost_total: ${token.total_cost ?? 0}`,
      `disabled_tools: ${disabledTools}`,
      `browser_memory: ${memoryInfo}`,
    ].join("\n");
  } catch (err) {
    ui.statsSummary.textContent = `stats error: ${String(err)}`;
  }
}

async function refreshSessions() {
  try {
    const data = await fetchJson("/api/sessions");
    state.sessions = data.sessions || [];
    state.currentSession = data.current_session_name || "";
    renderSessionTabs();
  } catch (err) {
    pushEvent("sessions.error", String(err));
  }
}

async function refreshTools() {
  try {
    const data = await fetchJson("/api/tools");
    state.tools = data.tools || [];
  } catch (err) {
    pushEvent("tools.error", String(err));
  }
}

function connectSSE() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(api("/api/events"));
  state.eventSource.onmessage = (evt) => {
    try {
      const payload = JSON.parse(evt.data);
      pushEvent(payload.event || "event", payload.payload || payload);
    } catch {
      pushEvent("event", evt.data);
    }
  };
  state.eventSource.onerror = () => setConnected(false, "SSE disconnected");
}

function renderSettingField(def, values) {
  const wrapper = document.createElement("div");
  wrapper.className = "setting-item";
  const left = document.createElement("div");
  left.innerHTML = `<div class="label">${def.label}</div><div class="desc">${def.description || def.key}</div>`;
  wrapper.title = `${def.key}: ${def.description || ""}`;
  wrapper.appendChild(left);

  const value = values[def.key];
  let input;
  if (def.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!value;
  } else if (def.type === "number") {
    input = document.createElement("input");
    input.type = "number";
    input.value = Number(value ?? 0);
  } else if (def.type === "select") {
    input = document.createElement("select");
    for (const option of def.options || []) {
      const opt = document.createElement("option");
      opt.value = option;
      opt.textContent = option;
      if ((value ?? "") === option) opt.selected = true;
      input.appendChild(opt);
    }
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = value ?? "";
  }
  input.dataset.varKey = def.key;
  wrapper.appendChild(input);
  return wrapper;
}

function renderToolsSettings() {
  const disabled = new Set(state.runtime?.disabled_tools || []);
  ui.toolsList.innerHTML = "";

  const grouped = new Map();
  const bucketForTool = (tool) => {
    if (tool.name.startsWith("git_")) return "GIT";
    if (tool.name.includes("url") || tool.name.includes("document")) return "Internet / External";
    if (tool.execution_kind === "read") return "Read";
    if (tool.execution_kind === "mutate") return "Write / Mutate";
    if (tool.execution_kind === "memory") return "Memory";
    if (tool.execution_kind === "control") return "Control";
    return "Other";
  };

  for (const tool of state.tools) {
    const bucket = bucketForTool(tool);
    if (!grouped.has(bucket)) grouped.set(bucket, []);
    grouped.get(bucket).push(tool);
  }

  const groupsContainer = document.createElement("div");
  groupsContainer.className = "tool-groups";
  for (const [groupName, tools] of grouped.entries()) {
    const group = document.createElement("details");
    group.className = "tool-group";
    group.open = true;
    group.innerHTML = `<summary>${groupName} (${tools.length})</summary>`;

    const body = document.createElement("div");
    body.className = "tool-group-body";
    for (const tool of tools) {
      const item = document.createElement("div");
      item.className = "setting-item";
      item.innerHTML = `<div><div class="label">${tool.name}</div><div class="desc">${tool.description || `${tool.execution_kind} · ${tool.server_policy}`}</div></div>`;
      item.title = `${tool.name}: ${tool.description || `${tool.execution_kind} · ${tool.server_policy}`}`;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = !disabled.has(tool.name);
      checkbox.dataset.toolName = tool.name;
      item.appendChild(checkbox);
      body.appendChild(item);
    }
    group.appendChild(body);
    groupsContainer.appendChild(group);
  }
  ui.toolsList.appendChild(groupsContainer);
}

function renderVariableSections() {
  const vars = state.runtime?.variables || {};
  ui.variablesList.innerHTML = "";
  ui.memoryList.innerHTML = "";
  for (const def of VARIABLE_DEFS) {
    const field = renderSettingField(def, vars);
    if (def.group === "memory") ui.memoryList.appendChild(field);
    else ui.variablesList.appendChild(field);
  }
}

function openModal() {
  ui.settingsModal.classList.remove("hidden");
  requestAnimationFrame(() => ui.settingsModal.classList.add("show"));
}

function closeModal() {
  ui.settingsModal.classList.remove("show");
  setTimeout(() => ui.settingsModal.classList.add("hidden"), 180);
}

function openSimpleModal(modalEl) {
  modalEl.classList.remove("hidden");
  requestAnimationFrame(() => modalEl.classList.add("show"));
}

function closeSimpleModal(modalEl) {
  modalEl.classList.remove("show");
  setTimeout(() => modalEl.classList.add("hidden"), 180);
}

function renderSessionTabs() {
  ui.sessionTabs.innerHTML = "";
  for (const sessionName of state.sessions) {
    const wrapper = document.createElement("div");
    wrapper.className = `session-tab ${sessionName === state.currentSession ? "active" : ""}`;

    const loadBtn = document.createElement("button");
    loadBtn.className = "name";
    loadBtn.textContent = sessionName;
    loadBtn.title = sessionName;
    loadBtn.addEventListener("click", () => loadSession(sessionName));

    const menuBtn = document.createElement("button");
    menuBtn.className = "menu-btn";
    menuBtn.textContent = "⋯";
    menuBtn.title = "Session actions";
    menuBtn.addEventListener("click", () => openSessionActionModal(sessionName));

    wrapper.appendChild(loadBtn);
    wrapper.appendChild(menuBtn);
    ui.sessionTabs.appendChild(wrapper);
  }
}

function openSessionActionModal(sessionName) {
  state.selectedSessionAction = sessionName;
  ui.sessionActionNameInput.value = sessionName;
  openSimpleModal(ui.sessionActionModal);
}

async function loadSession(name) {
  try {
    await fetchJson("/api/sessions/load", { method: "POST", body: JSON.stringify({ name }) });
    pushEvent("session.loaded", name);
    await refreshRuntime();
    await refreshSessions();
    await refreshHistory();
  } catch (err) {
    pushEvent("session.load_error", String(err));
  }
}

async function deleteSession(name) {
  try {
    await fetchJson("/api/sessions/delete", { method: "POST", body: JSON.stringify({ name }) });
    pushEvent("session.deleted", name);
    await refreshRuntime();
    await refreshSessions();
  } catch (err) {
    pushEvent("session.delete_error", String(err));
  }
}

async function renameSession(name) {
  const newName = ui.sessionActionNameInput.value.trim();
  if (!newName || newName === name) return;
  try {
    await fetchJson("/api/sessions/rename", {
      method: "POST",
      body: JSON.stringify({ old_name: name, new_name: newName }),
    });
    pushEvent("session.renamed", `${name} → ${newName}`);
    await refreshRuntime();
    await refreshSessions();
  } catch (err) {
    pushEvent("session.rename_error", String(err));
  }
}

function applyTheme() {
  const mode = localStorage.getItem("mucli_gui_theme_mode") || "dark";
  const accent = localStorage.getItem("mucli_gui_accent") || "orange";
  document.documentElement.setAttribute("data-theme", mode);
  document.documentElement.setAttribute("data-accent", accent);
  ui.themeModeSelect.value = mode;
  ui.accentSelect.value = accent;
}

async function sendMessage() {
  const text = ui.messageInput.value.trim();
  if (!text) return;
  ui.messageInput.value = "";
  pushEvent("message.out", text);
  try {
    const result = await fetchJson("/api/message", { method: "POST", body: JSON.stringify({ text }) });
    pushEvent("message.in", result);
    await refreshHistory();
  } catch (err) {
    pushEvent("message.error", String(err));
  }
}

async function applyRuntime() {
  try {
    const res = await fetchJson("/api/runtime", {
      method: "POST",
      body: JSON.stringify({ model: ui.modelInput.value.trim(), agentic: ui.agenticToggle.checked, thinking: ui.thinkingToggle.checked }),
    });
    pushEvent("runtime.updated", res);
    await refreshRuntime();
  } catch (err) {
    pushEvent("runtime.update_error", String(err));
  }
}

async function saveSettings() {
  const disabled_tools = [];
  ui.toolsList.querySelectorAll("input[data-tool-name]").forEach((input) => {
    if (!input.checked) disabled_tools.push(input.dataset.toolName);
  });

  const variables = { ...(state.runtime?.variables || {}) };
  [...ui.variablesList.querySelectorAll("[data-var-key]"), ...ui.memoryList.querySelectorAll("[data-var-key]")].forEach((input) => {
    const key = input.dataset.varKey;
    if (!key) return;
    if (input.type === "checkbox") variables[key] = input.checked;
    else if (input.type === "number") variables[key] = Number(input.value || 0);
    else variables[key] = input.value;
  });

  localStorage.setItem("mucli_gui_theme_mode", ui.themeModeSelect.value);
  localStorage.setItem("mucli_gui_accent", ui.accentSelect.value);
  applyTheme();

  try {
    const res = await fetchJson("/api/runtime", {
      method: "POST",
      body: JSON.stringify({
        disabled_tools,
        variables,
        model: ui.settingsModelInput.value.trim(),
        system_instruction: ui.systemPromptInput.value,
      }),
    });
    pushEvent("settings.saved", res);
    closeModal();
    await refreshRuntime();
    await refreshTools();
  } catch (err) {
    pushEvent("settings.save_error", String(err));
  }
}

async function runCommand(command) {
  try {
    const res = await fetchJson("/api/command", { method: "POST", body: JSON.stringify({ command }) });
    pushEvent(`command ${command}`, res);
    await refreshRuntime();
  } catch (err) {
    pushEvent("command.error", String(err));
  }
}

function setupTabSwitching() {
  ui.settingsTabs.querySelectorAll(".settings-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      ui.settingsTabs.querySelectorAll(".settings-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".settings-pane").forEach((pane) => pane.classList.remove("active"));
      tab.classList.add("active");
      document.querySelector(`.settings-pane[data-pane="${name}"]`)?.classList.add("active");
    });
  });
}

function setupHandlers() {
  ui.sendBtn.addEventListener("click", sendMessage);
  ui.refreshBtn.addEventListener("click", async () => {
    await refreshRuntime();
    await refreshTools();
    await refreshStats();
  });
  ui.clearLogBtn.addEventListener("click", () => {
    state.events = [];
    renderEvents();
  });
  ui.toggleMetaBtn.addEventListener("click", () => {
    ui.metaPanel.classList.toggle("collapsed");
    const collapsed = ui.metaPanel.classList.contains("collapsed");
    ui.toggleMetaBtn.textContent = collapsed ? "Metadata ▶" : "Metadata ◀";
  });
  ui.applyRuntimeBtn.addEventListener("click", applyRuntime);

  ui.apiBaseInput.addEventListener("change", async () => {
    state.apiBase = ui.apiBaseInput.value.trim().replace(/\/$/, "");
    localStorage.setItem("mucli_gui_api_base", state.apiBase);
    connectSSE();
    await refreshRuntime();
    await refreshTools();
  });

  document.querySelectorAll(".cmd-btn").forEach((btn) => btn.addEventListener("click", () => runCommand(btn.dataset.cmd)));

  ui.openSettingsBtn.addEventListener("click", async () => {
    openModal();
    await refreshRuntime();
    await refreshTools();
    renderToolsSettings();
    renderVariableSections();
  });
  ui.closeSettingsBtn.addEventListener("click", closeModal);
  ui.settingsModal.addEventListener("click", (evt) => {
    if (evt.target === ui.settingsModal) closeModal();
  });
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") {
      closeModal();
      closeSimpleModal(ui.newSessionModal);
      closeSimpleModal(ui.sessionActionModal);
    }
    if ((evt.metaKey || evt.ctrlKey) && evt.key === "Enter") sendMessage();
  });
  ui.saveSettingsBtn.addEventListener("click", saveSettings);

  ui.themeModeSelect.addEventListener("change", () => {
    localStorage.setItem("mucli_gui_theme_mode", ui.themeModeSelect.value);
    applyTheme();
  });
  ui.accentSelect.addEventListener("change", () => {
    localStorage.setItem("mucli_gui_accent", ui.accentSelect.value);
    applyTheme();
  });

  ui.collapseBtn.addEventListener("click", () => {
    ui.configPanel.classList.add("collapsed");
    ui.appShell.classList.add("panel-collapsed");
    ui.expandBtn.classList.remove("hidden");
  });
  ui.expandBtn.addEventListener("click", () => {
    ui.configPanel.classList.remove("collapsed");
    ui.appShell.classList.remove("panel-collapsed");
    ui.expandBtn.classList.add("hidden");
  });

  ui.newSessionBtn.addEventListener("click", () => openSimpleModal(ui.newSessionModal));
  ui.closeNewSessionBtn.addEventListener("click", () => closeSimpleModal(ui.newSessionModal));
  ui.newSessionModal.addEventListener("click", (evt) => {
    if (evt.target === ui.newSessionModal) closeSimpleModal(ui.newSessionModal);
  });
  ui.closeSessionActionBtn.addEventListener("click", () => closeSimpleModal(ui.sessionActionModal));
  ui.sessionActionModal.addEventListener("click", (evt) => {
    if (evt.target === ui.sessionActionModal) closeSimpleModal(ui.sessionActionModal);
  });
  ui.saveSessionActionBtn.addEventListener("click", async () => {
    if (!state.selectedSessionAction) return;
    await renameSession(state.selectedSessionAction);
    closeSimpleModal(ui.sessionActionModal);
  });
  ui.deleteSessionActionBtn.addEventListener("click", async () => {
    if (!state.selectedSessionAction) return;
    await deleteSession(state.selectedSessionAction);
    closeSimpleModal(ui.sessionActionModal);
  });
  ui.createSessionConfirmBtn.addEventListener("click", async () => {
    const name = ui.newSessionNameInput.value.trim();
    const provider = ui.newSessionProviderSelect.value.trim();
    const model = ui.newSessionModelInput.value.trim();

    try {
      await fetchJson("/api/sessions/new", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      if (provider) {
        await fetchJson("/api/command", {
          method: "POST",
          body: JSON.stringify({ command: `/provider ${provider}` }),
        });
      }
      if (model) {
        await fetchJson("/api/runtime", {
          method: "POST",
          body: JSON.stringify({ model }),
        });
      }
      closeSimpleModal(ui.newSessionModal);
      pushEvent("session.created", { name: name || "(auto)", provider, model });
      await refreshRuntime();
      await refreshSessions();
      await refreshHistory();
    } catch (err) {
      pushEvent("session.create_error", String(err));
    }
  });
}

setupHandlers();
setupTabSwitching();
applyTheme();
connectSSE();
refreshRuntime();
refreshTools();
refreshSessions();
refreshHistory();
refreshStats();
setInterval(() => {
  refreshRuntime();
  refreshTools();
  refreshSessions();
  refreshStats();
}, 12000);
