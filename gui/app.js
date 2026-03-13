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
let workspaceBrowserPath = "";
let workspaceBrowserSelection = "";
const thinkingStatus = new Map();
const assistantDraftBuffers = new Map();
let timelineCache = [];

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


function refreshModalOpenState() {
  const hasVisibleModal = Array.from(document.querySelectorAll(".modal")).some((node) => !node.classList.contains("hidden"));
  document.body.classList.toggle("modal-open", hasVisibleModal);
}

function openModal(id) {
  const node = el(id);
  if (!node) return;
  node.classList.remove("hidden");
  refreshModalOpenState();
}

function closeModal(id) {
  const node = el(id);
  if (!node) return;
  node.classList.add("hidden");
  refreshModalOpenState();
}


function initSectionToggles() {
  document.querySelectorAll(".section-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      if (!targetId) return;
      const body = el(targetId);
      if (!body) return;
      const nextHidden = !body.classList.contains("hidden") ? true : false;
      body.classList.toggle("hidden", nextHidden);
      btn.setAttribute("aria-expanded", String(!nextHidden));
      const chevron = btn.querySelector("span");
      if (chevron) chevron.textContent = nextHidden ? "▸" : "▾";
    });
  });
}


function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function highlightCodeBlocks(container) {
  if (!container || !window.hljs) return;
  container.querySelectorAll("pre.md-code-block code").forEach((node) => {
    window.hljs.highlightElement(node);
  });
}

function createMarkdownRenderer() {
  if (!window.marked) return null;

  const renderer = new window.marked.Renderer();
  renderer.code = (code, infostring = "") => {
    const language = String(infostring || "").trim().split(/\s+/)[0] || "plaintext";
    const rawCode = typeof code === "string" ? code : String(code?.text || "");
    return `<pre class="md-code-block"><button class="code-copy-btn" type="button" data-code="${escapeHtml(rawCode)}">Copy</button><code class="language-${escapeHtml(language)}">${escapeHtml(rawCode)}</code></pre>`;
  };

  window.marked.setOptions({
    gfm: true,
    breaks: true,
    renderer,
  });

  return renderer;
}

const markdownRenderer = createMarkdownRenderer();

function renderMarkdown(value) {
  const raw = String(value || "");
  if (!window.marked || !markdownRenderer) {
        return `<p>${escapeHtml(raw).replace(/\n/g, "<br />")}</p>`;
  }

  const parsed = window.marked.parse(raw);
  if (window.DOMPurify) {
    return window.DOMPurify.sanitize(parsed);
  }
  return parsed;
}


function formatLocalTimestamp(value = null) {
  const dt = value ? new Date(value) : new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)}.${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
}



function applyTheme(theme) {
  document.body.dataset.theme = theme;
  const darkTheme = el("hljs-theme-dark");
  const lightTheme = el("hljs-theme-light");
  if (darkTheme) darkTheme.disabled = theme !== "dark";
  if (lightTheme) lightTheme.disabled = theme === "dark";
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
  const maxContextChars = Number(session?.context_state?.max_context_chars || 8000);
  const maxStageTurns = Number(session?.context_state?.max_stage_turns || 3);
  return {
    maxTimeout: Number.isFinite(maxTimeout) ? maxTimeout : 300,
    maxContext: Number.isFinite(maxContext) ? maxContext : 40,
    maxContextChars: Number.isFinite(maxContextChars) ? maxContextChars : 8000,
    maxStageTurns: Number.isFinite(maxStageTurns) ? maxStageTurns : 3,
  };
}

function toolHeadline(eventType, payload) {
  const name = payload?.tool_name || payload?.result?.tool_name || "tool";
  if (eventType === "tool_call") return `call ${name}`;
  if (eventType === "tool_result") {
    const result = payload?.result || {};
    const status = result.status || (typeof result.exit_code === "number" ? (result.exit_code === 0 ? "ok" : `exit ${result.exit_code}`) : (result.error ? "error" : "ok"));
    return `result ${name} (${status})`;
  }
  return "tool";
}

