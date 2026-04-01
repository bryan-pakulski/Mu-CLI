const el = (id) => document.getElementById(id);
const ACTIVITY_STORAGE_KEY = "mucli_gui_activity_v1";

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
  pendingBySession: {},
  activityBySession: {},
  taskBySession: {},
  memoryBySession: {},
  lastMemoryToolBySession: {},
  taskPollTimer: null,
  memoryPollTimer: null,
  activeEventSource: null,
};

const ui = {
  apiBaseInput: el("apiBaseInput"),
  modelInput: el("modelInput"),
  agenticToggle: el("agenticToggle"),
  thinkingToggle: el("thinkingToggle"),
  agenticToggleSettings: el("agenticToggleSettings"),
  thinkingToggleSettings: el("thinkingToggleSettings"),
  applyRuntimeBtn: el("applyRuntimeBtn"),
  statusBadge: el("statusBadge"),
  sessionList: el("sessionList"),
  newSessionBtn: el("newSessionBtn"),
  feed: el("feed"),
  activityList: el("activityList"),
  activitySummary: el("activitySummary"),
  composer: el("composer"),
  messageInput: el("messageInput"),
  sendBtn: el("sendBtn"),
  menuBtn: el("menuBtn"),
  chatMenu: el("chatMenu"),
  memoryOption: el("memoryOption"),
  clearChatOption: el("clearChatOption"),
  fileBtn: el("fileBtn"),
  fileInput: el("fileInput"),
  attachMenu: el("attachMenu"),
  attachFileOption: el("attachFileOption"),
  attachFolderOption: el("attachFolderOption"),
  attachCloseOption: el("attachCloseOption"),
  workspaceStatus: el("workspaceStatus"),
  workspaceAddTrigger: el("workspaceAddTrigger"),
  workspaceFolders: el("workspaceFolders"),
  folderModal: el("folderModal"),
  folderPathInput: el("folderPathInput"),
  attachFolderConfirmBtn: el("attachFolderConfirmBtn"),
  closeFolderModalBtn: el("closeFolderModalBtn"),
  memoryModal: el("memoryModal"),
  memorySearchInput: el("memorySearchInput"),
  memoryRuntimeList: el("memoryRuntimeList"),
  memoryBufferList: el("memoryBufferList"),
  scratchpadBufferList: el("scratchpadBufferList"),
  memoryActivityList: el("memoryActivityList"),
  closeMemoryModalBtn: el("closeMemoryModalBtn"),
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

function loadPersistedActivity() {
  try {
    const raw = localStorage.getItem(ACTIVITY_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const cleaned = {};
    for (const [sessionName, items] of Object.entries(parsed)) {
      if (!Array.isArray(items)) continue;
      cleaned[sessionName] = items
        .filter((x) => x && typeof x.title === "string")
        .slice(-120)
        .map((x) => ({
          title: String(x.title || ""),
          detail: String(x.detail || ""),
          at: Number(x.at || Date.now()),
        }));
    }
    return cleaned;
  } catch {
    return {};
  }
}

function persistActivity() {
  try {
    localStorage.setItem(ACTIVITY_STORAGE_KEY, JSON.stringify(state.activityBySession || {}));
  } catch {
    return;
  }
}

state.activityBySession = loadPersistedActivity();

function sessionMemory(sessionName = state.currentSession) {
  if (!state.memoryBySession[sessionName]) state.memoryBySession[sessionName] = { runtime: {}, activity: [], buffer: [], scratchpad: [], query: "" };
  return state.memoryBySession[sessionName];
}

function applyThemeFromStorage() {
  const mode = localStorage.getItem("mucli_theme_mode") || "dark";
  const accent = localStorage.getItem("mucli_theme_accent") || "indigo";
  document.documentElement.dataset.mode = mode;
  document.documentElement.dataset.accent = accent;
  ui.themeModeSelect.value = mode;
  ui.accentSelect.value = accent;

  const darkCss = document.getElementById("hljsDark");
  const lightCss = document.getElementById("hljsLight");
  if (darkCss && lightCss) {
    const isDark = mode === "dark";
    darkCss.disabled = !isDark;
    lightCss.disabled = isDark;
  }
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

function stripLeakedDirectiveText(text) {
  const raw = String(text || "");
  const trimmed = raw.trimStart();
  if (!/^(FEATURE MODE DIRECTIVE:|SYSTEM PROMPT:)/i.test(trimmed)) return raw;
  const cut = raw.search(/\n{2,}/);
  if (cut < 0) return "";
  return raw.slice(cut).trim();
}

function normalizedMessages(history = []) {
  return history
    .filter((m) => ["user", "assistant"].includes(m.role))
    .map((m) => ({ role: m.role, text: stripLeakedDirectiveText(textFromParts(m.parts || [])) }))
    .filter((m) => m.text);
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

function normalizeNestedCodeFences(text) {
  const lines = String(text || "").split("\n");
  const fenceIndices = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (/^\s*```+/.test(lines[i])) fenceIndices.push(i);
  }
  if (fenceIndices.length < 3) return String(text || "");

  let i = 0;
  while (i < lines.length) {
    const openMatch = lines[i].match(/^(\s*)(`{3,})(.*)$/);
    if (!openMatch) {
      i += 1;
      continue;
    }
    const openLen = openMatch[2].length;
    const closeCandidates = [];
    for (let j = i + 1; j < lines.length; j += 1) {
      if (new RegExp(`^\\s*\`{${openLen},}\\s*$`).test(lines[j])) closeCandidates.push(j);
    }
    if (!closeCandidates.length) break;
    const close = closeCandidates[closeCandidates.length - 1];
    if (closeCandidates.length > 1) {
      lines[i] = `${openMatch[1]}${"`".repeat(openLen + 1)}${openMatch[3] || ""}`;
      lines[close] = lines[close].replace(new RegExp(`\`{${openLen},}`), "`".repeat(openLen + 1));
    }
    i = close + 1;
  }
  return lines.join("\n");
}

function renderMarkdown(container, text) {
  const normalizedText = normalizeNestedCodeFences(text);
  const rendered = window.marked ? window.marked.parse(normalizedText, { gfm: true, breaks: true }) : normalizedText;
  container.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(rendered) : rendered;
  container.querySelectorAll("pre code").forEach((block) => {
    window.hljs?.highlightElement(block);
    const pre = block.closest("pre");
    if (!pre || pre.querySelector(".copy-code-btn")) return;
    const btn = document.createElement("button");
    btn.className = "copy-code-btn";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => copyText(block.textContent || "", btn));
    pre.appendChild(btn);
  });
}


async function copyText(value, button) {
  try {
    await navigator.clipboard.writeText(value || "");
    if (button) {
      button.classList.add("copied");
      const original = button.textContent;
      button.textContent = "✓";
      setTimeout(() => {
        button.classList.remove("copied");
        button.textContent = original;
      }, 900);
    }
  } catch {
    if (button) {
      button.classList.add("copy-fail");
      setTimeout(() => button.classList.remove("copy-fail"), 900);
    }
  }
}

function sessionActivity(sessionName = state.currentSession) {
  if (!state.activityBySession[sessionName]) state.activityBySession[sessionName] = [];
  return state.activityBySession[sessionName];
}

function pushActivity(sessionName, title, detail = "") {
  const bucket = sessionActivity(sessionName);
  bucket.push({ title, detail, at: Date.now() });
  if (bucket.length > 120) bucket.splice(0, bucket.length - 120);
  persistActivity();
  if (sessionName === state.currentSession) renderActivityPanel();
}

function formatSince(ts) {
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

function renderActivityPanel() {
  const sessionName = state.currentSession;
  const items = sessionActivity(sessionName);
  const taskMeta = state.taskBySession[sessionName];
  ui.activityList.innerHTML = "";
  if (!items.length) {
    ui.activityList.innerHTML = '<div class="activity-empty">No recent activity yet.</div>';
  } else {
    for (const item of items.slice(-50).reverse()) {
      const card = document.createElement("article");
      card.className = "activity-item";
      card.innerHTML = `
        <div class="activity-title">${item.title}</div>
        ${item.detail ? `<div class="activity-detail">${item.detail}</div>` : ""}
        <div class="activity-time">${new Date(item.at).toLocaleTimeString()}</div>
      `;
      ui.activityList.appendChild(card);
    }
  }

  if (!taskMeta) {
    ui.activitySummary.textContent = "Idle";
  } else if (taskMeta.status === "running") {
    ui.activitySummary.textContent = `Thinking • ${formatSince(taskMeta.startedAt)}`;
  } else if (taskMeta.status === "awaiting_approval") {
    ui.activitySummary.textContent = "Awaiting approval";
  } else if (taskMeta.status === "awaiting_input") {
    ui.activitySummary.textContent = "Awaiting input";
  } else if (taskMeta.status === "error") {
    ui.activitySummary.textContent = "Error";
  } else {
    ui.activitySummary.textContent = "Done";
  }
}

function memoryToolName(name = "") {
  return /^(save_memory|search_memory|list_memory|save_scratchpad|search_scratchpad|list_scratchpad|clear_scratchpad)$/i.test(String(name || ""));
}

function pushMemoryEvent(sessionName, title, body = "") {
  const mem = sessionMemory(sessionName);
  mem.activity.push({ title, body, at: Date.now() });
  if (mem.activity.length > 80) mem.activity.splice(0, mem.activity.length - 80);
  if (!ui.memoryModal.classList.contains("hidden") && sessionName === state.currentSession) {
    renderMemoryModal();
  }
}

async function refreshMemoryRuntime(sessionName = state.currentSession) {
  try {
    const payload = await fetchJson("/api/state");
    const variables = payload.state?.variables || payload.variables || {};
    const filtered = Object.fromEntries(
      Object.entries(variables).filter(([k]) => /(memory|scratchpad|collat|compact)/i.test(k)),
    );
    sessionMemory(sessionName).runtime = filtered;
    if (!ui.memoryModal.classList.contains("hidden") && sessionName === state.currentSession) renderMemoryModal();
  } catch {
    return;
  }
}

function toolResultText(payload = {}) {
  const candidates = [
    payload.result,
    payload.output,
    payload.visible_result,
    payload.tool_result,
    payload.message,
    payload.data,
  ];
  for (const c of candidates) {
    if (typeof c === "string") return c;
    if (c && typeof c === "object") {
      if (typeof c.result === "string") return c.result;
      if (typeof c.output === "string") return c.output;
      if (typeof c.message === "string") return c.message;
    }
  }
  return JSON.stringify(payload || {}, null, 2);
}

function parseMemoryRows(raw) {
  const rows = String(raw || "").split("\n").map((x) => x.trim()).filter(Boolean);
  return rows.map((line) => {
    const match = line.match(/^#(\d+)\s+tags=(\[[^\]]*\])\s+source=([^:]+)\s+::\s+([\s\S]+)$/);
    if (!match) return { id: "", tags: [], source: "", content: line };
    let tags = [];
    try { tags = JSON.parse(match[2]); } catch { tags = []; }
    return {
      id: match[1],
      tags,
      source: match[3],
      content: match[4],
    };
  });
}

async function refreshMemoryBuffers(sessionName = state.currentSession) {
  try {
    const [memoryRes, scratchRes] = await Promise.all([
      fetchJson("/api/tool", { method: "POST", body: JSON.stringify({ tool_name: "list_memory", tool_args: { limit: 50 }, structured: false }) }),
      fetchJson("/api/tool", { method: "POST", body: JSON.stringify({ tool_name: "list_scratchpad", tool_args: { limit: 50 }, structured: false }) }),
    ]);
    const mem = sessionMemory(sessionName);
    mem.buffer = parseMemoryRows(toolResultText(memoryRes));
    mem.scratchpad = parseMemoryRows(toolResultText(scratchRes));
    if (!ui.memoryModal.classList.contains("hidden") && sessionName === state.currentSession) renderMemoryModal();
  } catch {
    return;
  }
}

function renderMemoryModal() {
  const mem = sessionMemory(state.currentSession);
  const query = String(mem.query || "").trim().toLowerCase();
  const filtered = (items) => {
    if (!query) return items;
    return items.filter((item) => {
      const hay = `${item.id} ${(item.tags || []).join(" ")} ${item.source} ${item.content}`.toLowerCase();
      return hay.includes(query);
    });
  };
  const runtimeEntries = Object.entries(mem.runtime || {});
  ui.memoryRuntimeList.innerHTML = runtimeEntries.length
    ? runtimeEntries.map(([k, v]) => `<article class="memory-item"><div class="memory-item-title">${k}</div><div class="memory-item-body">${String(v)}</div></article>`).join("")
    : '<div class="activity-empty">No runtime memory variables available.</div>';

  const memoryEntries = filtered(mem.buffer || []);
  ui.memoryBufferList.innerHTML = memoryEntries.length
    ? memoryEntries.map((e) => `<article class="memory-item"><div class="memory-item-title">#${e.id || "?"} ${(e.tags || []).length ? `· tags: ${(e.tags || []).join(", ")}` : ""}${e.source ? ` · source: ${e.source}` : ""}</div><div class="memory-item-body">${e.content || ""}</div></article>`).join("")
    : '<div class="activity-empty">No memory entries found.</div>';

  const scratchEntries = filtered(mem.scratchpad || []);
  ui.scratchpadBufferList.innerHTML = scratchEntries.length
    ? scratchEntries.map((e) => `<article class="memory-item"><div class="memory-item-title">#${e.id || "?"} ${(e.tags || []).length ? `· tags: ${(e.tags || []).join(", ")}` : ""}${e.source ? ` · source: ${e.source}` : ""}</div><div class="memory-item-body">${e.content || ""}</div></article>`).join("")
    : '<div class="activity-empty">No scratchpad entries found.</div>';

  ui.memoryActivityList.innerHTML = mem.activity.length
    ? mem.activity.slice(-60).reverse().map((e) => `<article class="memory-item"><div class="memory-item-title">${e.title} · ${new Date(e.at).toLocaleTimeString()}</div><div class="memory-item-body">${e.body || ""}</div></article>`).join("")
    : '<div class="activity-empty">No memory tool activity yet.</div>';
}

function openMemoryModal() {
  ui.memoryModal.classList.remove("hidden");
  ui.memorySearchInput.value = sessionMemory(state.currentSession).query || "";
  renderMemoryModal();
  refreshMemoryRuntime();
  refreshMemoryBuffers();
  if (state.memoryPollTimer) clearInterval(state.memoryPollTimer);
  state.memoryPollTimer = setInterval(() => {
    refreshMemoryRuntime();
    refreshMemoryBuffers();
  }, 2500);
}

function closeMemoryModal() {
  ui.memoryModal.classList.add("hidden");
  if (state.memoryPollTimer) {
    clearInterval(state.memoryPollTimer);
    state.memoryPollTimer = null;
  }
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
    if (item.role === "assistant") {
      const footer = document.createElement("div");
      footer.className = "message-footer";
      const copyBtn = document.createElement("button");
      copyBtn.className = "copy-msg-btn";
      copyBtn.textContent = "⧉";
      copyBtn.title = "Copy response";
      copyBtn.addEventListener("click", () => copyText(item.text, copyBtn));
      footer.appendChild(copyBtn);
      card.appendChild(footer);
    }
    ui.feed.appendChild(card);
  }

  const pending = state.pendingBySession[state.currentSession];
  if (pending) {
    const userCard = document.createElement("article");
    userCard.className = "message pending";
    userCard.innerHTML = `<span class="role">user</span><span class="text"></span>`;
    renderMarkdown(userCard.querySelector(".text"), pending.userText);
    ui.feed.appendChild(userCard);

    const aiCard = document.createElement("article");
    aiCard.className = "message pending";
    const latestActivity = pending.latestActivity || "Thinking through response";
    const runtimeMeta = pending.startedAt ? `Running for ${formatSince(pending.startedAt)}` : "Running";
    aiCard.innerHTML = `
      <span class="role">assistant</span>
      <span class="text">
        <span class="thinking-status"><span class="thinking-pulse"></span>${latestActivity}</span>
        <div class="thinking-meta">${runtimeMeta}</div>
      </span>
    `;
    ui.feed.appendChild(aiCard);
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
    ui.workspaceFolders.innerHTML = "";
    for (const folder of folders) {
      const row = document.createElement("div");
      row.className = "workspace-folder";
      row.innerHTML = `<span title="${folder}">${folder}</span><button class="workspace-remove" title="Remove">−</button>`;
      row.querySelector(".workspace-remove").addEventListener("click", async () => {
        await fetchJson("/api/workspaces/remove", { method: "POST", body: JSON.stringify({ path: folder }) });
        await refreshWorkspace();
      });
      ui.workspaceFolders.appendChild(row);
    }

    ui.workspaceStatus.textContent = folders.length
      ? `${folders.length} folder${folders.length === 1 ? "" : "s"} • ${tracked} tracked file${tracked === 1 ? "" : "s"}`
      : "No workspace attached";
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
    if (ui.agenticToggleSettings) ui.agenticToggleSettings.checked = !!runtime.agentic;
    if (ui.thinkingToggleSettings) ui.thinkingToggleSettings.checked = !!runtime.thinking;
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
      ui.sessionList.querySelectorAll(".session-item").forEach((i) => i.classList.remove("menu-open"));
      popup.classList.toggle("hidden");
      item.classList.toggle("menu-open", !popup.classList.contains("hidden"));
    });

    item.querySelector('[data-action="apply-rename"]').addEventListener("click", async () => {
      const newName = item.querySelector(".session-rename-input").value.trim();
      if (!newName || newName === name) return;
      await renameSession(name, newName);
    });

    item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      await deleteSession(name);
    });

    item.querySelector('[data-action="close"]').addEventListener("click", () => { popup.classList.add("hidden"); item.classList.remove("menu-open"); });
    ui.sessionList.appendChild(item);
  }
  renderActivityPanel();
}

