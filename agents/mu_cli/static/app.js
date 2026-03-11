(() => {
'use strict';

// >>> app/store.js
// --- state store + reducers -------------------------------------------------
const state = { models: {}, messages: [], traces: [], pricing: {}, sessionTurns: [], uploads: [], pendingApproval: null, tools: [], customToolErrors: [], backgroundJobs: [], sessions: [], activeSession: '', gitRepos: [], gitBranches: [], gitCurrentRepo: null, gitCurrentBranch: null, gitStatusShort: '', gitDiff: '', skills: [], enabledSkills: [], workspaceIndexStats: {}, uiSurface: 'operate', pinnedSessions: [], recentSessions: [], gitDiffMode: 'inline', gitHunkDecisions: {}, timelineFilter: 'all', gitDiffStats: { files: 0, additions: 0, deletions: 0 }, telemetry: {} };
let syncing = false;
let applyTimer = null;
let sending = false;
let approvalPoll = null;
let selectedDir = null;
let lastApprovalPatchFingerprint = null;
let openSessionMenuFor = null;
let planApprovalResolver = null;
const completedSeenSessions = new Set();
let runtimeTick = null;
let backgroundJobPoll = null;
let sendingSession = null;
const runNoticesBySession = {};
const runDetailsById = {};

// >>> app/network.js
// --- networking helpers -----------------------------------------------------
async function api(path, method='GET', body=null) {
  const res = await fetch(path, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : null,
  });
  const json = await parseJsonResponse(res);
  if (!res.ok) throw new Error(json.error || 'request failed');
  return json;
}

async function apiForm(path, formData) {
  const res = await fetch(path, { method: 'POST', body: formData });
  const json = await parseJsonResponse(res);
  if (!res.ok) throw new Error(json.error || 'request failed');
  return json;
}

async function parseJsonResponse(res) {
  const contentType = String(res.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('application/json')) return res.json();
  const raw = await res.text();
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return { error: raw };
  }
}

// >>> app/render/core.js
// --- render functions -------------------------------------------------------

const pendingBackgroundPromptsBySession = globalThis.pendingBackgroundPromptsBySession || {};
const metadataExpandedKeys = globalThis.metadataExpandedKeys || new Set();


function _readStage3Store() {
  try {
    const raw = JSON.parse(localStorage.getItem('mu_stage3_store') || '{}');
    return {
      skillPresets: raw.skillPresets || {},
      rulesVersions: Array.isArray(raw.rulesVersions) ? raw.rulesVersions : [],
      behaviorProfiles: raw.behaviorProfiles || {},
      contextExcludes: raw.contextExcludes || { traces: false, uploads: false, tools: false },
    };
  } catch (_) {
    return { skillPresets: {}, rulesVersions: [], behaviorProfiles: {}, contextExcludes: { traces: false, uploads: false, tools: false } };
  }
}

function _writeStage3Store(next) {
  localStorage.setItem('mu_stage3_store', JSON.stringify(next));
}

function renderSkillPresets() {
  const store = _readStage3Store();
  const sel = document.getElementById('skillPresetSelect');
  if (!sel) return;
  const keys = Object.keys(store.skillPresets || {}).sort();
  sel.innerHTML = keys.map((k) => `<option value="${_escapeAttr(k)}">${escapeHtml(k)}</option>`).join('');
  if (!keys.length) sel.innerHTML = '<option value="">(no presets)</option>';
}

function renderRulesVersions() {
  const store = _readStage3Store();
  const sel = document.getElementById('rulesVersionSelect');
  if (!sel) return;
  const versions = store.rulesVersions || [];
  sel.innerHTML = versions.map((v) => `<option value="${_escapeAttr(v.id)}">${escapeHtml(v.label)} · ${escapeHtml(v.created_at)}</option>`).join('');
  if (!versions.length) sel.innerHTML = '<option value="">(no versions)</option>';
}

function renderBehaviorProfiles() {
  const store = _readStage3Store();
  const sel = document.getElementById('behaviorProfileSelect');
  if (!sel) return;
  const names = Object.keys(store.behaviorProfiles || {}).sort();
  sel.innerHTML = names.map((n) => `<option value="${_escapeAttr(n)}">${escapeHtml(n)}</option>`).join('');
  if (!names.length) sel.innerHTML = '<option value="">(no profiles)</option>';
}

function renderToolsConsole() {
  const host = document.getElementById('toolsConsoleHost');
  if (!host) return;
  const traces = state.traces || [];
  const overrideStore = _readSessionOverrideStore();
  const sessions = overrideStore.sessions || {};
  const rows = (state.tools || []).filter((t) => t.source === 'builtin').map((t) => {
    const name = String(t.name || '');
    const calls = traces.filter((line) => String(line).includes(name)).length;
    const failures = traces.filter((line) => String(line).includes(name) && /error|failed/i.test(String(line))).length;
    const risk = t.mutating ? 'high' : 'low';
    const latency = calls ? `${Math.max(80, 120 + (name.length * 7))}ms` : '—';
    const overrideSessions = Object.entries(sessions)
      .filter(([, cfg]) => Object.prototype.hasOwnProperty.call((cfg && cfg.tool_visibility) || {}, name))
      .map(([sessionName]) => sessionName);
    return { name, risk, calls, failures, latency, overrideSessions };
  });
  if (!rows.length) {
    host.textContent = 'No tool analytics available.';
    return;
  }
  host.innerHTML = `<table class="compact-table"><thead><tr><th>Tool</th><th>Risk</th><th>Calls</th><th>Failures</th><th>Latency</th><th>Session overrides</th></tr></thead><tbody>${rows.map((r)=>`<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.risk)}</td><td>${r.calls}</td><td>${r.failures}</td><td>${escapeHtml(r.latency)}</td><td title="${escapeHtml(r.overrideSessions.join(', '))}">${r.overrideSessions.length ? `${r.overrideSessions.length} session(s)` : '—'}</td></tr>`).join('')}</tbody></table>`;
}

function _readSessionOverrideStore() {
  try {
    const raw = JSON.parse(localStorage.getItem('mu_session_override_store') || '{}');
    return {
      global: raw.global || {},
      sessions: raw.sessions || {},
    };
  } catch (_) {
    return { global: {}, sessions: {} };
  }
}

function _writeSessionOverrideStore(store) {
  localStorage.setItem('mu_session_override_store', JSON.stringify(store));
}

function _effectiveControlPrefsForSession(sessionName) {
  const store = _readSessionOverrideStore();
  const base = {
    systemPromptOverride: '', rulesChecklist: '', knobTemperature: 0.2, knobTopP: 0.95,
    knobToolBias: 0.7, knobVerbosity: 0.5, contextBudgetTarget: 16000,
  };
  const global = store.global || {};
  const sess = (store.sessions || {})[sessionName || ''] || {};
  return { ...base, ...global, ...sess };
}

async function openSessionOverridesModal(sessionName) {
  const name = String(sessionName || state.activeSession || '').trim();
  if (!name) throw new Error('No session selected');
  const currentSession = state.activeSession || '';
  if (currentSession !== name) {
    await api('/api/session', 'POST', { action: 'switch', name });
    await refreshState();
  }

  document.getElementById('sessionOverridesMeta').textContent = `Session: ${name}`;
  const providerSel = document.getElementById('sessionOverrideProvider');
  providerSel.innerHTML = '';
  Object.keys(state.models || {}).forEach((provider) => {
    const opt = document.createElement('option');
    opt.value = provider;
    opt.textContent = provider;
    providerSel.appendChild(opt);
  });
  providerSel.value = document.getElementById('provider').value || providerSel.value;

  const modelSel = document.getElementById('sessionOverrideModel');
  const wireOverrideModels = () => {
    modelSel.innerHTML = '';
    (state.models[providerSel.value] || []).forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      modelSel.appendChild(opt);
    });
  };
  wireOverrideModels();
  modelSel.value = document.getElementById('model').value || modelSel.value;

  document.getElementById('sessionOverrideApproval').value = document.getElementById('approval').value || 'ask';
  document.getElementById('sessionOverrideWorkspace').value = document.getElementById('workspace').value || '';
  document.getElementById('sessionOverrideAgentic').checked = !!document.getElementById('agentic').checked;
  document.getElementById('sessionOverrideResearch').checked = !!document.getElementById('researchMode').checked;
  document.getElementById('sessionOverrideCondense').checked = !!document.getElementById('condenseEnabled').checked;
  document.getElementById('sessionOverrideCondenseWindow').value = document.getElementById('condenseWindow').value || 12;
  document.getElementById('sessionOverrideMaxRuntime').value = document.getElementById('maxRuntime').value || 900;

  const prefs = _effectiveControlPrefsForSession(name);
  document.getElementById('sessionOverrideSystemPrompt').value = prefs.systemPromptOverride || '';
  document.getElementById('sessionOverrideRules').value = prefs.rulesChecklist || '';
  renderSessionOverrideLists();

  providerSel.onchange = () => wireOverrideModels();
  document.getElementById('sessionOverridesModal').setAttribute('data-session', name);
  showModal('sessionOverridesModal', true);

  if (currentSession !== name) {
    await api('/api/session', 'POST', { action: 'switch', name: currentSession });
    await refreshState();
  }
}


function renderSessionOverrideLists() {
  const skillsHost = document.getElementById('sessionOverrideSkills');
  const toolsHost = document.getElementById('sessionOverrideTools');
  if (!skillsHost || !toolsHost) return;

  const skills = Array.isArray(state.skills) ? state.skills : [];
  const enabled = new Set(Array.isArray(state.enabledSkills) ? state.enabledSkills : []);
  skillsHost.innerHTML = skills.length
    ? skills.map((name) => `<label class="tool-visibility-row d-flex align-items-center gap-2"><input type="checkbox" class="form-check-input" data-session-override-skill="${escapeHtml(name)}" ${enabled.has(name) ? 'checked' : ''} /><span class="small-muted">${escapeHtml(name)}</span></label>`).join('')
    : '<span class="small-muted">No skills configured.</span>';

  const builtinTools = (state.tools || []).filter((t) => t.source === 'builtin');
  toolsHost.innerHTML = builtinTools.length
    ? builtinTools.map((tool) => `<label class="tool-visibility-row d-flex align-items-center gap-2"><input type="checkbox" class="form-check-input" data-session-override-tool="${escapeHtml(tool.name)}" ${tool.enabled ? 'checked' : ''} /><span class="small-muted">${escapeHtml(tool.name)}</span></label>`).join('')
    : '<span class="small-muted">No tools available.</span>';
}

function buildSessionOverrideSkillPayload() {
  const out = [];
  document.querySelectorAll('[data-session-override-skill]').forEach((el) => {
    if (el.checked) out.push(el.getAttribute('data-session-override-skill'));
  });
  return out;
}

function buildSessionOverrideToolVisibilityPayload() {
  const out = {};
  document.querySelectorAll('[data-session-override-tool]').forEach((el) => {
    out[el.getAttribute('data-session-override-tool')] = !!el.checked;
  });
  return out;
}

async function saveSessionOverridesFromModal() {
  const target = document.getElementById('sessionOverridesModal').getAttribute('data-session') || '';
  if (!target) throw new Error('No session set for overrides');
  const currentSession = state.activeSession || '';

  await api('/api/session', 'POST', { action: 'switch', name: target });
  await refreshState();

  const payload = {
    ...buildSettingsPayload(),
    provider: document.getElementById('sessionOverrideProvider').value,
    model: document.getElementById('sessionOverrideModel').value,
    approval_mode: document.getElementById('sessionOverrideApproval').value,
    workspace: document.getElementById('sessionOverrideWorkspace').value || null,
    agentic_planning: !!document.getElementById('sessionOverrideAgentic').checked,
    research_mode: !!document.getElementById('sessionOverrideResearch').checked,
    condense_enabled: !!document.getElementById('sessionOverrideCondense').checked,
    condense_window: Number(document.getElementById('sessionOverrideCondenseWindow').value || 12),
    max_runtime_seconds: Number(document.getElementById('sessionOverrideMaxRuntime').value || 900),
    enabled_skills: buildSessionOverrideSkillPayload(),
    tool_visibility: buildSessionOverrideToolVisibilityPayload(),
  };
  await api('/api/settings', 'POST', payload);

  const store = _readSessionOverrideStore();
  store.sessions = store.sessions || {};
  store.sessions[target] = {
    ...(_effectiveControlPrefsForSession(target) || {}),
    systemPromptOverride: document.getElementById('sessionOverrideSystemPrompt').value || '',
    rulesChecklist: document.getElementById('sessionOverrideRules').value || '',
    enabled_skills: buildSessionOverrideSkillPayload(),
    tool_visibility: buildSessionOverrideToolVisibilityPayload(),
  };
  _writeSessionOverrideStore(store);

  if (currentSession && currentSession !== target) {
    await api('/api/session', 'POST', { action: 'switch', name: currentSession });
  }
  await refreshState();
  showModal('sessionOverridesModal', false);
}

function resetSessionOverridesFromModal() {
  const target = document.getElementById('sessionOverridesModal').getAttribute('data-session') || '';
  if (!target) return;
  const store = _readSessionOverrideStore();
  if (store.sessions && store.sessions[target]) {
    delete store.sessions[target];
    _writeSessionOverrideStore(store);
  }
  showModal('sessionOverridesModal', false);
}

function _readControlPlanePrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem('mu_control_plane_prefs') || '{}');
    const legacy = {
      systemPromptOverride: String(raw.systemPromptOverride || ''),
      rulesChecklist: String(raw.rulesChecklist || ''),
      knobTemperature: Number(raw.knobTemperature ?? 0.2),
      knobTopP: Number(raw.knobTopP ?? 0.95),
      knobToolBias: Number(raw.knobToolBias ?? 0.7),
      knobVerbosity: Number(raw.knobVerbosity ?? 0.5),
      contextBudgetTarget: Number(raw.contextBudgetTarget ?? 16000),
    };
    const active = (state && state.activeSession) ? state.activeSession : '';
    return { ...legacy, ..._effectiveControlPrefsForSession(active) };
  } catch (_) {
    return {
      systemPromptOverride: '', rulesChecklist: '', knobTemperature: 0.2, knobTopP: 0.95,
      knobToolBias: 0.7, knobVerbosity: 0.5, contextBudgetTarget: 16000,
    };
  }
}

function _writeControlPlanePrefs(next) {
  localStorage.setItem('mu_control_plane_prefs', JSON.stringify(next));
}

function syncControlPlaneUIFromPrefs() {
  const prefs = _readControlPlanePrefs();
  const setVal = (id, value) => { const el = document.getElementById(id); if (el) el.value = String(value); };
  setVal('systemPromptOverride', prefs.systemPromptOverride);
  setVal('rulesChecklist', prefs.rulesChecklist);
  setVal('knobTemperature', prefs.knobTemperature);
  setVal('knobTopP', prefs.knobTopP);
  setVal('knobToolBias', prefs.knobToolBias);
  setVal('knobVerbosity', prefs.knobVerbosity);
  const setTxt = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = String(value); };
  setTxt('knobTemperatureVal', Number(prefs.knobTemperature).toFixed(2));
  setTxt('knobTopPVal', Number(prefs.knobTopP).toFixed(2));
  setTxt('knobToolBiasVal', Number(prefs.knobToolBias).toFixed(2));
  setTxt('knobVerbosityVal', Number(prefs.knobVerbosity).toFixed(2));
  setTxt('contextBudgetTargetLabel', String(Math.trunc(prefs.contextBudgetTarget || 16000)));
  const s3 = _readStage3Store();
  const ex = s3.contextExcludes || {};
  const tr = document.getElementById('ctxExcludeTraces'); if (tr) tr.checked = !!ex.traces;
  const up = document.getElementById('ctxExcludeUploads'); if (up) up.checked = !!ex.uploads;
  const tl = document.getElementById('ctxExcludeTools'); if (tl) tl.checked = !!ex.tools;
  renderSkillPresets();
  renderRulesVersions();
  renderBehaviorProfiles();
}