function appendToolMetadata(details, eventType, payload) {
  if (!["tool_call", "tool_result"].includes(eventType)) return;

  const box = document.createElement("div");
  box.className = "meta-tool-box";

  const title = document.createElement("div");
  title.className = "meta-tool-title";
  title.textContent = payload?.tool_name || payload?.result?.tool_name || "tool";
  box.appendChild(title);

  if (eventType === "tool_call") {
    const paramsPre = document.createElement("pre");
    paramsPre.className = "meta-tool-json";
    paramsPre.textContent = JSON.stringify(payload?.constraints || {}, null, 2);
    box.appendChild(paramsPre);
  }

  if (eventType === "tool_result") {
    const status = document.createElement("div");
    status.className = "meta-tool-status";
    const result = payload?.result || {};
    const statusText = result.status || (result.error ? "error" : (typeof result.exit_code === "number" ? `exit_code=${result.exit_code}` : "ok"));
    status.textContent = `status: ${statusText}`;
    box.appendChild(status);

    const resultPre = document.createElement("pre");
    resultPre.className = "meta-tool-json";
    resultPre.textContent = JSON.stringify(result, null, 2);
    box.appendChild(resultPre);
  }

  details.appendChild(box);
}

function buildTimelineNode(eventType, payload, createdAt = null, queryStartMs = null) {
  const wrapper = document.createElement("div");
  wrapper.className = "meta-entry";
  wrapper.dataset.eventType = eventType;
  if (!isVisibleForFilter(eventType)) wrapper.classList.add("hidden");

  const details = document.createElement("details");
  details.className = "meta-details";

  const summary = document.createElement("summary");
  const tag = document.createElement("span");
  tag.className = `meta-tag ${String(eventType || "event").replace(/[^a-z0-9_-]/gi, "-").toLowerCase()}`;
  tag.textContent = String(eventType || "event").toUpperCase();

  const eventMs = createdAt ? Date.parse(createdAt) : Date.now();
  const baseMs = Number.isFinite(queryStartMs) ? queryStartMs : eventMs;
  const deltaS = Math.max(0, (eventMs - baseMs) / 1000).toFixed(1);

  const delta = document.createElement("span");
  delta.className = "meta-delta";
  delta.textContent = `+${deltaS}s`;

  const description = ["tool_call", "tool_result"].includes(eventType)
    ? toolHeadline(eventType, payload)
    : (typeof payload?.message === "string"
      ? payload.message
      : (payload?.stage?.label ? `${payload.stage.label}` : "details"));
  const headline = document.createElement("span");
  headline.className = "meta-headline";
  headline.textContent = description;

  summary.append(tag, delta, headline);
  details.appendChild(summary);

  appendToolMetadata(details, eventType, payload);

  const pre = document.createElement("pre");
  pre.className = "meta-payload";
  pre.textContent = JSON.stringify(payload || {}, null, 2);
  details.appendChild(pre);

  wrapper.appendChild(details);
  return wrapper;
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
        max_context_chars: Number(el("max-context-chars").value || 8000),
        max_stage_turns: Number(el("max-stage-turns").value || 3),
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

  el("max-context-chars").addEventListener("change", () => {
    persistCurrentConfig().catch(() => null);
  });

  el("max-stage-turns").addEventListener("change", () => {
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
    node.innerHTML = `<div class="tag-row"><div class="tag">${msg.role}</div><div class="timestamp">${msg.created_at || formatLocalTimestamp()}</div></div><div class="message-content">${renderMarkdown(msg.content)}</div>`;
    chatWindow.appendChild(node);
  });

  highlightCodeBlocks(chatWindow);

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
  const contentNode = latestAssistantMessage.querySelector(".message-content");
  if (contentNode) {
    contentNode.innerHTML = renderMarkdown(text);
    highlightCodeBlocks(contentNode);
  }
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
  if (selectedFilter === "model_io") return ["model_request", "model_response"].includes(eventType);
  if (selectedFilter === "tool") return ["tool_call", "tool_result"].includes(eventType);
  return eventType === selectedFilter;
}

function eventQueryKey(payload = {}) {
  return payload?.query?.id || "ungrouped";
}