async function refreshHistory(resetToBottom = true) {
  if (!state.currentSession) return;
  const payload = await fetchJson(`/api/history?limit=300&session_name=${encodeURIComponent(state.currentSession)}`);
  state.loadedMessages = normalizedMessages(payload.history || []);
  state.visibleCount = Math.min(24, state.loadedMessages.length || 24);
  renderFeed(resetToBottom);
  renderActivityPanel();
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
  if (state.activityBySession[currentName] && currentName !== newName.trim()) {
    state.activityBySession[newName.trim()] = state.activityBySession[currentName];
    delete state.activityBySession[currentName];
    persistActivity();
  }
  if (state.currentSession === currentName) state.currentSession = newName.trim();
  await refreshSessions();
  await refreshHistory(false);
}

async function deleteSession(name) {
  await fetchJson("/api/sessions/delete", { method: "POST", body: JSON.stringify({ name }) });
  if (state.activityBySession[name]) {
    delete state.activityBySession[name];
    persistActivity();
  }
  if (state.currentSession === name) state.currentSession = "";
  await refreshSessions();
  await refreshRuntime();
  await refreshHistory(false);
}

async function applyRuntime() {
  const agentic = ui.agenticToggleSettings?.checked ?? ui.agenticToggle.checked;
  const thinking = ui.thinkingToggleSettings?.checked ?? ui.thinkingToggle.checked;
  await fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ model: ui.modelInput.value, agentic, thinking }) });
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

