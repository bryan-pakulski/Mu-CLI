const api = "";
let currentSession = null;
let currentJob = null;

const el = (id) => document.getElementById(id);

async function req(path, options = {}) {
  const res = await fetch(api + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function addTimeline(line) {
  const li = document.createElement("li");
  li.textContent = `${new Date().toLocaleTimeString()} ${line}`;
  el("timeline").prepend(li);
}

async function refreshSessions() {
  const sessions = await req("/sessions");
  const sel = el("session-select");
  sel.innerHTML = "";
  sessions.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.id;
    o.textContent = `${s.id.slice(0, 8)} (${s.mode})`;
    sel.appendChild(o);
  });
  if (sessions.length > 0) {
    currentSession = sel.value;
  }
}

async function refreshApprovals() {
  if (!currentSession) return;
  const approvals = await req(`/sessions/${currentSession}/approvals/pending`);
  const ul = el("approvals");
  ul.innerHTML = "";
  approvals.forEach((a) => {
    const li = document.createElement("li");
    li.textContent = `${a.tool_name}: ${a.reason}`;
    const approve = document.createElement("button");
    approve.textContent = "approve";
    approve.onclick = async () => {
      await req(`/jobs/${a.job_id}/approvals/${a.id}`, {
        method: "POST",
        body: JSON.stringify({ decision: "approved" }),
      });
      addTimeline(`approval ${a.id} approved`);
      refreshApprovals();
    };
    li.appendChild(approve);
    ul.appendChild(li);
  });
}

async function pollJob() {
  if (!currentJob) return;
  try {
    const job = await req(`/jobs/${currentJob}`);
    el("job-state").textContent = JSON.stringify(job, null, 2);
  } catch {
    return;
  }
}

el("refresh-sessions").onclick = refreshSessions;
el("session-select").onchange = (e) => {
  currentSession = e.target.value;
};

el("create-session").onclick = async () => {
  const workspace_path = el("workspace").value;
  const s = await req("/sessions", {
    method: "POST",
    body: JSON.stringify({ workspace_path, mode: "interactive" }),
  });
  currentSession = s.id;
  addTimeline(`session ${s.id} created`);
  await refreshSessions();
};

el("create-job").onclick = async () => {
  if (!currentSession) return;
  const goal = el("goal").value;
  const j = await req(`/sessions/${currentSession}/jobs`, {
    method: "POST",
    body: JSON.stringify({ goal }),
  });
  currentJob = j.id;
  addTimeline(`job ${j.id} created`);
  await pollJob();
};

el("cancel-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/cancel`, { method: "POST" });
  addTimeline(`job ${currentJob} cancel requested`);
  await pollJob();
};

el("resume-job").onclick = async () => {
  if (!currentJob) return;
  await req(`/jobs/${currentJob}/resume`, { method: "POST" });
  addTimeline(`job ${currentJob} resume requested`);
  await pollJob();
};

el("refresh-approvals").onclick = refreshApprovals;

el("save-config").onclick = async () => {
  if (!currentSession) return;
  const mode = el("mode").value;
  const policy_profile = el("policy").value;
  const ordered = el("providers").value.split(",").map((s) => s.trim()).filter(Boolean);
  const s = await req(`/sessions/${currentSession}`, {
    method: "PATCH",
    body: JSON.stringify({ mode, policy_profile, provider_preferences: { ordered } }),
  });
  addTimeline(`session ${s.id} config updated`);
};

refreshSessions().catch(console.error);
setInterval(pollJob, 1200);
