const api = "";
let currentSession = null;
let currentJob = null;

const el = (id) => document.getElementById(id);

async function req(path, options = {}) {
  const res = await fetch(api + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

function pushChat(tag, message) {
  const node = document.createElement("div");
  node.className = "message";
  node.innerHTML = `<div class="tag">${tag}</div><div>${message}</div>`;
  el("chat-window").prepend(node);
}

function addTimeline(line) {
  const li = document.createElement("li");
  li.textContent = `${new Date().toLocaleTimeString()}  ${line}`;
  el("timeline").prepend(li);
}

function updateSessionSummary(session) {
  if (!session) {
    el("session-summary").textContent = "No session selected.";
    return;
  }
  const providers = (session.provider_preferences?.ordered || []).join(", ");
  el("session-summary").textContent = `mode=${session.mode} | policy=${session.policy_profile} | providers=${providers}`;
  el("mode").value = session.mode || "interactive";
  el("policy").value = session.policy_profile || "default";
  el("providers").value = providers || "ollama";
}

async function refreshSessions() {
  const sessions = await req("/sessions");
  const sel = el("session-select");
  sel.innerHTML = "";

  sessions.forEach((s) => {
    const option = document.createElement("option");
    option.value = s.id;
    option.textContent = `${s.id.slice(0, 8)} · ${s.status} · ${s.mode}`;
    option.dataset.session = JSON.stringify(s);
    sel.appendChild(option);
  });

  if (sessions.length > 0) {
    if (!currentSession || !sessions.some((s) => s.id === currentSession)) {
      currentSession = sessions[0].id;
    }
    sel.value = currentSession;
    updateSessionSummary(sessions.find((s) => s.id === currentSession));
  } else {
    currentSession = null;
    updateSessionSummary(null);
  }
}

async function refreshApprovals() {
  if (!currentSession) {
    return;
  }

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
      await req(`/jobs/${a.job_id}/approvals/${a.id}`, {
        method: "POST",
        body: JSON.stringify({ decision: "approved" }),
      });
      addTimeline(`approval ${a.id.slice(0, 8)} approved`);
      pushChat("approval", `${a.tool_name} approved`);
      refreshApprovals();
    };

    li.appendChild(document.createElement("br"));
    li.appendChild(approve);
    ul.appendChild(li);
  });
}

async function pollJob() {
  if (!currentJob) return;
  try {
    const job = await req(`/jobs/${currentJob}`);
    el("job-state").textContent = JSON.stringify(job, null, 2);

    const events = await req(`/jobs/${currentJob}/events`);
    const latest = events.slice(-1)[0];
    if (latest) {
      addTimeline(`${latest.event_type}: ${JSON.stringify(latest.payload)}`);
    }
  } catch {
    // no-op
  }
}

el("session-select").onchange = async (event) => {
  currentSession = event.target.value;
  const session = await req(`/sessions/${currentSession}`);
  updateSessionSummary(session);
  addTimeline(`session switched to ${currentSession.slice(0, 8)}`);
};

el("create-session").onclick = async () => {
  const workspace_path = el("workspace").value;
  const created = await req("/sessions", {
    method: "POST",
    body: JSON.stringify({ workspace_path, mode: "interactive" }),
  });
  currentSession = created.id;
  pushChat("system", `session created: ${created.id}`);
  addTimeline(`session created ${created.id.slice(0, 8)}`);
  await refreshSessions();
};

el("refresh-sessions").onclick = refreshSessions;

el("create-job").onclick = async () => {
  if (!currentSession) return;
  const goal = el("goal").value;
  if (!goal.trim()) return;

  pushChat("user", goal);
  const job = await req(`/sessions/${currentSession}/jobs`, {
    method: "POST",
    body: JSON.stringify({ goal }),
  });
  currentJob = job.id;
  pushChat("agent", `job queued: ${job.id}`);
  addTimeline(`job created ${job.id.slice(0, 8)}`);
  el("goal").value = "";
  await pollJob();
};

el("cancel-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/cancel`, { method: "POST" });
  addTimeline(`cancel requested ${currentJob.slice(0, 8)}`);
  pushChat("control", "cancel requested");
};

el("resume-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/resume`, { method: "POST" });
  addTimeline(`resume requested ${currentJob.slice(0, 8)}`);
  pushChat("control", "resume requested");
};

el("save-config").onclick = async () => {
  if (!currentSession) return;
  const mode = el("mode").value;
  const policy_profile = el("policy").value;
  const ordered = el("providers")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  const updated = await req(`/sessions/${currentSession}`, {
    method: "PATCH",
    body: JSON.stringify({
      mode,
      policy_profile,
      provider_preferences: { ordered },
    }),
  });
  updateSessionSummary(updated);
  addTimeline(`config updated for ${updated.id.slice(0, 8)}`);
  pushChat("settings", "runtime settings updated");
};

el("refresh-approvals").onclick = refreshApprovals;

refreshSessions().then(refreshApprovals).catch(console.error);
setInterval(() => {
  pollJob();
  refreshApprovals();
}, 1500);
