const el = (id) => document.getElementById(id);

const state = {
  apiBase: localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765",
  events: [],
  eventSource: null,
};

const ui = {
  apiBaseInput: el("apiBaseInput"),
  messageInput: el("messageInput"),
  sendBtn: el("sendBtn"),
  refreshBtn: el("refreshBtn"),
  clearLogBtn: el("clearLogBtn"),
  eventStream: el("eventStream"),
  stateBadge: el("stateBadge"),
  runtimeSummary: el("runtimeSummary"),
  modelInput: el("modelInput"),
  agenticToggle: el("agenticToggle"),
  thinkingToggle: el("thinkingToggle"),
  applyRuntimeBtn: el("applyRuntimeBtn"),
  collapseBtn: el("collapseBtn"),
  expandBtn: el("expandBtn"),
  configPanel: el("configPanel"),
  openSettingsBtn: el("openSettingsBtn"),
  closeSettingsBtn: el("closeSettingsBtn"),
  saveSettingsBtn: el("saveSettingsBtn"),
  settingsModal: el("settingsModal"),
  disabledToolsInput: el("disabledToolsInput"),
  variablesInput: el("variablesInput"),
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
  state.events.unshift({
    ts: new Date().toLocaleTimeString(),
    kind,
    payload: clip(payload),
  });
  state.events = state.events.slice(0, 300);
  renderEvents();
}

function renderEvents() {
  ui.eventStream.innerHTML = "";
  for (const event of state.events) {
    const card = document.createElement("div");
    card.className = "event-card";
    card.innerHTML = `
      <div class="event-head">${event.ts} · ${event.kind}</div>
      <div class="event-body"></div>
    `;
    card.querySelector(".event-body").textContent = event.payload;
    ui.eventStream.appendChild(card);
  }
}

async function fetchJson(path, options = {}) {
  const resp = await fetch(api(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

function setConnected(connected, text = "Connected") {
  ui.stateBadge.classList.remove("connected", "error");
  if (connected) {
    ui.stateBadge.classList.add("connected");
    ui.stateBadge.textContent = text;
  } else {
    ui.stateBadge.classList.add("error");
    ui.stateBadge.textContent = text;
  }
}

async function refreshRuntime() {
  try {
    const runtime = await fetchJson("/api/runtime");
    ui.modelInput.value = runtime.model || "";
    ui.agenticToggle.checked = !!runtime.agentic;
    ui.thinkingToggle.checked = !!runtime.thinking;
    ui.disabledToolsInput.value = (runtime.disabled_tools || []).join(", ");
    ui.variablesInput.value = JSON.stringify(runtime.variables || {}, null, 2);

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

function connectSSE() {
  if (state.eventSource) {
    state.eventSource.close();
  }
  state.eventSource = new EventSource(api("/api/events"));

  state.eventSource.onmessage = (evt) => {
    try {
      const payload = JSON.parse(evt.data);
      pushEvent(payload.event || "event", payload.payload || payload);
    } catch {
      pushEvent("event", evt.data);
    }
  };

  state.eventSource.onerror = () => {
    setConnected(false, "SSE disconnected");
  };
}

async function sendMessage() {
  const text = ui.messageInput.value.trim();
  if (!text) return;
  ui.messageInput.value = "";
  pushEvent("message.out", text);
  try {
    const result = await fetchJson("/api/message", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    pushEvent("message.in", result);
  } catch (err) {
    pushEvent("message.error", String(err));
  }
}

async function applyRuntime() {
  const payload = {
    model: ui.modelInput.value.trim(),
    agentic: ui.agenticToggle.checked,
    thinking: ui.thinkingToggle.checked,
  };
  try {
    const res = await fetchJson("/api/runtime", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    pushEvent("runtime.updated", res);
    await refreshRuntime();
  } catch (err) {
    pushEvent("runtime.update_error", String(err));
  }
}

async function saveSettings() {
  let variables = {};
  try {
    variables = JSON.parse(ui.variablesInput.value || "{}");
  } catch (err) {
    pushEvent("settings.error", `Invalid variables JSON: ${err.message}`);
    return;
  }
  const disabled_tools = ui.disabledToolsInput.value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

  try {
    const res = await fetchJson("/api/runtime", {
      method: "POST",
      body: JSON.stringify({ disabled_tools, variables }),
    });
    pushEvent("settings.saved", res);
    ui.settingsModal.classList.add("hidden");
    await refreshRuntime();
  } catch (err) {
    pushEvent("settings.save_error", String(err));
  }
}

async function runCommand(command) {
  try {
    const res = await fetchJson("/api/command", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
    pushEvent(`command ${command}`, res);
    await refreshRuntime();
  } catch (err) {
    pushEvent("command.error", String(err));
  }
}

function setupHandlers() {
  ui.sendBtn.addEventListener("click", sendMessage);
  ui.refreshBtn.addEventListener("click", refreshRuntime);
  ui.clearLogBtn.addEventListener("click", () => {
    state.events = [];
    renderEvents();
  });
  ui.applyRuntimeBtn.addEventListener("click", applyRuntime);

  ui.apiBaseInput.addEventListener("change", () => {
    state.apiBase = ui.apiBaseInput.value.trim().replace(/\/$/, "");
    localStorage.setItem("mucli_gui_api_base", state.apiBase);
    connectSSE();
    refreshRuntime();
  });

  document.querySelectorAll(".cmd-btn").forEach((btn) => {
    btn.addEventListener("click", () => runCommand(btn.dataset.cmd));
  });

  ui.openSettingsBtn.addEventListener("click", () => {
    ui.settingsModal.classList.remove("hidden");
  });
  ui.closeSettingsBtn.addEventListener("click", () => {
    ui.settingsModal.classList.add("hidden");
  });
  ui.saveSettingsBtn.addEventListener("click", saveSettings);

  ui.collapseBtn.addEventListener("click", () => {
    ui.configPanel.classList.add("collapsed");
    ui.expandBtn.classList.remove("hidden");
  });
  ui.expandBtn.addEventListener("click", () => {
    ui.configPanel.classList.remove("collapsed");
    ui.expandBtn.classList.add("hidden");
  });

  ui.messageInput.addEventListener("keydown", (evt) => {
    if ((evt.metaKey || evt.ctrlKey) && evt.key === "Enter") {
      sendMessage();
    }
  });
}

setupHandlers();
connectSSE();
refreshRuntime();
setInterval(refreshRuntime, 12000);
