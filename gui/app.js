import van from "./vendor/van-1.6.0.min.js";

const { a, aside, button, div, h1, h2, header, input, li, main, p, section, span, textarea, ul } = van.tags;

const storage = {
  apiBase: "mucli_van_api_base",
  session: "mucli_van_active_session",
};

const state = {
  apiBase: van.state(localStorage.getItem(storage.apiBase) || "http://127.0.0.1:8765"),
  sessions: van.state([]),
  activeSession: van.state(localStorage.getItem(storage.session) || ""),
  messages: van.state([]),
  input: van.state(""),
  status: van.state("Idle"),
  streamStatus: van.state("Disconnected"),
  streamError: van.state(""),
  activeTaskId: van.state(""),
  busy: van.state(false),
  notices: van.state([]),
};

let taskEventSource = null;

const api = (path) => `${state.apiBase.val.replace(/\/$/, "")}${path}`;

const pushNotice = (text, tone = "info") => {
  state.notices.val = [...state.notices.val, { id: crypto.randomUUID(), text, tone }].slice(-6);
};

async function fetchJson(path, options = {}) {
  const resp = await fetch(api(path), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || data?.ok === false) {
    throw new Error(data?.error || `${resp.status} ${resp.statusText}`);
  }
  return data;
}

function closeTaskStream() {
  if (taskEventSource) {
    taskEventSource.close();
    taskEventSource = null;
  }
  state.streamStatus.val = "Disconnected";
}

function connectTaskStream(taskId) {
  closeTaskStream();
  state.streamError.val = "";
  state.streamStatus.val = "Connecting";
  const es = new EventSource(api(`/api/events?task_id=${encodeURIComponent(taskId)}`));
  taskEventSource = es;

  es.addEventListener("stream.open", () => {
    state.streamStatus.val = "Live";
  });

  es.addEventListener("task.updated", (evt) => {
    const data = JSON.parse(evt.data || "{}");
    const task = data?.payload?.task;
    if (!task) return;

    state.status.val = `Task: ${task.status}`;
    if (task.status === "completed") {
      const result = task.result || {};
      const history = result.history || [];
      if (history.length) {
        state.messages.val = history;
      }
      state.busy.val = false;
      state.activeTaskId.val = "";
      closeTaskStream();
      pushNotice("Task completed", "ok");
      refreshHistory();
    }

    if (task.status === "error") {
      state.busy.val = false;
      state.activeTaskId.val = "";
      state.streamError.val = task.error || "Task failed";
      closeTaskStream();
      pushNotice(`Task failed: ${state.streamError.val}`, "error");
      refreshHistory();
    }
  });

  es.onerror = () => {
    state.streamStatus.val = "Error";
    state.streamError.val = "SSE stream dropped. Reconnect by sending again.";
  };
}

async function refreshSessions() {
  const data = await fetchJson("/api/sessions");
  const sessions = data?.sessions || [];
  state.sessions.val = sessions;

  if (!state.activeSession.val && sessions.length) {
    state.activeSession.val = sessions[0];
  }
  if (state.activeSession.val && !sessions.includes(state.activeSession.val) && sessions.length) {
    state.activeSession.val = sessions[0];
  }

  if (state.activeSession.val) {
    localStorage.setItem(storage.session, state.activeSession.val);
  }
}