async function clearConversationContext() {
  await fetchJson("/api/command", { method: "POST", body: JSON.stringify({ command: "/clear" }) });
  state.loadedMessages = [];
  delete state.pendingBySession[state.currentSession];
  state.taskBySession[state.currentSession] = { status: "completed", startedAt: Date.now(), taskId: "" };
  pushActivity(state.currentSession, "Conversation cleared", "Cleared chat history for the current session.");
  await refreshHistory(true);
}

function closeEventStream() {
  if (state.activeEventSource) {
    state.activeEventSource.close();
    state.activeEventSource = null;
  }
}

function mapEventToActivity(evt) {
  const payload = evt.payload || {};
  if (evt.event === "trace.tool") {
    return {
      title: `Using tool: ${payload.tool_name || "unknown"}`,
      detail: payload.visible_result ? String(payload.visible_result).slice(0, 220) : "",
    };
  }
  if (evt.event === "trace.tool_result") return { title: "Tool result", detail: payload.preview || "" };
  if (evt.event === "trace.info") return { title: "Info", detail: payload.message || "" };
  if (evt.event === "trace.error") return { title: "Error", detail: payload.message || "" };
  if (evt.event === "trace.message") return { title: `Model message (${payload.role || "assistant"})`, detail: String(payload.content || "").slice(0, 220) };
  if (evt.event === "task.awaiting_approval") return { title: "Awaiting approval", detail: "A tool call needs approval before continuing." };
  if (evt.event === "task.awaiting_input") return { title: "Awaiting input", detail: payload.blocker?.reason || "Task requires more input." };
  if (evt.event === "task.running") return { title: "Task running", detail: "" };
  if (evt.event === "task.completed") return { title: "Task complete", detail: "" };
  if (evt.event === "task.error") return { title: "Task error", detail: payload.error || "" };
  return null;
}