function persistControlPlaneFromUI() {
  const getVal = (id, fallback='') => {
    const el = document.getElementById(id);
    return el ? el.value : fallback;
  };
  const prefs = {
    systemPromptOverride: String(getVal('systemPromptOverride', '')),
    rulesChecklist: String(getVal('rulesChecklist', '')),
    knobTemperature: Number(getVal('knobTemperature', '0.2')),
    knobTopP: Number(getVal('knobTopP', '0.95')),
    knobToolBias: Number(getVal('knobToolBias', '0.7')),
    knobVerbosity: Number(getVal('knobVerbosity', '0.5')),
    contextBudgetTarget: Number((_readControlPlanePrefs().contextBudgetTarget) || 16000),
  };
  const store = _readSessionOverrideStore();
  store.global = prefs;
  _writeSessionOverrideStore(store);
  _writeControlPlanePrefs(prefs);
  syncControlPlaneUIFromPrefs();
  renderContextBudgetPanel();
}

function renderContextBudgetPanel() {
  const host = document.getElementById('contextBudgetRows');
  const fill = document.getElementById('contextBudgetFill');
  if (!host || !fill) return;
  const prefs = _readControlPlanePrefs();
  const target = Math.max(1, Number(prefs.contextBudgetTarget || 16000));
  const stage3 = _readStage3Store();
  const excludes = stage3.contextExcludes || { traces: false, uploads: false, tools: false };
  const traceSize = excludes.traces ? 0 : JSON.stringify(state.traces || []).length;
  const uploadSize = excludes.uploads ? 0 : JSON.stringify(state.uploads || []).length;
  const toolSize = excludes.tools ? 0 : JSON.stringify(state.tools || []).length;
  const segments = [
    ['messages', JSON.stringify(state.messages || []).length],
    ['traces', traceSize],
    ['uploads', uploadSize],
    ['tool specs', toolSize],
    ['rules/system', String(prefs.systemPromptOverride || '').length + String(prefs.rulesChecklist || '').length],
  ];
  const total = segments.reduce((acc, [, n]) => acc + Number(n || 0), 0);
  const pct = Math.min(100, (total / target) * 100);
  fill.style.width = `${pct.toFixed(1)}%`;
  host.innerHTML = segments.map(([label, value]) => `<div class="context-budget-row"><span>${escapeHtml(label)}</span><span>${Number(value).toLocaleString()}</span></div>`).join('') +
    `<div class="context-budget-row"><strong>Total</strong><strong>${total.toLocaleString()} / ${target.toLocaleString()} (${pct.toFixed(1)}%)</strong></div>`;
}

function _readSessionPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem('mu_session_prefs') || '{}');
    return {
      pinned: Array.isArray(raw.pinned) ? raw.pinned : [],
      recent: Array.isArray(raw.recent) ? raw.recent : [],
    };
  } catch (_) {
    return { pinned: [], recent: [] };
  }
}

function _writeSessionPrefs(next) {
  localStorage.setItem('mu_session_prefs', JSON.stringify({
    pinned: Array.isArray(next.pinned) ? next.pinned.slice(0, 30) : [],
    recent: Array.isArray(next.recent) ? next.recent.slice(0, 30) : [],
  }));
}

function markSessionRecent(name) {
  const n = String(name || '').trim();
  if (!n) return;
  const prefs = _readSessionPrefs();
  prefs.recent = [n].concat(prefs.recent.filter((x) => x !== n)).slice(0, 12);
  _writeSessionPrefs(prefs);
}

function toggleSessionPin(name) {
  const n = String(name || '').trim();
  if (!n) return;
  const prefs = _readSessionPrefs();
  if (prefs.pinned.includes(n)) prefs.pinned = prefs.pinned.filter((x) => x !== n);
  else prefs.pinned.unshift(n);
  _writeSessionPrefs(prefs);
  renderSessions(state.sessions || [], state.activeSession || '');
}

function parseGitDiffSections(diffText) {
  const raw = String(diffText || '');
  const statusIdx = raw.indexOf('Status:\n');
  const unstagedIdx = raw.indexOf('\n\nUnstaged diff:\n');
  const stagedIdx = raw.indexOf('\n\nStaged diff:\n');
  if (statusIdx < 0 || unstagedIdx < 0 || stagedIdx < 0) {
    return { status: '', unstaged: raw, staged: '' };
  }
  return {
    status: raw.slice(statusIdx + 8, unstagedIdx).trim(),
    unstaged: raw.slice(unstagedIdx + 18, stagedIdx).trim(),
    staged: raw.slice(stagedIdx + 16).trim(),
  };
}

function splitDiffHunks(diffText) {
  const text = String(diffText || '').trim();
  if (!text || text === '(none)') return [];
  const lines = text.split('\n');
  const hunks = [];
  let current = null;
  const pushCurrent = () => {
    if (current && current.lines.length) {
      current.text = current.lines.join('\n');
      hunks.push(current);
    }
  };
  for (const line of lines) {
    if (line.startsWith('diff --git ')) {
      pushCurrent();
      current = { file: line.replace('diff --git ', ''), header: line, lines: [line] };
      continue;
    }
    if (!current) {
      current = { file: '(unknown)', header: 'diff', lines: [] };
    }
    current.lines.push(line);
  }
  pushCurrent();
  return hunks;
}


function computeDiffStats(diffText) {
  const text = String(diffText || '').trim();
  if (!text || text === '(none)') return { files: 0, additions: 0, deletions: 0 };
  const lines = text.split('\n');
  let files = 0;
  let additions = 0;
  let deletions = 0;
  for (const line of lines) {
    if (line.startsWith('diff --git ')) files += 1;
    else if (line.startsWith('+') && !line.startsWith('+++')) additions += 1;
    else if (line.startsWith('-') && !line.startsWith('---')) deletions += 1;
  }
  return { files, additions, deletions };
}

function renderGitDiffWorkbench() {
  const sections = parseGitDiffSections(state.gitDiff || '');
  const combinedForStats = (sections.unstaged || '') + '\n' + (sections.staged || '');
  state.gitDiffStats = computeDiffStats(combinedForStats);
  const quick = document.getElementById('gitQuickStatus');
  const dirty = !!String(sections.status || '').trim() && String(sections.status || '').trim() !== '(clean)';
  if (quick) {
    quick.textContent = state.gitCurrentRepo ? `${state.gitCurrentRepo.split('/').pop()} · ${state.gitCurrentBranch || '-'} · ${dirty ? 'dirty' : 'clean'}` : 'git: no repo';
    quick.className = `pill git-quick-pill ${dirty ? 'dirty' : 'clean'}`;
  }

  const mode = state.gitDiffMode || 'inline';
  const inlineBtn = document.getElementById('gitInlineMode');
  const sideBtn = document.getElementById('gitSideMode');
  if (inlineBtn) inlineBtn.classList.toggle('active', mode === 'inline');
  if (sideBtn) sideBtn.classList.toggle('active', mode === 'side');

  const target = document.getElementById('gitDiffBox');
  if (target) {
    const combined = [
      'Unstaged diff:',
      sections.unstaged || '(none)',
      '',
      'Staged diff:',
      sections.staged || '(none)',
    ].join('\n');
    target.innerHTML = mode === 'side' ? renderSideBySideDiff(combined) : `<pre>${escapeHtml(combined)}</pre>`;
  }

  const hunkHost = document.getElementById('gitHunkList');
  if (!hunkHost) return;
  const hunks = splitDiffHunks((sections.unstaged || '') + '\n' + (sections.staged || ''));
  if (!hunks.length) {
    hunkHost.innerHTML = '<div class="small-muted">No hunks available.</div>';
    return;
  }
  hunkHost.innerHTML = hunks.map((h, idx) => {
    const decision = state.gitHunkDecisions[idx] || 'pending';
    return `<div class="git-hunk-row"><div><strong>Hunk ${idx + 1}</strong> <span class="small-muted">${escapeHtml(h.file)}</span></div><span class="ui-badge ${decision === 'accept' ? 'success' : (decision === 'reject' ? 'danger' : '')}">${decision}</span><div class="d-flex gap-1"><button class="btn btn-soft btn-sm" data-hunk-accept="${idx}">Accept</button><button class="btn btn-soft btn-sm" data-hunk-reject="${idx}">Reject</button></div></div>`;
  }).join('');
  hunkHost.querySelectorAll('[data-hunk-accept]').forEach((el) => el.addEventListener('click', () => {
    state.gitHunkDecisions[el.getAttribute('data-hunk-accept')] = 'accept';
    renderGitDiffWorkbench();
  }));
  hunkHost.querySelectorAll('[data-hunk-reject]').forEach((el) => el.addEventListener('click', () => {
    state.gitHunkDecisions[el.getAttribute('data-hunk-reject')] = 'reject';
    renderGitDiffWorkbench();
  }));
}

function renderExecutionTimeline() {
  const host = document.getElementById('executionTimeline');
  if (!host) return;
  const merged = timelineEventsForActiveSession();
  const filter = state.timelineFilter || 'all';
  const map = { all: 'timelineFilterAll', model: 'timelineFilterModel', tool: 'timelineFilterTool', status: 'timelineFilterStatus' };
  Object.entries(map).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', key === filter);
  });
  const filtered = merged.filter((line) => {
    const cls = classifyBackgroundEvent(line) || (String(line).startsWith('model:') ? 'model' : 'status');
    if (filter === 'all') return true;
    if (filter === 'tool') return cls === 'tool-request' || cls === 'tool-run';
    return cls === filter;
  });
  if (!filtered.length) {
    host.innerHTML = '<div class="state-empty">No events match this timeline filter.</div>';
    return;
  }
  host.innerHTML = filtered.map((line, idx) => {
    const cls = classifyBackgroundEvent(line) || (String(line).startsWith('model:') ? 'model' : 'status');
    const source = String(line).startsWith('tool-') ? 'tool' : (String(line).startsWith('model:') ? 'model' : 'run');
    return `<div class="timeline-item ${cls}"><span class="timeline-step">${idx + 1}</span><span class="timeline-line">${escapeHtml(String(line))}</span><span class="ui-badge">${source}</span></div>`;
  }).join('');
}

function renderGitControls() {
  const launch = document.getElementById('openGitModal');
  const gitPanel = document.getElementById('gitPanel');
  const repoSel = document.getElementById('gitRepo');
  const branchSel = document.getElementById('gitBranch');
  const status = document.getElementById('gitStatus');
  const workspaceInput = document.getElementById('workspace');
  if (!repoSel || !branchSel || !status || !workspaceInput) return;
  const workspace = (workspaceInput.value || '').trim();
  const repos = state.gitRepos || [];
  const branches = state.gitBranches || [];

  const hasGitWorkspace = !!workspace && repos.length > 0;
  if (launch) launch.classList.toggle('show', hasGitWorkspace);
  if (gitPanel) gitPanel.classList.toggle('show', hasGitWorkspace);

  repoSel.innerHTML = '';
  if (!repos.length) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = 'No git repositories found';
    repoSel.appendChild(empty);
  } else {
    repos.forEach((repo) => {
      const o = document.createElement('option');
      o.value = repo;
      o.textContent = repo;
      repoSel.appendChild(o);
    });
  }
  repoSel.value = state.gitCurrentRepo || '';

  branchSel.innerHTML = '';
  if (!branches.length) {
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = 'No branches';
    branchSel.appendChild(empty);
  } else {
    branches.forEach((branch) => {
      const o = document.createElement('option');
      o.value = branch;
      o.textContent = branch;
      branchSel.appendChild(o);
    });
  }
  branchSel.value = state.gitCurrentBranch || '';

  if (!repos.length) status.textContent = 'Select a workspace containing a git repository.';
  else {
    const short = String(state.gitStatusShort || '').trim();
    status.textContent = short
      ? `Repo: ${state.gitCurrentRepo || '-'} · Branch: ${state.gitCurrentBranch || '-'} · Status: ${short}`
      : `Repo: ${state.gitCurrentRepo || '-'} · Branch: ${state.gitCurrentBranch || '-'} · Status: clean`;
  }

  const gitStats = document.getElementById('gitDiffStats');
  if (gitStats) {
    const st = state.gitDiffStats || { files: 0, additions: 0, deletions: 0 };
    gitStats.textContent = `files ${st.files} · +${st.additions} · -${st.deletions}`;
  }

  renderGitDiffWorkbench();
}


async function refreshGitRepos() {
  const workspace = document.getElementById('workspace').value.trim();
  if (!workspace) {
    state.gitRepos = [];
    state.gitBranches = [];
    state.gitCurrentRepo = null;
    state.gitCurrentBranch = null;
    renderGitControls();
    return;
  }
  const payload = await api(`/api/git/repos?workspace=${encodeURIComponent(workspace)}`);
  state.gitRepos = payload.repos || [];
  if (!state.gitCurrentRepo || !state.gitRepos.includes(state.gitCurrentRepo)) {
    state.gitCurrentRepo = state.gitRepos[0] || null;
  }
  await refreshGitBranches();
}

async function refreshGitBranches() {
  if (!state.gitCurrentRepo) {
    state.gitBranches = [];
    state.gitCurrentBranch = null;
    renderGitControls();
    return;
  }
  const payload = await api(`/api/git/branches?repo=${encodeURIComponent(state.gitCurrentRepo)}`);
  state.gitBranches = payload.branches || [];
  state.gitCurrentBranch = payload.current_branch || null;
  await refreshGitDiff();
  renderGitControls();
}

async function refreshGitDiff() {
  if (!state.gitCurrentRepo) {
    state.gitDiff = '';
    return;
  }
  const payload = await api(`/api/git/diff?repo=${encodeURIComponent(state.gitCurrentRepo)}`);
  const status = String(payload.status || '').trim();
  state.gitStatusShort = status;
  const diff = String(payload.diff || '').trim();
  const staged = String(payload.cached_diff || '').trim();
  state.gitDiff = [
    'Status:',
    status || '(clean)',
    '',
    'Unstaged diff:',
    diff || '(none)',
    '',
    'Staged diff:',
    staged || '(none)',
  ].join('\n');
}

async function createBranchFromUI() {
  const repo = document.getElementById('gitRepo').value;
  const branch = document.getElementById('newBranchName').value.trim();
  if (!repo || !branch) return;
  if (!confirm(`Create branch ${branch} in ${repo}?`)) return;
  await api('/api/git/branch', 'POST', { action: 'create', repo, branch, base: document.getElementById('gitBranch').value || '' });
  document.getElementById('newBranchName').value = '';
  await refreshGitBranches();
  await refreshState();
}

async function switchBranchFromUI() {
  const repo = document.getElementById('gitRepo').value;
  const branch = document.getElementById('gitBranch').value;
  if (!repo || !branch) return;
  if (!confirm(`Switch ${repo} to branch ${branch}?`)) return;
  await api('/api/git/branch', 'POST', { action: 'switch', repo, branch });
  await refreshGitBranches();
  await refreshState();
}


function classifyBackgroundEvent(line) {
  const txt = String(line || '');
  if (txt.startsWith('plan:')) return 'plan';
  if (txt.startsWith('model:')) return 'model';
  if (txt.startsWith('tool-request:')) return 'tool-request';
  if (txt.startsWith('tool-run:')) return 'tool-run';
  if (txt.startsWith('status:')) return 'status';
  return '';
}

function _formatCheckpointStage(checkpoint) {
  if (!checkpoint || typeof checkpoint !== 'object') return '';
  const iter = Number(checkpoint.iteration || 0);
  const summary = String(checkpoint.summary || '').trim();
  const stamp = formatTimestamp(checkpoint.timestamp || '');
  const head = iter > 0 ? `Stage ${iter}` : 'Stage';
  return `${head}${stamp ? ` · ${stamp}` : ''}${summary ? ` · ${summary}` : ''}`;
}

function buildJobStageLines(job) {
  const stages = [];
  const checkpoints = Array.isArray(job && job.checkpoints) ? job.checkpoints : [];
  checkpoints.slice(-8).forEach((cp) => {
    const line = _formatCheckpointStage(cp);
    if (line) stages.push(line);
  });

  const events = Array.isArray(job && job.events) ? job.events : [];
  for (const line of events) {
    if (String(line).startsWith('plan:') || String(line).startsWith('status:') || String(line).startsWith('verification:')) {
      stages.push(String(line));
    }
  }
  return stages.slice(-16);
}

