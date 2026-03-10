// --- render functions -------------------------------------------------------

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

function renderGitDiffWorkbench() {
  const sections = parseGitDiffSections(state.gitDiff || '');
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
  const active = activeSearchingJob();
  const jobEvents = Array.isArray(active && active.events) ? active.events : [];
  const traceEvents = (state.traces || []).slice(-20);
  const merged = traceEvents.concat(jobEvents).slice(-32);
  if (!merged.length) {
    host.innerHTML = '<div class="state-empty">No active timeline yet. Run a task to see model/tool steps in real time.</div>';
    return;
  }
  host.innerHTML = merged.map((line, idx) => {
    const cls = classifyBackgroundEvent(line) || (String(line).startsWith('model:') ? 'model' : 'status');
    return `<div class="timeline-item ${cls}"><span class="timeline-step">${idx + 1}</span><span class="timeline-line">${escapeHtml(String(line))}</span></div>`;
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
  else status.textContent = `Repo: ${state.gitCurrentRepo || '-'} · Current branch: ${state.gitCurrentBranch || '-'}`;

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

let _lastBackgroundActivityKey = null;
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
    body.innerHTML = recentEvents.map((line) => {
      const cls = classifyBackgroundEvent(line);
      const safe = escapeHtml(String(line));
      return `<div class="bg-live-line ${cls}">${safe}</div>`;
    }).join('');
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
      await pollApproval();
      if (latest.usage) updateUsagePanel(latest.usage);
      const reportEl = document.getElementById('report');
      if (reportEl) {
        if (latest.last_step) reportEl.textContent = `background ${latest.status}: ${latest.last_step}`;
        else reportEl.textContent = `background ${latest.status}: ${latest.iterations || 0} iteration(s)`;
      }
      if (latest.session === state.activeSession) { renderBackgroundActivity(latest); renderMetadataPanel(); }
    } catch (_) {
      // ignored to avoid breaking UI polling loop
    }
  }, 900);
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

