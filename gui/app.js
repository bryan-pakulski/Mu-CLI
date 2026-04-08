const el = (id) => document.getElementById(id);
const ACTIVITY_STORAGE_KEY = "mucli_gui_activity_v1";
const SESSIONS_STORAGE_KEY = "mucli_gui_sessions_v1";
const DRAFTS_STORAGE_KEY = "mucli_gui_drafts_v1";
const PENDING_STORAGE_KEY = "mucli_gui_pending_v1";
const BOARD_MODE_STORAGE_KEY = "mucli_gui_board_modes_v1";
const THEME_MODE_KEY = "mucli_theme_mode";
const THEME_ACCENT_KEY = "mucli_theme_accent_value";

const PRESET_ACCENTS = [
  { name: "Zinc", value: "#71717a" },
  { name: "Red", value: "#ef4444" },
  { name: "Orange", value: "#f97316" },
  { name: "Rose", value: "#f43f5e" },
  { name: "Green", value: "#22c55e" },
  { name: "Blue", value: "#3b82f6" },
  { name: "Yellow", value: "#eab308" },
  { name: "Violet", value: "#8b5cf6" },
  { name: "Cyan", value: "#06b6d4" },
  { name: "Emerald", value: "#10b981" },
];
const VARIABLE_HELP = {
  agent_mode: "Selects assistant operating strategy (default/debug/feature/research).",
  ollama_host: "Base URL for Ollama provider requests.",
  strict_mode: "When enabled, tool usage is treated more conservatively.",
  max_iterations: "Maximum internal loop/tool iterations per request.",
  compact_history: "Compacts prior history to reduce token usage.",
  yolo: "Bypass approvals (high risk).",
  make_timeout: "Timeout (seconds) for long-running execution tools.",
  make_max_output: "Maximum captured output length from execution tools.",
  collation_enabled: "Enable read-result collation before flush.",
  collation_flush_command: "Command alias used to flush collated context.",
  memory_enabled: "Enable durable cross-turn task memory.",
  memory_max_entries: "Maximum retained memory entries.",
  memory_summary_limit: "How many memory items are summarized in prompts.",
  scratchpad_enabled: "Enable temporary per-turn scratchpad notes.",
  scratchpad_max_entries: "Maximum retained scratchpad entries.",
  tool_context_window: "Recent tool result window to keep in context.",
  context_token_limit: "Hard token budget for assembled context.",
  context_trim_threshold: "Fraction of budget at which trimming starts.",
  structured_tool_results: "Prefer structured tool outputs over raw blobs.",
};

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  currentSession: "",
  currentView: "chat",
  sessions: [],
  serverSession: "",
  runtime: null,
  tools: [],
  allVariables: {},
  loadedMessages: [],
  visibleCount: 24,
  pendingBySession: {},
  sendQueue: [],
  draftBySession: {},
  openSessionMenu: "",
  activityBySession: {},
  activityTagFilterBySession: {},
  taskBySession: {},
  errorSignatureBySession: {},
  runErrorBySession: {},
  memoryBySession: {},
  lastMemoryToolBySession: {},
  pendingApprovals: [],
  taskPollTimersBySession: {},
  memoryPollTimer: null,
  approvalPollTimer: null,
  serverTaskPollTimer: null,
  eventSourceBySession: {},
  thinkingPlaceholderTimer: null,
  board: {
    modeBySession: {},
    planBySession: {},
    featureListBySession: {},
    selectedFeatureIdBySession: {},
    selectedTaskIdBySession: {},
    featureDraftBySession: {},
    filterBySession: {},
    pollTimer: null,
    stream: null,
    refreshQueued: false,
    refreshInFlight: false,
    phaseOpenBySession: {},
    drag: null,
  },
};

const ui = {
  apiBaseInput: el("apiBaseInput"),
  modelInput: el("modelInput"),
  agenticToggle: el("agenticToggle"),
  thinkingToggle: el("thinkingToggle"),
  yoloToggle: el("yoloToggle"),
  agentModeSelect: el("agentModeSelect"),
  chatFeatureWrap: el("chatFeatureWrap"),
  chatFeatureSelect: el("chatFeatureSelect"),
  agenticToggleSettings: el("agenticToggleSettings"),
  thinkingToggleSettings: el("thinkingToggleSettings"),
  applyRuntimeBtn: el("applyRuntimeBtn"),
  statusBadge: el("statusBadge"),
  topFlash: el("topFlash"),
  sessionList: el("sessionList"),
  newSessionBtn: el("newSessionBtn"),
  feed: el("feed"),
  approvalBar: el("approvalBar"),
  approvalText: el("approvalText"),
  approvalApproveBtn: el("approvalApproveBtn"),
  approvalRejectBtn: el("approvalRejectBtn"),
  approvalExplainBtn: el("approvalExplainBtn"),
  activityList: el("activityList"),
  activitySummary: el("activitySummary"),
  activityFilters: el("activityFilters"),
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
  browseFolderBtn: el("browseFolderBtn"),
  folderUpBtn: el("folderUpBtn"),
  folderBrowserPath: el("folderBrowserPath"),
  folderBrowserList: el("folderBrowserList"),
  attachFolderConfirmBtn: el("attachFolderConfirmBtn"),
  closeFolderModalBtn: el("closeFolderModalBtn"),
  memoryModal: el("memoryModal"),
  memorySearchInput: el("memorySearchInput"),
  memoryBufferList: el("memoryBufferList"),
  scratchpadBufferList: el("scratchpadBufferList"),
  memoryLayersList: el("memoryLayersList"),
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
  themeModeDark: el("themeModeDark"),
  themeModeLight: el("themeModeLight"),
  accentSwatches: el("accentSwatches"),
  accentCustomBtn: el("accentCustomBtn"),
  customAccentInput: el("customAccentInput"),
  chatViewBtn: el("chatViewBtn"),
  boardViewBtn: el("boardViewBtn"),
  chatView: el("chatView"),
  boardView: el("boardView"),
  chatBoardSwitch: el("chatBoardSwitch"),
  chatToggleLabel: el("chatToggleLabel"),
  boardToggleLabel: el("boardToggleLabel"),
  centerPanel: document.querySelector(".center-panel"),
  boardSummary: el("boardSummary"),
  boardFeatureCard: el("boardFeatureCard"),
  boardFeatureMenu: el("boardFeatureMenu"),
  boardFeatureActionsBtn: el("boardFeatureActionsBtn"),
  boardFeatureActionsMenu: el("boardFeatureActionsMenu"),
  boardRunBtn: el("boardRunBtn"),
  boardFeatureArchiveBtn: el("boardFeatureArchiveBtn"),
  boardFeatureDeleteBtn: el("boardFeatureDeleteBtn"),
  boardFeatureUnloadBtn: el("boardFeatureUnloadBtn"),
  boardSearchInput: el("boardSearchInput"),
  boardPhaseFilter: el("boardPhaseFilter"),
  boardRefreshBtn: el("boardRefreshBtn"),
  boardLanes: el("boardLanes"),
  boardError: el("boardError"),
  ticketModal: el("ticketModal"),
  ticketTitle: el("ticketTitle"),
  ticketStatusPill: el("ticketStatusPill"),
  ticketTitleInput: el("ticketTitleInput"),
  ticketOverviewInput: el("ticketOverviewInput"),
  ticketObjectivesInput: el("ticketObjectivesInput"),
  ticketActionsInput: el("ticketActionsInput"),
  ticketExitInput: el("ticketExitInput"),
  ticketNotesInput: el("ticketNotesInput"),
  ticketMeta: el("ticketMeta"),
  ticketEvents: el("ticketEvents"),
  ticketSaveBtn: el("ticketSaveBtn"),
  ticketCloseBtn: el("ticketCloseBtn"),
  createFeatureModal: el("createFeatureModal"),
  createFeatureNameInput: el("createFeatureNameInput"),
  featureEpicList: el("featureEpicList"),
  addEpicBtn: el("addEpicBtn"),
  saveFeatureDraftBtn: el("saveFeatureDraftBtn"),
  createFeatureCancelBtn: el("createFeatureCancelBtn"),
  createFeatureStubBtn: el("createFeatureStubBtn"),
};

ui.apiBaseInput.value = state.apiBase;
try {
  const cached = JSON.parse(localStorage.getItem(SESSIONS_STORAGE_KEY) || "{}");
  if (Array.isArray(cached.sessions)) state.sessions = cached.sessions;
  if (cached.currentSession) state.currentSession = cached.currentSession;
} catch {
  // ignore session cache parse errors
}
try {
  const drafts = JSON.parse(localStorage.getItem(DRAFTS_STORAGE_KEY) || "{}");
  if (drafts && typeof drafts === "object") state.draftBySession = drafts;
} catch {
  state.draftBySession = {};
}
{
  const restored = loadPersistedPendingState();
  if (restored.pending && typeof restored.pending === "object") {
    state.pendingBySession = restored.pending;
  }
  if (restored.tasks && typeof restored.tasks === "object") {
    state.taskBySession = restored.tasks;
  }
}
try {
  const boardModes = JSON.parse(localStorage.getItem(BOARD_MODE_STORAGE_KEY) || "{}");
  if (boardModes && typeof boardModes === "object") state.board.modeBySession = boardModes;
} catch {
  state.board.modeBySession = {};
}

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

function loadPersistedPendingState() {
  try {
    const raw = JSON.parse(localStorage.getItem(PENDING_STORAGE_KEY) || "{}");
    const pendingRaw = raw.pending && typeof raw.pending === "object" ? raw.pending : {};
    const pending = {};
    const cutoff = Date.now() - 6 * 60 * 60 * 1000;
    for (const [sessionName, entry] of Object.entries(pendingRaw)) {
      const startedAt = Number(entry?.startedAt || 0);
      if (startedAt && startedAt < cutoff) continue;
      pending[sessionName] = entry;
    }
    const tasks = raw.tasks && typeof raw.tasks === "object" ? raw.tasks : {};
    return { pending, tasks };
  } catch {
    return { pending: {}, tasks: {} };
  }
}

function persistPendingState() {
  try {
    localStorage.setItem(
      PENDING_STORAGE_KEY,
      JSON.stringify({
        pending: state.pendingBySession || {},
        tasks: state.taskBySession || {},
      })
    );
  } catch {
    return;
  }
}

state.activityBySession = loadPersistedActivity();

function persistDrafts() {
  try {
    localStorage.setItem(DRAFTS_STORAGE_KEY, JSON.stringify(state.draftBySession || {}));
  } catch {
    return;
  }
}

function sessionMemory(sessionName = state.currentSession) {
  if (!state.memoryBySession[sessionName]) state.memoryBySession[sessionName] = { runtime: {}, activity: [], buffer: [], scratchpad: [], layers: [], query: "" };
  return state.memoryBySession[sessionName];
}

function hexToHsl(hexColor) {
  const hex = String(hexColor || "").replace("#", "").trim();
  if (![3, 6].includes(hex.length)) return null;
  const full = hex.length === 3 ? hex.split("").map((c) => c + c).join("") : hex;
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
  }
  h = Math.round(h * 60);
  if (h < 0) h += 360;
  const l = (max + min) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  return { h, s: s * 100, l: l * 100 };
}

