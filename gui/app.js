const el = (id) => document.getElementById(id);

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  events: [],
  eventSource: null,
  tools: [],
  runtime: null,
  sessions: [],
  currentSession: "",
  sessionStatus: {},
  pendingBySession: {},
  currentConversation: [],
  approvalsBySession: {},
  workspace: { folders: [], tracked_files: [] },
  serverActiveSession: "",
  modelsByProvider: {},
};
try {
  const persistedEvents = JSON.parse(localStorage.getItem("mucli_gui_events") || "[]");
  if (Array.isArray(persistedEvents)) state.events = persistedEvents.slice(0, 300);
} catch {
  state.events = [];
}

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
  agenticBasePromptInput: el("agenticBasePromptInput"),
  agenticModeDefaultInput: el("agenticModeDefaultInput"),
  agenticModeDebugInput: el("agenticModeDebugInput"),
  agenticModeFeatureInput: el("agenticModeFeatureInput"),
  agenticModeResearchInput: el("agenticModeResearchInput"),
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
  chooseWorkspaceBtn: el("chooseWorkspaceBtn"),
  chatWrap: el("chatWrap"),
  approvalOverlay: el("approvalOverlay"),
  approvalSummary: el("approvalSummary"),
  approvalReasonInput: el("approvalReasonInput"),
  approveBtn: el("approveBtn"),
  rejectBtn: el("rejectBtn"),
  explainBtn: el("explainBtn"),
  workspaceTree: el("workspaceTree"),
  workspaceAddInput: el("workspaceAddInput"),
  workspaceAddBtn: el("workspaceAddBtn"),
  workspaceFoldersList: el("workspaceFoldersList"),
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
  localStorage.setItem("mucli_gui_events", JSON.stringify(state.events));
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
  if (history) {
    state.currentConversation = history;
  }
  ui.chatStream.innerHTML = "";
  for (const message of state.currentConversation || []) {
    if (!["user", "assistant"].includes(message.role)) continue;
    const textParts = (message.parts || [])
      .filter((part) => part.type === "text")
      .map((part) => part.text || "")
      .filter(Boolean);
    if (!textParts.length) continue;

    const card = document.createElement("div");
    card.className = "event-card";
    card.innerHTML = `<div class="event-head"><span>${message.role}</span><button class="ghost-btn copy-entry-btn" title="Copy full message">Copy</button></div><div class="event-body markdown-body"></div>`;
    const fullText = textParts.join("\n\n");
    renderMarkdown(card.querySelector(".event-body"), fullText);
    const copyEntryBtn = card.querySelector(".copy-entry-btn");
    copyEntryBtn.addEventListener("click", async () => copyText(fullText, copyEntryBtn));
    ui.chatStream.appendChild(card);
  }

  const pending = state.pendingBySession[state.currentSession];
  if (pending) {
    const userCard = document.createElement("div");
    userCard.className = "event-card pending";
    userCard.innerHTML = `<div class="event-head"><span>user</span><button class="ghost-btn copy-entry-btn" title="Copy full message">Copy</button></div><div class="event-body markdown-body"></div>`;
    renderMarkdown(userCard.querySelector(".event-body"), pending.userText);
    const pendingCopyBtn = userCard.querySelector(".copy-entry-btn");
    pendingCopyBtn.addEventListener("click", async () => copyText(pending.userText, pendingCopyBtn));
    ui.chatStream.appendChild(userCard);

    const agentCard = document.createElement("div");
    agentCard.className = "event-card pending";
    agentCard.innerHTML = `<div class="event-head">assistant</div><div class="event-body"><span class="typing-dots"><span>.</span><span>.</span><span>.</span></span></div>`;
    ui.chatStream.appendChild(agentCard);
  }
  ui.chatStream.scrollTop = ui.chatStream.scrollHeight;
  renderApprovalOverlay();
}

function flashCopied(buttonEl, ok = true) {
  if (!buttonEl) return;
  const original = buttonEl.dataset.originalLabel || buttonEl.textContent || "Copy";
  if (!buttonEl.dataset.originalLabel) buttonEl.dataset.originalLabel = original;
  buttonEl.classList.remove("copied", "copy-failed");
  void buttonEl.offsetWidth;
  buttonEl.classList.add(ok ? "copied" : "copy-failed");
  buttonEl.textContent = ok ? "✓ Copied" : "Copy failed";
  setTimeout(() => {
    buttonEl.classList.remove("copied", "copy-failed");
    buttonEl.textContent = original;
  }, 1200);
}