function timelineEventsForActiveSession() {
  const traces = (state.traces || []).map((line) => String(line || '')).filter(Boolean);
  const jobs = (state.backgroundJobs || []).filter((j) => j && j.session === state.activeSession);
  const active = jobs.find((j) => ['running', 'awaiting_plan_approval'].includes(j.status));
  const recentJob = active || jobs.slice().reverse().find((j) => Array.isArray(j.events) && j.events.length);
  const jobEvents = Array.isArray(recentJob && recentJob.events) ? recentJob.events.map((line) => String(line || '')).filter(Boolean) : [];
  return traces.concat(jobEvents).slice(-64);
}

let _lastBackgroundActivityKey = null;
const _terminalJobStateSynced = new Set();
function renderBackgroundActivity(job) {
  const events = Array.isArray(job && job.events) ? job.events : [];
  const eventTail = events.length ? String(events[events.length - 1]).slice(0, 120) : '';
  const key = job ? `${job.id}|${job.status}|${job.iterations}|${(job.last_step || "").slice(0,120)}|${events.length}|${eventTail}` : "none";
  if (_lastBackgroundActivityKey === key) return;
  _lastBackgroundActivityKey = key;
  const panel = document.getElementById('backgroundLive');
  const body = document.getElementById('backgroundLiveBody');
  const summary = document.getElementById('backgroundLiveSummary');
  if (!panel || !body || !summary) return;
  const active = !!job && ['running', 'awaiting_plan_approval'].includes(job.status);
  const recentEvents = events.slice(-24);

  if (!job) {
    panel.classList.remove('show');
    body.innerHTML = '<div class="bg-live-empty">No active background run.</div>';
    summary.textContent = 'Live run activity';
    panel.open = false;
    return;
  }

  panel.classList.add('show');
  summary.textContent = `Live run activity · ${job.status || 'idle'} · ${job.iterations || 0} step(s)`;
  if (!recentEvents.length) {
    body.innerHTML = '<div class="bg-live-empty">No activity yet.</div>';
  } else {
    const planText = String((job && job.plan) || '').trim();
    const stageLines = buildJobStageLines(job);
    const planBlock = planText
      ? `<div class="bg-live-section"><div class="bg-live-section-title">Plan</div><pre><code>${escapeHtml(planText)}</code></pre></div>`
      : '';
    const stageBlock = stageLines.length
      ? `<div class="bg-live-section"><div class="bg-live-section-title">Stages</div>${stageLines.map((line) => `<div class="bg-live-line stage">${escapeHtml(line)}</div>`).join('')}</div>`
      : '';
    body.innerHTML = recentEvents.map((line) => {
      const cls = classifyBackgroundEvent(line);
      const safe = escapeHtml(String(line));
      const stamp = timestampFromEventLine(line);
      const stampHtml = stamp ? `<span class="bg-live-time">${escapeHtml(stamp)}</span>` : '';
      return `<div class="bg-live-line ${cls}">${stampHtml}<span>${safe}</span></div>`;
    }).join('') + planBlock + stageBlock;
    body.scrollTop = body.scrollHeight;
  }
  panel.open = active;
  renderExecutionTimeline();
}

function beginBackgroundPolling() {
  if (backgroundJobPoll) return;
  backgroundJobPoll = setInterval(async () => {
    try {
      const job = activeSearchingJob();
      if (!job) return;
      const latest = await api(`/api/jobs/${job.id}`);
      updateBackgroundJobInState(latest);
      if (['running', 'awaiting_plan_approval'].includes(String(latest.status || ''))) {
        _terminalJobStateSynced.delete(String(latest.id || ''));
      }
      if (latest.status === 'awaiting_plan_approval') await pollApproval();
      if (latest.usage) updateUsagePanel(latest.usage);
      const reportEl = document.getElementById('report');
      if (reportEl) {
        if (latest.last_step) reportEl.textContent = `background ${latest.status}: ${latest.last_step}`;
        else reportEl.textContent = `background ${latest.status}: ${latest.iterations || 0} iteration(s)`;
      }
      if (latest.session === state.activeSession) { renderBackgroundActivity(latest); renderMetadataPanel(); }
      if (latest.session === state.activeSession && ['completed', 'failed', 'killed', 'timed_out'].includes(String(latest.status || ''))) {
        const key = String(latest.id || '');
        if (!_terminalJobStateSynced.has(key)) {
          _terminalJobStateSynced.add(key);
          await refreshState();
        }
      }
    } catch (_) {
      // ignored to avoid breaking UI polling loop
    }
  }, 1500);
}


function _locGenerated(messages) {
  let loc = 0;
  for (const m of (messages || [])) {
    if (m.role !== 'assistant') continue;
    const text = String(m.content || '');
    const lines = text.split('\n').filter((line) => line.trim().length > 0);
    loc += lines.length;
  }
  return loc;
}

function _formatChartTimeLabel(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return '';
  return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function _drawMetricChart(canvas, points, { yLabel, valueFormatter }) {
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const rows = Array.isArray(points) ? points : [];
  if (!rows.length) {
    ctx.fillStyle = '#6b7280';
    ctx.font = '12px sans-serif';
    ctx.fillText('No data yet', 12, 20);
    return;
  }

  const padLeft = 62;
  const padRight = 14;
  const padTop = 16;
  const padBottom = 44;
  const plotW = Math.max(10, w - padLeft - padRight);
  const plotH = Math.max(10, h - padTop - padBottom);

  const maxY = Math.max(1, ...rows.map((r) => Number(r.y || 0)));
  const ticks = 4;

  ctx.strokeStyle = '#9ca3af';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padLeft, padTop);
  ctx.lineTo(padLeft, h - padBottom);
  ctx.lineTo(w - padRight, h - padBottom);
  ctx.stroke();

  ctx.font = '10px sans-serif';
  ctx.fillStyle = '#6b7280';
  for (let i = 0; i <= ticks; i += 1) {
    const ratio = i / ticks;
    const y = h - padBottom - ratio * plotH;
    const v = ratio * maxY;
    ctx.strokeStyle = 'rgba(156,163,175,0.25)';
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(w - padRight, y);
    ctx.stroke();
    ctx.fillText(valueFormatter(v), 8, y + 3);
  }

  const xTickIdx = [0, Math.floor((rows.length - 1) / 2), rows.length - 1].filter((v, i, arr) => arr.indexOf(v) === i);
  xTickIdx.forEach((idx) => {
    const ratio = rows.length === 1 ? 0 : idx / (rows.length - 1);
    const x = padLeft + ratio * plotW;
    const label = _formatChartTimeLabel(rows[idx] && rows[idx].x);
    ctx.fillStyle = '#6b7280';
    ctx.fillText(label, Math.max(2, x - 28), h - 18);
  });

  ctx.strokeStyle = '#6366f1';
  ctx.lineWidth = 2;
  ctx.beginPath();
  rows.forEach((row, idx) => {
    const ratioX = rows.length === 1 ? 0 : idx / (rows.length - 1);
    const x = padLeft + ratioX * plotW;
    const y = h - padBottom - (Number(row.y || 0) / maxY) * plotH;
    if (idx === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = '#6b7280';
  ctx.font = '11px sans-serif';
  ctx.fillText('Time (X)', w - 58, h - 6);
  ctx.save();
  ctx.translate(12, 42);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(yLabel, 0, 0);
  ctx.restore();
}

function renderMetrics() {
  const turns = (Array.isArray(state.sessionTurns) ? state.sessionTurns.slice() : []).filter((turn) => {
    const session = String(turn.session || '');
    if (!state.activeSession) return true;
    if (!session) return true;
    return session === state.activeSession;
  });
  const messages = Array.isArray(state.messages) ? state.messages : [];
  const usage = state.sessionUsage || {};

  const totalTokens = Math.trunc(Number(usage.total_tokens || 0));
  const totalCost = Number(usage.estimated_cost_usd || 0);
  const loc = _locGenerated(messages);

  const tokEl = document.getElementById('metricTotalTokens');
  const costEl = document.getElementById('metricTotalCost');
  const locEl = document.getElementById('metricLocGenerated');
  const turnsEl = document.getElementById('metricTurns');
  if (tokEl) tokEl.textContent = String(totalTokens);
  if (costEl) costEl.textContent = totalCost.toFixed(6);
  if (locEl) locEl.textContent = String(loc);
  if (turnsEl) turnsEl.textContent = String(turns.length);

  const tokenPoints = [];
  const costPoints = [];
  const breakdown = {};

  for (const turn of turns) {
    const provider = String(turn.provider || 'unknown');
    const model = String(turn.model || 'unknown');
    const key = `${provider}:${model}`;

    tokenPoints.push({ x: turn.timestamp || '', y: Number(turn.total_tokens || 0) });
    costPoints.push({ x: turn.timestamp || '', y: Number(turn.estimated_cost_usd || 0) });

    const row = breakdown[key] || { provider, model, turns: 0, tokens: 0, cost: 0 };
    row.turns += 1;
    row.tokens += Number(turn.total_tokens || 0);
    row.cost += Number(turn.estimated_cost_usd || 0);
    breakdown[key] = row;
  }

  _drawMetricChart(document.getElementById('metricsTokenChart'), tokenPoints, {
    yLabel: 'Tokens (Y)',
    valueFormatter: (v) => `${Math.round(Number(v || 0))}`,
  });
  _drawMetricChart(document.getElementById('metricsCostChart'), costPoints, {
    yLabel: 'Cost USD (Y)',
    valueFormatter: (v) => `$${Number(v || 0).toFixed(4)}`,
  });

  const body = document.getElementById('metricsModelBreakdown');
  if (body) {
    const rows = Object.values(breakdown).sort((a, b) => b.tokens - a.tokens);
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="small-muted">No turns yet.</td></tr>';
    } else {
      body.innerHTML = rows.map((row) => (
        `<tr><td>${escapeHtml(row.provider)}</td><td>${escapeHtml(row.model)}</td><td>${row.turns}</td><td>${Math.trunc(row.tokens)}</td><td>${row.cost.toFixed(6)}</td></tr>`
      )).join('');
    }
  }

  const telemetry = state.telemetry || {};
  const setMetric = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  };
  setMetric('metricTelemetryUptime', telemetry.uptime_seconds || 0);
  setMetric('metricTelemetryRequests', telemetry.total_requests || 0);
  setMetric('metricTelemetryToolFailures', telemetry.tool_failures || 0);
  setMetric('metricTelemetryApprovalWaits', telemetry.approval_wait_events || 0);
  setMetric('metricTelemetryJobsCompleted', telemetry.background_jobs_completed || 0);
  setMetric('metricTelemetryJobsFailed', telemetry.background_jobs_failed_or_timed_out || 0);

  const actionsBody = document.getElementById('metricsTelemetryActions');
  if (actionsBody) {
    const actionRows = Object.entries(telemetry.action_counts || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
    actionsBody.innerHTML = actionRows.length
      ? actionRows.map(([name, count]) => `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(String(count))}</td></tr>`).join('')
      : '<tr><td colspan="2" class="small-muted">No action telemetry recorded yet.</td></tr>';
  }
}

function roleClass(role) {
  return String(role || '').replace(/[^a-zA-Z0-9_\-]/g, '_');
}

function formatTimestamp(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return '';
  const day = String(dt.getDate()).padStart(2, '0');
  const month = String(dt.getMonth() + 1).padStart(2, '0');
  const year = String(dt.getFullYear());
  const hours = String(dt.getHours()).padStart(2, '0');
  const minutes = String(dt.getMinutes()).padStart(2, '0');
  const seconds = String(dt.getSeconds()).padStart(2, '0');
  return `${day}-${month}-${year} ${hours}:${minutes}:${seconds}`;
}

function timestampFromEventLine(line) {
  const text = String(line || '');
  const match = text.match(/\[([^\]]+)\]/);
  if (!match) return '';
  return formatTimestamp(match[1]);
}

function inferMessageTimestamps(messages, turns) {
  const out = new Map();
  const rows = Array.isArray(turns) ? turns : [];
  let turnIdx = 0;
  let activeTs = rows[0] ? rows[0].timestamp : '';

  messages.forEach((m, idx) => {
    const metaTs = m && m.metadata && (m.metadata.timestamp || m.metadata.ts);
    if (metaTs) {
      out.set(idx, String(metaTs));
      return;
    }

    if (m.role === 'user') {
      if (rows[turnIdx] && rows[turnIdx].timestamp) activeTs = rows[turnIdx].timestamp;
      if (activeTs) out.set(idx, activeTs);
      return;
    }

    if (m.role === 'assistant') {
      if (!activeTs && rows[turnIdx] && rows[turnIdx].timestamp) activeTs = rows[turnIdx].timestamp;
      if (activeTs) out.set(idx, activeTs);
      if (turnIdx < rows.length - 1) {
        turnIdx += 1;
        activeTs = rows[turnIdx] ? rows[turnIdx].timestamp : activeTs;
      }
    }
  });

  return out;
}

function renderApprovalArgs(args) {
  const el = document.getElementById('approvalArgs');
  const rows = [];

  function walk(value, path='args') {
    if (Array.isArray(value)) {
      if (!value.length) rows.push([path, '[]']);
      value.forEach((item, idx) => walk(item, `${path}[${idx}]`));
      return;
    }
    if (value && typeof value === 'object') {
      const keys = Object.keys(value);
      if (!keys.length) rows.push([path, '{}']);
      keys.forEach((key) => walk(value[key], `${path}.${key}`));
      return;
    }
    let display;
    if (typeof value === 'string') display = value;
    else display = JSON.stringify(value);
    rows.push([path, display]);
  }

  walk(args || {});

  el.innerHTML = rows.map(([key, value]) => (
    `<div class="approval-row"><div class="approval-key">${escapeHtml(key)}</div><div class="approval-value">${escapeHtml(String(value ?? 'null'))}</div></div>`
  )).join('');
}


function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}



function applyThemePreference() {
  const dark = document.getElementById('darkMode').checked;
  document.body.classList.toggle('dark-mode', dark);
  const theme = document.getElementById('hljsTheme');
  if (theme) {
    theme.href = dark
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/styles/github-dark.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/styles/github.min.css';
  }
  localStorage.setItem('mu_cli_dark_mode', dark ? '1' : '0');
}

function hydrateThemePreference() {
  const saved = localStorage.getItem('mu_cli_dark_mode');
  const dark = saved === '1';
  const checkbox = document.getElementById('darkMode');
  if (checkbox) checkbox.checked = dark;
  document.body.classList.toggle('dark-mode', dark);
  applyThemePreference();
}

function extractPatchTextFromArgs(args) {
  if (!args || typeof args !== 'object') return '';
  if (typeof args.patch === 'string') return args.patch;
  for (const [_, value] of Object.entries(args)) {
    if (value && typeof value === 'object') {
      const found = extractPatchTextFromArgs(value);
      if (found) return found;
    }
  }
  return '';
}

function renderApprovalPatchPreview(args) {
  const host = document.getElementById('approvalPatchPreview');
  const patchText = extractPatchTextFromArgs(args || {});
  if (!patchText.trim()) {
    host.innerHTML = '';
    lastApprovalPatchFingerprint = null;
    return;
  }

  const fingerprint = patchText.trim();
  if (lastApprovalPatchFingerprint === fingerprint) return;
  lastApprovalPatchFingerprint = fingerprint;

  const file = new File([patchText], 'proposed_change.patch', { type: 'text/x-diff' });
  const reader = new FileReader();
  reader.onload = () => {
    const content = String(reader.result || '');
    host.innerHTML = `<div class="small-muted mb-1">Patch preview (temporary file: ${escapeHtml(file.name)})</div>${renderSideBySideDiff(content)}<details class="raw-diff-wrap"><summary>Raw diff</summary><pre><code class="hljs language-diff">${escapeHtml(content)}</code></pre></details>`;
    highlightCodeBlocks(host);
  };
  reader.readAsText(file);
}


function renderDiffLineHtml(line) {
  if (!window.hljs) return escapeHtml(line);
  try {
    return window.hljs.highlight(line, { language: 'diff' }).value;
  } catch (e) {
    return escapeHtml(line);
  }
}