function applyThemeFromStorage() {
  const mode = localStorage.getItem(THEME_MODE_KEY) || "dark";
  const legacyAccent = localStorage.getItem("mucli_theme_accent");
  const accentMap = { indigo: "#8b5cf6", teal: "#14b8a6", amber: "#f59e0b", rose: "#f43f5e" };
  const accent = localStorage.getItem(THEME_ACCENT_KEY) || accentMap[legacyAccent] || "#8b5cf6";
  document.documentElement.dataset.mode = mode;
  document.documentElement.style.setProperty("--accent", accent);
  const hsl = hexToHsl(accent);
  if (hsl) {
    const lanePending = `hsl(${hsl.h} ${Math.min(100, hsl.s + 10).toFixed(0)}% ${Math.max(46, hsl.l + 6).toFixed(0)}%)`;
    const laneInProgress = `hsl(${(hsl.h + 58) % 360} 86% 56%)`;
    const laneBlocked = `hsl(${(hsl.h + 132) % 360} 92% 56%)`;
    const laneCompleted = `hsl(${(hsl.h + 176) % 360} 78% 52%)`;
    document.documentElement.style.setProperty("--lane-pending", lanePending);
    document.documentElement.style.setProperty("--lane-in-progress", laneInProgress);
    document.documentElement.style.setProperty("--lane-blocked", laneBlocked);
    document.documentElement.style.setProperty("--lane-completed", laneCompleted);
  }
  ui.customAccentInput.value = accent;
  ui.themeModeDark?.classList.toggle("active", mode === "dark");
  ui.themeModeLight?.classList.toggle("active", mode === "light");
  const presetValues = PRESET_ACCENTS.map((item) => item.value.toLowerCase());
  ui.accentCustomBtn?.classList.toggle("active", !presetValues.includes(accent.toLowerCase()));
  ui.accentSwatches?.querySelectorAll(".accent-chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.value?.toLowerCase() === accent.toLowerCase());
  });

  const darkCss = document.getElementById("hljsDark");
  const lightCss = document.getElementById("hljsLight");
  if (darkCss && lightCss) {
    const isDark = mode === "dark";
    darkCss.disabled = !isDark;
    lightCss.disabled = isDark;
  }
}

function renderAccentSwatches() {
  if (!ui.accentSwatches) return;
  ui.accentSwatches.innerHTML = "";
  for (const accent of PRESET_ACCENTS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "accent-chip";
    btn.dataset.value = accent.value;
    btn.innerHTML = `<span class="accent-dot" style="--swatch:${accent.value}"></span>${accent.name}`;
    btn.addEventListener("click", () => {
      localStorage.setItem(THEME_ACCENT_KEY, accent.value);
      applyThemeFromStorage();
    });
    ui.accentSwatches.appendChild(btn);
  }
}

function api(path) {
  return `${state.apiBase}${path}`;
}

async function fetchJson(path, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let resp;
  try {
    const method = (options.method || "GET").toUpperCase();
    const headers = { ...(options.headers || {}) };
    if (options.body && !headers["Content-Type"] && method !== "GET") {
      headers["Content-Type"] = "application/json";
    }
    try {
      resp = await fetch(api(path), { ...options, method, headers, signal: controller.signal });
    } catch (err) {
      if (err?.name === "AbortError") {
        throw new Error(`Request timed out after ${Math.ceil(timeoutMs / 1000)}s.`);
      }
      throw err;
    }
  } finally {
    clearTimeout(timeout);
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function setStatus(text, kind = "") {
  ui.statusBadge.classList.remove("connected", "error", "warning");
  if (kind) ui.statusBadge.classList.add(kind);
  ui.statusBadge.textContent = text;
  window.dispatchEvent(
    new CustomEvent("mucli:status", {
      detail: { text: String(text || ""), kind: String(kind || "") },
    }),
  );
  if (!["error", "warning"].includes(String(kind || "").toLowerCase())) return;
  if (!ui.topFlash) return;
  ui.topFlash.classList.remove("connected", "error", "warning", "show", "hidden");
  if (kind) ui.topFlash.classList.add(kind);
  ui.topFlash.textContent = text;
  requestAnimationFrame(() => ui.topFlash.classList.add("show"));
  const durationMs = kind === "error" ? 4200 : 2400;
  if (setStatus._timer) clearTimeout(setStatus._timer);
  setStatus._timer = setTimeout(() => {
    ui.topFlash.classList.remove("show");
    setTimeout(() => ui.topFlash?.classList.add("hidden"), 260);
  }, durationMs);
}

function showModal(modalEl) {
  if (!modalEl) return;
  modalEl.classList.remove("closing", "hidden");
}

function hideModal(modalEl) {
  if (!modalEl || modalEl.classList.contains("hidden")) return;
  modalEl.classList.add("closing");
  setTimeout(() => {
    modalEl.classList.add("hidden");
    modalEl.classList.remove("closing");
  }, 180);
}

function renderFeatureSelectors(sessionName = state.currentSession) {
  const list = state.board.featureListBySession[sessionName] || [];
  const selectedId = String(state.board.selectedFeatureIdBySession[sessionName] || "").trim();
  const options = ['<option value="">Active feature</option>']
    .concat(list.map((feature) => {
      const id = String(feature.feature_id || "");
      const status = String(feature.status || "").trim();
      return `<option value="${id}">${feature.feature_name || id}${status ? ` (${status})` : ""}</option>`;
    }))
    .join("");
  if (ui.boardFeatureCard) {
    const record = selectedFeatureRecord(sessionName);
    const title = record?.feature_name || record?.feature_id || (list.length ? "Load feature" : "Load feature");
    const status = String(record?.status || "").trim();
    ui.boardFeatureCard.textContent = status ? `${title} (${status})` : title;
  }
  if (ui.boardFeatureMenu) {
    ui.boardFeatureMenu.innerHTML = "";
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "board-feature-option active";
      empty.textContent = "No features available";
      ui.boardFeatureMenu.appendChild(empty);
      const createBtn = document.createElement("button");
      createBtn.type = "button";
      createBtn.className = "board-feature-option";
      createBtn.textContent = "Create feature";
      createBtn.addEventListener("click", (evt) => {
        evt.stopPropagation();
        ui.boardFeatureMenu?.classList.add("hidden");
        currentFeatureDraft(sessionName);
        renderFeatureDraftEditor(sessionName);
        showModal(ui.createFeatureModal);
      });
      ui.boardFeatureMenu.appendChild(createBtn);
    }
    for (const feature of list) {
      const id = String(feature.feature_id || "");
      const status = String(feature.status || "").trim();
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `board-feature-option ${id === selectedId ? "active" : ""}`;
      btn.textContent = `${feature.feature_name || id}${status ? ` (${status})` : ""}`;
      btn.addEventListener("click", async (evt) => {
        evt.stopPropagation();
        ui.boardFeatureMenu?.classList.add("hidden");
        await activateFeature(id);
      });
      ui.boardFeatureMenu.appendChild(btn);
    }
  }
  if (ui.chatFeatureSelect) {
    ui.chatFeatureSelect.innerHTML = options;
    ui.chatFeatureSelect.value = selectedId;
  }
  const featureModeEnabled = String(ui.agentModeSelect?.value || "default") === "feature";
  ui.chatFeatureWrap?.classList.toggle("hidden", !featureModeEnabled);
  const record = selectedFeatureRecord(sessionName);
  if (ui.boardFeatureArchiveBtn) ui.boardFeatureArchiveBtn.disabled = !record || String(record.status || "").toLowerCase() === "archived";
  if (ui.boardFeatureDeleteBtn) ui.boardFeatureDeleteBtn.disabled = !record;
  if (ui.boardFeatureActionsBtn) ui.boardFeatureActionsBtn.disabled = !record;
  if (ui.boardRunBtn) ui.boardRunBtn.disabled = !record && !state.pendingBySession[sessionName];
}

function selectedFeatureRecord(sessionName = state.currentSession) {
  const featureId = String(state.board.selectedFeatureIdBySession[sessionName] || "").trim();
  const list = state.board.featureListBySession[sessionName] || [];
  if (featureId) return list.find((feature) => String(feature.feature_id || "") === featureId) || null;
  return list.find((feature) => String(feature.active || "").toLowerCase() === "true") || null;
}

function selectedFeatureArchived(sessionName = state.currentSession) {
  const record = selectedFeatureRecord(sessionName);
  return String(record?.status || "").toLowerCase() === "archived";
}

function slugifyFeatureId(value = "") {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48) || `feature_${Date.now()}`;
}

function currentFeatureDraft(sessionName = state.currentSession) {
  if (!state.board.featureDraftBySession[sessionName]) {
    state.board.featureDraftBySession[sessionName] = {
      name: "",
      epics: [{ title: "Epic 1", tasks: [""] }],
      status: "draft",
    };
  }
  return state.board.featureDraftBySession[sessionName];
}

function renderFeatureDraftEditor(sessionName = state.currentSession) {
  if (!ui.featureEpicList) return;
  const draft = currentFeatureDraft(sessionName);
  ui.createFeatureNameInput.value = draft.name || "";
  ui.featureEpicList.innerHTML = "";
  draft.epics.forEach((epic, epicIdx) => {
    const card = document.createElement("section");
    card.className = "feature-epic-card";
    card.innerHTML = `
      <div class="feature-epic-head">
        <input class="feature-epic-title" value="${epic.title || ""}" placeholder="Epic title" />
        <button class="btn feature-epic-delete" type="button">−</button>
      </div>
      <div class="feature-task-list"></div>
      <button class="btn feature-task-add" type="button">+ Add task</button>
    `;
    const titleInput = card.querySelector(".feature-epic-title");
    titleInput.addEventListener("input", () => {
      draft.epics[epicIdx].title = titleInput.value;
    });
    card.querySelector(".feature-epic-delete").addEventListener("click", () => {
      draft.epics.splice(epicIdx, 1);
      if (!draft.epics.length) draft.epics.push({ title: "Epic 1", tasks: [""] });
      renderFeatureDraftEditor(sessionName);
    });
    const list = card.querySelector(".feature-task-list");
    (epic.tasks || []).forEach((taskTitle, taskIdx) => {
      const row = document.createElement("div");
      row.className = "feature-task-row";
      row.innerHTML = `
        <input class="feature-task-input" value="${taskTitle || ""}" placeholder="Task title" />
        <button class="btn feature-task-delete" type="button">−</button>
      `;
      const input = row.querySelector(".feature-task-input");
      input.addEventListener("input", () => {
        draft.epics[epicIdx].tasks[taskIdx] = input.value;
      });
      row.querySelector(".feature-task-delete").addEventListener("click", () => {
        draft.epics[epicIdx].tasks.splice(taskIdx, 1);
        if (!draft.epics[epicIdx].tasks.length) draft.epics[epicIdx].tasks.push("");
        renderFeatureDraftEditor(sessionName);
      });
      list.appendChild(row);
    });
    card.querySelector(".feature-task-add").addEventListener("click", () => {
      draft.epics[epicIdx].tasks.push("");
      renderFeatureDraftEditor(sessionName);
    });
    ui.featureEpicList.appendChild(card);
  });
}