function startTaskEventStream(taskId, sessionName) {
  closeEventStream();
  const es = new EventSource(api(`/api/events?task_id=${encodeURIComponent(taskId)}`));
  state.activeEventSource = es;
  const handleEvent = (raw) => {
    if (!raw?.data) return;
    let evt;
    try {
      evt = JSON.parse(raw.data);
    } catch {
      return;
    }
    const mapped = mapEventToActivity(evt);
    if (!mapped) return;
    if (state.taskBySession[sessionName]) {
      if (evt.event === "task.completed") state.taskBySession[sessionName].status = "completed";
      if (evt.event === "task.error") state.taskBySession[sessionName].status = "error";
      if (evt.event === "task.awaiting_approval") state.taskBySession[sessionName].status = "awaiting_approval";
      if (evt.event === "task.awaiting_input") state.taskBySession[sessionName].status = "awaiting_input";
      if (evt.event === "task.running") state.taskBySession[sessionName].status = "running";
    }
    if (evt.event === "trace.tool" && memoryToolName(evt.payload?.tool_name)) {
      const toolName = String(evt.payload?.tool_name || "");
      state.lastMemoryToolBySession[sessionName] = toolName;
      pushMemoryEvent(sessionName, `Tool: ${toolName}`, JSON.stringify(evt.payload?.tool_args || {}, null, 2));
      refreshMemoryRuntime(sessionName);
      refreshMemoryBuffers(sessionName);
    }
    if (evt.event === "trace.tool_result" && state.lastMemoryToolBySession[sessionName]) {
      pushMemoryEvent(
        sessionName,
        `Result: ${state.lastMemoryToolBySession[sessionName]}`,
        String(evt.payload?.preview || evt.payload?.result || "").slice(0, 600),
      );
    }
    pushActivity(sessionName, mapped.title, mapped.detail);
    const pending = state.pendingBySession[sessionName];
    if (pending) pending.latestActivity = mapped.title;
    renderFeed(false);
  };
  [
    "stream.open",
    "task.created",
    "task.running",
    "task.awaiting_approval",
    "task.awaiting_input",
    "task.completed",
    "task.error",
    "trace.tool",
    "trace.tool_result",
    "trace.message",
    "trace.info",
    "trace.error",
  ].forEach((name) => es.addEventListener(name, handleEvent));
  es.onerror = () => {
    pushActivity(sessionName, "Activity stream disconnected", "Waiting for task status updates.");
    closeEventStream();
  };
}