async function copyText(text, sourceButton = null) {
  try {
    await navigator.clipboard.writeText(text || "");
    flashCopied(sourceButton, true);
    pushEvent("copy.success", "Copied to clipboard.");
  } catch (err) {
    flashCopied(sourceButton, false);
    pushEvent("copy.error", String(err));
  }
}

function renderMarkdown(container, text) {
  const source = String(text || "");
  const rendered = window.marked ? window.marked.parse(source, { gfm: true, breaks: true }) : source;
  const sanitized = window.DOMPurify ? window.DOMPurify.sanitize(rendered) : rendered;
  container.innerHTML = sanitized;
  container.querySelectorAll("pre code").forEach((block) => {
    if (window.hljs) window.hljs.highlightElement(block);
    const pre = block.closest("pre");
    if (!pre || pre.querySelector(".copy-code-btn")) return;
    const copyBtn = document.createElement("button");
    copyBtn.className = "ghost-btn copy-code-btn";
    copyBtn.textContent = "Copy code";
    copyBtn.addEventListener("click", async () => copyText(block.textContent || "", copyBtn));
    pre.appendChild(copyBtn);
  });
}

function renderApprovalOverlay() {
  const approval = state.approvalsBySession[state.currentSession];
  if (!approval) {
    ui.chatWrap.classList.remove("needs-approval");
    ui.approvalOverlay.classList.add("hidden");
    return;
  }
  ui.chatWrap.classList.add("needs-approval");
  ui.approvalOverlay.classList.remove("hidden");
  ui.approvalSummary.textContent = [
    `Tool: ${approval.tool_name || "unknown"}`,
    `Prompt: ${approval.prompt_text || ""}`,
    `Args: ${JSON.stringify(approval.display_args || approval.tool_args || {}, null, 2)}`,
  ].join("\n\n");
}