async function confirmFeatureDraft(sessionName = state.currentSession) {
  const draft = currentFeatureDraft(sessionName);
  draft.name = String(ui.createFeatureNameInput?.value || "").trim();
  if (!draft.name) throw new Error("Feature name is required.");
  const featureId = slugifyFeatureId(draft.name);
  await fetchJson("/api/tool", {
    method: "POST",
    body: JSON.stringify({
      tool_name: "create_feature",
      tool_args: {
        feature_name: draft.name,
        feature_id: featureId,
        feature_request: draft.name,
        design_plan: "Manual draft created in board UI",
      },
    }),
  });
  const phases = draft.epics
    .map((epic, index) => ({ id: index + 1, title: String(epic.title || `Epic ${index + 1}`).trim(), goal: "", order: index + 1 }))
    .filter((phase) => phase.title);
  if (phases.length) {
    await fetchJson("/api/tool", {
      method: "POST",
      body: JSON.stringify({
        tool_name: "create_phases",
        tool_args: { feature_id: featureId, phases },
      }),
    });
  }
  for (const [idx, epic] of draft.epics.entries()) {
    for (const taskTitle of epic.tasks || []) {
      const cleanTitle = String(taskTitle || "").trim();
      if (!cleanTitle) continue;
      await fetchJson("/api/tool", {
        method: "POST",
        body: JSON.stringify({
          tool_name: "create_task",
          tool_args: { feature_id: featureId, phase_id: idx + 1, title: cleanTitle, overview: "", design: [], exit_criteria: [] },
        }),
      });
    }
  }
  draft.status = "approved";
  await activateFeature(featureId);
  await refreshBoardData({ force: true });
  setStatus(`Feature "${draft.name}" created and approved.`, "connected");
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

function canonicalMessageText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function normalizeMessageTimestamp(rawTs) {
  const n = Number(rawTs);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n > 10_000_000_000 ? n : n * 1000;
}

function formatMessageTime(rawTs) {
  const ts = normalizeMessageTimestamp(rawTs);
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString();
}

function normalizedMessages(history = []) {
  const normalized = history
    .filter((m) => ["user", "assistant"].includes(m.role))
    .map((m) => ({
      role: m.role,
      text: stripLeakedDirectiveText(textFromParts(m.parts || [])),
      at: normalizeMessageTimestamp(m.timestamp ?? m.created_at ?? m.at),
    }))
    .filter((m) => m.text);
  const deduped = [];
  for (const msg of normalized) {
    const prev = deduped[deduped.length - 1];
    if (
      prev
      && prev.role === msg.role
      && canonicalMessageText(prev.text) === canonicalMessageText(msg.text)
    ) {
      continue;
    }
    deduped.push(msg);
  }
  return deduped;
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

function normalizeActivityTag(rawTag = "") {
  const value = String(rawTag || "").trim().toLowerCase();
  return value || "general";
}

function inferActivityTag(title = "", detail = "") {
  const haystack = `${title}\n${detail}`.toLowerCase();
  if (/\berror\b|failed|exception|cancelled/.test(haystack)) return "error";
  if (/\btool\b/.test(haystack)) return "tool";
  if (/queued|queue/.test(haystack)) return "queue";
  if (/approval|awaiting input|task/.test(haystack)) return "task";
  if (/memory|scratchpad/.test(haystack)) return "memory";
  if (/workspace|session|feature|board/.test(haystack)) return "system";
  return "general";
}

function pushActivity(sessionName, title, detail = "", tag = "") {
  const bucket = sessionActivity(sessionName);
  bucket.push({ title, detail, at: Date.now(), tag: normalizeActivityTag(tag || inferActivityTag(title, detail)) });
  if (bucket.length > 120) bucket.splice(0, bucket.length - 120);
  persistActivity();
  if (sessionName === state.currentSession) renderActivityPanel();
}

function appendChatErrorEntry(sessionName, message, detail = "") {
  const text = `⚠️ Execution failed\n${message || "Unknown error"}${detail ? `\n\n${detail}` : ""}`;
  const signature = `${sessionName}:${text}`;
  if (state.errorSignatureBySession[sessionName] === signature) return;
  state.errorSignatureBySession[sessionName] = signature;
  state.loadedMessages.push({ role: "assistant", text, at: Date.now() });
  if (state.currentSession === sessionName) renderFeed(true);
}

function rememberRunError(sessionName, message, detail = "") {
  state.runErrorBySession[sessionName] = {
    message: String(message || "Unknown error"),
    detail: String(detail || ""),
  };
}

function formatSince(ts) {
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

function activityTagClass(tag) {
  const normalized = normalizeActivityTag(tag);
  return ["error", "tool", "task", "queue", "memory", "system", "general"].includes(normalized) ? normalized : "general";
}

function ensureActivityFilter(sessionName, tags = []) {
  if (!state.activityTagFilterBySession[sessionName]) state.activityTagFilterBySession[sessionName] = {};
  const filter = state.activityTagFilterBySession[sessionName];
  for (const tag of tags) {
    const normalized = normalizeActivityTag(tag);
    if (!(normalized in filter)) filter[normalized] = true;
  }
  return filter;
}

function renderActivityFilters(sessionName, tags = []) {
  if (!ui.activityFilters) return;
  const uniqueTags = [...new Set(tags.map((tag) => normalizeActivityTag(tag)))];
  const filter = ensureActivityFilter(sessionName, uniqueTags);
  ui.activityFilters.innerHTML = "";
  for (const tag of uniqueTags) {
    const btn = document.createElement("button");
    const enabled = !!filter[tag];
    btn.type = "button";
    btn.className = `activity-filter-chip tag-${activityTagClass(tag)} ${enabled ? "active" : ""}`;
    btn.textContent = `${enabled ? "☑" : "☐"} ${tag}`;
    btn.title = enabled ? `Hide ${tag} events` : `Show ${tag} events`;
    btn.addEventListener("click", () => {
      filter[tag] = !filter[tag];
      renderActivityPanel();
    });
    ui.activityFilters.appendChild(btn);
  }
}

function renderActivityPanel() {
  const sessionName = state.currentSession;
  const items = sessionActivity(sessionName);
  const knownTags = ["error", "task", "tool", "queue", "memory", "system", "general"];
  const allTags = items.map((item) => normalizeActivityTag(item.tag || inferActivityTag(item.title, item.detail)));
  renderActivityFilters(sessionName, [...knownTags, ...allTags]);
  const filter = ensureActivityFilter(sessionName, [...knownTags, ...allTags]);
  const filteredItems = items.filter((item) => {
    const tag = normalizeActivityTag(item.tag || inferActivityTag(item.title, item.detail));
    return filter[tag] !== false;
  });
  const taskMeta = state.taskBySession[sessionName];
  ui.activityList.innerHTML = "";
  if (!filteredItems.length) {
    ui.activityList.innerHTML = '<div class="activity-empty">No recent activity yet.</div>';
  } else {
    for (const item of filteredItems.slice(-50).reverse()) {
      const tag = normalizeActivityTag(item.tag || inferActivityTag(item.title, item.detail));
      const card = document.createElement("article");
      card.className = `activity-item tag-${activityTagClass(tag)}`;
      card.innerHTML = `
        <div class="activity-top">
          <span class="activity-tag tag-${activityTagClass(tag)}">${tag}</span>
          <div class="activity-title">${item.title}</div>
        </div>
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

function boardMode(sessionName = state.currentSession) {
  return state.board.modeBySession[sessionName] || "chat";
}

function persistBoardModes() {
  try {
    localStorage.setItem(BOARD_MODE_STORAGE_KEY, JSON.stringify(state.board.modeBySession || {}));
  } catch {
    return;
  }
}

function setBoardError(message = "") {
  if (!ui.boardError) return;
  if (!message) {
    ui.boardError.classList.add("hidden");
    ui.boardError.textContent = "";
    return;
  }
  ui.boardError.classList.remove("hidden");
  ui.boardError.textContent = message;
}

function boardFilters(sessionName = state.currentSession) {
  if (!state.board.filterBySession[sessionName]) {
    state.board.filterBySession[sessionName] = {
      search: "",
      phase: "",
    };
  }
  return state.board.filterBySession[sessionName];
}

function setViewMode(mode, sessionName = state.currentSession) {
  if (ui.centerPanel) {
    ui.centerPanel.classList.remove("view-switching");
    void ui.centerPanel.offsetWidth;
    ui.centerPanel.classList.add("view-switching");
    if (setViewMode._fadeTimer) clearTimeout(setViewMode._fadeTimer);
    setViewMode._fadeTimer = setTimeout(() => ui.centerPanel?.classList.remove("view-switching"), 240);
  }
  state.currentView = mode;
  state.board.modeBySession[sessionName] = mode;
  persistBoardModes();
  const boardActive = mode === "board";
  if (ui.centerPanel) {
    ui.centerPanel.dataset.view = boardActive ? "board" : "chat";
  }
  ui.chatViewBtn?.classList.toggle("active", !boardActive);
  ui.boardViewBtn?.classList.toggle("active", boardActive);
  ui.chatView?.classList.toggle("hidden", boardActive);
  ui.boardView?.classList.toggle("hidden", !boardActive);
  if (ui.chatBoardSwitch) ui.chatBoardSwitch.checked = boardActive;
  ui.chatToggleLabel?.classList.toggle("active", !boardActive);
  ui.boardToggleLabel?.classList.toggle("active", boardActive);
  ui.boardFeatureMenu?.classList.add("hidden");
  ui.boardFeatureActionsMenu?.classList.add("hidden");
  if (ui.chatView) ui.chatView.style.display = boardActive ? "none" : "";
  if (ui.boardView) ui.boardView.style.display = boardActive ? "" : "none";
  if (boardActive) renderBoard();
}

function allowedTransitions(status) {
  const model = {
    pending: ["in_progress", "blocked", "completed"],
    not_started: ["in_progress", "blocked", "completed"],
    in_progress: ["blocked", "completed", "not_started"],
    blocked: ["in_progress", "not_started"],
    completed: ["archived", "in_progress", "not_started"],
    archived: [],
  };
  return model[String(status || "").toLowerCase()] || [];
}

function laneForStatus(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "in_progress") return "in_progress";
  if (normalized === "blocked") return "blocked";
  if (normalized === "completed") return "completed";
  return "pending";
}

function currentBoardPlan(sessionName = state.currentSession) {
  return state.board.planBySession[sessionName] || null;
}

async function moveBoardTask(taskId, status) {
  if (selectedFeatureArchived()) {
    setBoardError("Archived features are read-only.");
    return;
  }
  try {
    const result = await fetchJson("/api/command", {
      method: "POST",
      body: JSON.stringify({ command: `/feature move ${taskId} ${status}` }),
    });
    if (!result?.ok) throw new Error(result?.message || "Unable to move task.");
    setBoardError("");
    await refreshBoardData({ force: true });
  } catch (err) {
    setBoardError(`Could not move task: ${err.message}`);
  }
}

function findTaskById(plan, taskId) {
  const phases = Array.isArray(plan?.phases) ? plan.phases : [];
  return phases.find((task) => Number(task.id) === Number(taskId) || Number(task.number) === Number(taskId));
}

function findPhaseForTask(plan, task) {
  if (!task) return null;
  const phasesMeta = Array.isArray(plan?.phases_meta) ? plan.phases_meta : [];
  return phasesMeta.find((phase) => Number(phase.id) === Number(task.phase_id)) || null;
}

function filteredBoardTasks(plan, filters) {
  const all = Array.isArray(plan?.phases) ? plan.phases : [];
  return all.filter((task) => {
    if (filters.phase && String(task.phase_id || "") !== String(filters.phase)) return false;
    if (!filters.search) return true;
    const hay = `${task.title || ""} ${(task.overview || "")} ${(task.notes || "")}`.toLowerCase();
    return hay.includes(filters.search.toLowerCase());
  });
}

function openTicketModal(taskId) {
  const plan = currentBoardPlan();
  const task = findTaskById(plan, taskId);
  if (!task) return;
  const phase = findPhaseForTask(plan, task);
  const events = (plan.event_log || []).filter((evt) => Number(evt.entity_id) === Number(task.id)).slice(-12).reverse();
  const editable = !selectedFeatureArchived() && ["pending", "not_started"].includes(String(task.status || "").toLowerCase());

  state.board.selectedTaskIdBySession[state.currentSession] = Number(task.id);
  ui.ticketTitle.textContent = `Task ${task.id}: ${task.title || ""}`;
  ui.ticketStatusPill.textContent = task.status || "unknown";
  ui.ticketTitleInput.value = task.title || "";
  ui.ticketOverviewInput.value = task.overview || task.notes || "";
  ui.ticketObjectivesInput.value = (task.objectives || []).join("\n");
  ui.ticketActionsInput.value = (task.action_points || []).join("\n");
  ui.ticketExitInput.value = (task.exit_criteria || []).join("\n");
  ui.ticketNotesInput.value = task.notes || "";
  [ui.ticketTitleInput, ui.ticketOverviewInput, ui.ticketObjectivesInput, ui.ticketActionsInput, ui.ticketExitInput, ui.ticketNotesInput]
    .forEach((el) => { el.disabled = !editable; });
  ui.ticketSaveBtn.disabled = !editable;

  ui.ticketMeta.innerHTML = `
    <div class="settings-row"><span>Status</span><span>${task.status || "unknown"}</span></div>
    <div class="settings-row"><span>Phase</span><span>${phase?.title || `#${task.phase_id || "-"}`}</span></div>
    <div class="settings-row"><span>Task ID</span><span>${task.id}</span></div>
    <div class="settings-row"><span>Editable</span><span>${editable ? "Yes" : "No (only pending and non-archived feature)"}</span></div>
  `;
  ui.ticketEvents.innerHTML = events.length
    ? events.map((evt) => `<div class="settings-row"><span>${evt.kind || evt.type || "event"}</span><span>${new Date((evt.created_at || Date.now() / 1000) * 1000).toLocaleTimeString()}</span></div>`).join("")
    : '<div class="activity-empty">No task events.</div>';
  showModal(ui.ticketModal);
}

async function saveTicketEdits() {
  const taskId = Number(state.board.selectedTaskIdBySession[state.currentSession] || 0);
  if (!taskId) return;
  const payload = {
    tool_name: "update_feature_task",
    tool_args: {
      task_id: taskId,
      title: ui.ticketTitleInput.value.trim(),
      objectives: ui.ticketObjectivesInput.value.split("\n").map((x) => x.trim()).filter(Boolean),
      action_points: ui.ticketActionsInput.value.split("\n").map((x) => x.trim()).filter(Boolean),
      exit_criteria: ui.ticketExitInput.value.split("\n").map((x) => x.trim()).filter(Boolean),
      notes: ui.ticketNotesInput.value.trim(),
    },
    structured: true,
  };
  try {
    const result = await fetchJson("/api/tool", { method: "POST", body: JSON.stringify(payload) }, 6000);
    const toolResult = result?.result || result;
    if (toolResult?.ok === false) throw new Error(toolResult?.error || "Save failed.");
    setBoardError("");
    await refreshBoardData({ force: true });
    hideModal(ui.ticketModal);
  } catch (err) {
    setBoardError(`Could not save ticket edits: ${err.message}`);
  }
}

function renderBoard() {
  if (ui.boardView?.classList.contains("hidden")) return;
  const runPending = !!state.pendingBySession[state.currentSession];
  if (ui.boardRunBtn) {
    ui.boardRunBtn.textContent = runPending ? "■ Stop" : "▶ Play";
    ui.boardRunBtn.classList.toggle("running", runPending);
    ui.boardRunBtn.title = runPending ? "Stop active feature run" : "Start feature run";
  }
  const plan = currentBoardPlan();
  renderFeatureSelectors(state.currentSession);
  const featureIsArchived = selectedFeatureArchived(state.currentSession);
  if (!plan) {
    const draft = state.board.featureDraftBySession[state.currentSession];
    ui.boardSummary.textContent = draft?.name ? `Draft: ${draft.name}` : "No active feature.";
    ui.boardLanes.innerHTML = '<div class="board-empty">No feature plan available for this session.</div>';
    return;
  }
  const filters = boardFilters();
  ui.boardSummary.textContent = `${plan.feature_name || plan.feature_id || "Feature"} · ${plan.task_count || 0} tasks`;
  const phasesMeta = Array.isArray(plan.phases_meta) ? plan.phases_meta : [];
  ui.boardPhaseFilter.innerHTML = `<option value="">All phases</option>${phasesMeta.map((p) => `<option value="${p.id}">${p.title || `Phase ${p.id}`}</option>`).join("")}`;
  ui.boardPhaseFilter.value = filters.phase || "";
  ui.boardSearchInput.value = filters.search || "";

  const tasks = filteredBoardTasks(plan, filters);
  const lanes = {
    pending: tasks.filter((t) => laneForStatus(t.status) === "pending"),
    in_progress: tasks.filter((t) => laneForStatus(t.status) === "in_progress"),
    blocked: tasks.filter((t) => laneForStatus(t.status) === "blocked"),
    completed: tasks.filter((t) => laneForStatus(t.status) === "completed"),
  };
  const laneOrder = [
    ["pending", "Pending"],
    ["in_progress", "In Progress"],
    ["blocked", "Blocked"],
    ["completed", "Completed"],
  ];
  ui.boardLanes.innerHTML = "";
  const phaseOpenMap = state.board.phaseOpenBySession[state.currentSession] || {};
  for (const [laneId, laneLabel] of laneOrder) {
    const laneEl = document.createElement("section");
    laneEl.className = `board-lane lane-${laneId}`;
    laneEl.dataset.laneId = laneId;
    laneEl.innerHTML = `<div class="board-lane-head"><span>${laneLabel}${featureIsArchived ? ' <span class="lane-lock" title="Feature archived">🔒</span>' : ""}</span><span class="board-lane-count">${lanes[laneId].length}</span></div><div class="board-lane-body"></div>`;
    const laneBody = laneEl.querySelector(".board-lane-body");
    const byPhase = new Map();
    for (const task of lanes[laneId]) {
      const pid = String(task.phase_id || "none");
      if (!byPhase.has(pid)) byPhase.set(pid, []);
      byPhase.get(pid).push(task);
    }
    if (!byPhase.size) {
      laneBody.innerHTML = '<div class="board-empty">No tasks in this lane.</div>';
    } else {
      for (const [phaseId, items] of byPhase.entries()) {
        const phaseMeta = phasesMeta.find((p) => String(p.id) === phaseId);
        const wrap = document.createElement("details");
        wrap.className = "phase-group";
        const phaseKey = `${laneId}:${phaseId}`;
        wrap.open = phaseOpenMap[phaseKey] !== false;
        wrap.addEventListener("toggle", () => {
          if (!state.board.phaseOpenBySession[state.currentSession]) {
            state.board.phaseOpenBySession[state.currentSession] = {};
          }
          state.board.phaseOpenBySession[state.currentSession][phaseKey] = wrap.open;
        });
        wrap.innerHTML = `<summary><span>${phaseMeta?.title || "Unassigned"}</span><span>${items.length}</span></summary><div class="phase-group-cards"></div>`;
        const cardWrap = wrap.querySelector(".phase-group-cards");
        for (const task of items) {
          const card = document.createElement("article");
          const selected = Number(state.board.selectedTaskIdBySession[state.currentSession]) === Number(task.id);
          card.className = `task-card${selected ? " active" : ""}`;
          card.draggable = !featureIsArchived;
          card.dataset.taskId = String(task.id);
          card.dataset.currentLane = laneForStatus(task.status);
          card.innerHTML = `
            <div class="task-title">${task.title || `Task ${task.id}`}</div>
            <div class="task-meta">#${task.id} · ${task.status || "unknown"} · Drag to move</div>
          `;

          let dragged = false;
          let startX = 0;
          let startY = 0;
          card.addEventListener("pointerdown", (evt) => {
            dragged = false;
            startX = evt.clientX;
            startY = evt.clientY;
          });
          card.addEventListener("pointermove", (evt) => {
            if (Math.abs(evt.clientX - startX) + Math.abs(evt.clientY - startY) > 6) {
              dragged = true;
            }
          });
          card.addEventListener("click", () => {
            if (dragged) return;
            state.board.selectedTaskIdBySession[state.currentSession] = Number(task.id);
            openTicketModal(task.id);
          });

          card.addEventListener("dragstart", (evt) => {
            const allowed = allowedTransitions(task.status)
              .map((target) => laneForStatus(target))
              .filter((lane) => lane && lane !== laneForStatus(task.status));
            state.board.drag = {
              taskId: Number(task.id),
              allowedLanes: new Set(allowed),
            };
            card.classList.add("dragging");
            laneBody.classList.add("drag-origin");
            evt.dataTransfer.effectAllowed = "move";
            evt.dataTransfer.setData("text/plain", String(task.id));
            for (const laneNode of ui.boardLanes.querySelectorAll(".board-lane")) {
              if (state.board.drag.allowedLanes.has(laneNode.dataset.laneId)) {
                laneNode.classList.add("drop-allowed");
              }
            }
          });
          card.addEventListener("dragend", () => {
            card.classList.remove("dragging");
            for (const laneNode of ui.boardLanes.querySelectorAll(".board-lane")) {
              laneNode.classList.remove("drop-allowed", "drop-active");
            }
            laneBody.classList.remove("drag-origin");
            state.board.drag = null;
          });
          cardWrap.appendChild(card);
        }
        laneBody.appendChild(wrap);
      }
    }

    laneBody.addEventListener("dragover", (evt) => {
      const dragState = state.board.drag;
      if (!dragState || !dragState.allowedLanes?.has(laneId)) return;
      evt.preventDefault();
      evt.dataTransfer.dropEffect = "move";
      laneEl.classList.add("drop-active");
    });
    laneBody.addEventListener("dragleave", () => {
      laneEl.classList.remove("drop-active");
    });
    laneBody.addEventListener("drop", async (evt) => {
      const dragState = state.board.drag;
      laneEl.classList.remove("drop-active");
      if (!dragState || !dragState.allowedLanes?.has(laneId)) return;
      evt.preventDefault();
      const targetByLane = {
        pending: "pending",
        in_progress: "in_progress",
        blocked: "blocked",
        completed: "completed",
      };
      const targetStatus = targetByLane[laneId];
      if (!targetStatus) return;
      await moveBoardTask(dragState.taskId, targetStatus);
    });

    ui.boardLanes.appendChild(laneEl);
  }
}

function relevantBoardEvent(evt) {
  const eventName = String(evt?.event || "");
  if (/^task\./.test(eventName)) return true;
  if (eventName === "command.completed") {
    const command = String(evt?.payload?.command || "");
    return command.startsWith("/feature");
  }
  return false;
}

function scheduleBoardRefresh() {
  if (state.board.refreshQueued) return;
  state.board.refreshQueued = true;
  setTimeout(async () => {
    state.board.refreshQueued = false;
    await refreshBoardData();
  }, 250);
}

function startBoardEventStream() {
  if (state.board.stream) return;
  const es = new EventSource(api("/api/events"));
  state.board.stream = es;
  const handle = (raw) => {
    if (!raw?.data) return;
    try {
      const evt = JSON.parse(raw.data);
      if (relevantBoardEvent(evt)) scheduleBoardRefresh();
    } catch {
      return;
    }
  };
  ["task.created", "task.running", "task.awaiting_input", "task.completed", "task.error", "command.completed"].forEach((name) => es.addEventListener(name, handle));
  es.onerror = () => {
    if (state.board.stream) {
      state.board.stream.close();
      state.board.stream = null;
    }
    setTimeout(startBoardEventStream, 1500);
  };
}

async function refreshBoardData({ force = false } = {}) {
  const sessionName = state.currentSession;
  if (!sessionName) return;
  if (!force && state.currentView !== "board") return;
  if (state.board.refreshInFlight) return;
  state.board.refreshInFlight = true;
  try {
    const statePayload = await fetchJson("/api/state", {}, 8000);
    const featureState = statePayload?.feature_state || state.runtime?.feature_state || {};
    const featuresPayload = await fetchJson("/api/features", {}, 8000);
    const featureList = featuresPayload?.features || [];
    state.board.featureListBySession[sessionName] = featureList;
    const selectedFeatureId = String(
      state.board.selectedFeatureIdBySession[sessionName]
      || featureState?.feature_id
      || ""
    ).trim();
    let directory = String(featureState?.directory || "").trim();
    if (selectedFeatureId) {
      const featureRecord = featureList.find((feature) => String(feature.feature_id || "") === selectedFeatureId);
      if (featureRecord) {
        directory = String(featureRecord.directory || "").trim() || directory;
        state.board.selectedFeatureIdBySession[sessionName] = selectedFeatureId;
      }
    }
    renderFeatureSelectors(sessionName);
    if (!directory) {
      state.board.planBySession[sessionName] = null;
      setBoardError("");
      renderBoard();
      return;
    }
    const planPayload = await fetchJson(`/api/feature-plan?directory=${encodeURIComponent(directory)}`, {}, 10000);
    state.board.planBySession[sessionName] = planPayload?.feature_plan || null;
    const selected = state.board.selectedTaskIdBySession[sessionName];
    if (!selected && (planPayload?.feature_plan?.phases || []).length) {
      state.board.selectedTaskIdBySession[sessionName] = Number(planPayload.feature_plan.phases[0].id);
    }
    setBoardError("");
  } catch (err) {
    const msg = String(err?.message || "");
    if (/Request timed out/i.test(msg)) {
      // Keep existing board state while model is busy; avoid noisy timeout errors.
      setBoardError("");
      return;
    }
    setBoardError(`Board refresh failed: ${msg}`);
  } finally {
    state.board.refreshInFlight = false;
  }
  renderBoard();
}

async function refreshApprovals() {
  try {
    const data = await fetchJson("/api/approvals", {}, 2500);
    state.pendingApprovals = data.pending_approvals || [];
  } catch {
    state.pendingApprovals = [];
  }
  renderApprovalBar();
}

function renderApprovalBar() {
  const next = state.pendingApprovals[0];
  if (!next) {
    ui.approvalBar.classList.add("hidden");
    return;
  }
  ui.approvalBar.classList.remove("hidden");
  const tool = next.tool_name || "tool";
  const count = state.pendingApprovals.length;
  ui.approvalText.textContent = `${tool} requires approval${count > 1 ? ` (${count} pending)` : ""}`;
}

async function resolveApproval(decision) {
  const next = state.pendingApprovals[0];
  if (!next?.approval_id) return;
  let reason = undefined;
  if (decision === "e") reason = prompt("Explain request to the model (optional):") || "";
  await fetchJson("/api/approvals/resolve", {
    method: "POST",
    body: JSON.stringify({ approval_id: next.approval_id, decision, reason }),
  });
  await refreshApprovals();
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
  return sessionMemory(sessionName).runtime;
}

async function refreshMemoryBuffers(sessionName = state.currentSession) {
  try {
    const payload = await fetchJson("/api/memory-buffers", {}, 3000);
    const mem = sessionMemory(sessionName);
    mem.buffer = Array.isArray(payload.memory_entries) ? payload.memory_entries : [];
    mem.scratchpad = Array.isArray(payload.scratchpad_entries) ? payload.scratchpad_entries : [];
    mem.layers = Array.isArray(payload.context_layers) ? payload.context_layers : [];
    if (!ui.memoryModal.classList.contains("hidden") && sessionName === state.currentSession) renderMemoryModal();
  } catch {
    return;
  }
}

function renderMemoryModal() {
  const mem = sessionMemory(state.currentSession);
  const expanded = mem.expanded || {};
  const query = String(mem.query || "").trim().toLowerCase();
  const filtered = (items) => {
    if (!query) return items;
    return items.filter((item) => {
      const hay = `${item.id} ${(item.tags || []).join(" ")} ${item.source} ${item.content}`.toLowerCase();
      return hay.includes(query);
    });
  };
  const memoryEntries = filtered(mem.buffer || []);
  const renderEntry = (e, bucket) => {
    const preview = String(e.content || "").slice(0, 200);
    const full = String(e.content || "");
    const hasMore = full.length > preview.length;
    const entryKey = `${bucket}:${e.id || "?"}`;
    const isOpen = !!expanded[entryKey];
    return `<article class="memory-item">
      <div class="memory-item-title">#${e.id || "?"} ${(e.tags || []).length ? `· tags: ${(e.tags || []).join(", ")}` : ""}${e.source ? ` · source: ${e.source}` : ""}</div>
      <div class="memory-item-body">${preview || ""}${hasMore ? "…" : ""}</div>
      ${hasMore ? `<details data-memory-key="${entryKey}" ${isOpen ? "open" : ""}><summary>View full context</summary><div class="memory-item-body memory-item-full">${full}</div></details>` : ""}
    </article>`;
  };
  ui.memoryBufferList.innerHTML = memoryEntries.length
    ? memoryEntries.map((entry) => renderEntry(entry, "memory")).join("")
    : '<div class="activity-empty">No memory entries found.</div>';

  const scratchEntries = filtered(mem.scratchpad || []);
  ui.scratchpadBufferList.innerHTML = scratchEntries.length
    ? scratchEntries.map((entry) => renderEntry(entry, "scratchpad")).join("")
    : '<div class="activity-empty">No scratchpad entries found.</div>';

  const layers = Array.isArray(mem.layers) ? mem.layers : [];
  ui.memoryLayersList.innerHTML = layers.length
    ? layers.map((layer) => {
      const current = Number(layer.current || 0);
      const maximum = Math.max(1, Number(layer.maximum || 1));
      const pct = Math.max(0, Math.min(100, Math.round((current / maximum) * 100)));
      return `<article class="memory-item">
        <div class="memory-item-title">${layer.layer || ""} · ${layer.name || "Layer"} · ${current}/${maximum} (${pct}%)</div>
        <div class="memory-item-body">${layer.description || ""}</div>
        <div class="meter"><div class="meter-fill" style="width:${pct}%"></div></div>
      </article>`;
    }).join("")
    : '<div class="activity-empty">No context layer stats available.</div>';

  ui.memoryActivityList.innerHTML = mem.activity.length
    ? mem.activity.slice(-60).reverse().map((e) => `<article class="memory-item"><div class="memory-item-title">${e.title} · ${new Date(e.at).toLocaleTimeString()}</div><div class="memory-item-body">${e.body || ""}</div></article>`).join("")
    : '<div class="activity-empty">No memory tool activity yet.</div>';
  [ui.memoryBufferList, ui.scratchpadBufferList].forEach((listEl) => {
    listEl?.querySelectorAll("details[data-memory-key]").forEach((detailsEl) => {
      detailsEl.addEventListener("toggle", () => {
        const key = detailsEl.getAttribute("data-memory-key");
        if (!key) return;
        mem.expanded = mem.expanded || {};
        mem.expanded[key] = detailsEl.open;
      });
    });
  });
}

function openMemoryModal() {
  showModal(ui.memoryModal);
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
  hideModal(ui.memoryModal);
  if (state.memoryPollTimer) {
    clearInterval(state.memoryPollTimer);
    state.memoryPollTimer = null;
  }
}

async function refreshFolderNavigator(path = "") {
  try {
    const payload = await fetchJson(
      "/api/workspaces/list-dir",
      { method: "POST", body: JSON.stringify({ path }) },
      15000,
    );
    const current = payload.current_path || path || "";
    ui.folderBrowserPath.textContent = current;
    ui.folderUpBtn.dataset.path = payload.parent_path || "";
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    ui.folderBrowserList.innerHTML = entries.length
      ? entries
          .map(
            (entry) =>
              `<article class="memory-item folder-entry" data-path="${entry.path}"><div class="memory-item-title">Directory</div><div class="memory-item-body">${entry.name}</div></article>`,
          )
          .join("")
      : '<div class="activity-empty">No subdirectories.</div>';
    ui.folderBrowserList.querySelectorAll(".folder-entry").forEach((el) => {
      el.addEventListener("click", () => {
        const nextPath = el.getAttribute("data-path") || "";
        if (!nextPath) return;
        ui.folderPathInput.value = nextPath;
        refreshFolderNavigator(nextPath);
      });
    });
  } catch (err) {
    ui.folderBrowserPath.textContent = "Navigator unavailable";
    ui.folderBrowserList.innerHTML = `<div class="activity-empty">${escapeHtml(
      err.message || "Unable to browse directories.",
    )}</div>`;
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
    const roleTime = formatMessageTime(item.at);
    card.innerHTML = `<div class="message-meta"><span class="role">${item.role}</span>${roleTime ? `<span class="message-time">${roleTime}</span>` : ""}</div><span class="text"></span>`;
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
    const lastLoaded = state.loadedMessages[state.loadedMessages.length - 1];
    const isDuplicateUserEcho =
      lastLoaded?.role === "user"
      && canonicalMessageText(lastLoaded.text) === canonicalMessageText(pending.userText);
    if (!isDuplicateUserEcho && canonicalMessageText(pending.userText)) {
      const userCard = document.createElement("article");
      userCard.className = "message pending";
      const pendingAt = formatMessageTime(pending.startedAt);
      userCard.innerHTML = `<div class="message-meta"><span class="role">user</span>${pendingAt ? `<span class="message-time">${pendingAt}</span>` : ""}</div><span class="text"></span>`;
      renderMarkdown(userCard.querySelector(".text"), pending.userText);
      ui.feed.appendChild(userCard);
    }

    const aiCard = document.createElement("article");
    aiCard.className = "message pending";
    const latestActivity = pending.latestActivity || "Thinking through response";
    const runtimeMeta = pending.startedAt ? `Running for ${formatSince(pending.startedAt)}` : "Running";
    const pendingAt = formatMessageTime(pending.startedAt);
    aiCard.innerHTML = `
      <div class="message-meta"><span class="role">assistant</span>${pendingAt ? `<span class="message-time">${pendingAt}</span>` : ""}</div>
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

function updateComposerState() {
  const pending = !!state.pendingBySession[state.currentSession];
  ui.messageInput.disabled = pending;
  if (state.thinkingPlaceholderTimer) {
    clearInterval(state.thinkingPlaceholderTimer);
    state.thinkingPlaceholderTimer = null;
  }
  if (pending) {
    const frames = ["Model is thinking.", "Model is thinking..", "Model is thinking..."];
    let index = 0;
    ui.messageInput.placeholder = frames[index];
    state.thinkingPlaceholderTimer = setInterval(() => {
      index = (index + 1) % frames.length;
      ui.messageInput.placeholder = frames[index];
    }, 420);
  } else {
    ui.messageInput.placeholder = "Type a message...";
  }
  ui.sendBtn.disabled = false;
  ui.sendBtn.classList.toggle("stop-btn", pending);
  ui.sendBtn.textContent = pending ? "■" : "➤";
  ui.sendBtn.title = pending ? "Stop current run" : "Send message";
}

function renderSettingsList(target, items, mode = "checkbox") {
  target.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("label");
    row.className = "settings-row";
    const help = item.description ? `<span class="settings-hint" title="${item.description}">ⓘ</span>` : "";
    if (item.description) row.title = item.description;
    if (mode === "text") {
      if (item.kind === "bool") {
        row.innerHTML = `<span>${item.label} ${help}</span><input type="checkbox" data-key="${item.key}" data-kind="bool" ${item.value ? "checked" : ""} />`;
      } else {
        row.innerHTML = `<span>${item.label} ${help}</span><input data-key="${item.key}" data-kind="${item.kind || "text"}" value="${String(item.value ?? "")}" />`;
      }
    } else {
      row.innerHTML = `<span>${item.label} ${help}</span><input type="checkbox" data-key="${item.key}" ${item.enabled ? "checked" : ""} />`;
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
    renderSettingsList(
      list,
      items.map((t) => ({
        key: t.name,
        label: t.name,
        enabled: !t.disabled,
        description: t.description || "",
      })),
      "checkbox",
    );
    ui.toolsList.appendChild(group);
  }
}

function populateSettingsPanels() {
  renderGroupedTools();

  const vars = state.allVariables || state.runtime?.variables || {};
  const entries = Object.entries(vars);
  const memoryEntries = entries.filter(([k]) => /(memory|compact|collation|timeout|max_)/i.test(k));
  const generalEntries = entries.filter(([k]) => !/(memory|compact|collation|timeout|max_)/i.test(k));

  const toItem = ([key, value]) => ({
    key,
    label: key,
    value,
    kind: typeof value === "boolean" ? "bool" : typeof value === "number" ? "number" : "text",
    description: VARIABLE_HELP[key] || "",
  });
  renderSettingsList(ui.variablesList, generalEntries.map(toItem), "text");
  renderSettingsList(ui.memoryList, memoryEntries.map(toItem), "text");
}

async function refreshWorkspace() {
  try {
    const data = await fetchJson("/api/workspaces", {}, 20000);
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
    state.serverSession = runtime.session_name || state.serverSession;
    if (!state.currentSession) state.currentSession = runtime.session_name || "";
    ui.agenticToggle.checked = !!runtime.agentic;
    ui.thinkingToggle.checked = !!runtime.thinking;
    ui.yoloToggle.checked = !!runtime.variables?.yolo;
    if (ui.agentModeSelect) ui.agentModeSelect.value = String(runtime.variables?.agent_mode || "default");
    if (String(ui.agentModeSelect?.value || "default") !== "feature") {
      ui.chatFeatureWrap?.classList.add("hidden");
    }
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
  try {
    const data = await fetchJson("/api/sessions", {}, 2500);
    state.sessions = data.sessions || state.sessions || [];
    if (!state.currentSession) state.currentSession = data.current || state.sessions[0] || "";
    localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify({ sessions: state.sessions, currentSession: state.currentSession }));
  } catch {
    if (!state.currentSession) state.currentSession = state.sessions[0] || "";
  }
  renderSessions();
}

function renderSessions() {
  ui.sessionList.innerHTML = "";
  for (const name of state.sessions) {
    const item = document.createElement("div");
    const isPending = !!state.pendingBySession[name];
    const isMenuOpen = state.openSessionMenu === name;
    item.className = `session-item ${name === state.currentSession ? "active" : ""} ${isPending ? "pending" : ""}`;
    item.innerHTML = `
      <div class="session-row">
        <button class="session-title">${isPending ? '<span class="session-pending-dot"></span>' : ""}${name}</button>
        <button class="session-menu-btn" title="Session options">⋯</button>
      </div>
      <div class="session-popup ${isMenuOpen ? "" : "hidden"}">
        <div class="session-popup-actions">
          <button class="btn" data-action="rename">Rename</button>
          <button class="btn" data-action="delete">Delete</button>
          <button class="btn" data-action="close">Close</button>
        </div>
      </div>
    `;
    item.classList.toggle("menu-open", isMenuOpen);

    item.querySelector(".session-title").addEventListener("click", () => {
      state.openSessionMenu = "";
      loadSession(name);
    });
    const popup = item.querySelector(".session-popup");
    item.querySelector(".session-menu-btn").addEventListener("click", (evt) => {
      evt.stopPropagation();
      state.openSessionMenu = state.openSessionMenu === name ? "" : name;
      renderSessions();
    });

    item.querySelector('[data-action="rename"]').addEventListener("click", async () => {
      const newName = prompt("Rename session:", name)?.trim() || "";
      if (!newName || newName === name) return;
      state.openSessionMenu = "";
      await renameSession(name, newName);
    });

    item.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      state.openSessionMenu = "";
      await deleteSession(name);
    });

    item.querySelector('[data-action="close"]').addEventListener("click", () => {
      state.openSessionMenu = "";
      popup.classList.add("hidden");
      item.classList.remove("menu-open");
    });
    ui.sessionList.appendChild(item);
  }
  persistPendingState();
  renderActivityPanel();
}

async function refreshHistory(resetToBottom = true) {
  const path = state.currentSession
    ? `/api/history?limit=300&session_name=${encodeURIComponent(state.currentSession)}`
    : "/api/history?limit=300";
  const payload = await fetchJson(path, {}, 3000);
  if (!state.currentSession && payload.session_name) {
    state.currentSession = payload.session_name;
    if (!state.sessions.includes(payload.session_name)) state.sessions.unshift(payload.session_name);
    renderSessions();
  }
  state.loadedMessages = normalizedMessages(payload.history || []);
  state.visibleCount = Math.min(24, state.loadedMessages.length || 24);
  renderFeed(resetToBottom);
  renderActivityPanel();
}

async function loadSession(name) {
  if (Object.keys(state.pendingBySession).length > 0) {
    state.currentSession = name;
    renderSessions();
    await refreshServerTaskState(name);
    await refreshHistory(true);
    ui.messageInput.value = state.draftBySession[state.currentSession] || "";
    ui.messageInput.style.height = "auto";
    ui.messageInput.style.height = `${Math.min(ui.messageInput.scrollHeight, 180)}px`;
    updateComposerState();
    renderActivityPanel();
    setViewMode(boardMode(name), name);
    await refreshBoardData({ force: true });
    return;
  }
  await fetchJson("/api/sessions/load", { method: "POST", body: JSON.stringify({ name }) });
  state.serverSession = name;
  state.currentSession = name;
  localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify({ sessions: state.sessions, currentSession: state.currentSession }));
  await refreshSessions();
  await refreshRuntime();
  await refreshServerTaskState(name);
  await refreshHistory(true);
  await refreshWorkspace();
  ui.messageInput.value = state.draftBySession[state.currentSession] || "";
  ui.messageInput.style.height = "auto";
  ui.messageInput.style.height = `${Math.min(ui.messageInput.scrollHeight, 180)}px`;
  updateComposerState();
  setViewMode(boardMode(name), name);
  await refreshBoardData({ force: true });
}

async function createSession() {
  const name = prompt("New session name:");
  if (!name) return;
  await fetchJson("/api/sessions/new", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
  state.serverSession = name.trim();
  state.currentSession = name.trim();
  await refreshSessions();
  await loadSession(state.currentSession);
}

async function renameSession(currentName, newName) {
  await fetchJson("/api/sessions/rename", { method: "POST", body: JSON.stringify({ name: currentName, new_name: newName.trim() }) });
  if (state.serverSession === currentName) state.serverSession = newName.trim();
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
  if (state.serverSession === name) state.serverSession = "";
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

async function setAgentMode(mode) {
  const variables = { ...(state.runtime?.variables || {}), agent_mode: String(mode || "default") };
  await fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ variables }) });
  state.runtime = state.runtime || {};
  state.runtime.variables = variables;
  renderFeatureSelectors(state.currentSession);
}

async function activateFeature(featureId) {
  const resolved = String(featureId || "").trim();
  if (!resolved) {
    state.board.selectedFeatureIdBySession[state.currentSession] = "";
    renderFeatureSelectors(state.currentSession);
    return;
  }
  await fetchJson("/api/features/activate", {
    method: "POST",
    body: JSON.stringify({ feature_id: resolved }),
  });
  state.board.selectedFeatureIdBySession[state.currentSession] = resolved;
  await refreshRuntime();
  await refreshBoardData({ force: true });
  setStatus("Active feature updated.", "connected");
}

async function archiveSelectedFeature() {
  const record = selectedFeatureRecord();
  if (!record?.feature_id) return setStatus("No feature selected.", "warning");
  await fetchJson("/api/features/archive", {
    method: "POST",
    body: JSON.stringify({ feature_id: record.feature_id }),
  });
  await refreshBoardData({ force: true });
  setStatus("Feature archived. It is now read-only.", "warning");
}

async function deleteSelectedFeature() {
  const record = selectedFeatureRecord();
  if (!record?.feature_id) return setStatus("No feature selected.", "warning");
  await fetchJson("/api/features/delete", {
    method: "POST",
    body: JSON.stringify({ feature_id: record.feature_id }),
  });
  state.board.selectedFeatureIdBySession[state.currentSession] = "";
  await refreshBoardData({ force: true });
  setStatus("Feature deleted.", "warning");
}

async function unloadFeature() {
  await fetchJson("/api/features/unload", {
    method: "POST",
    body: JSON.stringify({}),
  });
  state.board.selectedFeatureIdBySession[state.currentSession] = "";
  state.board.planBySession[state.currentSession] = null;
  renderFeatureSelectors(state.currentSession);
  renderBoard();
  setStatus("Feature unloaded.", "warning");
}

async function saveSettings() {
  const disabled_tools = [...ui.toolsList.querySelectorAll('input[type="checkbox"]')].filter((x) => !x.checked).map((x) => x.dataset.key);
  const variables = {};
  [...ui.variablesList.querySelectorAll("input[data-key]"), ...ui.memoryList.querySelectorAll("input[data-key]")].forEach((x) => {
    const raw = x.type === "checkbox" ? String(x.checked) : x.value;
    variables[x.dataset.key] = parseVariableValue(raw, x.dataset.kind);
  });

  await fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ disabled_tools, variables }) });

  localStorage.setItem(THEME_MODE_KEY, document.documentElement.dataset.mode || "dark");
  localStorage.setItem(THEME_ACCENT_KEY, ui.customAccentInput.value || "#8b5cf6");
  applyThemeFromStorage();
  hideModal(ui.settingsModal);
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

