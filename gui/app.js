const el = (id) => document.getElementById(id);

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  currentSession: "",
  sessions: [],
  runtime: null,
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
  activeSessionTitle: el("activeSessionTitle"),
  refreshBtn: el("refreshBtn"),
  feed: el("feed"),
  composer: el("composer"),
  messageInput: el("messageInput"),
  sendBtn: el("sendBtn"),
};

ui.apiBaseInput.value = state.apiBase;

function api(path) {
  return `${state.apiBase}${path}`;
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(api(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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
  return parts
    .filter((p) => p && p.type === "text")
    .map((p) => p.text || "")
    .filter(Boolean)
    .join("\n\n");
}

function normalizedMessages(history = []) {
  return history
    .filter((m) => ["user", "assistant"].includes(m.role))
    .map((m) => ({ role: m.role, text: textFromParts(m.parts || []) }))
    .filter((m) => m.text);
}

function renderFeed(resetToBottom = false) {
  const prevHeight = ui.feed.scrollHeight;
  const prevTop = ui.feed.scrollTop;
  const start = Math.max(0, state.loadedMessages.length - state.visibleCount);
  const slice = state.loadedMessages.slice(start);

  ui.feed.innerHTML = "";
  for (const item of slice) {
    const card = document.createElement("article");
    card.className = "message";
    card.innerHTML = `<div class="role">${item.role}</div><div class="text"></div>`;
    card.querySelector(".text").textContent = item.text;
    ui.feed.appendChild(card);
  }

  if (resetToBottom) {
    ui.feed.scrollTop = ui.feed.scrollHeight;
    return;
  }

  const grew = ui.feed.scrollHeight - prevHeight;
  if (prevTop < 60 && grew > 0) {
    ui.feed.scrollTop = prevTop + grew;
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
      <button class="session-title">${name}</button>
      <div class="session-actions">
        <button class="btn" data-action="rename">Rename</button>
        <button class="btn" data-action="delete">Delete</button>
      </div>
    `;

    item.querySelector(".session-title").addEventListener("click", () => loadSession(name));
    item.querySelector('[data-action="rename"]').addEventListener("click", () => renameSessionPrompt(name));
    item.querySelector('[data-action="delete"]').addEventListener("click", () => deleteSession(name));
    ui.sessionList.appendChild(item);
  }
}

async function refreshHistory(resetToBottom = true) {
  if (!state.currentSession) return;
  const payload = await fetchJson(`/api/history?limit=300&session_name=${encodeURIComponent(state.currentSession)}`);
  state.loadedMessages = normalizedMessages(payload.history || []);
  state.visibleCount = Math.min(24, state.loadedMessages.length || 24);
  ui.activeSessionTitle.textContent = state.currentSession;
  renderFeed(resetToBottom);
}

async function loadSession(name) {
  await fetchJson("/api/sessions/load", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  state.currentSession = name;
  await refreshSessions();
  await refreshRuntime();
  await refreshHistory(true);
}

async function createSession() {
  const name = prompt("New session name:");
  if (!name) return;
  await fetchJson("/api/sessions/new", {
    method: "POST",
    body: JSON.stringify({ name: name.trim() }),
  });
  state.currentSession = name.trim();
  await refreshSessions();
  await loadSession(state.currentSession);
}

async function renameSessionPrompt(currentName) {
  const newName = prompt("Rename session:", currentName);
  if (!newName || newName === currentName) return;
  await fetchJson("/api/sessions/rename", {
    method: "POST",
    body: JSON.stringify({ name: currentName, new_name: newName.trim() }),
  });
  if (state.currentSession === currentName) state.currentSession = newName.trim();
  await refreshSessions();
  await refreshHistory(false);
}

async function deleteSession(name) {
  if (!confirm(`Delete session '${name}'?`)) return;
  await fetchJson("/api/sessions/delete", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  if (state.currentSession === name) state.currentSession = "";
  await refreshSessions();
  await refreshRuntime();
  await refreshHistory(false);
}

async function applyRuntime() {
  await fetchJson("/api/runtime", {
    method: "POST",
    body: JSON.stringify({
      model: ui.modelInput.value,
      agentic: ui.agenticToggle.checked,
      thinking: ui.thinkingToggle.checked,
    }),
  });
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
    await fetchJson("/api/message", {
      method: "POST",
      body: JSON.stringify({ text, session_name: state.currentSession }),
    });
    ui.messageInput.value = "";
    await refreshHistory(true);
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

  ui.applyRuntimeBtn.addEventListener("click", async () => {
    try {
      await applyRuntime();
    } catch (err) {
      setStatus(`Error: ${err.message}`, "error");
    }
  });

  ui.newSessionBtn.addEventListener("click", async () => {
    try {
      await createSession();
    } catch (err) {
      setStatus(`Error: ${err.message}`, "error");
    }
  });

  ui.refreshBtn.addEventListener("click", async () => {
    try {
      await bootstrap();
    } catch (err) {
      setStatus(`Error: ${err.message}`, "error");
    }
  });

  ui.feed.addEventListener("scroll", () => {
    if (ui.feed.scrollTop < 40 && state.visibleCount < state.loadedMessages.length) {
      const next = Math.min(state.loadedMessages.length, state.visibleCount + 16);
      if (next !== state.visibleCount) {
        state.visibleCount = next;
        renderFeed(false);
      }
    }
  });

  ui.composer.addEventListener("submit", async (evt) => {
    try {
      await sendMessage(evt);
    } catch (err) {
      setStatus(`Error: ${err.message}`, "error");
    }
  });
}

async function bootstrap() {
  await refreshRuntime();
  await refreshSessions();
  await refreshHistory(true);
}

wireEvents();
bootstrap().catch((err) => setStatus(`Error: ${err.message}`, "error"));