async function refreshHistory(sessionName = state.currentSession) {
  try {
    if (!sessionName) return;
    const payload = await fetchJson(`/api/history?limit=120&session_name=${encodeURIComponent(sessionName)}`);
    if (sessionName === state.currentSession) {
      renderConversation(payload.history || []);
    }
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
    state.serverActiveSession = runtime.session_name || "";
    if (!state.currentSession) state.currentSession = state.serverActiveSession;
    await refreshModels(runtime.provider);
    setModelSelectOptions(ui.modelInput, state.modelsByProvider[runtime.provider] || [], runtime.model || "");
    setModelSelectOptions(ui.settingsModelInput, state.modelsByProvider[runtime.provider] || [], runtime.model || "");
    ui.agenticToggle.checked = !!runtime.agentic;
    ui.thinkingToggle.checked = !!runtime.thinking;
    ui.systemPromptInput.value = runtime.system_instruction || "";
    ui.agenticBasePromptInput.value = runtime.agentic_system_base || "";
    ui.agenticModeDefaultInput.value = runtime.agentic_mode_prompts?.default || "";
    ui.agenticModeDebugInput.value = runtime.agentic_mode_prompts?.debug || "";
    ui.agenticModeFeatureInput.value = runtime.agentic_mode_prompts?.feature || "";
    ui.agenticModeResearchInput.value = runtime.agentic_mode_prompts?.research || "";

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

function setModelSelectOptions(selectEl, models, selectedValue = "") {
  const options = Array.isArray(models) ? models.filter(Boolean) : [];
  const current = String(selectedValue || "").trim();
  const uniq = [...new Set(options)];
  if (current && !uniq.includes(current)) uniq.unshift(current);
  if (!uniq.length) uniq.push(current || "");

  selectEl.innerHTML = "";
  for (const model of uniq) {
    const opt = document.createElement("option");
    opt.value = model;
    opt.textContent = model || "(provider default)";
    if (model === current) opt.selected = true;
    selectEl.appendChild(opt);
  }
}

async function refreshModels(providerName = state.runtime?.provider || "openai") {
  const provider = String(providerName || "").trim();
  if (!provider) return;
  try {
    const data = await fetchJson(`/api/models?provider=${encodeURIComponent(provider)}`);
    state.modelsByProvider[provider] = data.models || [];
    if (data.error) {
      pushEvent("models.warning", `${provider}: ${data.error}`);
    }
  } catch (err) {
    pushEvent("models.error", String(err));
    if (!state.modelsByProvider[provider]) state.modelsByProvider[provider] = [];
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
    const candidate = state.currentSession || data.current_session_name || "";
    state.currentSession = state.sessions.includes(candidate) ? candidate : (data.current_session_name || state.sessions[0] || "");
    for (const name of state.sessions) {
      if (!state.sessionStatus[name]) state.sessionStatus[name] = "idle";
    }
    renderSessionTabs();
  } catch (err) {
    pushEvent("sessions.error", String(err));
  }
}

function normalizePath(value) {
  return String(value || "").replace(/\\/g, "/").replace(/\/+$/, "");
}

function buildWorkspaceTree(folders, trackedFiles) {
  const root = {};
  const normalizedFolders = folders.map(normalizePath);
  for (const filePath of trackedFiles || []) {
    const normalizedFile = normalizePath(filePath);
    const baseFolder = normalizedFolders.find((folder) => normalizedFile.startsWith(`${folder}/`) || normalizedFile === folder);
    if (!baseFolder) continue;
    const relPath = normalizedFile.slice(baseFolder.length + 1);
    if (!relPath) continue;
    const segments = relPath.split("/").filter(Boolean);
    let cursor = root[baseFolder] || (root[baseFolder] = { __files: [], __dirs: {} });
    for (let i = 0; i < segments.length; i += 1) {
      const seg = segments[i];
      const isLeaf = i === segments.length - 1;
      if (isLeaf) {
        cursor.__files.push(seg);
      } else {
        cursor.__dirs[seg] = cursor.__dirs[seg] || { __files: [], __dirs: {} };
        cursor = cursor.__dirs[seg];
      }
    }
  }
  return root;
}

function renderTreeNode(container, name, node) {
  const details = document.createElement("details");
  details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = `📁 ${name}`;
  details.appendChild(summary);
  const dirNames = Object.keys(node.__dirs || {}).sort((a, b) => a.localeCompare(b));
  for (const dir of dirNames) {
    renderTreeNode(details, dir, node.__dirs[dir]);
  }
  const files = [...(node.__files || [])].sort((a, b) => a.localeCompare(b));
  for (const file of files) {
    const row = document.createElement("div");
    row.className = "tree-file";
    row.textContent = `📄 ${file}`;
    details.appendChild(row);
  }
  container.appendChild(details);
}

function renderWorkspace() {
  const folders = state.workspace.folders || [];
  const tracked = state.workspace.tracked_files || [];
  ui.workspaceTree.innerHTML = "";
  ui.workspaceFoldersList.innerHTML = "";

  if (!folders.length) {
    ui.workspaceTree.textContent = "No folders attached.";
    return;
  }

  const tree = buildWorkspaceTree(folders, tracked);
  for (const folder of folders) {
    const folderNode = tree[normalizePath(folder)] || { __files: [], __dirs: {} };
    renderTreeNode(ui.workspaceTree, folder.split("/").filter(Boolean).pop() || folder, folderNode);

    const row = document.createElement("div");
    row.className = "workspace-folder-row";
    const label = document.createElement("div");
    label.className = "desc";
    label.textContent = folder;
    const removeBtn = document.createElement("button");
    removeBtn.className = "ghost-btn workspace-remove-btn";
    removeBtn.textContent = "− Remove";
    removeBtn.addEventListener("click", async () => {
      await removeWorkspaceFolder(folder);
    });
    row.appendChild(label);
    row.appendChild(removeBtn);
    ui.workspaceFoldersList.appendChild(row);
  }
}

async function refreshWorkspace() {
  try {
    const data = await fetchJson("/api/workspaces");
    state.workspace = {
      folders: data.folders || [],
      tracked_files: data.tracked_files || [],
    };
    renderWorkspace();
  } catch (err) {
    pushEvent("workspace.error", String(err));
  }
}

async function addWorkspaceFolder(path) {
  const value = String(path || "").trim();
  if (!value) return;
  try {
    await fetchJson("/api/workspaces/add", { method: "POST", body: JSON.stringify({ path: value }) });
    ui.workspaceAddInput.value = "";
    pushEvent("workspace.added", value);
    await refreshWorkspace();
  } catch (err) {
    pushEvent("workspace.add_error", String(err));
  }
}

async function removeWorkspaceFolder(path) {
  try {
    await fetchJson("/api/workspaces/remove", { method: "POST", body: JSON.stringify({ path }) });
    pushEvent("workspace.removed", path);
    await refreshWorkspace();
  } catch (err) {
    pushEvent("workspace.remove_error", String(err));
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
    const status = state.sessionStatus[sessionName] || "idle";
    loadBtn.innerHTML = `<span class="status-light ${status}"></span>${sessionName}`;
    loadBtn.title = sessionName;
    loadBtn.addEventListener("click", () => loadSession(sessionName));

    const menuBtn = document.createElement("button");
    menuBtn.className = "menu-btn";
    menuBtn.textContent = "⋯";
    menuBtn.title = "Session actions";
    const dropdown = document.createElement("div");
    dropdown.className = "session-menu";

    const editRow = document.createElement("div");
    editRow.className = "session-menu-row";
    editRow.innerHTML = "<span>Edit Name</span>";
    const renameInput = document.createElement("input");
    renameInput.type = "text";
    renameInput.className = "session-rename-input";
    renameInput.value = sessionName;
    renameInput.addEventListener("keydown", async (evt) => {
      if (evt.key === "Enter") {
        await renameSession(sessionName, renameInput.value.trim());
        dropdown.classList.remove("open");
      }
    });
    renameInput.addEventListener("blur", async () => {
      if (renameInput.value.trim() && renameInput.value.trim() !== sessionName) {
        await renameSession(sessionName, renameInput.value.trim());
      }
    });
    editRow.appendChild(renameInput);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "session-delete-btn";
    deleteBtn.innerHTML = "🗑 Delete";
    deleteBtn.addEventListener("click", async () => {
      await deleteSession(sessionName);
      dropdown.classList.remove("open");
    });

    dropdown.appendChild(editRow);
    dropdown.appendChild(deleteBtn);
    menuBtn.addEventListener("click", (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      document.querySelectorAll(".session-menu.open").forEach((m) => m.classList.remove("open"));
      dropdown.classList.toggle("open");
    });
    dropdown.addEventListener("click", (evt) => evt.stopPropagation());

    wrapper.appendChild(loadBtn);
    wrapper.appendChild(menuBtn);
    wrapper.appendChild(dropdown);
    ui.sessionTabs.appendChild(wrapper);
  }
}

async function loadSession(name) {
  state.currentSession = name;
  renderSessionTabs();
  await refreshHistory(name);
  renderApprovalOverlay();
  if (name === state.serverActiveSession) return;
  try {
    await fetchJson("/api/sessions/load", { method: "POST", body: JSON.stringify({ name }) });
    state.serverActiveSession = name;
    pushEvent("session.loaded", name);
    await refreshRuntime();
    await refreshSessions();
    await refreshHistory(name);
    await refreshWorkspace();
    renderApprovalOverlay();
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

async function renameSession(name, newName) {
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
  const sessionName = state.currentSession;
  ui.messageInput.value = "";
  pushEvent("message.out", text);
  if (state.currentSession && state.currentSession !== state.serverActiveSession) {
    await loadSession(state.currentSession);
    if (state.currentSession !== state.serverActiveSession) {
      pushEvent("message.error", "Could not activate selected session. Message not sent.");
      return;
    }
  }
  state.pendingBySession[sessionName] = { userText: text };
  state.sessionStatus[sessionName] = "thinking";
  renderSessionTabs();
  renderConversation();
  try {
    const result = await fetchJson("/api/message", {
      method: "POST",
      body: JSON.stringify({ text, async: true }),
    });
    const taskId = result?.task?.task_id;
    if (!taskId) {
      delete state.pendingBySession[sessionName];
      state.sessionStatus[sessionName] = "idle";
      await refreshHistory();
      renderSessionTabs();
      renderConversation();
      return;
    }
    await monitorMessageTask(taskId, sessionName);
  } catch (err) {
    pushEvent("message.error", String(err));
    delete state.pendingBySession[sessionName];
    state.sessionStatus[sessionName] = "error";
    renderSessionTabs();
    renderConversation();
  }
}

async function monitorMessageTask(taskId, sessionName) {
  for (let i = 0; i < 240; i += 1) {
    const taskPayload = await fetchJson(`/api/tasks/${taskId}`);
    const task = taskPayload.task || {};
    if (task.status === "awaiting_approval") {
      state.sessionStatus[sessionName] = "approval";
      if (task.approval_id) {
        try {
          const approvalPayload = await fetchJson(`/api/approvals/${task.approval_id}`);
          state.approvalsBySession[sessionName] = approvalPayload.approval;
        } catch (err) {
          pushEvent("approval.fetch_error", String(err));
        }
      }
      renderSessionTabs();
      renderApprovalOverlay();
    } else if (task.status === "running" || task.status === "pending") {
      state.sessionStatus[sessionName] = "thinking";
      delete state.approvalsBySession[sessionName];
      renderSessionTabs();
      renderApprovalOverlay();
    } else if (task.status === "completed") {
      delete state.pendingBySession[sessionName];
      delete state.approvalsBySession[sessionName];
      state.sessionStatus[sessionName] = "idle";
      if (state.currentSession === sessionName) {
        await refreshHistory();
      }
      renderSessionTabs();
      renderConversation();
      return;
    } else if (task.status === "error") {
      delete state.pendingBySession[sessionName];
      delete state.approvalsBySession[sessionName];
      state.sessionStatus[sessionName] = "error";
      renderSessionTabs();
      renderConversation();
      pushEvent("message.error", task.error || "Task failed");
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
  state.sessionStatus[sessionName] = "error";
  renderSessionTabs();
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
        agentic_system_base: ui.agenticBasePromptInput.value,
        agentic_mode_prompts: {
          default: ui.agenticModeDefaultInput.value,
          debug: ui.agenticModeDebugInput.value,
          feature: ui.agenticModeFeatureInput.value,
          research: ui.agenticModeResearchInput.value,
        },
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

async function resolveApproval(decision) {
  const approval = state.approvalsBySession[state.currentSession];
  if (!approval) return;
  try {
    await fetchJson("/api/approvals/resolve", {
      method: "POST",
      body: JSON.stringify({
        approval_id: approval.approval_id,
        decision,
        reason: ui.approvalReasonInput.value.trim() || undefined,
      }),
    });
    pushEvent("approval.resolved", { decision, approval_id: approval.approval_id });
    delete state.approvalsBySession[state.currentSession];
    renderApprovalOverlay();
  } catch (err) {
    pushEvent("approval.resolve_error", String(err));
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
    localStorage.removeItem("mucli_gui_events");
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
    await refreshModels(state.runtime?.provider);
  });

  document.querySelectorAll(".cmd-btn").forEach((btn) => btn.addEventListener("click", () => runCommand(btn.dataset.cmd)));
  ui.chooseWorkspaceBtn.addEventListener("click", async () => {
    const path = window.prompt("Workspace folder path");
    if (!path) return;
    await addWorkspaceFolder(path);
  });
  ui.approveBtn.addEventListener("click", () => resolveApproval("approve"));
  ui.rejectBtn.addEventListener("click", () => resolveApproval("reject"));
  ui.explainBtn.addEventListener("click", () => resolveApproval("explain"));

  ui.openSettingsBtn.addEventListener("click", async () => {
    openModal();
    await refreshRuntime();
    await refreshTools();
    await refreshModels(state.runtime?.provider);
    await refreshWorkspace();
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
      document.querySelectorAll(".session-menu.open").forEach((m) => m.classList.remove("open"));
    }
    if ((evt.metaKey || evt.ctrlKey) && evt.key === "Enter") sendMessage();
  });
  ui.saveSettingsBtn.addEventListener("click", saveSettings);
  ui.workspaceAddBtn.addEventListener("click", async () => addWorkspaceFolder(ui.workspaceAddInput.value));
  ui.workspaceAddInput.addEventListener("keydown", async (evt) => {
    if (evt.key === "Enter") {
      evt.preventDefault();
      await addWorkspaceFolder(ui.workspaceAddInput.value);
    }
  });

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
  ui.newSessionBtn.addEventListener("click", async () => {
    const provider = ui.newSessionProviderSelect.value || state.runtime?.provider || "openai";
    await refreshModels(provider);
    setModelSelectOptions(ui.newSessionModelInput, state.modelsByProvider[provider] || [], "");
  });
  ui.newSessionProviderSelect.addEventListener("change", async () => {
    const provider = ui.newSessionProviderSelect.value || state.runtime?.provider || "openai";
    await refreshModels(provider);
    setModelSelectOptions(ui.newSessionModelInput, state.modelsByProvider[provider] || [], "");
  });
  ui.closeNewSessionBtn.addEventListener("click", () => closeSimpleModal(ui.newSessionModal));
  ui.newSessionModal.addEventListener("click", (evt) => {
    if (evt.target === ui.newSessionModal) closeSimpleModal(ui.newSessionModal);
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
  document.addEventListener("click", () => {
    document.querySelectorAll(".session-menu.open").forEach((m) => m.classList.remove("open"));
  });
}

setupHandlers();
setupTabSwitching();
applyTheme();
renderEvents();
connectSSE();
refreshRuntime();
refreshTools();
refreshSessions();
refreshHistory();
refreshStats();
refreshWorkspace();
setInterval(() => {
  refreshRuntime();
  refreshTools();
  refreshSessions();
  refreshStats();
  refreshWorkspace();
}, 12000);