function closeEventStream(sessionName) {
  const src = state.eventSourceBySession[sessionName];
  if (src) {
    src.close();
    delete state.eventSourceBySession[sessionName];
  }
}

function mapEventToActivity(evt) {
  const payload = evt.payload || {};
  if (evt.event === "trace.tool") {
    return {
      title: `Using tool: ${payload.tool_name || "unknown"}`,
      detail: payload.visible_result ? String(payload.visible_result).slice(0, 220) : "",
      tag: "tool",
    };
  }
  if (evt.event === "trace.tool_result") return { title: "Tool result", detail: payload.preview || "", tag: "tool" };
  if (evt.event === "trace.info") return { title: "Info", detail: payload.message || "", tag: "system" };
  if (evt.event === "trace.error") return { title: "Error", detail: payload.message || "", tag: "error" };
  if (evt.event === "trace.message") return { title: `Model message (${payload.role || "assistant"})`, detail: String(payload.content || "").slice(0, 220), tag: "general" };
  if (evt.event === "task.awaiting_approval") return { title: "Awaiting approval", detail: "A tool call needs approval before continuing.", tag: "task" };
  if (evt.event === "task.awaiting_input") return { title: "Awaiting input", detail: payload.blocker?.reason || "Task requires more input.", tag: "task" };
  if (evt.event === "task.running") return { title: "Task running", detail: "", tag: "task" };
  if (evt.event === "task.completed") return { title: "Task complete", detail: "", tag: "task" };
  if (evt.event === "task.cancelled") return { title: "Task cancelled", detail: "Stopped by user.", tag: "error" };
  if (evt.event === "task.error") return { title: "Task error", detail: payload.error || "", tag: "error" };
  return null;
}