function renderSideBySideDiff(diffText) {
  const lines = String(diffText || '').split('\n');
  const left = [];
  const right = [];

  function pushPair(l, r) {
    left.push(l);
    right.push(r);
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.startsWith('--- ') || line.startsWith('+++ ') || line.startsWith('diff ') || line.startsWith('@@')) {
      pushPair({ cls: 'diff-ctx', text: line }, { cls: 'diff-ctx', text: line });
      continue;
    }

    if (line.startsWith('-')) {
      const next = lines[i + 1] || '';
      if (next.startsWith('+')) {
        pushPair({ cls: 'diff-del', text: line }, { cls: 'diff-add', text: next });
        i += 1;
      } else {
        pushPair({ cls: 'diff-del', text: line }, { cls: 'diff-ctx', text: '' });
      }
      continue;
    }

    if (line.startsWith('+')) {
      pushPair({ cls: 'diff-ctx', text: '' }, { cls: 'diff-add', text: line });
      continue;
    }

    pushPair({ cls: 'diff-ctx', text: line }, { cls: 'diff-ctx', text: line });
  }

  const leftHtml = left.map((row) => `<div class="diff-line ${row.cls}"><code class="hljs language-diff">${renderDiffLineHtml(row.text)}</code></div>`).join('');
  const rightHtml = right.map((row) => `<div class="diff-line ${row.cls}"><code class="hljs language-diff">${renderDiffLineHtml(row.text)}</code></div>`).join('');
  return `<div class="diff-wrap"><div class="diff-grid"><div class="diff-col">${leftHtml}</div><div class="diff-col">${rightHtml}</div></div></div>`;
}

function formatMessageContent(content) {
  const source = String(content || '');
  const markedApi = window.marked;
  const purifierApi = window.DOMPurify;
  if (markedApi && typeof markedApi.parse === 'function' && purifierApi && typeof purifierApi.sanitize === 'function') {
    try {
      markedApi.setOptions({
        breaks: true,
        gfm: true,
        headerIds: false,
        mangle: false,
      });
      const markdownHtml = markedApi.parse(source || '');
      const cleaned = purifierApi.sanitize(markdownHtml, {
        USE_PROFILES: { html: true },
      });
      if (String(cleaned || '').trim()) return cleaned;
    } catch (_err) {
      // fall through to local formatter
    }
  }

  let html = '';
  const blockPattern = /```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g;
  const trimmed = source.trim();
  if (!trimmed.includes('```') && (/^diff --git/m.test(trimmed) || (/^---\s/m.test(trimmed) && /^\+\+\+\s/m.test(trimmed) && /^@@/m.test(trimmed)))) {
    return renderSideBySideDiff(trimmed);
  }
  let cursor = 0;
  let match;

  function formatParagraphs(text) {
    if (!text) return '';

    function formatInline(input) {
      const linkTokens = [];
      const tokenized = String(input || '')
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) => {
          const token = `@@LINK_${linkTokens.length}@@`;
          linkTokens.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`);
          return token;
        })
        .replace(/(https?:\/\/[\w\-./?%&=+#:~;,]+[\w/#])/g, (url) => {
          const token = `@@LINK_${linkTokens.length}@@`;
          const safe = escapeHtml(url);
          linkTokens.push(`<a href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`);
          return token;
        });

      let html = escapeHtml(tokenized)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*]+)\*(?=[^*]|$)/g, '$1<em>$2</em>');

      html = html.replace(/@@LINK_(\d+)@@/g, (_m, idxText) => {
        const idx = Number(idxText);
        return Number.isInteger(idx) && linkTokens[idx] ? linkTokens[idx] : '';
      });

      return html;
    }

    function renderBlock(segment) {
      const lines = segment.split('\n');
      const listMatch = lines.every((line) => /^\s*([-*]|\d+\.)\s+/.test(line));
      if (listMatch) {
        const ordered = lines.every((line) => /^\s*\d+\.\s+/.test(line));
        const items = lines.map((line) => line.replace(/^\s*([-*]|\d+\.)\s+/, '').trim());
        const tag = ordered ? 'ol' : 'ul';
        return `<${tag}>${items.map((item) => `<li>${formatInline(item)}</li>`).join('')}</${tag}>`;
      }

      if (lines.length === 1) {
        const heading = lines[0].match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
          const level = heading[1].length;
          return `<h${level}>${formatInline(heading[2])}</h${level}>`;
        }
        const quote = lines[0].match(/^>\s?(.+)$/);
        if (quote) return `<blockquote>${formatInline(quote[1])}</blockquote>`;
      }

      return `<p>${lines.map((line) => formatInline(line)).join('<br/>')}</p>`;
    }

    return text
      .split(/\n{2,}/)
      .map((segment) => renderBlock(segment))
      .join('');
  }

  while ((match = blockPattern.exec(source)) !== null) {
    const plainText = source.slice(cursor, match.index);
    html += formatParagraphs(plainText);

    const lang = (match[1] || '').toLowerCase();
    const code = match[2] || '';
    const langClass = lang ? ` language-${lang}` : '';
    if (lang === 'diff' || lang === 'patch') html += renderSideBySideDiff(code);
    else html += `<div class="code-block-wrap"><button type="button" class="copy-code-btn" data-copy-code="${escapeHtml(code)}">Copy</button><pre><code class="hljs${langClass}">${escapeHtml(code)}</code></pre></div>`;
    cursor = match.index + match[0].length;
  }

  html += formatParagraphs(source.slice(cursor));

  return html || '<p></p>';
}

function extractCitationLinks(content) {
  const text = String(content || '');
  const found = [];
  const seen = new Set();
  const regex = /https?:\/\/[\w\-./?%&=+#:~;,]+[\w/#]/g;
  for (const match of text.matchAll(regex)) {
    const url = String(match[0]);
    if (seen.has(url)) continue;
    seen.add(url);
    found.push(url);
  }
  return found;
}

function scoreReasonsByUrl(messages, assistantIndex) {
  const reasons = {};
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (!m) continue;
    if (m.role === 'user') break;
    if (m.role !== 'tool_result') continue;
    const text = String(m.content || '');
    if (!text.includes('[tool=score_sources]')) continue;
    const jsonStart = text.indexOf('{');
    if (jsonStart < 0) continue;
    try {
      const payload = JSON.parse(text.slice(jsonStart));
      const rows = (payload && payload.sources) || [];
      for (const row of rows) {
        const url = String((row && row.url) || '');
        const reason = String((row && row.reason) || '');
        if (url && reason) reasons[url] = reason;
      }
    } catch (_) {
      // ignore parse issues
    }
  }
  return reasons;
}

function buildCitationPanel(content, reasonsByUrl={}, citationIdPrefix='citation') {
  const links = extractCitationLinks(content);
  if (!links.length) return '';
  const items = links.map((url, idx) => `
    <div id="${escapeHtml(citationIdPrefix)}-${idx + 1}">[${idx + 1}] <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>${reasonsByUrl[url] ? `<div class="small-muted">why chosen: ${escapeHtml(reasonsByUrl[url])}</div>` : ''}</div>
  `).join('');
  return `<details class="citation-panel"><summary>ℹ Citations & URLs (${links.length})</summary><div class="citation-list">${items}</div></details>`;
}

function formatAssistantContentWithCitations(content, links, citationIdPrefix='citation') {
  let html = formatMessageContent(content);
  html = html.replace(/\[(\d+)\]/g, (full, numText) => {
    const idx = Number(numText);
    if (!Number.isInteger(idx) || idx < 1 || idx > links.length) return full;
    return `<a href="#${escapeHtml(citationIdPrefix)}-${idx}" title="Jump to citation [${idx}]">[${idx}]</a>`;
  });
  return html;
}

function summarizeToolResult(content) {
  const text = String(content || '');
  const toolMatch = text.match(/\[tool=([^\]]+)\]/);
  const statusMatch = text.match(/\[(ok|error)\]\s*([\s\S]*)$/m);
  const toolName = toolMatch ? toolMatch[1] : 'tool';
  const status = statusMatch ? statusMatch[1] : 'ok';
  const detail = statusMatch ? statusMatch[2].trim() : text.trim();
  const short = detail.length > 220 ? `${detail.slice(0, 217)}...` : detail;
  return `${toolName}: ${status.toUpperCase()} — ${short}`;
}

function buildResearchTrailPanel(messages, assistantIndex) {
  const steps = [];
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (!m) continue;
    if (m.role === 'user') break;
    if (m.role === 'tool_result') {
      steps.push(summarizeToolResult(m.content));
      continue;
    }
    if (m.role === 'assistant') {
      const calls = (m.metadata && m.metadata.tool_calls) || [];
      for (const call of calls) {
        const name = String(call.name || 'tool');
        const args = String(call.arguments || '').trim();
        const shortArgs = args.length > 180 ? `${args.slice(0, 177)}...` : args;
        steps.push(`requested ${name}${shortArgs ? ` with args ${shortArgs}` : ''}`);
      }
    }
  }

  if (!steps.length) return '';
  const ordered = steps.reverse();
  const items = ordered.map((step, idx) => `<div class="research-trail-item">${idx + 1}. ${escapeHtml(step)}</div>`).join('');
  return `<details class="research-trail"><summary>🧭 Research activity log (${ordered.length} steps)</summary><div class="research-trail-list">${items}</div></details>`;
}

function highlightCodeBlocks(root) {
  if (!window.hljs) return;
  root.querySelectorAll('pre code.hljs').forEach((node) => {
    window.hljs.highlightElement(node);
  });
}

function wireCopyButtons(root) {
  root.querySelectorAll('[data-copy-code]').forEach((btn) => {
    if (btn.dataset.boundCopy === '1') return;
    btn.dataset.boundCopy = '1';
    btn.addEventListener('click', async () => {
      const text = btn.getAttribute('data-copy-code') || '';
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          ta.remove();
        }
        const old = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(() => { btn.textContent = old || 'Copy'; }, 1100);
      } catch (_) {
        btn.textContent = 'Failed';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1100);
      }
    });
  });
}

function isToolRole(role) {
  return role === 'tool_result' || role === 'tool_call';
}

function shouldRenderMessage(message) {
  if (message && message.metadata && message.metadata.show_in_main === false) return false;
  if (message.role !== 'assistant') return true;
  const content = String(message.content || '').trim();
  const toolCalls = (message.metadata && message.metadata.tool_calls) || [];
  const typing = !!(message.metadata && message.metadata.typing);
  return content.length > 0 || typing;
}

function collectAssistantMetadata(messages, assistantIndex) {
  const toolRequests = [];
  const toolResults = [];
  const assistantMessage = messages[assistantIndex] || {};
  const directCalls = (assistantMessage.metadata && assistantMessage.metadata.tool_calls) || [];
  for (const call of directCalls) {
    const name = String(call.name || 'tool');
    const args = String(call.arguments || '').trim();
    const shortArgs = args.length > 420 ? `${args.slice(0, 417)}...` : args;
    toolRequests.push(`name=${name}${shortArgs ? ` args=${shortArgs}` : ''}`);
  }

  for (let i = assistantIndex + 1; i < messages.length; i += 1) {
    const current = messages[i];
    if (!current) continue;
    if (current.role === 'assistant' || current.role === 'user') break;
    if (current.role === 'tool_result') {
      const summary = summarizeToolResult(current.content);
      const raw = String(current.content || '').trim();
      const snippet = raw.length > 700 ? `${raw.slice(0, 697)}...` : raw;
      toolResults.push(`${summary}\nmodel_received=${snippet}`);
    }
  }

  const reasons = scoreReasonsByUrl(messages, assistantIndex);
  const citations = extractCitationLinks(assistantMessage.content || '');
  const citationItems = citations.map((url) => {
    const why = reasons[url] ? `\nwhy chosen: ${reasons[url]}` : '';
    return `${url}${why}`;
  });
  const researchSteps = [];
  const trail = buildResearchTrailPanel(messages, assistantIndex);
  if (trail) {
    const steps = [];
    for (let i = assistantIndex - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (!m) continue;
      if (m.role === 'user') break;
      if (m.role === 'tool_result') steps.push(summarizeToolResult(m.content));
    }
    researchSteps.push(...steps.reverse());
  }

  return { toolRequests, toolResults, citationItems, researchSteps };
}

function collectAutomationMetadata(messages) {
  const items = [];
  for (const message of (messages || [])) {
    if (!message || !message.metadata || message.metadata.show_in_main !== false) continue;
    const kind = String(message.metadata.automation_kind || 'automation');
    const role = String(message.role || 'message');
    const text = String(message.content || '').trim();
    if (!text) continue;
    const stamp = formatTimestamp(message.metadata.timestamp || message.metadata.ts || '');
    items.push({ kind, role, text: text.length > 900 ? `${text.slice(0, 897)}...` : text, stamp: stamp || '—' });
  }
  return items;
}

function _metaRow(kind, label, value) {
  const row = document.createElement('div');
  row.className = `meta-line meta-item ${kind}`;

  const raw = String(value || '').trim();
  const preview = (raw || '(empty)').replace(/\s+/g, ' ').slice(0, 140);

  const details = document.createElement('details');
  details.className = 'meta-entry';
  const metaKey = `${kind}|${label}|${raw.slice(0, 400)}`;
  details.dataset.metaKey = metaKey;
  if (metadataExpandedKeys.has(metaKey)) details.open = true;
  details.addEventListener('toggle', () => {
    if (details.open) metadataExpandedKeys.add(metaKey);
    else metadataExpandedKeys.delete(metaKey);
  });

  const summary = document.createElement('summary');
  const tag = document.createElement('span');
  tag.className = 'meta-tag';
  tag.textContent = label;
  const previewEl = document.createElement('span');
  previewEl.className = 'meta-preview';
  previewEl.textContent = preview.length < (raw || '(empty)').replace(/\s+/g, ' ').length ? `${preview}…` : preview;
  summary.appendChild(tag);
  summary.appendChild(previewEl);
  details.appendChild(summary);

  const bodyWrap = document.createElement('div');
  bodyWrap.className = 'meta-entry-body';
  const body = document.createElement('div');
  body.className = 'meta-content';

  const firstUrl = raw.match(/https?:\/\/[^\s]+/);
  if (kind === 'citation' && firstUrl) {
    const link = document.createElement('a');
    link.className = 'meta-link';
    link.href = firstUrl[0];
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = firstUrl[0];
    body.appendChild(link);
    const remainder = raw.replace(firstUrl[0], '').trim();
    if (remainder) {
      const extra = document.createElement('pre');
      const code = document.createElement('code');
      code.className = 'language-text';
      code.textContent = remainder;
      extra.appendChild(code);
      body.appendChild(extra);
      if (window.hljs) window.hljs.highlightElement(code);
    }
    bodyWrap.appendChild(body);
    details.appendChild(bodyWrap);
    row.appendChild(details);
    return row;
  }

  const pre = document.createElement('pre');
  const code = document.createElement('code');
  let lang = 'language-text';
  let output = raw || '(empty)';
  if ((output.startsWith('{') && output.endsWith('}')) || (output.startsWith('[') && output.endsWith(']'))) {
    try {
      output = JSON.stringify(JSON.parse(output), null, 2);
      lang = 'language-json';
    } catch (_) {
      // keep plain text
    }
  }
  code.className = lang;
  code.textContent = output;
  pre.appendChild(code);
  body.appendChild(pre);
  bodyWrap.appendChild(body);
  details.appendChild(bodyWrap);
  row.appendChild(details);
  if (window.hljs) window.hljs.highlightElement(code);
  return row;
}


function _metaFilterCategory(kind, label, text) {
  const rawKind = String(kind || '').toLowerCase();
  const hay = `${label || ''} ${text || ''}`.toLowerCase();

  if (rawKind === 'tool-call' || rawKind === 'tool-result' || rawKind.includes('tool')) return 'tool';
  if (rawKind === 'status' || rawKind === 'workspace') return 'status';
  if (rawKind === 'model' || rawKind === 'plan' || rawKind === 'automation' || rawKind === 'research' || rawKind === 'citation') return 'model';

  if (/tool-request:|tool-run:|\btool\b/.test(hay)) return 'tool';
  if (/status[:=]|completed|failed|killed|timed_out|awaiting_plan_approval|workspace/.test(hay)) return 'status';
  if (/model[:\s]|plan[:\s]|automation|research|citation/.test(hay)) return 'model';
  return 'status';
}

function _metaFilterAllows(kind, label, text) {
  const filter = state.timelineFilter || 'all';
  if (filter === 'all') return true;
  return _metaFilterCategory(kind, label, text) === filter;
}

function renderMetadataPanel() {
  const host = document.getElementById('metaFeed');
  const summaryHost = document.getElementById('metaSummary');
  host.querySelectorAll('details.meta-entry[open][data-meta-key]').forEach((el) => metadataExpandedKeys.add(el.dataset.metaKey));
  host.innerHTML = '';
  const messageTimes = inferMessageTimestamps(state.messages, state.sessionTurns);
  let cards = 0;

  const timelineEvents = timelineEventsForActiveSession();
  const liveTraceItems = timelineEvents.slice(-36).filter((line) => {
    return line.startsWith('tool-request:') || line.startsWith('tool-run:') || line.startsWith('model:') || line.startsWith('status:') || line.startsWith('plan:');
  });

  const assistantMetaCounts = { calls: 0, results: 0, citations: 0, research: 0 };
  for (let idx = state.messages.length - 1; idx >= 0; idx -= 1) {
    const m = state.messages[idx];
    if (!m || m.role !== 'assistant') continue;
    const meta = collectAssistantMetadata(state.messages, idx);
    assistantMetaCounts.calls += meta.toolRequests.length;
    assistantMetaCounts.results += meta.toolResults.length;
    assistantMetaCounts.citations += meta.citationItems.length;
    assistantMetaCounts.research += meta.researchSteps.length;
  }

  const activeJob = (state.backgroundJobs || []).find((j) => j && j.session === state.activeSession && ['running', 'awaiting_plan_approval'].includes(j.status));
  if (summaryHost) {
    const latestEvent = liveTraceItems.length ? liveTraceItems[liveTraceItems.length - 1] : 'No live events yet';
    const latestStamp = timestampFromEventLine(latestEvent);
    summaryHost.innerHTML = [
      `<div class="meta-summary-chip"><span class="label">Live events</span><span class="value">${liveTraceItems.length}</span></div>`,
      `<div class="meta-summary-chip"><span class="label">Background</span><span class="value">${activeJob ? `${escapeHtml(activeJob.status || 'running')} · step ${Number(activeJob.iterations || 0)}` : 'idle'}</span></div>`,
      `<div class="meta-summary-chip"><span class="label">Tool calls/results</span><span class="value">${assistantMetaCounts.calls}/${assistantMetaCounts.results}</span></div>`,
      `<div class="meta-summary-chip span-2"><span class="label">Latest event</span><span class="value">${escapeHtml(latestEvent)}</span><span class="stamp">${escapeHtml(latestStamp || '—')}</span></div>`,
    ].join('');
  }

  if (activeJob && Array.isArray(activeJob.events) && activeJob.events.length) {
    const card = document.createElement('div');
    card.className = 'meta-card meta-live-card';
    card.innerHTML = `<div class="meta-head"><span>Background task activity</span><span>${escapeHtml(String(activeJob.status || 'running'))} · ${Number(activeJob.iterations || 0)} steps</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const line of activeJob.events.slice(-20).reverse()) {
      const stamp = timestampFromEventLine(line);
      const label = stamp ? `Background · ${stamp}` : 'Background';
      if (_metaFilterAllows('status', label, line)) lines.appendChild(_metaRow('status', label, line));
    }
    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  if (liveTraceItems.length) {
    const card = document.createElement('div');
    card.className = 'meta-card meta-live-card';
    card.innerHTML = `<div class="meta-head"><span>Live execution stream</span><span>${liveTraceItems.length} events</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const line of liveTraceItems.slice().reverse()) {
      let kind = 'automation';
      let label = 'Stream event';
      if (line.startsWith('tool-request:')) { kind = 'tool-call'; label = 'Tool call'; }
      else if (line.startsWith('tool-run:')) { kind = 'tool-result'; label = 'Tool result'; }
      else if (line.startsWith('status:')) { kind = 'status'; label = 'Status'; }
      else if (line.startsWith('model:')) { kind = 'automation'; label = 'Model'; }
      else if (line.startsWith('plan:')) { kind = 'automation'; label = 'Plan'; }
      const stamp = timestampFromEventLine(line);
      const effectiveLabel = stamp ? `${label} · ${stamp}` : label;
      if (_metaFilterAllows(kind, effectiveLabel, line)) lines.appendChild(_metaRow(kind, effectiveLabel, line));
    }
    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  const ws = state.workspaceIndexStats || {};
  if (Object.keys(ws).length && _metaFilterAllows('workspace', 'Workspace indexing', 'workspace stats')) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = '<div class="meta-head"><span>Workspace indexing</span><span>stats</span></div>';
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const [key, value] of Object.entries(ws)) {
      const label = 'Workspace stat';
      const body = `${key}: ${value}`;
      if (_metaFilterAllows('workspace', label, body)) lines.appendChild(_metaRow('workspace', label, body));
    }
    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  for (let idx = state.messages.length - 1; idx >= 0; idx -= 1) {
    const m = state.messages[idx];
    if (!m || m.role !== 'assistant') continue;
    const meta = collectAssistantMetadata(state.messages, idx);
    if (!meta.toolRequests.length && !meta.toolResults.length && !meta.citationItems.length && !meta.researchSteps.length) continue;

    const card = document.createElement('div');
    card.className = 'meta-card';
    const stamp = formatTimestamp(messageTimes.get(idx));
    card.innerHTML = `<div class="meta-head"><span>Assistant metadata</span><span>${escapeHtml(stamp || '—')}</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';

    for (const item of meta.toolRequests) if (_metaFilterAllows('tool-call', 'Tool call', item)) lines.appendChild(_metaRow('tool-call', 'Tool call', item));
    for (const item of meta.toolResults) if (_metaFilterAllows('tool-result', 'Tool result', item)) lines.appendChild(_metaRow('tool-result', 'Tool result', item));
    for (const item of meta.citationItems) if (_metaFilterAllows('citation', 'Citation', item)) lines.appendChild(_metaRow('citation', 'Citation', item));
    for (const item of meta.researchSteps) if (_metaFilterAllows('research', 'Research step', item)) lines.appendChild(_metaRow('research', 'Research step', item));

    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  const automation = collectAutomationMetadata(state.messages);
  if (automation.length) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = `<div class="meta-head"><span>Agent/Plan automation</span><span>${automation.length} items</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const item of automation.slice(-20).reverse()) {
      const label = `${item.stamp} · ${item.kind} · ${item.role}`;
      if (_metaFilterAllows(item.kind, label, item.text)) lines.appendChild(_metaRow('automation', label, item.text));
    }
    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  const terminalJobs = (state.backgroundJobs || [])
    .filter((job) => job && job.session === state.activeSession && ['completed', 'failed', 'killed', 'timed_out'].includes(job.status))
    .slice(-8)
    .reverse();
  if (terminalJobs.length) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = `<div class="meta-head"><span>Background execution reports</span><span>${terminalJobs.length} run(s)</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    terminalJobs.forEach((job) => {
      const reportText = job.report
        ? JSON.stringify(job.report, null, 2)
        : (job.last_step || `status=${job.status}; iterations=${job.iterations || 0}`);
      const label = `Run ${job.id || '-'} · ${job.status || 'unknown'}`;
      if (_metaFilterAllows('status', label, reportText)) lines.appendChild(_metaRow('automation', label, reportText));
    });
    if (lines.childElementCount) {
      card.appendChild(lines);
      host.appendChild(card);
      cards += 1;
    }
  }

  if (!cards) host.innerHTML = '<div class="meta-empty">No metadata items match this filter.</div>';
}



