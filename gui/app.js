const el = (id) => document.getElementById(id);

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  currentSession: "",
  sessions: [],
  runtime: null,
  tools: [],
  allVariables: {},
  loadedMessages: [],
  visibleCount: 24,
  isSending: false,
};

const ui = {
  apiBaseInput: el("apiBaseInput"),
  modelInput: el("modelInput"),
  agenticToggle: el("agenticToggle"),
  thinkingToggle: el("thinkingToggle"),
  applyRuntimeBtn: el("applyRuntimeBtn"),
  statusBadge: el("statusBadge"),
  sessionList: el("sessionList"),
  newSessionBtn: el("newSessionBtn"),
  feed: el("feed"),
  composer: el("composer"),
  messageInput: el("messageInput"),
  sendBtn: el("sendBtn"),
  menuBtn: el("menuBtn"),
  chatMenu: el("chatMenu"),
  fileBtn: el("fileBtn"),
  fileInput: el("fileInput"),
  workspaceStatus: el("workspaceStatus"),
  addWorkspaceBtn: el("addWorkspaceBtn"),
  removeWorkspaceBtn: el("removeWorkspaceBtn"),
  settingsBtn: el("settingsBtn"),
  settingsModal: el("settingsModal"),
  closeSettingsBtn: el("closeSettingsBtn"),
  saveSettingsBtn: el("saveSettingsBtn"),
  settingsTabs: el("settingsTabs"),
  toolsList: el("toolsList"),
  variablesList: el("variablesList"),
  memoryList: el("memoryList"),
  themeModeSelect: el("themeModeSelect"),
  accentSelect: el("accentSelect"),
};

ui.apiBaseInput.value = state.apiBase;

function applyThemeFromStorage() {
  const mode = localStorage.getItem("mucli_theme_mode") || "dark";
  const accent = localStorage.getItem("mucli_theme_accent") || "indigo";
  document.documentElement.dataset.mode = mode;
  document.documentElement.dataset.accent = accent;
  ui.themeModeSelect.value = mode;
  ui.accentSelect.value = accent;
}

function api(path) {
  return `${state.apiBase}${path}`;
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(api(path), { headers: { "Content-Type": "application/json" }, ...options });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function setStatus(text, kind = "") {
  ui.statusBadge.classList.remove("connected", "error");
  if (kind) ui.statusBadge.classList.add(kind);
  ui.statusBadge.textContent = text;
}

function textFromParts(parts = []) {
  return parts.filter((p) => p?.type === "text").map((p) => p.text || "").filter(Boolean).join("\n\n");
}

function normalizedMessages(history = []) {
  return history.filter((m) => ["user", "assistant"].includes(m.role)).map((m) => ({ role: m.role, text: textFromParts(m.parts || []) })).filter((m) => m.text);
}


function groupTools(tools = []) {
  const groups = { "Workspace & Files": [], "Execution": [], "Git": [], "System": [], "Other": [] };
  for (const tool of tools) {
    const name = String(tool.name || "");
    if (/(workspace|file|dir|chunk|search)/i.test(name)) groups["Workspace & Files"].push(tool);
    else if (/(run_|batch|agent_task|task)/i.test(name)) groups["Execution"].push(tool);
    else if (/git/i.test(name)) groups["Git"].push(tool);
    else if (/(time|status|tool|variable|model|provider)/i.test(name)) groups["System"].push(tool);
    else groups["Other"].push(tool);
  }
  return Object.entries(groups).filter(([, items]) => items.length);
}

function parseVariableValue(raw, typeHint = "") {
  const type = String(typeHint || "").toLowerCase();
  if (type === "bool" || type === "boolean") return raw === "true";
  if (type === "int" || type === "number") return Number(raw);
  if (raw === "true" || raw === "false") return raw === "true";
  if (!Number.isNaN(Number(raw)) && raw !== "") return Number(raw);
  return raw;
}

function renderMarkdown(container, text) {
  const rendered = window.marked ? window.marked.parse(String(text || ""), { gfm: true, breaks: true }) : String(text || "");
  container.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(rendered) : rendered;
  container.querySelectorAll("pre code").forEach((block) => window.hljs?.highlightElement(block));
}

function renderFeed(resetToBottom = false) {
  const prevHeight = ui.feed.scrollHeight;
  const prevTop = ui.feed.scrollTop;
  const start = Math.max(0, state.loadedMessages.length - state.visibleCount);
  ui.feed.innerHTML = "";
  for (const item of state.loadedMessages.slice(start)) {
    const card = document.createElement("article");
    card.className = "message";
    card.innerHTML = `<span class="role">${item.role}</span><span class="text"></span>`;
    renderMarkdown(card.querySelector(".text"), item.text);
    ui.feed.appendChild(card);
  }
  if (resetToBottom) return void (ui.feed.scrollTop = ui.feed.scrollHeight);
  const grew = ui.feed.scrollHeight - prevHeight;
  if (prevTop < 60 && grew > 0) ui.feed.scrollTop = prevTop + grew;
}

function renderSettingsList(target, items, mode = "checkbox") {
  target.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("label");
    row.className = "settings-row";
    if (mode === "text") {
      if (item.kind === "bool") {
        row.innerHTML = `<span>${item.label}</span><input type="checkbox" data-key="${item.key}" data-kind="bool" ${item.value ? "checked" : ""} />`;
      } else {
        row.innerHTML = `<span>${item.label}</span><input data-key="${item.key}" data-kind="${item.kind || "text"}" value="${String(item.value ?? "")}" />`;
      }
    } else {
      row.innerHTML = `<span>${item.label}</span><input type="checkbox" data-key="${item.key}" ${item.enabled ? "checked" : ""} />`;
    }
    target.appendChild(row);
  }
}