function startTaskEventStream(taskId, sessionName) {
  closeEventStream(sessionName);
  const es = new EventSource(api(`/api/events?task_id=${encodeURIComponent(taskId)}`));
  state.eventSourceBySession[sessionName] = es;
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
      if (evt.event === "task.cancelled") state.taskBySession[sessionName].status = "cancelled";
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
    if (evt.event === "task.error") {
      rememberRunError(
        sessionName,
        "The model run failed before producing a final response.",
        String(evt.payload?.error || "")
      );
      appendChatErrorEntry(
        sessionName,
        "The model run failed before producing a final response.",
        String(evt.payload?.error || "")
      );
    } else if (evt.event === "trace.error") {
      rememberRunError(
        sessionName,
        "A runtime/provider error occurred during execution.",
        String(evt.payload?.message || "")
      );
      appendChatErrorEntry(
        sessionName,
        "A runtime/provider error occurred during execution.",
        String(evt.payload?.message || "")
      );
    }
    pushActivity(sessionName, mapped.title, mapped.detail, mapped.tag);
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
    "task.cancelled",
    "task.error",
    "trace.tool",
    "trace.tool_result",
    "trace.message",
    "trace.info",
    "trace.error",
  ].forEach((name) => es.addEventListener(name, handleEvent));
  es.onerror = () => {
    pushActivity(sessionName, "Activity stream disconnected", "Waiting for task status updates.");
    closeEventStream(sessionName);
  };
}