function renderTimeline() {
  const timeline = el("timeline");
  if (!timeline) return;
  timeline.innerHTML = "";

  const grouped = new Map();
  timelineCache.forEach((evt) => {
    const key = eventQueryKey(evt.payload || {});
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(evt);
  });

  [...grouped.entries()].forEach(([queryId, events]) => {
    const queryStartMs = Math.min(
      ...events.map((evt) => {
        const ms = evt.created_at ? Date.parse(evt.created_at) : Date.now();
        return Number.isFinite(ms) ? ms : Date.now();
      }),
    );

    const group = document.createElement("section");
    group.className = "block collapsible query-separator";

    const details = document.createElement("details");

    const summary = document.createElement("summary");
    summary.className = "meta-group-summary";
    const idLabel = queryId === "ungrouped" ? "GENERAL" : queryId.slice(0, 8).toUpperCase();
    const startLabel = formatLocalTimestamp(new Date(queryStartMs).toISOString());
    summary.textContent = `QUERY ${idLabel}       ${events.length} EVENTS       ${startLabel}`;
    details.appendChild(summary);

    const eventsWrap = document.createElement("div");
    eventsWrap.className = "meta-group-events";
    events.forEach((evt) => {
      const node = buildTimelineNode(evt.event_type, evt.payload || {}, evt.created_at, queryStartMs);
      eventsWrap.appendChild(node);
    });

    details.appendChild(eventsWrap);
    group.appendChild(details);
    timeline.appendChild(group);
  });

  applyTimelineFilter();
}


function addTimeline(eventType, payload, createdAt = null) {
  timelineCache.unshift({ event_type: eventType, payload: payload || {}, created_at: createdAt });
  if (timelineCache.length > 250) timelineCache = timelineCache.slice(0, 250);
  renderTimeline();
}

function applyTimelineFilter() {
  document.querySelectorAll("#timeline [data-event-type]").forEach((node) => {
    node.classList.toggle("hidden", !isVisibleForFilter(node.dataset.eventType || ""));
  });
}