function renderGroupedTools() {
  ui.toolsList.innerHTML = "";
  for (const [groupName, items] of groupTools(state.tools)) {
    const group = document.createElement("section");
    group.className = "settings-group";
    group.innerHTML = `<h4>${groupName}</h4><div class="settings-list"></div>`;
    const list = group.querySelector(".settings-list");
    renderSettingsList(list, items.map((t) => ({ key: t.name, label: t.name, enabled: !t.disabled })), "checkbox");
    ui.toolsList.appendChild(group);
  }
}

function populateSettingsPanels() {
  renderGroupedTools();

  const vars = state.allVariables || state.runtime?.variables || {};
  const entries = Object.entries(vars);
  const memoryEntries = entries.filter(([k]) => /(memory|compact|collation|timeout|max_)/i.test(k));
  const generalEntries = entries.filter(([k]) => !/(memory|compact|collation|timeout|max_)/i.test(k));

  const toItem = ([key, value]) => ({ key, label: key, value, kind: typeof value === "boolean" ? "bool" : typeof value === "number" ? "number" : "text" });
  renderSettingsList(ui.variablesList, generalEntries.map(toItem), "text");
  renderSettingsList(ui.memoryList, memoryEntries.map(toItem), "text");
}

async function refreshWorkspace() {
  try {
    const data = await fetchJson("/api/workspaces");
    const folders = Array.isArray(data.folders) ? data.folders : [];
    const tracked = Array.isArray(data.tracked_files) ? data.tracked_files.length : 0;
    if (!folders.length) {
      ui.workspaceStatus.textContent = "No workspace attached";
      return;
    }
    ui.workspaceStatus.textContent = `${folders[0]}${folders.length > 1 ? ` (+${folders.length - 1} more)` : ""} • ${tracked} tracked file${tracked === 1 ? "" : "s"}`;
  } catch {
    ui.workspaceStatus.textContent = "Workspace unavailable";
  }
}

async function refreshRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime");
    state.runtime = runtime;
    if (!state.currentSession) state.currentSession = runtime.session_name || "";
    ui.agenticToggle.checked = !!runtime.agentic;
    ui.thinkingToggle.checked = !!runtime.thinking;
    const model = runtime.model || "";
    ui.modelInput.innerHTML = `<option value="${model}">${model || "(default)"}</option>`;
    setStatus("Connected", "connected");
  } catch (err) {
    setStatus(`Error: ${err.message}`, "error");
  }
}