function startTaskTicker(sessionName) {
  if (state.taskPollTimersBySession[sessionName]) clearInterval(state.taskPollTimersBySession[sessionName]);
  state.taskPollTimersBySession[sessionName] = setInterval(() => {
    if (!state.pendingBySession[sessionName]) return;
    if (sessionName === state.currentSession) {
      renderFeed(false);
      renderActivityPanel();
    }
  }, 1000);
}

function stopTaskTicker(sessionName) {
  if (state.taskPollTimersBySession[sessionName]) {
    clearInterval(state.taskPollTimersBySession[sessionName]);
    delete state.taskPollTimersBySession[sessionName];
  }
}

async function waitForTaskDone(taskId, sessionName) {
  while (true) {
    const payload = await fetchJson(`/api/tasks/${encodeURIComponent(taskId)}`);
    const task = payload.task || {};
    const status = task.status || "pending";
    state.taskBySession[sessionName].status = status;
    renderActivityPanel();
    if (status === "completed" || status === "error" || status === "cancelled") return task;
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

function runningPendingSessions() {
  return Object.entries(state.pendingBySession).filter(([, p]) => (p?.status || "running") !== "queued").map(([name]) => name);
}

function isTerminalTaskStatus(status) {
  return ["completed", "error", "cancelled"].includes(String(status || "").toLowerCase());
}

async function refreshServerTaskState(sessionName = state.currentSession) {
  if (!sessionName && !state.sessions.length) return;
  try {
    const payload = await fetchJson("/api/tasks", {}, 2500);
    const tasks = Array.isArray(payload?.tasks) ? payload.tasks : [];
    const activeTasks = tasks
      .filter((task) => !isTerminalTaskStatus(task.status))
      .sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0));

    const activeBySession = new Map();
    for (const task of activeTasks) {
      const taskSession = String(task?.payload?.session_name || task?.session_name || "").trim();
      if (!taskSession || activeBySession.has(taskSession)) continue;
      activeBySession.set(taskSession, task);
    }

    const knownSessions = new Set([...(state.sessions || []), sessionName, state.currentSession].filter(Boolean));
    for (const session of knownSessions) {
      const active = activeBySession.get(session);
      if (!active) {
        const existing = state.pendingBySession[session];
        if (existing && existing.status !== "queued") {
          delete state.pendingBySession[session];
          if (state.taskBySession[session]?.status === "running") {
            state.taskBySession[session].status = "completed";
          }
        }
        continue;
      }

      const status = String(active.status || "running");
      const pretty = status.replaceAll("_", " ");
      state.taskBySession[session] = {
        ...(state.taskBySession[session] || {}),
        taskId: String(active.task_id || ""),
        status,
        startedAt: Number((active.created_at || Date.now() / 1000) * 1000),
      };
      state.pendingBySession[session] = {
        ...(state.pendingBySession[session] || {}),
        userText: state.pendingBySession[session]?.userText || "",
        latestActivity: pretty.charAt(0).toUpperCase() + pretty.slice(1),
        startedAt: Number((active.created_at || Date.now() / 1000) * 1000),
        status: "running",
      };
    }

    renderSessions();
    updateComposerState();
    if (state.currentSession === sessionName) renderFeed(false);
    if (state.currentSession && state.pendingBySession[state.currentSession]) {
      refreshHistory(false).catch(() => {});
      if (state.currentView === "board") refreshBoardData().catch(() => {});
    }
  } catch {
    return;
  }
}

