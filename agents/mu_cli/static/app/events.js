// --- event wiring -----------------------------------------------------------
for (const id of ['provider', 'model', 'openaiApiKey', 'googleApiKey', 'ollamaContextWindow', 'approval', 'workspace', 'debug', 'agentic', 'researchMode', 'condenseEnabled', 'condenseWindow', 'maxRuntime', 'darkMode']) {
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