async function refreshTools() {
  try {
    const data = await fetchJson("/api/tools");
    state.tools = data.tools || [];
  } catch {
    state.tools = [];
  }
}

async function refreshStateVariables() {
  try {
    const data = await fetchJson("/api/state");
    state.allVariables = data.variables || state.runtime?.variables || {};
  } catch {
    state.allVariables = state.runtime?.variables || {};
  }
}

async function refreshSessions() {
  const data = await fetchJson("/api/sessions");
  state.sessions = data.sessions || [];
  if (!state.currentSession) state.currentSession = data.current || state.sessions[0] || "";
  renderSessions();
}

function renderSessions() {
  ui.sessionList.innerHTML = "";
  for (const name of state.sessions) {
    const item = document.createElement("div");
    item.className = `session-item ${name === state.currentSession ? "active" : ""}`;
    item.innerHTML = `
      <div class="session-row">
        <button class="session-title">${name}</button>
        <button class="session-menu-btn" title="Session options">⋯</button>
      </div>
      <div class="session-popup hidden">
        <input class="session-rename-input" value="${name}" />
        <div class="session-popup-actions">
          <button class="btn" data-action="apply-rename">Rename</button>
          <button class="btn" data-action="delete">Delete</button>
          <button class="btn" data-action="close">Close</button>
        </div>
      </div>
    `;

    item.querySelector(".session-title").addEventListener("click", () => loadSession(name));
    const popup = item.querySelector(".session-popup");
    item.querySelector(".session-menu-btn").addEventListener("click", (evt) => {
      evt.stopPropagation();
      ui.sessionList.querySelectorAll(".session-popup").forEach((p) => p.classList.add("hidden"));
      popup.classList.toggle("hidden");
    });

    item.querySelector('[data-action="apply-rename"]').addEventListener("click", async () => {
      const newName = item.querySelector(".session-rename-input").value.trim();
      if (!newName || newName === name) return;
      await renameSession(name, newName);
    });

    item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      await deleteSession(name);
    });

    item.querySelector('[data-action="close"]').addEventListener("click", () => popup.classList.add("hidden"));
    ui.sessionList.appendChild(item);
  }
}

async function refreshHistory(resetToBottom = true) {
  if (!state.currentSession) return;
  const payload = await fetchJson(`/api/history?limit=300&session_name=${encodeURIComponent(state.currentSession)}`);
  state.loadedMessages = normalizedMessages(payload.history || []);
  state.visibleCount = Math.min(24, state.loadedMessages.length || 24);
  renderFeed(resetToBottom);
}

async function loadSession(name) {
  await fetchJson("/api/sessions/load", { method: "POST", body: JSON.stringify({ name }) });
  state.currentSession = name;
  await refreshSessions();
  await refreshRuntime();
  await refreshHistory(true);
  await refreshWorkspace();
}

async function createSession() {
  const name = prompt("New session name:");
  if (!name) return;
  await fetchJson("/api/sessions/new", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
  state.currentSession = name.trim();
  await refreshSessions();
  await loadSession(state.currentSession);
}

async function renameSession(currentName, newName) {
  await fetchJson("/api/sessions/rename", { method: "POST", body: JSON.stringify({ name: currentName, new_name: newName.trim() }) });
  if (state.currentSession === currentName) state.currentSession = newName.trim();
  await refreshSessions();
  await refreshHistory(false);
}

async function deleteSession(name) {
  await fetchJson("/api/sessions/delete", { method: "POST", body: JSON.stringify({ name }) });
  if (state.currentSession === name) state.currentSession = "";
  await refreshSessions();
  await refreshRuntime();
  await refreshHistory(false);
}

async function applyRuntime() {
  await fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ model: ui.modelInput.value, agentic: ui.agenticToggle.checked, thinking: ui.thinkingToggle.checked }) });
  await refreshRuntime();
}