async function processQueuedSends() {
  if (runningPendingSessions().length) return;
  const next = state.sendQueue.shift();
  if (!next) return;
  if (state.pendingBySession[next.sessionName]?.status === "queued") {
    delete state.pendingBySession[next.sessionName];
    renderSessions();
  }
  await executeSend(next.sessionName, next.text);
}

async function executeSend(sessionAtSend, text) {
  if (state.serverSession !== sessionAtSend) {
    await fetchJson("/api/sessions/load", { method: "POST", body: JSON.stringify({ name: sessionAtSend }) });
    state.serverSession = sessionAtSend;
  }
  if (state.pendingBySession[sessionAtSend]) return;
  state.errorSignatureBySession[sessionAtSend] = "";
  state.pendingBySession[sessionAtSend] = { userText: text, latestActivity: "Queued", startedAt: Date.now(), status: "running" };
  state.taskBySession[sessionAtSend] = { status: "running", startedAt: Date.now(), taskId: "" };
  state.draftBySession[sessionAtSend] = "";
  persistDrafts();
  pushActivity(sessionAtSend, "Message sent", text.slice(0, 220));
  renderSessions();
  if (state.currentSession === sessionAtSend) renderFeed(true);

  ui.messageInput.value = "";
  ui.messageInput.style.height = "auto";
  startTaskTicker(sessionAtSend);
  updateComposerState();
  try {
    const start = await fetchJson("/api/message", { method: "POST", body: JSON.stringify({ text, session_name: sessionAtSend, async: true }) });
    const taskId = start.task?.task_id;
    if (!taskId) throw new Error("Task ID missing from async response.");
    state.taskBySession[sessionAtSend].taskId = taskId;
    state.pendingBySession[sessionAtSend].latestActivity = "Thinking";
    pushActivity(sessionAtSend, "Task started", `Task ID: ${taskId}`);
    startTaskEventStream(taskId, sessionAtSend);
    const finalTask = await waitForTaskDone(taskId, sessionAtSend);
    if (finalTask.status === "cancelled") {
      pushActivity(sessionAtSend, "Run cancelled", `Task ${taskId} was cancelled.`);
      return;
    }
    if (finalTask.status === "error") {
      throw new Error(finalTask.error || "Task failed.");
    }
    pushActivity(sessionAtSend, "Final response received", "");
  } catch (err) {
    rememberRunError(sessionAtSend, "The request failed.", String(err?.message || "Unknown error"));
    appendChatErrorEntry(sessionAtSend, "The request failed.", String(err?.message || "Unknown error"));
    pushActivity(sessionAtSend, "Request failed", String(err?.message || "Unknown error"));
    throw err;
  } finally {
    closeEventStream(sessionAtSend);
    stopTaskTicker(sessionAtSend);
    delete state.pendingBySession[sessionAtSend];
    renderSessions();
    updateComposerState();
    if (state.taskBySession[sessionAtSend]?.status === "running") {
      state.taskBySession[sessionAtSend].status = "completed";
    }
    if (state.currentSession === sessionAtSend) {
      await refreshHistory(true);
      const runError = state.runErrorBySession[sessionAtSend];
      if (runError) {
        appendChatErrorEntry(sessionAtSend, runError.message, runError.detail);
      }
      renderFeed(true);
      await refreshWorkspace();
      renderActivityPanel();
    }
    delete state.runErrorBySession[sessionAtSend];
    processQueuedSends().catch((err) => setStatus(`Error: ${err.message}`, "error"));
  }
}

async function sendMessage(evt) {
  evt?.preventDefault();
  const text = ui.messageInput.value.trim();
  if (!text) return;
  const sessionAtSend = state.currentSession;
  if (state.pendingBySession[sessionAtSend]) {
    await stopCurrentRun();
    return;
  }

  const running = runningPendingSessions();
  if (running.length) {
    state.sendQueue.push({ sessionName: sessionAtSend, text });
    state.pendingBySession[sessionAtSend] = { userText: text, latestActivity: `Queued behind ${running[0]}`, startedAt: Date.now(), status: "queued" };
    state.draftBySession[sessionAtSend] = "";
    persistDrafts();
    pushActivity(sessionAtSend, "Message queued", `Queued while ${running[0]} is running.`);
    renderSessions();
    if (state.currentSession === sessionAtSend) renderFeed(true);
    ui.messageInput.value = "";
    ui.messageInput.style.height = "auto";
    updateComposerState();
    return;
  }
  await executeSend(sessionAtSend, text);
}

