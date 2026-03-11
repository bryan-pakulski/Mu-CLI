// --- state reducers ---------------------------------------------------------
function updateBackgroundJobInState(job) {
  const idx = (state.backgroundJobs || []).findIndex((item) => item.id === job.id);
  if (idx >= 0) state.backgroundJobs[idx] = job;
  else state.backgroundJobs.push(job);
  renderSessions(state.sessions || [], state.activeSession || '');
  updateChatBusyState();
  maybeRecordJobTerminalNotice(job);
  if (job.session === state.activeSession) renderBackgroundActivity(job);
}


function askPlanApproval(job) {
  return new Promise((resolve) => {
    planApprovalResolver = resolve;
    document.getElementById('planApprovalMeta').textContent = `Session: ${job.session} · Job: ${job.id}`;
    document.getElementById('planApprovalContent').value = job.plan || '(No plan text returned)';
    showModal('planApprovalModal', true);
  });
}

async function sendPrompt(background = false) {
  if (sending) return;
  const text = document.getElementById('prompt').value.trim();
  if (!text) return;

  sending = true;
  sendingSession = selectedSessionName() || state.activeSession || null;
  updateThinking(true);
  updateChatBusyState();
  document.getElementById('prompt').value = '';

  const reportEl = document.getElementById('report');
  reportEl.textContent = 'streaming...';

  state.messages.push({ role: 'user', content: text });
  let draft = { role: 'assistant', content: '', metadata: { typing: true } };
  state.messages.push(draft);
  renderMessages();
  renderMetadataPanel();

  try {
    if (background) {
      const active = selectedSessionName();
      const bg = await api('/api/chat/background', 'POST', { text, session: active || undefined });
      reportEl.textContent = `background job started: ${bg.job_id}`;
      startBackgroundJobStream(bg.job_id, bg.session || active || state.activeSession || '');
      await refreshState();
      return;
    }

    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, session: selectedSessionName() || undefined }),
    });
    if (!res.ok || !res.body) throw new Error('stream request failed');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === 'assistant_chunk') {
          updateThinking(false);
          if (!draft) {
            draft = { role: 'assistant', content: '' };
            state.messages.push(draft);
          }
          if (draft.metadata && draft.metadata.typing) delete draft.metadata.typing;
          draft.content += event.chunk;
          renderMessages();
          renderMetadataPanel();
        } else if (event.type === 'trace') {
          state.traces.push(event.line);
          state.traces = state.traces.slice(-50);
          renderTraces();
          renderMetadataPanel();
          updateChatBusyState();
        } else if (event.type === 'report') {
          const r = event.report;
          reportEl.textContent = `in=${r.input_tokens} out=${r.output_tokens} total=${r.total_tokens} est=$${Number(r.estimated_cost_usd).toFixed(6)}`;
        } else if (event.type === 'error') {
          throw new Error(event.error || 'streaming failed');
        }
      }
    }

    await refreshState();
  } finally {
    updateThinking(false);
    sending = false;
    sendingSession = null;
    updateChatBusyState();
  }
}

function selectedSessionName() {
  const active = document.querySelector('#sessionList .session-item.active');
  return active ? (active.getAttribute('data-session-name') || '') : '';
}

function wireNewSessionModels(provider) {
  const modelSel = document.getElementById('newSessionModel');
  modelSel.innerHTML = '';
  for (const model of (state.models[provider] || [])) {
    const opt = document.createElement('option');
    opt.value = model;
    opt.textContent = model;
    modelSel.appendChild(opt);
  }
}

function openNewSessionModal() {
  const providerSel = document.getElementById('newSessionProvider');
  providerSel.innerHTML = '';
  Object.keys(state.models || {}).forEach((p) => {
    const o = document.createElement('option');
    o.value = p;
    o.textContent = p;
    providerSel.appendChild(o);
  });
  providerSel.value = document.getElementById('provider').value || providerSel.value;
  wireNewSessionModels(providerSel.value);
  document.getElementById('newSessionModel').value = document.getElementById('model').value;
  document.getElementById('newSessionWorkspace').value = document.getElementById('workspace').value || '';
  document.getElementById('newSessionAgentic').checked = document.getElementById('agentic').checked;
  document.getElementById('newSessionResearch').checked = document.getElementById('researchMode').checked;
  document.getElementById('newSessionName').value = '';
  showModal('newSessionModal', true);
}

async function createSessionFromModal() {
  const name = document.getElementById('newSessionName').value.trim();
  if (!name) throw new Error('Session name is required.');
  await sessionAction('new', name, {
    provider: document.getElementById('newSessionProvider').value,
    model: document.getElementById('newSessionModel').value,
    workspace: document.getElementById('newSessionWorkspace').value.trim() || null,
    agentic_planning: document.getElementById('newSessionAgentic').checked,
    research_mode: document.getElementById('newSessionResearch').checked,
    enabled_skills: buildEnabledSkillsPayload(),
  });
  showModal('newSessionModal', false);
}


function openCondenseModal(sessionName) {
  document.getElementById('condenseModalEnabled').checked = document.getElementById('condenseEnabled').checked;
  document.getElementById('condenseModalWindow').value = document.getElementById('condenseWindow').value || 12;
  document.getElementById('condenseModal').setAttribute('data-session', sessionName || state.activeSession || '');
  showModal('condenseModal', true);
}

async function runCondenseFromModal() {
  const sessionName = document.getElementById('condenseModal').getAttribute('data-session') || state.activeSession || '';
  const enabled = document.getElementById('condenseModalEnabled').checked;
  const window = Number(document.getElementById('condenseModalWindow').value || 12);
  await api('/api/session', 'POST', { action: 'switch', name: sessionName });
  await api('/api/settings', 'POST', { ...buildSettingsPayload(), condense_enabled: enabled, condense_window: window });
  await api('/api/session', 'POST', { action: 'condense', name: sessionName, window });
  showModal('condenseModal', false);
  await refreshState();
}

async function sessionAction(action, explicitName = null, extra = {}) {
  const name = explicitName || selectedSessionName();
  if (action === "switch" && name) completedSeenSessions.add(name);
  const status = document.getElementById('sessionActionStatus');
  status.textContent = '';
  await api('/api/session', 'POST', { action, name, ...extra });
  if (action === 'switch' && name) markSessionRecent(name);
  await refreshState();
  if (action === 'condense') status.textContent = 'Session context condensed.';
}

function byId(id) {
  return document.getElementById(id);
}

function runWithAlert(task) {
  Promise.resolve().then(task).catch((e) => alert(e.message));
}

function bindClick(id, handler) {
  const el = byId(id);
  if (!el) return;
  el.addEventListener('click', (event) => runWithAlert(() => handler(event)));
}

function bindChange(id, handler) {
  const el = byId(id);
  if (!el) return;
  el.addEventListener('change', (event) => runWithAlert(() => handler(event)));
}

function resolvePlanApproval(ok, revisedPlan = '') {
  showModal('planApprovalModal', false);
  if (!planApprovalResolver) return;
  const resolve = planApprovalResolver;
  planApprovalResolver = null;
  resolve({ ok, revisedPlan: String(revisedPlan || '').trim() });
}