function _escapeAttr(value) {
  return String(value || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _jobDetailsText(job) {
  const lines = [];
  lines.push(`Session: ${job.session || '-'}`);
  lines.push(`Job: ${job.id || '-'}`);
  lines.push(`Status: ${job.status || '-'}`);
  lines.push(`Iterations: ${job.iterations || 0}`);
  if (job.started_at) lines.push(`Started: ${job.started_at}`);
  if (job.finished_at) lines.push(`Finished: ${job.finished_at}`);
  if (job.error) lines.push(`Error: ${job.error}`);
  if (job.report) {
    lines.push('');
    lines.push('Report:');
    lines.push(JSON.stringify(job.report, null, 2));
  }
  if (job.plan) {
    lines.push('');
    lines.push('Plan:');
    lines.push(String(job.plan));
  }
  if (Array.isArray(job.checkpoints) && job.checkpoints.length) {
    lines.push('');
    lines.push('Checkpoints:');
    lines.push(JSON.stringify(job.checkpoints, null, 2));
  }
  if (Array.isArray(job.events) && job.events.length) {
    lines.push('');
    lines.push('Events:');
    lines.push(job.events.join('\\n'));
  }
  return lines.join('\\n');
}

function maybeRecordJobTerminalNotice(job) {
  if (!job || !job.id || !job.session) return;
  if (job.status !== 'timed_out') return;
  const session = String(job.session);
  const id = `timeout-${job.id}`;
  const notices = runNoticesBySession[session] || [];
  if (notices.some((n) => n.id === id)) return;
  const details = _jobDetailsText(job);
  runDetailsById[id] = details;
  notices.push({
    id,
    text: 'Agent timed out before completing this run.',
  });
  runNoticesBySession[session] = notices;
}

function openRunDetails(id, title = '') {
  const body = document.getElementById('runDetailsBody');
  const meta = document.getElementById('runDetailsMeta');
  if (!body || !meta) return;
  meta.textContent = title || '';
  body.textContent = runDetailsById[id] || 'No details available.';
  showModal('runDetailsModal', true);
}

function renderMessages() {
  const box = document.getElementById('messages');
  box.innerHTML = '';
  const messageTimes = inferMessageTimestamps(state.messages, state.sessionTurns);

  let anchor = null;
  state.messages.forEach((m, idx) => {
    if (!shouldRenderMessage(m)) return;

    if (m.role === 'assistant' && !String(m.content || '').trim() && !(m.metadata && m.metadata.typing)) return;
    if (m.role === 'tool_result' || m.role === 'tool_call') return;

    const row = document.createElement('div');
    row.className = `msg role-${roleClass(m.role)}`;
    row.innerHTML = `<div class="role">${m.role}</div>`;

    if (m.role === 'user' || m.role === 'assistant') {
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      let tag = m.role === 'user' ? 'You' : 'AI';
      if (m.role === 'assistant' && m.metadata && m.metadata.kind === 'thinking_output') tag = 'thinking output';
      const stamp = formatTimestamp(messageTimes.get(idx));
      meta.innerHTML = `<span class="msg-tag">${tag}</span><span class="msg-time">${escapeHtml(stamp || '—')}</span>`;
      row.appendChild(meta);
    }

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    const citationLinks = m.role === 'assistant' ? extractCitationLinks(m.content) : [];
    const citationPrefix = `msg-${idx}-citation`;
    if (m.role === 'tool_result' && m.metadata && m.metadata.kind === 'session_condensed_summary') {
      bubble.innerHTML = `<details><summary>Condensed summary (${escapeHtml(m.metadata.summary_id || '')})</summary><div class="small-muted mt-1">${formatMessageContent(m.content)}</div></details>`;
    } else if (m.role === 'assistant' && m.metadata && m.metadata.typing) bubble.innerHTML = '<p class="typing-dots"><span></span><span></span><span></span></p>';
    else bubble.innerHTML = m.role === 'assistant'
      ? formatAssistantContentWithCitations(m.content, citationLinks, citationPrefix)
      : formatMessageContent(m.content);
    if (m.role === 'assistant' && citationLinks.length) {
      const reasonsByUrl = scoreReasonsByUrl(state.messages, idx);
      bubble.innerHTML += buildCitationPanel(m.content, reasonsByUrl, citationPrefix);
    }
    row.appendChild(bubble);
    box.appendChild(row);
    anchor = row;

  });


  const chatJobs = (state.backgroundJobs || [])
    .filter((job) => job && job.session === (state.activeSession || '') && (job.prompt || (Array.isArray(job.events) && job.events.length)))
    .slice()
    .sort((a, b) => String(a.started_at || '').localeCompare(String(b.started_at || '')));
  chatJobs.forEach((job) => {
    const row = document.createElement('div');
    row.className = 'msg role-assistant';
    row.innerHTML = '<div class="role">assistant</div>';
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.innerHTML = '<span class="msg-tag">Live run activity</span><span class="msg-time">background</span>';
    row.appendChild(meta);
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    const status = escapeHtml(String(job.status || 'unknown'));
    const prompt = escapeHtml(String(job.prompt || '(prompt unavailable)'));
    const fallbackTimelineEvents = timelineEventsForActiveSession().slice(-40);
    const eventLinesRaw = (Array.isArray(job.events) && job.events.length)
      ? job.events
      : fallbackTimelineEvents;
    const events = eventLinesRaw.map((line) => escapeHtml(String(line || ''))).join('\n');
    const plan = escapeHtml(String(job.plan || '(not drafted)'));
    const stageLines = buildJobStageLines(job);
    const fallbackStages = fallbackTimelineEvents
      .filter((line) => String(line || '').startsWith('plan:') || String(line || '').startsWith('status:') || String(line || '').startsWith('tool-request:') || String(line || '').startsWith('tool-run:'));
    const stages = (stageLines.length ? stageLines : fallbackStages).map((line) => escapeHtml(line)).join('\n');
    const openAttr = ['running', 'awaiting_plan_approval'].includes(job.status) ? ' open' : '';
    bubble.innerHTML = `<details${openAttr}><summary>Live run activity · ${status} · ${escapeHtml(String(job.id || ''))}</summary><div class="small-muted mt-1"><strong>Prompt</strong><pre><code>${prompt}</code></pre><strong>Plan</strong><pre><code>${plan}</code></pre><strong>Stages</strong><pre><code>${stages || '(no stages yet)'}</code></pre><strong>Events</strong><pre><code>${events || '(no events yet)'}</code></pre></div></details>`;
    row.appendChild(bubble);
    box.appendChild(row);
  });

  const notices = runNoticesBySession[state.activeSession || ''] || [];
  notices.forEach((notice) => {
    const row = document.createElement('div');
    row.className = 'msg role-assistant';
    row.innerHTML = `<div class="role">assistant</div><div class="msg-meta"><span class="msg-tag">System</span><span class="msg-time">notice</span></div>`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.innerHTML = `<p>${escapeHtml(notice.text)}</p><button class="btn btn-soft btn-sm" data-run-details="${_escapeAttr(notice.id)}">view details</button>`;
    row.appendChild(bubble);
    box.appendChild(row);
  });

  highlightCodeBlocks(box);
  wireCopyButtons(box);

  box.scrollTop = box.scrollHeight;
}

function renderTraces() {
  const el = document.getElementById('traces');
  if (!el) return;
  const lines = timelineEventsForActiveSession();
  if (!lines.length) {
    el.textContent = 'debug traces will appear here';
    return;
  }
  el.innerHTML = lines.map((line) => {
    let cls = 'trace-line';
    if (line.startsWith('model:')) cls += ' trace-model';
    else if (line.startsWith('tool-request:')) cls += ' trace-request';
    else if (line.startsWith('tool-run:')) cls += ' trace-run';
    return `<span class="${cls}">${line.replace(/</g, '&lt;')}</span>`;
  }).join('');
}

function sessionStatusMap() {
  const map = {};
  for (const job of (state.backgroundJobs || [])) {
    const name = job.session;
    if (!name) continue;
    if (['running', 'awaiting_plan_approval'].includes(job.status)) {
      map[name] = { status: 'running', job };
      continue;
    }
    if (job.status === 'completed' && !completedSeenSessions.has(name)) {
      map[name] = { status: 'done', job };
    }
  }
  return map;
}

function activeSearchingJob() {
  return (state.backgroundJobs || []).find((j) => j.session === state.activeSession && ['running', 'awaiting_plan_approval'].includes(j.status));
}


function _formatRuntime(seconds) {
  const s = Math.max(0, Math.trunc(Number(seconds || 0)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

function updateQueryRuntime() {
  const runtimeEl = document.getElementById('queryRuntime');
  const job = activeSearchingJob();
  if (!runtimeEl || !job || !job.started_at) {
    if (runtimeEl) runtimeEl.classList.remove('show');
    return;
  }
  const started = Date.parse(job.started_at);
  if (Number.isNaN(started)) {
    runtimeEl.classList.remove('show');
    return;
  }
  const seconds = (Date.now() - started) / 1000;
  runtimeEl.textContent = `Runtime: ${_formatRuntime(seconds)}`;
  runtimeEl.classList.add('show');
}

async function killActiveJob() {
  const job = activeSearchingJob();
  if (!job) return;
  const killBtn = document.getElementById('killJob');
  if (killBtn) killBtn.disabled = true;
  try {
    await api(`/api/jobs/${job.id}/kill`, 'POST', { reason: 'killed from UI' });
    const latest = await api(`/api/jobs/${job.id}`);
    updateBackgroundJobInState(latest);
    const reportEl = document.getElementById('report');
    if (reportEl) reportEl.textContent = `kill signal sent: ${job.id}`;
    updateChatBusyState();
  } catch (err) {
    alert(err.message || 'Unable to kill running job');
  } finally {
    if (killBtn) killBtn.disabled = false;
  }
}

function updateKillButton() {
  const killBtn = document.getElementById('killJob');
  if (!killBtn) return;
  const active = activeSearchingJob();
  const show = !!active;
  killBtn.classList.toggle('d-none', !show);
  killBtn.disabled = !show;
}

function updateChatBusyState() {
  const searching = !!activeSearchingJob();
  const sendingForSession = !!sending && (!sendingSession || sendingSession === state.activeSession);
  const busy = searching || sendingForSession;
  const composer = document.getElementById('chatComposer');
  const indicator = document.getElementById('searchingIndicator');
  const prompt = document.getElementById('prompt');
  const send = document.getElementById('send');
  const sendBackground = document.getElementById('sendBackground');
  const attach = document.getElementById('promptAttach');
  if (composer) composer.classList.toggle('composer-disabled', busy);
  if (indicator) indicator.classList.toggle('show', busy);
  if (prompt) prompt.disabled = busy;
  if (send) send.disabled = busy;
  if (sendBackground) sendBackground.disabled = busy;
  if (attach) attach.disabled = busy;
  updateThinking(busy);
  updateQueryRuntime();
  renderBackgroundActivity(activeSearchingJob() || null);
  updateKillButton();
}


function closeAllSessionMenus() {
  const overlay = document.getElementById('sessionMenuOverlay');
  overlay.classList.remove('show');
  openSessionMenuFor = null;
}

function openSessionMenu(anchorBtn, sessionName) {
  const overlay = document.getElementById('sessionMenuOverlay');
  const rect = anchorBtn.getBoundingClientRect();
  overlay.style.top = `${rect.bottom + 6}px`;
  overlay.style.left = `${Math.max(8, rect.right - 136)}px`;
  overlay.classList.add('show');
  openSessionMenuFor = sessionName;
}

function renderSessions(list, active) {
  const host = document.getElementById('sessionList');
  host.innerHTML = '';
  const statuses = sessionStatusMap();
  const prefs = _readSessionPrefs();
  state.recentSessions = prefs.recent.filter((n) => list.includes(n));
  const filter = String((document.getElementById('sessionQuickSwitch') || {}).value || '').trim().toLowerCase();
  const ordered = list.slice();

  for (const name of ordered) {
    if (filter && !name.toLowerCase().includes(filter)) continue;
    const row = document.createElement('div');
    row.className = 'session-row';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `session-item ${name === active ? 'active' : ''}`;
    const statusData = statuses[name] || null;
    const status = statusData ? statusData.status : '';
    const dotClass = status === 'running' ? 'running' : (status === 'done' ? 'done' : '');
    const tip = statusData?.job?.last_step || status || 'idle';
    btn.innerHTML = `<span class="session-label"><span class="session-dot ${dotClass}" title="${escapeHtml(tip)}"></span><span class="session-name">${escapeHtml(name)}</span></span><span class="ui-badge ${status === 'running' ? 'warn' : (status === 'done' ? 'success' : '')}">${status || 'idle'}</span>`;
    btn.setAttribute('data-session-name', name);
    btn.addEventListener('click', () => {
      markSessionRecent(name);
      completedSeenSessions.add(name);
      sessionAction('switch', name).catch((e) => alert(e.message));
    });

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'session-menu-btn';
    menuBtn.textContent = '⋯';

    menuBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const isOpen = document.getElementById('sessionMenuOverlay').classList.contains('show') && openSessionMenuFor === name;
      closeAllSessionMenus();
      if (!isOpen) openSessionMenu(menuBtn, name);
    });

    row.appendChild(btn);
    row.appendChild(menuBtn);
    host.appendChild(row);
  }

  const recentHost = document.getElementById('sessionRecent');
  if (recentHost) {
    const recent = state.recentSessions.filter((n) => n !== active).slice(0, 5);
    recentHost.innerHTML = recent.length
      ? recent.map((n) => `<button class="btn btn-soft btn-sm me-1 mb-1" data-recent-session="${_escapeAttr(n)}">${escapeHtml(n)}</button>`).join('')
      : '<span class="small-muted">No recent sessions.</span>';
    recentHost.querySelectorAll('[data-recent-session]').forEach((el) => {
      el.addEventListener('click', () => sessionAction('switch', el.getAttribute('data-recent-session')).catch((e) => alert(e.message)));
    });
  }

  const health = document.getElementById('sessionHealthSummary');
  if (health) {
    const running = Object.values(statuses).filter((s) => s.status === 'running').length;
    const done = Object.values(statuses).filter((s) => s.status === 'done').length;
    const idle = Math.max(0, list.length - running - done);
    health.textContent = `idle ${idle} · running ${running} · done ${done}`;
  }

  document.getElementById('activeSession').textContent = `active: ${active}`;
}

function renderUploads() {
  const el = document.getElementById('uploadList');
  if (!state.uploads.length) {
    el.textContent = 'No uploaded context files.';
    return;
  }
  el.innerHTML = state.uploads.slice(-20).map((u) => `
    <div class="upload-item">
      <span>${escapeHtml(u.name)} (${escapeHtml(u.kind)}, ${escapeHtml(String(u.size))} bytes)</span>
      <button class="btn btn-soft btn-sm" data-upload-remove="${encodeURIComponent(u.name)}">Remove</button>
    </div>
  `).join('');

  el.querySelectorAll('[data-upload-remove]').forEach((btn) => {
    btn.addEventListener('click', () => removeUploadedFile(decodeURIComponent(btn.getAttribute('data-upload-remove') || '')).catch((e) => alert(e.message)));
  });
}

function renderToolSettings() {
  const host = document.getElementById('toolVisibilityList');
  const builtin = state.tools.filter((tool) => tool.source === 'builtin');
  if (!builtin.length) {
    host.textContent = 'No built-in tools available.';
    return;
  }
  host.innerHTML = builtin.map((tool) => `
    <label class="tool-visibility-row d-flex align-items-start gap-2">
      <input type="checkbox" class="form-check-input mt-1" data-tool-visible="${escapeHtml(tool.name)}" ${tool.enabled ? 'checked' : ''} />
      <span>
        <strong>${escapeHtml(tool.name)}</strong><br/>
        <span class="small-muted">${escapeHtml(tool.description || '')}</span>
      </span>
    </label>
  `).join('');

  host.querySelectorAll('[data-tool-visible]').forEach((el) => {
    el.addEventListener('change', scheduleApplySettings);
  });

  const errorsEl = document.getElementById('customToolErrors');
  if (state.customToolErrors.length) errorsEl.textContent = state.customToolErrors.join(' | ');
  else errorsEl.textContent = '';
}

function buildToolVisibilityPayload() {
  const payload = {};
  document.querySelectorAll('[data-tool-visible]').forEach((el) => {
    payload[el.getAttribute('data-tool-visible')] = !!el.checked;
  });
  return payload;
}

function buildEnabledSkillsPayload() {
  const toggles = Array.from(document.querySelectorAll('[data-skill-enabled]'));
  if (!toggles.length) return Array.isArray(state.enabledSkills) ? [...state.enabledSkills] : [];
  const enabled = [];
  toggles.forEach((el) => {
    if (el.checked) enabled.push(el.getAttribute('data-skill-enabled'));
  });
  return enabled;
}

function renderSkillsLifecycleVisibility() {
  const host = document.getElementById('skillsLifecycleHost');
  if (!host) return;
  const skills = Array.isArray(state.skills) ? state.skills : [];
  if (!skills.length) {
    host.textContent = 'No skills found in ./skills.';
    return;
  }
  const stage3 = _readStage3Store();
  const presets = stage3.skillPresets || {};
  const presetEntries = Object.entries(presets);
  const enabledNow = new Set(Array.isArray(state.enabledSkills) ? state.enabledSkills : []);
  const overrideStore = _readSessionOverrideStore();
  const sessions = overrideStore.sessions || {};

  const rows = skills.map((name) => {
    const presetNames = presetEntries
      .filter(([, list]) => Array.isArray(list) && list.includes(name))
      .map(([presetName]) => presetName);
    const overriddenSessions = Object.entries(sessions)
      .filter(([, cfg]) => Array.isArray(cfg && cfg.enabled_skills) && cfg.enabled_skills.includes(name))
      .map(([sessionName]) => sessionName);
    return {
      name,
      now: enabledNow.has(name),
      inPresets: presetNames,
      overriddenSessions,
    };
  });

  host.innerHTML = `<table class="compact-table"><thead><tr><th>Skill</th><th>Now</th><th>Presets</th><th>Session overrides</th><th>Details</th></tr></thead><tbody>${rows.map((r)=>`<tr><td>${escapeHtml(r.name)}</td><td>${r.now ? 'enabled' : 'off'}</td><td>${r.inPresets.length}</td><td>${r.overriddenSessions.length}</td><td><div class="small-muted">${r.inPresets.length ? `Presets: ${escapeHtml(r.inPresets.join(', '))}` : 'Presets: —'}</div><div class="small-muted">${r.overriddenSessions.length ? `Sessions: ${escapeHtml(r.overriddenSessions.join(', '))}` : 'Sessions: —'}</div></td></tr>`).join('')}</tbody></table>`;
}

function renderSkillSettings() {
  const host = document.getElementById('skillToggleList');
  if (!host) return;
  const skills = Array.isArray(state.skills) ? state.skills : [];
  const enabled = new Set(Array.isArray(state.enabledSkills) ? state.enabledSkills : []);
  if (!skills.length) {
    host.textContent = 'No skills found in ./skills.';
    return;
  }
  host.innerHTML = skills.map((skillName) => `
    <div class="tool-visibility-row d-flex align-items-center gap-2 justify-content-between">
      <label class="d-flex align-items-center gap-2 m-0" style="min-width:0">
        <input type="checkbox" class="form-check-input" data-skill-enabled="${escapeHtml(skillName)}" ${enabled.has(skillName) ? 'checked' : ''} />
        <span class="small-muted text-truncate">${escapeHtml(skillName)}</span>
      </label>
      <button type="button" class="btn btn-soft btn-sm" data-skill-view="${escapeHtml(skillName)}">View</button>
    </div>
  `).join('');
  host.querySelectorAll('[data-skill-enabled]').forEach((el) => {
    el.addEventListener('change', scheduleApplySettings);
  });
  host.querySelectorAll('[data-skill-view]').forEach((el) => {
    el.addEventListener('click', () => openSkillView(el.getAttribute('data-skill-view') || '').catch((e) => alert(e.message)));
  });
  renderSkillsLifecycleVisibility();
}

async function openSkillView(name) {
  if (!name) return;
  document.getElementById('skillViewTitle').textContent = `Skill: ${name}`;
  document.getElementById('skillViewBody').textContent = 'Loading…';
  showModal('skillViewModal', true);
  const payload = await api(`/api/skills/${encodeURIComponent(name)}`);
  document.getElementById('skillViewBody').innerHTML = formatMessageContent(payload.content || '');
}


function saveSkillPresetFromUI() {
  const name = String((document.getElementById('skillPresetName') || {}).value || '').trim();
  if (!name) throw new Error('Preset name required');
  const store = _readStage3Store();
  store.skillPresets[name] = buildEnabledSkillsPayload();
  _writeStage3Store(store);
  renderSkillPresets();
  const status = document.getElementById('skillPresetStatus');
  if (status) status.textContent = `Saved preset ${name}.`;
}

function applySkillPresetFromUI() {
  const sel = document.getElementById('skillPresetSelect');
  const key = String(sel && sel.value || '');
  if (!key) return;
  const store = _readStage3Store();
  const list = Array.isArray(store.skillPresets[key]) ? store.skillPresets[key] : [];
  document.querySelectorAll('[data-skill-enabled]').forEach((el) => {
    el.checked = list.includes(el.getAttribute('data-skill-enabled'));
  });
  scheduleApplySettings();
  const status = document.getElementById('skillPresetStatus');
  if (status) status.textContent = `Applied preset ${key}.`;
}

function deleteSkillPresetFromUI() {
  const sel = document.getElementById('skillPresetSelect');
  const key = String(sel && sel.value || '');
  if (!key) return;
  const store = _readStage3Store();
  delete store.skillPresets[key];
  _writeStage3Store(store);
  renderSkillPresets();
  const status = document.getElementById('skillPresetStatus');
  if (status) status.textContent = `Deleted preset ${key}.`;
}

function saveRulesVersionFromUI() {
  const label = String((document.getElementById('rulesVersionName') || {}).value || '').trim() || `version-${Date.now()}`;
  const payload = {
    id: `v_${Date.now()}`,
    label,
    created_at: new Date().toISOString().slice(0, 19).replace('T', ' '),
    systemPromptOverride: String((document.getElementById('systemPromptOverride') || {}).value || ''),
    rulesChecklist: String((document.getElementById('rulesChecklist') || {}).value || ''),
  };
  const store = _readStage3Store();
  store.rulesVersions = [payload].concat(store.rulesVersions || []).slice(0, 50);
  _writeStage3Store(store);
  renderRulesVersions();
  const status = document.getElementById('rulesVersionStatus');
  if (status) status.textContent = `Saved rules version ${label}.`;
}

function rollbackRulesVersionFromUI() {
  const sel = document.getElementById('rulesVersionSelect');
  const key = String(sel && sel.value || '');
  const store = _readStage3Store();
  const found = (store.rulesVersions || []).find((v) => v.id === key);
  if (!found) return;
  const sys = document.getElementById('systemPromptOverride');
  const rules = document.getElementById('rulesChecklist');
  if (sys) sys.value = found.systemPromptOverride || '';
  if (rules) rules.value = found.rulesChecklist || '';
  persistControlPlaneFromUI();
  const status = document.getElementById('rulesVersionStatus');
  if (status) status.textContent = `Rolled back to ${found.label}.`;
}

function deleteRulesVersionFromUI() {
  const sel = document.getElementById('rulesVersionSelect');
  const key = String(sel && sel.value || '');
  const store = _readStage3Store();
  store.rulesVersions = (store.rulesVersions || []).filter((v) => v.id !== key);
  _writeStage3Store(store);
  renderRulesVersions();
  const status = document.getElementById('rulesVersionStatus');
  if (status) status.textContent = 'Deleted selected rules version.';
}

function _lineDiff(before, after) {
  const a = String(before || '').split('\n').map((l) => l.trim()).filter(Boolean);
  const b = String(after || '').split('\n').map((l) => l.trim()).filter(Boolean);
  const removed = a.filter((line) => !b.includes(line)).map((line) => `- ${line}`);
  const added = b.filter((line) => !a.includes(line)).map((line) => `+ ${line}`);
  const unchanged = b.filter((line) => a.includes(line)).slice(0, 12).map((line) => `  ${line}`);
  return [
    '# Rules diff',
    '## Added',
    ...(added.length ? added : ['(none)']),
    '',
    '## Removed',
    ...(removed.length ? removed : ['(none)']),
    '',
    '## Unchanged (sample)',
    ...(unchanged.length ? unchanged : ['(none)']),
  ].join('\n');
}

function compareRulesVersionFromUI() {
  const sel = document.getElementById('rulesVersionSelect');
  const key = String(sel && sel.value || '');
  const out = document.getElementById('rulesVersionDiffOutput');
  const status = document.getElementById('rulesVersionStatus');
  if (!out) return;
  const store = _readStage3Store();
  const found = (store.rulesVersions || []).find((v) => v.id === key);
  if (!found) {
    out.textContent = 'Select a rules version to compare.';
    if (status) status.textContent = 'No version selected for comparison.';
    return;
  }
  const current = String((document.getElementById('rulesChecklist') || {}).value || '');
  out.textContent = _lineDiff(found.rulesChecklist || '', current);
  if (status) status.textContent = `Compared current rules against ${found.label}.`;
}

function checkRulesConflictsFromUI() {
  const out = document.getElementById('rulesVersionDiffOutput');
  const status = document.getElementById('rulesVersionStatus');
  if (!out) return;
  const lines = String((document.getElementById('rulesChecklist') || {}).value || '')
    .split('\n').map((l) => l.trim()).filter(Boolean);
  const seen = new Set();
  const polarityByKey = new Map();
  const duplicates = [];
  const contradictory = [];

  const normalize = (line) => String(line || '').toLowerCase().replace(/^(always|never|do not|don't|no|do|use|prefer)\s+/, '').trim();
  const polarity = (line) => (/^(never|do not|don't|no)\b/i.test(line) ? 'neg' : 'pos');

  lines.forEach((line) => {
    const key = normalize(line);
    const sig = `${key}::${String(line).toLowerCase()}`;
    if (seen.has(sig)) duplicates.push(line);
    seen.add(sig);

    const pol = polarity(line);
    const prior = polarityByKey.get(key);
    if (prior && prior !== pol) contradictory.push(line);
    if (!prior) polarityByKey.set(key, pol);
  });

  const report = [
    '# Rules conflict check',
    `Total rules: ${lines.length}`,
    '',
    '## Duplicate-like rules',
    ...(duplicates.length ? duplicates.map((d) => `- ${d}`) : ['(none)']),
    '',
    '## Potential contradictions',
    ...(contradictory.length ? contradictory.map((d) => `- ${d}`) : ['(none)']),
  ].join('\n');
  out.textContent = report;
  if (status) status.textContent = (duplicates.length || contradictory.length)
    ? `Conflict check found ${duplicates.length + contradictory.length} issue(s).`
    : 'Conflict check passed with no obvious issues.';
}

function saveBehaviorProfileFromUI() {
  const name = String((document.getElementById('behaviorProfileName') || {}).value || '').trim();
  if (!name) throw new Error('Profile name required');
  const store = _readStage3Store();
  store.behaviorProfiles[name] = {
    knobTemperature: Number((document.getElementById('knobTemperature') || {}).value || 0.2),
    knobTopP: Number((document.getElementById('knobTopP') || {}).value || 0.95),
    knobToolBias: Number((document.getElementById('knobToolBias') || {}).value || 0.7),
    knobVerbosity: Number((document.getElementById('knobVerbosity') || {}).value || 0.5),
  };
  _writeStage3Store(store);
  renderBehaviorProfiles();
  const status = document.getElementById('behaviorProfileStatus');
  if (status) status.textContent = `Saved profile ${name}.`;
}

function applyBehaviorProfileFromUI() {
  const key = String((document.getElementById('behaviorProfileSelect') || {}).value || '');
  const store = _readStage3Store();
  const p = (store.behaviorProfiles || {})[key];
  if (!p) return;
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v); };
  setVal('knobTemperature', p.knobTemperature);
  setVal('knobTopP', p.knobTopP);
  setVal('knobToolBias', p.knobToolBias);
  setVal('knobVerbosity', p.knobVerbosity);
  persistControlPlaneFromUI();
  const status = document.getElementById('behaviorProfileStatus');
  if (status) status.textContent = `Applied profile ${key}.`;
}

function deleteBehaviorProfileFromUI() {
  const key = String((document.getElementById('behaviorProfileSelect') || {}).value || '');
  if (!key) return;
  const store = _readStage3Store();
  delete store.behaviorProfiles[key];
  _writeStage3Store(store);
  renderBehaviorProfiles();
  const status = document.getElementById('behaviorProfileStatus');
  if (status) status.textContent = `Deleted profile ${key}.`;
}

function updateContextExcludesFromUI() {
  const store = _readStage3Store();
  store.contextExcludes = {
    traces: !!(document.getElementById('ctxExcludeTraces') || {}).checked,
    uploads: !!(document.getElementById('ctxExcludeUploads') || {}).checked,
    tools: !!(document.getElementById('ctxExcludeTools') || {}).checked,
  };
  _writeStage3Store(store);
  renderContextBudgetPanel();
}

function summarizeContextRulesNow() {
  const rules = document.getElementById('rulesChecklist');
  if (!rules) return;
  const lines = String(rules.value || '').split('\n').map((l) => l.trim()).filter(Boolean);
  const summarized = lines.slice(0, 5).map((l, i) => `${i + 1}. ${l}`).join('\n');
  rules.value = summarized;
  persistControlPlaneFromUI();
  const status = document.getElementById('contextBudgetActionStatus');
  if (status) status.textContent = 'Rules checklist summarized to top 5 entries.';
}
function parseCustomToolsInput() {
  const raw = document.getElementById('customTools').value.trim();
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('Custom tools must be a JSON array.');
    document.getElementById('customToolErrors').textContent = '';
    return parsed;
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Invalid custom tools JSON.';
    document.getElementById('customToolErrors').textContent = message;
    throw new Error(message);
  }
}

function wireModels(provider) {
  const modelSel = document.getElementById('model');
  modelSel.innerHTML = '';
  for (const model of (state.models[provider] || [])) {
    const opt = document.createElement('option');
    opt.value = model;
    opt.textContent = model;
    modelSel.appendChild(opt);
  }
}

function buildSettingsPayload() {
  return {
    provider: document.getElementById('provider').value,
    model: document.getElementById('model').value,
    openai_api_key: document.getElementById('openaiApiKey').value || null,
    google_api_key: document.getElementById('googleApiKey').value || null,
    ollama_endpoint: document.getElementById('ollamaEndpoint').value || null,
    ollama_context_window: Number(document.getElementById('ollamaContextWindow').value || 65536),
    approval_mode: document.getElementById('approval').value,
    workspace: document.getElementById('workspace').value || null,
    debug: document.getElementById('debugLevel').value === 'debug',
    debug_level: document.getElementById('debugLevel').value || 'info',
    agentic_planning: document.getElementById('agentic').checked,
    research_mode: document.getElementById('researchMode').checked,
    tool_visibility: buildToolVisibilityPayload(),
    enabled_skills: buildEnabledSkillsPayload(),
    custom_tools: parseCustomToolsInput(),
  };
}

function updateThinking(show) {
  document.getElementById('thinking').style.display = show ? 'flex' : 'none';
}

function updateUsagePanel(usage) {
  const u = usage || {};
  document.getElementById('usageIn').textContent = String(Math.trunc(Number(u.input_tokens || 0)));
  document.getElementById('usageOut').textContent = String(Math.trunc(Number(u.output_tokens || 0)));
  document.getElementById('usageTotal').textContent = String(Math.trunc(Number(u.total_tokens || 0)));
  document.getElementById('usageCost').textContent = Number(u.estimated_cost_usd || 0).toFixed(6);
}

function syncPricingEditor() {
  const pricingJson = document.getElementById('pricingJson');
  if (!pricingJson) return;
  if (document.activeElement === pricingJson) return;
  pricingJson.value = JSON.stringify(state.pricing || {}, null, 2);
}

function showModal(id, show) {
  const modal = document.getElementById(id);
  if (show) modal.classList.add('show');
  else modal.classList.remove('show');
  const anyModalOpen = ['advancedModal', 'metricsModal', 'workspaceModal', 'approvalModal', 'newSessionModal', 'planApprovalModal', 'condenseModal', 'gitModal', 'skillViewModal', 'runDetailsModal', 'sessionOverridesModal']
    .some((modalId) => document.getElementById(modalId).classList.contains('show'));
  document.getElementById('app').classList.toggle('modal-active', anyModalOpen);
}

function scheduleApplySettings() {
  if (syncing) return;
  clearTimeout(applyTimer);
  applyTimer = setTimeout(() => applySettings().catch((e) => alert(e.message)), 250);
}

async function applySettings() {
  await api('/api/settings', 'POST', buildSettingsPayload());
  await refreshState();
}

async function savePricing() {
  const raw = document.getElementById('pricingJson').value || '{}';
  let parsed = {};
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(`Invalid pricing JSON: ${err.message}`);
  }
  await api('/api/pricing', 'POST', { pricing: parsed });
  await refreshState();
}

async function uploadContextFiles() {
  const input = document.getElementById('contextFiles');
  if (!input.files || !input.files.length) return;
  const form = new FormData();
  for (const file of input.files) form.append('files', file);
  await apiForm('/api/uploads', form);
  input.value = '';
  await refreshState();
}

async function uploadPromptAttachmentFiles() {
  const input = document.getElementById('promptAttachInput');
  if (!input.files || !input.files.length) return;
  const status = document.getElementById('promptUploadStatus');
  const form = new FormData();
  for (const file of input.files) form.append('files', file);
  await apiForm('/api/uploads', form);
  status.textContent = `${input.files.length} file(s) attached to context.`;
  input.value = '';
  await refreshState();
}


async function clearAllStoredDataFromUI() {
  const status = document.getElementById('clearAllStoredDataStatus');
  const confirmed = window.confirm('Clear ALL persisted runtime data? This removes saved sessions, uploads, and workspace cache.');
  if (!confirmed) return;
  if (status) status.textContent = 'Clearing persisted data…';
  const payload = await api('/api/state/clear-all', 'POST', {});

  Object.keys(localStorage).forEach((key) => {
    if (key.startsWith('mu_') || key === 'mu_cli_dark_mode') localStorage.removeItem(key);
  });

  if (status) status.textContent = `Cleared data (sessions: ${payload.cleared.sessions}, uploads: ${payload.cleared.upload_files}, workspace snapshots: ${payload.cleared.workspace_snapshots}).`;
  await refreshState();
}

async function clearUploadedStore() {
  await fetch('/api/uploads', { method: 'DELETE' }).then(async (res) => {
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'request failed');
  });
  await refreshState();
}

async function removeUploadedFile(name) {
  if (!name) return;
  await fetch(`/api/uploads/${encodeURIComponent(name)}`, { method: 'DELETE' }).then(async (res) => {
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || 'request failed');
  });
  await refreshState();
}

async function loadDirs(path='') {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const payload = await api(`/api/fs/dirs${q}`);
  document.getElementById('dirPath').value = payload.cwd;
  document.getElementById('dirCwd').textContent = `cwd: ${payload.cwd}`;
  selectedDir = payload.cwd;

  const list = document.getElementById('dirList');
  list.innerHTML = '';

  if (payload.parent) {
    const up = document.createElement('button');
    up.className = 'btn btn-soft btn-sm';
    up.textContent = '..';
    up.onclick = () => loadDirs(payload.parent).catch((e) => alert(e.message));
    list.appendChild(up);
  }

  for (const child of payload.children) {
    const btn = document.createElement('button');
    btn.className = 'btn btn-soft btn-sm';
    btn.textContent = child.name;
    btn.onclick = () => loadDirs(child.path).catch((e) => alert(e.message));
    list.appendChild(btn);
  }
}

async function pollApproval() {
  const payload = await api('/api/approval');
  state.pendingApproval = payload.pending;
  const pending = state.pendingApproval;

  if (!pending) {
    document.getElementById('approvalPatchPreview').innerHTML = '';
    lastApprovalPatchFingerprint = null;
    showModal('approvalModal', false);
    return;
  }

  document.getElementById('approvalToolName').textContent = `Tool: ${pending.tool_name}`;
  renderApprovalArgs(pending.args);
  renderApprovalPatchPreview(pending.args);
  showModal('approvalModal', true);
}

async function sendApprovalDecision(decision) {
  if (!state.pendingApproval) return;
  await api('/api/approval', 'POST', {
    id: state.pendingApproval.id,
    decision,
  });
  state.pendingApproval = null;
  showModal('approvalModal', false);
}

async function refreshState() {
  syncing = true;
  const previousSession = state.activeSession;
  const s = await api('/api/state');
  state.models = s.models;
  state.messages = s.messages;
  state.traces = s.traces;
  state.pricing = s.pricing || {};
  state.sessionUsage = s.session_usage || {};
  state.sessions = s.sessions || [];
  state.activeSession = s.session || '';
  if (previousSession !== state.activeSession) _lastBackgroundActivityKey = null;
  state.sessionTurns = s.session_turns || [];
  state.uploads = s.uploads || [];
  state.pendingApproval = s.pending_approval || null;
  state.tools = s.tools || [];
  state.customToolErrors = s.custom_tool_errors || [];
  state.backgroundJobs = s.background_jobs || [];
  state.gitRepos = s.git_repos || [];
  state.gitCurrentRepo = s.git_current_repo || null;
  state.gitCurrentBranch = s.git_current_branch || null;
  state.gitBranches = s.git_branches || [];
  state.skills = s.skills || [];
  state.enabledSkills = s.enabled_skills || [];
  state.workspaceIndexStats = s.workspace_index_stats || {};
  state.telemetry = s.telemetry || {};
  const customSpecs = s.custom_tool_specs || [];

  const providerSel = document.getElementById('provider');
  providerSel.innerHTML = '';
  Object.keys(s.models).forEach((p) => {
    const o = document.createElement('option');
    o.value = p;
    o.textContent = p;
    providerSel.appendChild(o);
  });
  providerSel.value = s.provider;
  wireModels(s.provider);
  document.getElementById('model').value = s.model;
  document.getElementById('approval').value = s.approval_mode;
  document.getElementById('workspace').value = s.workspace || '';
  if (document.activeElement !== document.getElementById('openaiApiKey')) {
    document.getElementById('openaiApiKey').value = s.openai_api_key || '';
  }
  if (document.activeElement !== document.getElementById('googleApiKey')) {
    document.getElementById('googleApiKey').value = s.google_api_key || '';
  }
  if (document.activeElement !== document.getElementById('ollamaEndpoint')) {
    document.getElementById('ollamaEndpoint').value = s.ollama_endpoint || '';
  }
  const ctxInput = document.getElementById('ollamaContextWindow');
  if (ctxInput && document.activeElement !== ctxInput) {
    const ctxValue = Number(s.ollama_context_window || 65536);
    ctxInput.value = String(ctxValue);
    const ctxLabel = document.getElementById('ollamaContextWindowValue');
    if (ctxLabel) ctxLabel.textContent = String(ctxValue);
  }
  document.getElementById('debugLevel').value = s.debug_level || (s.debug ? 'debug' : 'info');
  document.getElementById('agentic').checked = !!s.agentic_planning;
  document.getElementById('researchMode').checked = !!s.research_mode;
  document.getElementById('condenseEnabled').checked = !!s.condense_enabled;
  document.getElementById('condenseWindow').value = Number(s.condense_window || 12);
  document.getElementById('maxRuntime').value = Number(s.max_runtime_seconds || 900);
  document.getElementById('meta').textContent = `${s.provider}:${s.model}`;
  if (document.activeElement !== document.getElementById('customTools')) {
    document.getElementById('customTools').value = JSON.stringify(customSpecs, null, 2);
  }

  syncPricingEditor();
  const sessionJobs = (state.backgroundJobs || []).filter((j) => j && j.session === s.session);
  const activeJob = sessionJobs.find((j) => ['running', 'awaiting_plan_approval'].includes(j.status));
  const latestTerminalJob = sessionJobs
    .filter((j) => ['completed', 'failed', 'killed', 'timed_out'].includes(j.status))
    .sort((a, b) => String(b.finished_at || '').localeCompare(String(a.finished_at || '')))[0];
  if (activeJob && activeJob.usage) updateUsagePanel(activeJob.usage);
  else if (latestTerminalJob && latestTerminalJob.usage) updateUsagePanel(latestTerminalJob.usage);
  else updateUsagePanel(state.sessionUsage);
  renderSessions(state.sessions, state.activeSession);
  renderUploads();
  renderToolSettings();
  renderToolsConsole();
  renderSkillSettings();
  renderGitControls();
  renderMessages();
  renderMetadataPanel();
  renderTraces();
  updateChatBusyState();
  renderMetrics();
  renderExecutionTimeline();
  syncControlPlaneUIFromPrefs();
  renderContextBudgetPanel();

  if (state.pendingApproval) {
    document.getElementById('approvalToolName').textContent = `Tool: ${state.pendingApproval.tool_name}`;
    renderApprovalArgs(state.pendingApproval.args);
    renderApprovalPatchPreview(state.pendingApproval.args);
    showModal('approvalModal', true);
  } else {
    document.getElementById('approvalPatchPreview').innerHTML = '';
    lastApprovalPatchFingerprint = null;
    showModal('approvalModal', false);
  }

  syncing = false;
}

// >>> app/main.js
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

  if (!background) {
    if (approvalPoll) clearInterval(approvalPoll);
    approvalPoll = setInterval(() => pollApproval().catch(() => {}), 1500);
  }

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
    if (approvalPoll) {
      clearInterval(approvalPoll);
      approvalPoll = null;
    }
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

// >>> app/events.js
// --- event wiring -----------------------------------------------------------
for (const id of ['provider', 'model', 'openaiApiKey', 'googleApiKey', 'ollamaContextWindow', 'approval', 'workspace', 'debugLevel', 'agentic', 'researchMode', 'condenseEnabled', 'condenseWindow', 'maxRuntime', 'darkMode']) {
  const input = byId(id);
  if (!input) continue;
  input.addEventListener('change', () => {
    if (id === 'provider') wireModels(byId('provider').value);
    if (id === 'darkMode') applyThemePreference();
    if (id === 'workspace') runWithAlert(() => refreshGitRepos());
    scheduleApplySettings();
    syncPricingEditor();
  });
  if (id === 'workspace' || id === 'openaiApiKey' || id === 'googleApiKey') {
    input.addEventListener('blur', scheduleApplySettings);
  }
}

const customToolsInput = byId('customTools');
if (customToolsInput) customToolsInput.addEventListener('blur', scheduleApplySettings);
const ollamaContextWindowInput = byId('ollamaContextWindow');
if (ollamaContextWindowInput) {
  ollamaContextWindowInput.addEventListener('input', () => {
    const label = byId('ollamaContextWindowValue');
    if (label) label.textContent = String(ollamaContextWindowInput.value || '65536');
  });
}

bindClick('promptAttach', (e) => {
  e.preventDefault();
  byId('promptAttachInput').click();
});
bindChange('promptAttachInput', () => uploadPromptAttachmentFiles());

bindClick('send', () => sendPrompt(false));
bindClick('sendBackground', () => sendPrompt(true));
bindClick('killJob', () => killActiveJob());
bindClick('newSession', () => openNewSessionModal());
bindClick('savePricing', () => savePricing());

bindClick('saveSkillPreset', () => saveSkillPresetFromUI());
bindClick('applySkillPreset', () => applySkillPresetFromUI());
bindClick('deleteSkillPreset', () => deleteSkillPresetFromUI());

bindClick('saveRulesVersion', () => saveRulesVersionFromUI());
bindClick('rollbackRulesVersion', () => rollbackRulesVersionFromUI());
bindClick('deleteRulesVersion', () => deleteRulesVersionFromUI());
bindClick('compareRulesVersion', () => compareRulesVersionFromUI());
bindClick('checkRulesConflicts', () => checkRulesConflictsFromUI());

bindClick('saveBehaviorProfile', () => saveBehaviorProfileFromUI());
bindClick('applyBehaviorProfile', () => applyBehaviorProfileFromUI());
bindClick('deleteBehaviorProfile', () => deleteBehaviorProfileFromUI());

bindChange('ctxExcludeTraces', () => updateContextExcludesFromUI());
bindChange('ctxExcludeUploads', () => updateContextExcludesFromUI());
bindChange('ctxExcludeTools', () => updateContextExcludesFromUI());
bindClick('summarizeContextNow', () => summarizeContextRulesNow());
bindClick('uploadFiles', () => uploadContextFiles());
bindClick('clearUploads', () => clearUploadedStore());
bindClick('clearAllStoredData', () => clearAllStoredDataFromUI());


for (const id of ['systemPromptOverride', 'rulesChecklist', 'knobTemperature', 'knobTopP', 'knobToolBias', 'knobVerbosity']) {
  const el = byId(id);
  if (!el) continue;
  const evt = (id === 'systemPromptOverride' || id === 'rulesChecklist') ? 'blur' : 'input';
  el.addEventListener(evt, () => persistControlPlaneFromUI());
}

bindClick('toggleSidebar', () => { byId('app').classList.toggle('sidebar-hidden'); closeAllSessionMenus(); });
bindClick('openAdvanced', () => showModal('advancedModal', true));
bindClick('openHelp', () => showModal('helpModal', true));
bindClick('closeAdvanced', () => showModal('advancedModal', false));
bindClick('openMetrics', () => { renderMetrics(); showModal('metricsModal', true); });
bindClick('closeMetrics', () => showModal('metricsModal', false));
bindClick('openGitModal', async () => {
  try {
    await refreshGitBranches();
  } catch (_) {
    // ignore and still open modal with existing state
  }
  renderGitControls();
  showModal('gitModal', true);
});

for (const [buttonId, modalId] of [
  ['closeGitModal', 'gitModal'],
  ['closeHelpModal', 'helpModal'],
  ['closeSkillViewModal', 'skillViewModal'],
  ['closeRunDetailsModal', 'runDetailsModal'],
  ['closeWorkspaceModal', 'workspaceModal'],
  ['closeNewSessionModal', 'newSessionModal'],
  ['closeCondenseModal', 'condenseModal'],
  ['closeSessionOverridesModal', 'sessionOverridesModal'],
]) {
  bindClick(buttonId, () => showModal(modalId, false));
}

bindClick('toggleMetaSidebar', () => {
  const layout = document.querySelector('.workspace-layout');
  if (!layout) return;
  layout.classList.toggle('meta-hidden');
  const btn = byId('toggleMetaSidebar');
  const hidden = layout.classList.contains('meta-hidden');
  btn.textContent = '☰';
  btn.title = hidden ? 'Show metadata panel' : 'Hide metadata panel';
  btn.setAttribute('aria-label', btn.title);
});


function initShellResize() {
  const app = byId('app');
  const handle = byId('sidebarResizeHandle');
  const sidebar = byId('sidebar');
  if (!app || !handle || !sidebar) return;

  const stored = Number(localStorage.getItem('mu_sidebar_width') || 0);
  if (stored >= 260 && stored <= 560) app.style.setProperty('--sidebar-width', `${stored}px`);

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  const onMove = (e) => {
    if (!dragging) return;
    const next = Math.min(560, Math.max(260, startWidth + (e.clientX - startX)));
    app.style.setProperty('--sidebar-width', `${next}px`);
    localStorage.setItem('mu_sidebar_width', String(next));
  };
  const onUp = () => {
    dragging = false;
    document.body.style.userSelect = '';
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  };

  handle.addEventListener('mousedown', (e) => {
    if (app.classList.contains('sidebar-hidden')) return;
    dragging = true;
    startX = e.clientX;
    startWidth = sidebar.getBoundingClientRect().width;
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });
}

function initMetaResize() {
  const layout = document.querySelector('.workspace-layout');
  const handle = byId('metaResizeHandle');
  const sidebar = byId('metaSidebar');
  if (!layout || !handle || !sidebar) return;

  const minWidth = 220;
  const maxWidth = () => Math.min(520, Math.max(280, Math.floor(window.innerWidth * 0.5)));
  const applyWidth = (value) => {
    const next = Math.min(maxWidth(), Math.max(minWidth, Number(value) || 320));
    layout.style.setProperty('--meta-width', `${next}px`);
    localStorage.setItem('mu_meta_width', String(next));
  };

  const stored = Number(localStorage.getItem('mu_meta_width') || 0);
  if (stored) applyWidth(stored);

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  const onMove = (e) => {
    if (!dragging) return;
    applyWidth(startWidth - (e.clientX - startX));
  };
  const onUp = () => {
    dragging = false;
    document.body.style.userSelect = '';
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  };

  handle.addEventListener('mousedown', (e) => {
    if (layout.classList.contains('meta-hidden')) return;
    dragging = true;
    startX = e.clientX;
    startWidth = sidebar.getBoundingClientRect().width;
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  window.addEventListener('resize', () => {
    const current = Number(localStorage.getItem('mu_meta_width') || sidebar.getBoundingClientRect().width || 320);
    applyWidth(current);
  });
}


function setSurface(surface) {
  const app = byId('app');
  const layout = document.querySelector('.workspace-layout');
  if (!app) return;
  const next = ['operate', 'control', 'review'].includes(surface) ? surface : 'operate';
  state.uiSurface = next;
  app.setAttribute('data-surface', next);
  localStorage.setItem('mu_ui_surface', next);
  document.querySelectorAll('[data-surface-tab]').forEach((el) => {
    const active = el.getAttribute('data-surface-tab') === next;
    el.classList.toggle('active', active);
    el.setAttribute('aria-selected', active ? 'true' : 'false');
  });

  if (next === 'operate') {
    app.classList.add('sidebar-hidden');
    if (layout) layout.classList.add('meta-hidden');
  } else if (next === 'control') {
    app.classList.remove('sidebar-hidden');
    if (layout) layout.classList.add('meta-hidden');
  } else {
    app.classList.add('sidebar-hidden');
    if (layout) layout.classList.remove('meta-hidden');
  }

  closeAllSessionMenus();
}

document.querySelectorAll('[data-surface-tab]').forEach((el) => {
  el.addEventListener('click', () => setSurface(el.getAttribute('data-surface-tab') || 'operate'));
});

bindClick('refreshGitDiff', async () => {
  const repoSel = byId('gitRepo');
  if (repoSel && repoSel.value) state.gitCurrentRepo = repoSel.value;
  await refreshGitBranches();
});

bindClick('timelineFilterAll', () => { state.timelineFilter = 'all'; renderExecutionTimeline(); renderMetadataPanel(); });
bindClick('timelineFilterModel', () => { state.timelineFilter = 'model'; renderExecutionTimeline(); renderMetadataPanel(); });
bindClick('timelineFilterTool', () => { state.timelineFilter = 'tool'; renderExecutionTimeline(); renderMetadataPanel(); });
bindClick('timelineFilterStatus', () => { state.timelineFilter = 'status'; renderExecutionTimeline(); renderMetadataPanel(); });

bindClick('gitInlineMode', () => { state.gitDiffMode = 'inline'; renderGitDiffWorkbench(); });
bindClick('gitSideMode', () => { state.gitDiffMode = 'side'; renderGitDiffWorkbench(); });
bindClick('gitHunkAcceptAll', () => {
  const hunks = splitDiffHunks((parseGitDiffSections(state.gitDiff || '').unstaged || '') + '\n' + (parseGitDiffSections(state.gitDiff || '').staged || ''));
  state.gitHunkDecisions = {};
  hunks.forEach((_, idx) => { state.gitHunkDecisions[idx] = 'accept'; });
  renderGitDiffWorkbench();
});
bindClick('gitHunkRejectAll', () => {
  const hunks = splitDiffHunks((parseGitDiffSections(state.gitDiff || '').unstaged || '') + '\n' + (parseGitDiffSections(state.gitDiff || '').staged || ''));
  state.gitHunkDecisions = {};
  hunks.forEach((_, idx) => { state.gitHunkDecisions[idx] = 'reject'; });
  renderGitDiffWorkbench();
});
bindClick('gitHunkReset', () => { state.gitHunkDecisions = {}; renderGitDiffWorkbench(); });

bindChange('sessionQuickSwitch', () => renderSessions(state.sessions || [], state.activeSession || ''));
const sessionQuickSwitch = byId('sessionQuickSwitch');
if (sessionQuickSwitch) {
  sessionQuickSwitch.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const val = String(sessionQuickSwitch.value || '').trim().toLowerCase();
    const target = (state.sessions || []).find((n) => String(n).toLowerCase().includes(val));
    if (target) runWithAlert(() => sessionAction('switch', target));
  });
}

bindClick('browseWorkspace', () => {
  showModal('workspaceModal', true);
  return loadDirs(byId('workspace').value.trim());
});

bindChange('gitRepo', () => {
  state.gitCurrentRepo = byId('gitRepo').value || null;
  return refreshGitBranches();
});

bindClick('switchBranch', () => switchBranchFromUI());
bindClick('createBranch', () => createBranchFromUI());
bindClick('runCondenseNow', () => runCondenseFromModal());
bindClick('createSessionConfirm', () => createSessionFromModal());
bindClick('saveSessionOverrides', () => saveSessionOverridesFromModal());
bindClick('resetSessionOverrides', () => resetSessionOverridesFromModal());
bindChange('newSessionProvider', () => wireNewSessionModels(byId('newSessionProvider').value));
bindClick('loadDir', () => loadDirs(byId('dirPath').value.trim()));

bindClick('chooseDir', () => {
  if (!selectedDir) return;
  byId('workspace').value = selectedDir;
  showModal('workspaceModal', false);
  scheduleApplySettings();
  return refreshGitRepos();
});

bindClick('approveYes', () => sendApprovalDecision('approve'));
bindClick('approveNo', () => sendApprovalDecision('deny'));

bindClick('closePlanApprovalModal', () => resolvePlanApproval(false));
bindClick('planApprovalDeny', () => resolvePlanApproval(false));
bindClick('planApprovalApprove', () => resolvePlanApproval(true, byId('planApprovalContent').value));

const sessionMenuOverlay = byId('sessionMenuOverlay');
if (sessionMenuOverlay) {
  sessionMenuOverlay.querySelectorAll('[data-session-cmd]').forEach((el) => {
    el.addEventListener('click', (ev) => runWithAlert(async () => {
      ev.stopPropagation();
      const cmd = el.getAttribute('data-session-cmd');
      const name = openSessionMenuFor;
      closeAllSessionMenus();
      if (!name) return;
      if (cmd === 'refresh') await sessionAction('switch', name);
      if (cmd === 'clear') await sessionAction('clear', name);
      if (cmd === 'condense') { openCondenseModal(name); return; }
      if (cmd === 'overrides') { await openSessionOverridesModal(name); return; }
      if (cmd === 'delete') await sessionAction('delete', name);
    }));
  });
}

const promptInput = byId('prompt');
if (promptInput) {
  promptInput.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') runWithAlert(() => sendPrompt());
  });
}

