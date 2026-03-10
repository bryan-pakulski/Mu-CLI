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
refreshState().catch((e) => alert(e.message));
if (runtimeTick) clearInterval(runtimeTick);
runtimeTick = setInterval(() => updateQueryRuntime(), 1000);
beginBackgroundPolling();