function startTaskTicker(sessionName) {
  if (state.taskPollTimer) clearInterval(state.taskPollTimer);
  state.taskPollTimer = setInterval(() => {
    if (!state.pendingBySession[sessionName]) return;
    if (sessionName === state.currentSession) {
      renderFeed(false);
      renderActivityPanel();
    }
  }, 1000);
}

function stopTaskTicker() {
  if (state.taskPollTimer) {
    clearInterval(state.taskPollTimer);
    state.taskPollTimer = null;
  }
}

async function waitForTaskDone(taskId, sessionName) {
  while (true) {
    const payload = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`);
    const task = payload.task || {};
    const status = task.status || "pending";
    state.taskBySession[sessionName].status = status;
    renderActivityPanel();
    if (status === "completed" || status === "error") return task;
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

async function sendMessage(evt) {
  evt?.preventDefault();
  if (state.isSending) return;
  const text = ui.messageInput.value.trim();
  if (!text) return;

  const sessionAtSend = state.currentSession;
  state.pendingBySession[sessionAtSend] = { userText: text, latestActivity: "Queued", startedAt: Date.now() };
  state.taskBySession[sessionAtSend] = { status: "running", startedAt: Date.now(), taskId: "" };
  pushActivity(sessionAtSend, "Message sent", text.slice(0, 220));
  if (state.currentSession === sessionAtSend) renderFeed(true);

  state.isSending = true;
  ui.sendBtn.disabled = true;
  ui.messageInput.value = "";
  ui.messageInput.style.height = "auto";
  startTaskTicker(sessionAtSend);
  try {
    const start = await fetchJson("/api/message", { method: "POST", body: JSON.stringify({ text, session_name: sessionAtSend, async: true }) });
    const taskId = start.task?.task_id;
    if (!taskId) throw new Error("Task ID missing from async response.");
    state.taskBySession[sessionAtSend].taskId = taskId;
    state.pendingBySession[sessionAtSend].latestActivity = "Thinking";
    pushActivity(sessionAtSend, "Task started", `Task ID: ${taskId}`);
    startTaskEventStream(taskId, sessionAtSend);
    const finalTask = await waitForTaskDone(taskId, sessionAtSend);
    if (finalTask.status === "error") {
      throw new Error(finalTask.error || "Task failed.");
    }
    pushActivity(sessionAtSend, "Final response received", "");
  } finally {
    closeEventStream();
    stopTaskTicker();
    delete state.pendingBySession[sessionAtSend];
    state.isSending = false;
    ui.sendBtn.disabled = false;
    if (state.taskBySession[sessionAtSend]?.status === "running") {
      state.taskBySession[sessionAtSend].status = "completed";
    }
    if (state.currentSession === sessionAtSend) {
      await refreshHistory(true);
      await refreshWorkspace();
      renderActivityPanel();
    }
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
  ui.memoryOption.addEventListener("click", () => {
    ui.chatMenu.classList.add("hidden");
    openMemoryModal();
  });
  ui.clearChatOption.addEventListener("click", () => {
    ui.chatMenu.classList.add("hidden");
    clearConversationContext().catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.agenticToggle.addEventListener("change", () => { if (ui.agenticToggleSettings) ui.agenticToggleSettings.checked = ui.agenticToggle.checked; });
  ui.thinkingToggle.addEventListener("change", () => { if (ui.thinkingToggleSettings) ui.thinkingToggleSettings.checked = ui.thinkingToggle.checked; });
  document.addEventListener("click", () => {
    ui.chatMenu.classList.add("hidden");
    ui.attachMenu.classList.add("hidden");
    ui.sessionList.querySelectorAll(".session-popup").forEach((p) => p.classList.add("hidden"));
    ui.sessionList.querySelectorAll(".session-item").forEach((i) => i.classList.remove("menu-open"));
  });

  const autoResizeInput = () => {
    ui.messageInput.style.height = "auto";
    ui.messageInput.style.height = `${Math.min(ui.messageInput.scrollHeight, 180)}px`;
  };
  ui.messageInput.addEventListener("input", autoResizeInput);
  autoResizeInput();

  ui.fileBtn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    ui.chatMenu.classList.add("hidden");
    ui.attachMenu.classList.toggle("hidden");
  });
  ui.attachMenu.addEventListener("click", (evt) => evt.stopPropagation());
  ui.attachFileOption.addEventListener("click", () => {
    ui.attachMenu.classList.add("hidden");
    ui.fileInput.click();
  });
  ui.attachFolderOption.addEventListener("click", () => {
    ui.attachMenu.classList.add("hidden");
    ui.folderModal.classList.remove("hidden");
  });
  ui.attachCloseOption.addEventListener("click", () => ui.attachMenu.classList.add("hidden"));

  ui.fileInput.addEventListener("change", () => {
    const file = ui.fileInput.files?.[0];
    if (!file) return;
    const marker = `[attached file: ${file.name}]`;
    ui.messageInput.value = ui.messageInput.value.trim() ? `${ui.messageInput.value.trim()}
${marker}` : marker;
    autoResizeInput();
  });


  const openFolderModal = () => {
    ui.folderPathInput.value = "";
    ui.folderModal.classList.remove("hidden");
  };

  ui.workspaceAddTrigger.addEventListener("click", openFolderModal);
  ui.attachFolderConfirmBtn.addEventListener("click", async () => {
    const path = ui.folderPathInput.value.trim();
    if (!path) return;
    await fetchJson("/api/workspaces/add", { method: "POST", body: JSON.stringify({ path }) });
    ui.folderModal.classList.add("hidden");
    await refreshWorkspace();
  });
  ui.closeFolderModalBtn.addEventListener("click", () => ui.folderModal.classList.add("hidden"));
  ui.closeMemoryModalBtn.addEventListener("click", closeMemoryModal);
  ui.memorySearchInput.addEventListener("input", () => {
    sessionMemory(state.currentSession).query = ui.memorySearchInput.value || "";
    renderMemoryModal();
  });

  ui.settingsBtn.addEventListener("click", async () => {
    await refreshTools();
    await refreshRuntime();
    if (ui.agenticToggleSettings) ui.agenticToggleSettings.checked = ui.agenticToggle.checked;
    if (ui.thinkingToggleSettings) ui.thinkingToggleSettings.checked = ui.thinkingToggle.checked;
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
  await refreshMemoryRuntime();
  await refreshMemoryBuffers();
}

wireEvents();
bootstrap().catch((err) => setStatus(`Error: ${err.message}`, "error"));
