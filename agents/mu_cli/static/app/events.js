// --- event wiring -----------------------------------------------------------
for (const id of ['provider', 'model', 'openaiApiKey', 'googleApiKey', 'approval', 'workspace', 'debug', 'agentic', 'researchMode', 'condenseEnabled', 'condenseWindow', 'maxRuntime', 'darkMode']) {
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
bindClick('uploadFiles', () => uploadContextFiles());
bindClick('clearUploads', () => clearUploadedStore());

bindClick('toggleSidebar', () => byId('app').classList.toggle('sidebar-hidden'));
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
]) {
  bindClick(buttonId, () => showModal(modalId, false));
}

bindClick('toggleMetaSidebar', () => {
  const layout = document.querySelector('.workspace-layout');
  if (!layout) return;
  layout.classList.toggle('meta-hidden');
  const btn = byId('toggleMetaSidebar');
  const hidden = layout.classList.contains('meta-hidden');
  btn.textContent = hidden ? '>' : '<';
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

function setSurface(surface) {
  const app = byId('app');
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
  if (next === 'control') app.classList.remove('sidebar-hidden');
  if (next === 'review') {
    const layout = document.querySelector('.workspace-layout');
    if (layout) layout.classList.remove('meta-hidden');
  }
}

document.querySelectorAll('[data-surface-tab]').forEach((el) => {
  el.addEventListener('click', () => setSurface(el.getAttribute('data-surface-tab') || 'operate'));
});

bindClick('refreshGitDiff', async () => {
  await refreshGitDiff();
  renderGitControls();
});

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
  }
  if (e.key === '\\') {
    e.preventDefault();
    byId('toggleMetaSidebar')?.click();
  }
  if (e.key === ',') {
    e.preventDefault();
    showModal('advancedModal', true);
  }
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
  metaToggleBtn.textContent = '<';
  metaToggleBtn.title = 'Hide metadata panel';
  metaToggleBtn.setAttribute('aria-label', 'Hide metadata panel');
}
hydrateThemePreference();
initShellResize();
setSurface(localStorage.getItem('mu_ui_surface') || 'operate');
refreshState().catch((e) => alert(e.message));
if (runtimeTick) clearInterval(runtimeTick);
runtimeTick = setInterval(() => updateQueryRuntime(), 1000);
beginBackgroundPolling();