async function saveSettings() {
  const disabled_tools = [...ui.toolsList.querySelectorAll('input[type="checkbox"]')].filter((x) => !x.checked).map((x) => x.dataset.key);
  const variables = {};
  [...ui.variablesList.querySelectorAll("input[data-key]"), ...ui.memoryList.querySelectorAll("input[data-key]")].forEach((x) => {
    const raw = x.type === "checkbox" ? String(x.checked) : x.value;
    variables[x.dataset.key] = parseVariableValue(raw, x.dataset.kind);
  });

  await fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ disabled_tools, variables }) });

  localStorage.setItem("mucli_theme_mode", ui.themeModeSelect.value);
  localStorage.setItem("mucli_theme_accent", ui.accentSelect.value);
  applyThemeFromStorage();
  ui.settingsModal.classList.add("hidden");
  await refreshRuntime();
}

async function sendMessage(evt) {
  evt?.preventDefault();
  if (state.isSending) return;
  const text = ui.messageInput.value.trim();
  if (!text) return;
  state.isSending = true;
  ui.sendBtn.disabled = true;
  try {
    await fetchJson("/api/message", { method: "POST", body: JSON.stringify({ text, session_name: state.currentSession }) });
    ui.messageInput.value = "";
    await refreshHistory(true);
    await refreshWorkspace();
  } finally {
    state.isSending = false;
    ui.sendBtn.disabled = false;
  }
}

function wireEvents() {
  ui.apiBaseInput.addEventListener("change", async () => {
    state.apiBase = ui.apiBaseInput.value.trim() || state.apiBase;
    localStorage.setItem("mucli_gui_api_base", state.apiBase);
    await bootstrap();
  });
  ui.applyRuntimeBtn.addEventListener("click", () => applyRuntime().catch((err) => setStatus(`Error: ${err.message}`, "error")));
  ui.newSessionBtn.addEventListener("click", () => createSession().catch((err) => setStatus(`Error: ${err.message}`, "error")));

  ui.feed.addEventListener("scroll", () => {
    if (ui.feed.scrollTop < 40 && state.visibleCount < state.loadedMessages.length) {
      state.visibleCount = Math.min(state.loadedMessages.length, state.visibleCount + 16);
      renderFeed(false);
    }
  });

  ui.composer.addEventListener("submit", (evt) => sendMessage(evt).catch((err) => setStatus(`Error: ${err.message}`, "error")));
  ui.menuBtn.addEventListener("click", (evt) => { evt.stopPropagation(); ui.chatMenu.classList.toggle("hidden"); });
  ui.chatMenu.addEventListener("click", (evt) => evt.stopPropagation());
  document.addEventListener("click", () => {
    ui.chatMenu.classList.add("hidden");
    ui.sessionList.querySelectorAll(".session-popup").forEach((p) => p.classList.add("hidden"));
  });

  ui.fileBtn.addEventListener("click", () => ui.fileInput.click());
  ui.fileInput.addEventListener("change", () => {
    const file = ui.fileInput.files?.[0];
    if (!file) return;
    const marker = `[attached file: ${file.name}]`;
    ui.messageInput.value = ui.messageInput.value.trim() ? `${ui.messageInput.value.trim()}\n${marker}` : marker;
  });

  ui.settingsBtn.addEventListener("click", async () => {
    await refreshTools();
    await refreshRuntime();
    await refreshStateVariables();
    populateSettingsPanels();
    ui.settingsModal.classList.remove("hidden");
  });
  ui.closeSettingsBtn.addEventListener("click", () => ui.settingsModal.classList.add("hidden"));
  ui.saveSettingsBtn.addEventListener("click", () => saveSettings().catch((err) => setStatus(`Error: ${err.message}`, "error")));

  ui.settingsTabs.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".settings-tab");
    if (!btn) return;
    const tab = btn.dataset.tab;
    ui.settingsTabs.querySelectorAll(".settings-tab").forEach((x) => x.classList.toggle("active", x === btn));
    ui.settingsModal.querySelectorAll(".settings-pane").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === tab));
  });
}

async function bootstrap() {
  applyThemeFromStorage();
  await refreshRuntime();
  await refreshTools();
  await refreshStateVariables();
  await refreshSessions();
  await refreshHistory(true);
  await refreshWorkspace();
}

wireEvents();
bootstrap().catch((err) => setStatus(`Error: ${err.message}`, "error"));