async function loadSession(name) {
  await fetchJson("/api/sessions/load", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  state.activeSession.val = name;
  localStorage.setItem(storage.session, name);
  await refreshHistory();
}

async function refreshHistory() {
  const params = state.activeSession.val ? `?limit=300&session_name=${encodeURIComponent(state.activeSession.val)}` : "?limit=300";
  const data = await fetchJson(`/api/history${params}`);
  state.messages.val = data?.history || [];
}

async function createSession() {
  const base = `session-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}`;
  await fetchJson("/api/sessions/new", {
    method: "POST",
    body: JSON.stringify({ name: base }),
  });
  await refreshSessions();
  await loadSession(base);
  pushNotice(`Created ${base}`, "ok");
}

async function sendMessage() {
  if (state.busy.val) return;
  const text = state.input.val.trim();
  if (!text) return;

  const optimistic = {
    role: "user",
    content: text,
    timestamp: Date.now() / 1000,
  };
  state.messages.val = [...state.messages.val, optimistic];
  state.input.val = "";
  state.busy.val = true;
  state.status.val = "Submitting task";

  const payload = {
    text,
    async: true,
    session_name: state.activeSession.val,
  };

  const data = await fetchJson("/api/message", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  const taskId = data?.task?.task_id;
  if (!taskId) {
    throw new Error("Server did not return task_id");
  }

  state.activeTaskId.val = taskId;
  state.status.val = `Running task ${taskId.slice(0, 8)}…`;
  connectTaskStream(taskId);
}

async function bootstrap() {
  localStorage.setItem(storage.apiBase, state.apiBase.val);
  await refreshSessions();

  if (state.activeSession.val) {
    await loadSession(state.activeSession.val);
  } else {
    await refreshHistory();
  }

  state.status.val = "Ready";
}

const MessageItem = (msg) => {
  const role = String(msg?.role || "assistant");
  const text = String(msg?.content ?? msg?.text ?? "");
  const tone = role === "user" ? "user" : "assistant";
  return div({ class: `message ${tone}` },
    div({ class: "message-role" }, role),
    div({ class: "message-body" }, text || "…"),
  );
};

const App = () => main({ class: "shell" },
  header({ class: "top" },
    h1("Mu-CLI · VanJS frontend"),
    p({ class: "sub" }, "SSE reactive chat UI running on the existing Python backend."),
    div({ class: "api-row" },
      input({
        value: state.apiBase,
        oninput: (e) => {
          state.apiBase.val = e.target.value;
          localStorage.setItem(storage.apiBase, state.apiBase.val);
        },
      }),
      button({ onclick: async () => guarded(async () => { await bootstrap(); pushNotice("Reconnected", "ok"); }) }, "Reconnect"),
      a({ href: "https://vanjs.org/start", target: "_blank", rel: "noreferrer" }, "VanJS start docs"),
    ),
  ),
  div({ class: "layout" },
    aside({ class: "panel sessions" },
      div({ class: "panel-head" }, h2("Sessions"), button({ onclick: () => guarded(createSession) }, "+")),
      ul(
        () => state.sessions.val.map((name) => li(
          button(
            {
              class: () => name === state.activeSession.val ? "session active" : "session",
              onclick: () => guarded(async () => loadSession(name)),
            },
            name,
          ),
        )),
      ),
    ),
    section({ class: "panel chat" },
      div({ class: "status" },
        span(() => `Status: ${state.status.val}`),
        span(() => `SSE: ${state.streamStatus.val}`),
        span(() => state.activeTaskId.val ? `Task: ${state.activeTaskId.val}` : "Task: none"),
      ),
      div({ class: "feed" }, () => state.messages.val.map(MessageItem)),
      textarea({
        placeholder: "Send a message to Mu-CLI…",
        value: state.input,
        oninput: (e) => (state.input.val = e.target.value),
        rows: 4,
        disabled: () => state.busy.val,
      }),
      div({ class: "actions" },
        button({ disabled: () => state.busy.val, onclick: () => guarded(sendMessage) }, () => state.busy.val ? "Running…" : "Send"),
        button({ onclick: () => guarded(refreshHistory) }, "Refresh history"),
      ),
    ),
    aside({ class: "panel notices" },
      h2("Notices"),
      div({ class: "notice-list" }, () => state.notices.val.map((n) => div({ class: `notice ${n.tone}` }, n.text))),
      p({ class: "error" }, () => state.streamError.val),
    ),
  ),
);

async function guarded(fn) {
  try {
    await fn();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    pushNotice(message, "error");
    state.status.val = "Error";
    state.busy.val = false;
  }
}

van.add(document.getElementById("app"), App());
guarded(bootstrap);