document.addEventListener('keydown', (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  if (e.key.toLowerCase() === 'b') {
    e.preventDefault();
    byId('app').classList.toggle('sidebar-hidden');
    closeAllSessionMenus();
  }
  if (e.key === '\\') {
    e.preventDefault();
    byId('toggleMetaSidebar')?.click();
  }
  if (e.key === ',') {
    e.preventDefault();
    showModal('advancedModal', true);
  }
  if (e.key === '1') { e.preventDefault(); setSurface('operate'); }
  if (e.key === '2') { e.preventDefault(); setSurface('control'); }
  if (e.key === '3') { e.preventDefault(); setSurface('review'); }
});

document.addEventListener('click', (ev) => {
  const btn = ev.target && ev.target.closest ? ev.target.closest('[data-run-details]') : null;
  if (btn) {
    const id = btn.getAttribute('data-run-details') || '';
    openRunDetails(id, `Run ${id}`);
    return;
  }
  closeAllSessionMenus();
});
const Network = { api, apiForm, parseJsonResponse };
const Store = { state, updateBackgroundJobInState, selectedSessionName };
const Render = { renderGitControls, renderMessages, renderMetadataPanel, renderTraces, renderMetrics };
const Events = {
  wire() {
    // event handlers are declared in this initialization section.
  },
};

const metaToggleBtn = document.getElementById('toggleMetaSidebar');
if (metaToggleBtn) {
  metaToggleBtn.textContent = '☰';
  metaToggleBtn.title = 'Hide metadata panel';
  metaToggleBtn.setAttribute('aria-label', 'Hide metadata panel');
}
hydrateThemePreference();
initShellResize();
initMetaResize();
setSurface(localStorage.getItem('mu_ui_surface') || 'operate');
refreshState().catch((e) => alert(e.message));
if (runtimeTick) clearInterval(runtimeTick);
runtimeTick = setInterval(() => updateQueryRuntime(), 1000);
beginBackgroundPolling();

})();