async function stopCurrentRun() {
  const sessionName = state.currentSession;
  const pending = state.pendingBySession[sessionName];
  if (!pending) return;
  const taskId = state.taskBySession[sessionName]?.taskId;
  if (taskId) {
    try {
      await fetchJson("/api/tasks/cancel", {
        method: "POST",
        body: JSON.stringify({ task_id: taskId }),
      });
    } catch {
      // best effort; UI still clears pending state
    }
  }
  closeEventStream(sessionName);
  stopTaskTicker(sessionName);
  delete state.pendingBySession[sessionName];
  state.taskBySession[sessionName] = {
    ...(state.taskBySession[sessionName] || {}),
    status: "cancelled",
  };
  pushActivity(sessionName, "Run stopped", taskId ? `Stopped task ${taskId}` : "Stopped current run.");
  renderSessions();
  renderFeed(false);
  renderActivityPanel();
  updateComposerState();
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
  ui.yoloToggle.addEventListener("change", () => {
    const variables = { ...(state.runtime?.variables || {}), yolo: !!ui.yoloToggle.checked };
    fetchJson("/api/runtime", { method: "POST", body: JSON.stringify({ variables }) })
      .catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.agentModeSelect?.addEventListener("change", () => {
    setAgentMode(ui.agentModeSelect.value)
      .then(() => setStatus(`Mode set to ${ui.agentModeSelect.value}.`, "connected"))
      .catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.chatFeatureSelect?.addEventListener("change", () => {
    activateFeature(ui.chatFeatureSelect.value).catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.approvalApproveBtn.addEventListener("click", () => resolveApproval("y").catch((err) => setStatus(`Error: ${err.message}`, "error")));
  ui.approvalRejectBtn.addEventListener("click", () => resolveApproval("n").catch((err) => setStatus(`Error: ${err.message}`, "error")));
  ui.approvalExplainBtn.addEventListener("click", () => resolveApproval("e").catch((err) => setStatus(`Error: ${err.message}`, "error")));
  document.addEventListener("click", () => {
    ui.chatMenu.classList.add("hidden");
    ui.attachMenu.classList.add("hidden");
    ui.boardFeatureMenu?.classList.add("hidden");
    ui.boardFeatureActionsMenu?.classList.add("hidden");
    if (state.openSessionMenu) {
      state.openSessionMenu = "";
      renderSessions();
    }
  });

  const autoResizeInput = () => {
    ui.messageInput.style.height = "auto";
    ui.messageInput.style.height = `${Math.min(ui.messageInput.scrollHeight, 180)}px`;
  };
  ui.messageInput.addEventListener("input", () => {
    autoResizeInput();
    if (!state.currentSession) return;
    state.draftBySession[state.currentSession] = ui.messageInput.value;
    persistDrafts();
  });
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
    showModal(ui.folderModal);
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
    showModal(ui.folderModal);
    ui.folderPathInput.focus();
    refreshFolderNavigator("");
  };

  ui.workspaceAddTrigger.addEventListener("click", openFolderModal);
  ui.browseFolderBtn.addEventListener("click", async () => {
    refreshFolderNavigator(ui.folderPathInput.value.trim());
  });
  ui.folderUpBtn?.addEventListener("click", () => {
    const parentPath = ui.folderUpBtn.dataset.path || "";
    if (!parentPath) return;
    ui.folderPathInput.value = parentPath;
    refreshFolderNavigator(parentPath);
  });
  ui.attachFolderConfirmBtn.addEventListener("click", async () => {
    const path = ui.folderPathInput.value.trim();
    if (!path) return;
    ui.attachFolderConfirmBtn.disabled = true;
    const originalText = ui.attachFolderConfirmBtn.textContent;
    ui.attachFolderConfirmBtn.textContent = "Attaching…";
    try {
      await fetchJson(
        "/api/workspaces/add",
        { method: "POST", body: JSON.stringify({ path }) },
        60000,
      );
      hideModal(ui.folderModal);
      await refreshWorkspace();
      setStatus("Workspace folder attached.", "connected");
    } catch (err) {
      setStatus(`Error: ${err.message}`, "error");
    } finally {
      ui.attachFolderConfirmBtn.disabled = false;
      ui.attachFolderConfirmBtn.textContent = originalText;
    }
  });
  ui.closeFolderModalBtn.addEventListener("click", () => hideModal(ui.folderModal));
  ui.closeMemoryModalBtn.addEventListener("click", closeMemoryModal);
  ui.ticketCloseBtn?.addEventListener("click", () => hideModal(ui.ticketModal));
  ui.ticketSaveBtn?.addEventListener("click", () => saveTicketEdits());
  ui.createFeatureCancelBtn?.addEventListener("click", () => hideModal(ui.createFeatureModal));
  ui.addEpicBtn?.addEventListener("click", () => {
    const draft = currentFeatureDraft(state.currentSession);
    draft.epics.push({ title: `Epic ${draft.epics.length + 1}`, tasks: [""] });
    renderFeatureDraftEditor(state.currentSession);
  });
  ui.saveFeatureDraftBtn?.addEventListener("click", () => {
    const draft = currentFeatureDraft(state.currentSession);
    draft.name = String(ui.createFeatureNameInput?.value || "").trim();
    draft.status = "draft";
    hideModal(ui.createFeatureModal);
    renderBoard();
    setStatus("Feature draft saved.", "warning");
  });
  ui.createFeatureStubBtn?.addEventListener("click", () => {
    confirmFeatureDraft(state.currentSession)
      .then(() => hideModal(ui.createFeatureModal))
      .catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
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
    showModal(ui.settingsModal);
  });
  ui.closeSettingsBtn.addEventListener("click", () => hideModal(ui.settingsModal));
  ui.saveSettingsBtn.addEventListener("click", () => saveSettings().catch((err) => setStatus(`Error: ${err.message}`, "error")));

  [ui.folderModal, ui.memoryModal, ui.ticketModal, ui.createFeatureModal, ui.settingsModal].forEach((modalEl) => {
    modalEl?.addEventListener("click", (evt) => {
      if (evt.target === modalEl) hideModal(modalEl);
    });
  });

  ui.settingsTabs.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".settings-tab");
    if (!btn) return;
    const tab = btn.dataset.tab;
    ui.settingsTabs.querySelectorAll(".settings-tab").forEach((x) => x.classList.toggle("active", x === btn));
    ui.settingsModal.querySelectorAll(".settings-pane").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === tab));
  });
  ui.themeModeDark.addEventListener("click", () => {
    localStorage.setItem(THEME_MODE_KEY, "dark");
    applyThemeFromStorage();
  });
  ui.themeModeLight.addEventListener("click", () => {
    localStorage.setItem(THEME_MODE_KEY, "light");
    applyThemeFromStorage();
  });
  ui.accentCustomBtn.addEventListener("click", () => ui.customAccentInput.click());
  ui.customAccentInput.addEventListener("input", () => {
    localStorage.setItem(THEME_ACCENT_KEY, ui.customAccentInput.value);
    applyThemeFromStorage();
  });

  ui.chatViewBtn?.addEventListener("click", () => setViewMode("chat"));
  ui.boardViewBtn?.addEventListener("click", async () => {
    setViewMode("board");
    await refreshBoardData({ force: true });
  });
  ui.chatBoardSwitch?.addEventListener("change", async () => {
    if (ui.chatBoardSwitch.checked) {
      setViewMode("board");
      await refreshBoardData({ force: true });
      return;
    }
    setViewMode("chat");
  });
  ui.boardRefreshBtn?.addEventListener("click", () => refreshBoardData({ force: true }));
  ui.boardSearchInput?.addEventListener("input", () => {
    boardFilters().search = ui.boardSearchInput.value || "";
    renderBoard();
  });
  ui.boardPhaseFilter?.addEventListener("change", () => {
    boardFilters().phase = ui.boardPhaseFilter.value || "";
    renderBoard();
  });
  ui.boardFeatureCard?.addEventListener("click", (evt) => {
    evt.stopPropagation();
    ui.boardFeatureActionsMenu?.classList.add("hidden");
    ui.boardFeatureMenu?.classList.toggle("hidden");
  });
  ui.boardFeatureActionsBtn?.addEventListener("click", (evt) => {
    evt.stopPropagation();
    ui.boardFeatureMenu?.classList.add("hidden");
    ui.boardFeatureActionsMenu?.classList.toggle("hidden");
  });
  ui.boardFeatureMenu?.addEventListener("click", (evt) => {
    evt.stopPropagation();
  });
  ui.boardFeatureActionsMenu?.addEventListener("click", (evt) => {
    evt.stopPropagation();
  });
  ui.boardFeatureArchiveBtn?.addEventListener("click", () => {
    ui.boardFeatureActionsMenu?.classList.add("hidden");
    archiveSelectedFeature().catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.boardFeatureDeleteBtn?.addEventListener("click", () => {
    ui.boardFeatureActionsMenu?.classList.add("hidden");
    deleteSelectedFeature().catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.boardFeatureUnloadBtn?.addEventListener("click", () => {
    ui.boardFeatureActionsMenu?.classList.add("hidden");
    unloadFeature().catch((err) => setStatus(`Error: ${err.message}`, "error"));
  });
  ui.boardRunBtn?.addEventListener("click", async () => {
    if (state.pendingBySession[state.currentSession]) {
      await stopCurrentRun();
      renderBoard();
      return;
    }
    const feature = selectedFeatureRecord(state.currentSession);
    if (!feature?.feature_id) return setStatus("No active feature to run.", "warning");
    await executeSend(
      state.currentSession,
      `Start working on active feature "${feature.feature_name || feature.feature_id}" and continue with the next actionable task.`
    );
    renderBoard();
  });
}

async function bootstrap() {
  renderAccentSwatches();
  applyThemeFromStorage();
  renderSessions();
  await Promise.allSettled([
    refreshRuntime(),
    refreshTools(),
    refreshStateVariables(),
    refreshSessions(),
  ]);
  await Promise.allSettled([
    refreshHistory(true),
    refreshWorkspace(),
    refreshMemoryRuntime(),
    refreshMemoryBuffers(),
    refreshApprovals(),
    refreshBoardData({ force: true }),
    refreshServerTaskState(),
  ]);
  startBoardEventStream();
  if (state.board.pollTimer) clearInterval(state.board.pollTimer);
  state.board.pollTimer = setInterval(() => {
    if (state.currentView === "board") refreshBoardData();
  }, 3000);
  if (state.serverTaskPollTimer) clearInterval(state.serverTaskPollTimer);
  state.serverTaskPollTimer = setInterval(() => {
    refreshServerTaskState();
  }, 2000);
  setViewMode(boardMode(), state.currentSession);
  if (state.approvalPollTimer) clearInterval(state.approvalPollTimer);
  state.approvalPollTimer = setInterval(() => refreshApprovals(), 2000);
  ui.messageInput.value = state.draftBySession[state.currentSession] || "";
  ui.messageInput.style.height = "auto";
  ui.messageInput.style.height = `${Math.min(ui.messageInput.scrollHeight, 180)}px`;
  updateComposerState();
}

wireEvents();
bootstrap().catch((err) => setStatus(`Error: ${err.message}`, "error"));