function _drawLineChart(canvas, seriesMap) {
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const keys = Object.keys(seriesMap || {});
  if (!keys.length) {
    ctx.fillStyle = '#6b7280';
    ctx.font = '12px sans-serif';
    ctx.fillText('No data yet', 12, 20);
    return;
  }

  const colors = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#a855f7'];
  const pad = 28;
  ctx.strokeStyle = '#9ca3af';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, h - pad);
  ctx.lineTo(w - 8, h - pad);
  ctx.moveTo(pad, h - pad);
  ctx.lineTo(pad, 8);
  ctx.stroke();
  ctx.fillStyle = '#6b7280';
  ctx.font = '11px sans-serif';
  ctx.fillText('Turns (X)', w - 66, h - 10);
  ctx.save();
  ctx.translate(10, 44);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('Usage (Y)', 0, 0);
  ctx.restore();
  let maxLen = 0;
  let maxY = 0;
  keys.forEach((k) => {
    const arr = seriesMap[k] || [];
    maxLen = Math.max(maxLen, arr.length);
    arr.forEach((v) => { maxY = Math.max(maxY, Number(v || 0)); });
  });
  maxLen = Math.max(2, maxLen);
  maxY = Math.max(1, maxY);

  const plotW = w - pad * 2;
  const plotH = h - pad * 2;

  ctx.strokeStyle = '#9ca3af';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, h - pad);
  ctx.lineTo(w - pad, h - pad);
  ctx.stroke();

  keys.forEach((key, idx) => {
    const arr = seriesMap[key] || [];
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = 2;
    ctx.beginPath();
    arr.forEach((v, i) => {
      const x = pad + (i / (maxLen - 1)) * plotW;
      const y = h - pad - (Number(v || 0) / maxY) * plotH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  // legend
  keys.slice(0, 4).forEach((key, idx) => {
    ctx.fillStyle = colors[idx % colors.length];
    ctx.fillRect(pad + idx * 120, 8, 10, 10);
    ctx.fillStyle = '#6b7280';
    ctx.font = '11px sans-serif';
    ctx.fillText(key.slice(0, 18), pad + idx * 120 + 14, 17);
  });
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

  const byModelTokens = {};
  const byModelCost = {};
  const breakdown = {};

  for (const turn of turns) {
    const provider = String(turn.provider || 'unknown');
    const model = String(turn.model || 'unknown');
    const key = `${provider}:${model}`;

    if (!byModelTokens[key]) byModelTokens[key] = [];
    if (!byModelCost[key]) byModelCost[key] = [];

    const prevTok = byModelTokens[key].length ? byModelTokens[key][byModelTokens[key].length - 1] : 0;
    const prevCost = byModelCost[key].length ? byModelCost[key][byModelCost[key].length - 1] : 0;
    byModelTokens[key].push(prevTok + Number(turn.total_tokens || 0));
    byModelCost[key].push(prevCost + Number(turn.estimated_cost_usd || 0));

    const row = breakdown[key] || { provider, model, turns: 0, tokens: 0, cost: 0 };
    row.turns += 1;
    row.tokens += Number(turn.total_tokens || 0);
    row.cost += Number(turn.estimated_cost_usd || 0);
    breakdown[key] = row;
  }

  _drawLineChart(document.getElementById('metricsTokenChart'), byModelTokens);
  _drawLineChart(document.getElementById('metricsCostChart'), byModelCost);

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
  return `${day}/${month}/${year} ${hours}:${minutes}`;
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

function buildCitationPanel(content, reasonsByUrl={}) {
  const links = extractCitationLinks(content);
  if (!links.length) return '';
  const items = links.map((url, idx) => `
    <div id="citation-${idx + 1}">[${idx + 1}] <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>${reasonsByUrl[url] ? `<div class="small-muted">why chosen: ${escapeHtml(reasonsByUrl[url])}</div>` : ''}</div>
  `).join('');
  return `<details class="citation-panel"><summary>ℹ Citations & URLs (${links.length})</summary><div class="citation-list">${items}</div></details>`;
}

function formatAssistantContentWithCitations(content, links) {
  let html = formatMessageContent(content);
  html = html.replace(/\[(\d+)\]/g, (full, numText) => {
    const idx = Number(numText);
    if (!Number.isInteger(idx) || idx < 1 || idx > links.length) return full;
    return `<a href="#citation-${idx}" title="Jump to citation [${idx}]">[${idx}]</a>`;
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
    items.push({ kind, role, text: text.length > 900 ? `${text.slice(0, 897)}...` : text });
  }
  return items;
}

function _metaRow(kind, label, value) {
  const row = document.createElement('div');
  row.className = `meta-line meta-item ${kind}`;

  const tag = document.createElement('span');
  tag.className = 'meta-tag';
  tag.textContent = label;
  row.appendChild(tag);

  const body = document.createElement('div');
  body.className = 'meta-content';

  const raw = String(value || '').trim();
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
    row.appendChild(body);
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
  row.appendChild(body);
  if (window.hljs) window.hljs.highlightElement(code);
  return row;
}

function renderMetadataPanel() {
  const host = document.getElementById('metaFeed');
  host.innerHTML = '';
  const messageTimes = inferMessageTimestamps(state.messages, state.sessionTurns);
  let cards = 0;

  const ws = state.workspaceIndexStats || {};
  if (Object.keys(ws).length) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = '<div class="meta-head"><span>Workspace indexing</span><span>stats</span></div>';
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const [key, value] of Object.entries(ws)) {
      lines.appendChild(_metaRow('workspace', 'Workspace stat', `${key}: ${value}`));
    }
    card.appendChild(lines);
    host.appendChild(card);
    cards += 1;
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

    for (const item of meta.toolRequests) lines.appendChild(_metaRow('tool-call', 'Tool call', item));
    for (const item of meta.toolResults) lines.appendChild(_metaRow('tool-result', 'Tool result', item));
    for (const item of meta.citationItems) lines.appendChild(_metaRow('citation', 'Citation', item));
    for (const item of meta.researchSteps) lines.appendChild(_metaRow('research', 'Research step', item));

    card.appendChild(lines);
    host.appendChild(card);
    cards += 1;
  }

  const liveTraceItems = (state.traces || []).slice(-20).filter((line) => {
    return line.startsWith('tool-request:') || line.startsWith('tool-run:') || line.startsWith('model:') || line.startsWith('status:');
  });
  if (liveTraceItems.length) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = `<div class="meta-head"><span>Live execution stream</span><span>${liveTraceItems.length} events</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const line of liveTraceItems.slice().reverse()) {
      let kind = 'automation';
      let label = 'Stream event';
      if (line.startsWith('tool-request:')) { kind = 'tool-call'; label = 'Tool call'; }
      else if (line.startsWith('tool-run:')) { kind = 'tool-result'; label = 'Tool result'; }
      else if (line.startsWith('status:')) { kind = 'research'; label = 'Status'; }
      else if (line.startsWith('model:')) { kind = 'automation'; label = 'Model'; }
      lines.appendChild(_metaRow(kind, label, line));
    }
    card.appendChild(lines);
    host.appendChild(card);
    cards += 1;
  }

  const automation = collectAutomationMetadata(state.messages);
  if (automation.length) {
    const card = document.createElement('div');
    card.className = 'meta-card';
    card.innerHTML = `<div class="meta-head"><span>Agent/Plan automation</span><span>${automation.length} items</span></div>`;
    const lines = document.createElement('div');
    lines.className = 'meta-lines';
    for (const item of automation.slice(-20).reverse()) {
      lines.appendChild(_metaRow('automation', `${item.kind} · ${item.role}`, item.text));
    }
    card.appendChild(lines);
    host.appendChild(card);
    cards += 1;
  }

  if (!cards) host.innerHTML = '<div class="meta-empty">No tool/research metadata yet.</div>';
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

    if (m.role === 'assistant' && !String(m.content || '').trim()) return;
    if (m.role === 'tool_result' || m.role === 'tool_call') return;

    const row = document.createElement('div');
    row.className = `msg role-${roleClass(m.role)}`;
    row.innerHTML = `<div class="role">${m.role}</div>`;

    if (m.role === 'user' || m.role === 'assistant') {
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      const tag = m.role === 'user' ? 'You' : 'AI';
      const stamp = formatTimestamp(messageTimes.get(idx));
      meta.innerHTML = `<span class="msg-tag">${tag}</span><span class="msg-time">${escapeHtml(stamp || '—')}</span>`;
      row.appendChild(meta);
    }

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    const citationLinks = m.role === 'assistant' ? extractCitationLinks(m.content) : [];
    if (m.role === 'tool_result' && m.metadata && m.metadata.kind === 'session_condensed_summary') {
      bubble.innerHTML = `<details><summary>Condensed summary (${escapeHtml(m.metadata.summary_id || '')})</summary><div class="small-muted mt-1">${formatMessageContent(m.content)}</div></details>`;
    } else if (m.role === 'assistant' && m.metadata && m.metadata.typing) bubble.innerHTML = '<p class="typing-dots"><span></span><span></span><span></span></p>';
    else bubble.innerHTML = m.role === 'assistant'
      ? formatAssistantContentWithCitations(m.content, citationLinks)
      : formatMessageContent(m.content);
    row.appendChild(bubble);
    box.appendChild(row);
    anchor = row;

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
  if (!state.traces.length) {
    el.textContent = 'debug traces will appear here';
    return;
  }
  el.innerHTML = state.traces.map((line) => {
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
  state.pinnedSessions = prefs.pinned.filter((n) => list.includes(n));
  state.recentSessions = prefs.recent.filter((n) => list.includes(n));
  const filter = String((document.getElementById('sessionQuickSwitch') || {}).value || '').trim().toLowerCase();
  const ordered = state.pinnedSessions.concat(list.filter((n) => !state.pinnedSessions.includes(n)));

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
    const isPinned = state.pinnedSessions.includes(name);
    btn.innerHTML = `<span class="session-label"><span class="session-dot ${dotClass}" title="${escapeHtml(tip)}"></span><span class="session-name">${escapeHtml(name)}</span></span><span class="ui-badge ${status === 'running' ? 'warn' : (status === 'done' ? 'success' : '')}">${status || 'idle'}</span>`;
    btn.setAttribute('data-session-name', name);
    btn.addEventListener('click', () => {
      markSessionRecent(name);
      completedSeenSessions.add(name);
      sessionAction('switch', name).catch((e) => alert(e.message));
    });

    const pinBtn = document.createElement('button');
    pinBtn.type = 'button';
    pinBtn.className = 'session-menu-btn';
    pinBtn.title = isPinned ? 'Unpin session' : 'Pin session';
    pinBtn.textContent = isPinned ? '★' : '☆';
    pinBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      toggleSessionPin(name);
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
    row.appendChild(pinBtn);
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
  const enabled = [];
  document.querySelectorAll('[data-skill-enabled]').forEach((el) => {
    if (el.checked) enabled.push(el.getAttribute('data-skill-enabled'));
  });
  return enabled;
}

function renderSkillSettings() {
  const host = document.getElementById('skillToggleList');
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
}

async function openSkillView(name) {
  if (!name) return;
  document.getElementById('skillViewTitle').textContent = `Skill: ${name}`;
  document.getElementById('skillViewBody').textContent = 'Loading…';
  showModal('skillViewModal', true);
  const payload = await api(`/api/skills/${encodeURIComponent(name)}`);
  document.getElementById('skillViewBody').innerHTML = formatMessageContent(payload.content || '');
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
    approval_mode: document.getElementById('approval').value,
    workspace: document.getElementById('workspace').value || null,
    debug: document.getElementById('debug').checked,
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
  const anyModalOpen = ['advancedModal', 'metricsModal', 'workspaceModal', 'approvalModal', 'newSessionModal', 'planApprovalModal', 'condenseModal', 'gitModal', 'skillViewModal', 'runDetailsModal']
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
  document.getElementById('debug').checked = !!s.debug;
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
  const activeJob = (state.backgroundJobs || []).find((j) => j.session === s.session && ['running', 'awaiting_plan_approval'].includes(j.status));
  if (activeJob && activeJob.usage) updateUsagePanel(activeJob.usage);
  else updateUsagePanel(state.sessionUsage);
  renderSessions(state.sessions, state.activeSession);
  renderUploads();
  renderToolSettings();
  renderSkillSettings();
  renderGitControls();
  renderMessages();
  renderMetadataPanel();
  renderTraces();
  updateChatBusyState();
  renderMetrics();
  renderExecutionTimeline();

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