async function loadSessionTimeline(sessionId) {
  if (!sessionId) return;
  try {
    const events = await req(`/sessions/${sessionId}/events?limit=250`);
    timelineCache = events;
    renderTimeline();
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
      openModal("skill-editor-modal");
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
  el("session-summary").textContent = `session=${name} | mode=${session.mode} | policy=${session.policy_profile} | provider=${provider} | model=${model || "default"} | timeout=${limits.maxTimeout}s | context=${limits.maxContext} msgs/${limits.maxContextChars} chars | stage_turns=${limits.maxStageTurns}`;
  el("mode").value = session.mode || "interactive";
  el("policy").value = session.policy_profile || "default";
  el("providers").value = provider;
  el("max-timeout").value = limits.maxTimeout;
  el("max-context").value = limits.maxContext;
  el("max-context-chars").value = limits.maxContextChars;
  el("max-stage-turns").value = limits.maxStageTurns;
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

  el("modal-session-name").value = session.name || "default";
  el("modal-workspace").value = session.workspace_path || "";
  el("modal-providers").value = provider;
  await loadModelsForProvider(provider, "modal-model", model);

  const limits = getSessionLimits(session);
  el("modal-mode").value = session.mode || "interactive";
  el("modal-approval-mode").value = session.policy_profile || "default";
  el("modal-max-timeout").value = limits.maxTimeout;
  el("modal-condense-window").value = limits.maxContext;
  el("modal-context-chars").value = limits.maxContextChars;
  el("modal-max-stage-turns").value = limits.maxStageTurns;

  const tools = await req(`/sessions/${sessionId}/tools-config`);
  const skills = await req(`/sessions/${sessionId}/skills-config`);
  renderOverrideList("modal-enabled-tools", tools, new Set(tools.filter((t) => t.enabled).map((t) => t.name)));
  renderOverrideList("modal-enabled-skills", skills, new Set(skills.filter((sk) => sk.enabled).map((sk) => sk.name)));

  openModal("session-settings-modal");
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
  closeModal("session-settings-modal");
}

function connectStream() {
  if (!currentSession) return;
  if (streamSocket) streamSocket.close();

  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  streamSocket = new WebSocket(`${scheme}://${window.location.host}/stream/sessions/${currentSession}`);

  streamSocket.onmessage = (raw) => {
    const evt = JSON.parse(raw.data);
    const { event_type: eventType, payload, job_id: jobId, session_id: sessionId, created_at: createdAt } = evt;
    addTimeline(eventType, payload || {}, createdAt);

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
      max_context_chars: Number(el("max-context-chars").value || 8000),
      max_stage_turns: Number(el("max-stage-turns").value || 3),
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
  const current = el("workspace").value.trim();
  workspaceBrowserPath = current || "/workspace";
  workspaceBrowserSelection = workspaceBrowserPath;
  el("workspace-browser-path").value = workspaceBrowserPath;
  await loadWorkspaceBrowser(workspaceBrowserPath);
  openModal("workspace-browser-modal");
});

async function loadWorkspaceBrowser(pathValue) {
  try {
    const payload = await req(`/workspace/browse?path=${encodeURIComponent(pathValue || "/workspace")}`);
    workspaceBrowserPath = payload.cwd;
    workspaceBrowserSelection = payload.cwd;
    el("workspace-browser-path").value = payload.cwd;
    el("workspace-browser-cwd").textContent = `cwd: ${payload.cwd}`;

    const list = el("workspace-browser-list");
    list.innerHTML = "";
    const entries = [{ name: "..", path: payload.parent || payload.cwd }, ...payload.entries];
    entries.forEach((entry) => {
      const item = document.createElement("li");
      item.className = "workspace-entry";
      const button = document.createElement("button");
      button.className = "workspace-entry-btn";
      button.type = "button";
      button.textContent = entry.name;
      button.onclick = () => loadWorkspaceBrowser(entry.path);

      const selectBtn = document.createElement("button");
      selectBtn.className = "btn";
      selectBtn.type = "button";
      selectBtn.textContent = "Select";
      selectBtn.onclick = () => {
        workspaceBrowserSelection = entry.path;
        el("workspace-browser-cwd").textContent = `cwd: ${payload.cwd} · selected: ${workspaceBrowserSelection}`;
      };

      item.append(button, selectBtn);
      list.appendChild(item);
    });
  } catch (err) {
    window.alert(`Unable to browse workspace: ${err.message}`);
  }
}

setOnClick("workspace-browser-go", async () => {
  const requested = el("workspace-browser-path").value.trim();
  await loadWorkspaceBrowser(requested);
});

setOnClick("workspace-browser-select", () => {
  if (workspaceBrowserSelection) el("workspace").value = workspaceBrowserSelection;
  closeModal("workspace-browser-modal");
});
setOnClick("workspace-browser-close", () => closeModal("workspace-browser-modal"));

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
      max_context_chars: Number(el("modal-context-chars").value || 8000),
      max_stage_turns: Number(el("modal-max-stage-turns").value || 3),
      provider_preferences: { ordered: [el("modal-providers").value], model: el("modal-model").value || "default" },
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
  closeModal("skill-editor-modal");
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

const workspaceBrowserModal = el("workspace-browser-modal");
if (workspaceBrowserModal) workspaceBrowserModal.addEventListener("click", (event) => {
  if (event.target.id === "workspace-browser-modal") closeModal("workspace-browser-modal");
});

setOnClick("open-help", () => openModal("help-modal"));
setOnClick("help-close", () => closeModal("help-modal"));
const helpModal = el("help-modal");
if (helpModal) helpModal.addEventListener("click", (event) => {
  if (event.target.id === "help-modal") closeModal("help-modal");
});


const chatWindowNode = el("chat-window");
if (chatWindowNode) chatWindowNode.addEventListener("click", async (event) => {
  const button = event.target.closest(".code-copy-btn");
  if (!button) return;
  const rawCode = button.getAttribute("data-code") || "";
  try {
    await navigator.clipboard.writeText(rawCode);
    const original = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => {
      button.textContent = original;
    }, 1200);
  } catch {
    window.alert("Unable to copy code block");
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

initTheme();
initSectionToggles();
populateRuntimeOptions().then(refreshSessions).then(refreshApprovals).catch(console.error);
setInterval(() => {
  refreshApprovals().catch(() => null);
}, 2000);
