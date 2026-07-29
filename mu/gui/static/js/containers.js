(() => {
  const state = {
    containers: [], templates: [], defaults: null, shell: null,
    jobTimer: null, lastLogSeq: 0, mounts: [], editingName: null,
  };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const splitLines = value => String(value || '').split(/[\n,]/).map(v => v.trim()).filter(Boolean);
  const api = async (url, options = {}) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
    return data;
  };
  const show = id => { $(id).hidden = false; requestAnimationFrame(() => $(id).classList.add('is-open')); };
  const hide = id => { $(id).classList.remove('is-open'); setTimeout(() => { $(id).hidden = true; }, 150); };

  async function load() {
    const [listing, defaults] = await Promise.all([api('/api/containers'), api('/api/container-defaults')]);
    state.containers = listing.containers || [];
    state.templates = listing.templates || [];
    state.defaults = defaults;
    render();
  }

  function render() {
    $('container-empty').hidden = state.containers.length > 0;
    $('container-list').innerHTML = state.containers.map(ref => {
      const sessions = (ref.attached_sessions || []).map(name =>
        `<span class="session-chip">${esc(name)}<button data-action="detach" data-name="${esc(ref.name)}" data-session="${esc(name)}" title="Detach">×</button></span>`
      ).join('') || '—';
      const primary = ref.status === 'running' ? 'stop' : 'start';
      return `<article class="manager-card">
        <div class="manager-card-head"><div><span class="manager-card-kicker">environment</span><h3>${esc(ref.name)}</h3></div><span class="manager-status ${esc(ref.status)}">${esc(ref.status)}</span></div>
        <dl class="manager-meta"><dt>image</dt><dd>${esc(ref.image)}</dd><dt>template</dt><dd>${esc(ref.template_name || 'custom')}</dd><dt>sessions</dt><dd>${sessions}</dd><dt>network</dt><dd>${esc(ref.network_name || '—')}</dd></dl>
        <div class="manager-actions manager-actions-primary">
          <button class="primary" data-action="${primary}" data-name="${esc(ref.name)}">${primary}</button>
          <button data-action="shell" data-name="${esc(ref.name)}">shell</button>
          <button data-action="edit" data-name="${esc(ref.name)}">edit</button>
          <button data-action="clone" data-name="${esc(ref.name)}">clone</button>
        </div>
        <div class="manager-actions manager-actions-secondary">
          <button data-action="restart" data-name="${esc(ref.name)}">restart</button>
          <button data-action="attach" data-name="${esc(ref.name)}">attach session</button>
          <button data-action="snapshot" data-name="${esc(ref.name)}">create template</button>
          <button class="danger-text" data-action="remove" data-name="${esc(ref.name)}">remove</button>
        </div>
      </article>`;
    }).join('');

    $('template-empty').hidden = state.templates.length > 0;
    $('template-list').innerHTML = state.templates.map(item => `<article class="manager-card manager-template-card">
      <div class="manager-card-head"><div><span class="manager-card-kicker">template</span><h3>${esc(item.name)}</h3></div><span class="manager-status">snapshot</span></div>
      <p class="manager-template-description">${esc(item.description || 'Reusable container environment')}</p>
      <dl class="manager-meta"><dt>source</dt><dd>${esc(item.source_container)}</dd><dt>image</dt><dd>${esc(item.image)}</dd></dl>
      <div class="manager-actions"><button class="primary" data-template-action="use" data-name="${esc(item.name)}">create environment</button><button class="danger-text" data-template-action="remove" data-name="${esc(item.name)}">delete</button></div>
    </article>`).join('');

    $('environment-template').innerHTML = state.templates.map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');
    const option = $('environment-source').querySelector('option[value="template"]');
    if (option) option.disabled = state.templates.length === 0;
  }

  function resetForm() {
    state.editingName = null;
    state.mounts = [];
    $('environment-name').disabled = false;
    $('environment-name').value = '';
    $('environment-source').value = 'dockerfile';
    $('environment-template').value = '';
    $('environment-dockerfile').value = state.defaults?.dockerfile || '';
    $('environment-allow').value = (state.defaults?.egress_allow || []).join('\n');
    $('environment-deny').value = (state.defaults?.egress_deny || []).join('\n');
    $('environment-start').checked = true;
    $('environment-error').textContent = '';
    $('environment-kicker').textContent = 'New environment';
    $('environment-modal-title').textContent = 'Create environment';
    $('environment-modal-description').textContent = 'Standalone containers are not tied to a session.';
    $('submit-environment').textContent = 'create';
    syncSource(); renderMounts(); updateSummaries();
  }

  function applyConfiguration(config, {editing = false, clone = false} = {}) {
    resetForm();
    state.editingName = editing ? config.container_name || config.name : null;
    const sourceName = config.container_name || config.name || '';
    $('environment-name').value = clone ? `${sourceName.replace(/^mucli-/, '')}-copy` : sourceName;
    $('environment-name').disabled = editing;
    $('environment-source').value = config.template_name ? 'template' : 'dockerfile';
    $('environment-template').value = config.template_name || '';
    $('environment-dockerfile').value = config.dockerfile || state.defaults?.dockerfile || '';
    $('environment-allow').value = (config.egress_allow || state.defaults?.egress_allow || []).join('\n');
    $('environment-deny').value = (config.egress_deny || []).join('\n');
    state.mounts = (config.mounts || []).map(item => ({...item}));
    if (editing) {
      $('environment-kicker').textContent = 'Managed environment';
      $('environment-modal-title').textContent = `Edit ${sourceName}`;
      $('environment-modal-description').textContent = 'Saving recreates the worker while retaining named volumes and attached sessions.';
      $('submit-environment').textContent = 'save & recreate';
    } else if (clone) {
      $('environment-kicker').textContent = 'Clone environment';
      $('environment-modal-title').textContent = `Clone ${sourceName}`;
      $('environment-modal-description').textContent = 'Review the copied configuration and choose a new name.';
    }
    syncSource(); renderMounts(); updateSummaries(); show('create-modal');
  }

  function openCreate(templateName = '') {
    resetForm();
    if (templateName) {
      $('environment-source').value = 'template';
      $('environment-template').value = templateName;
      syncSource();
    }
    show('create-modal');
  }

  async function openEdit(name, clone = false) {
    const config = await api(`/api/containers/${encodeURIComponent(name)}/configuration`);
    applyConfiguration(config, {editing: !clone, clone});
  }

  function syncSource() {
    const useTemplate = $('environment-source').value === 'template';
    $('template-select-row').hidden = !useTemplate;
    $('dockerfile-row').hidden = useTemplate;
    $('edit-network').hidden = useTemplate;
  }

  function renderMounts() {
    const root = $('environment-mount-list');
    if (!state.mounts.length) {
      root.innerHTML = '<div class="manager-mount-empty">No additional folders attached.</div>';
      return;
    }
    root.innerHTML = state.mounts.map((mount, index) => `<div class="manager-mount-row" data-mount-index="${index}">
      <div><strong title="${esc(mount.host_path)}">${esc(mount.host_path)}</strong><small>host folder</small></div>
      <span>→</span>
      <input data-mount-target="${index}" value="${esc(mount.container_path)}" aria-label="Container path">
      <select data-mount-mode="${index}"><option value="rw" ${mount.mode !== 'ro' ? 'selected' : ''}>rw</option><option value="ro" ${mount.mode === 'ro' ? 'selected' : ''}>ro</option></select>
      <button data-remove-mount="${index}" class="icon-btn">×</button>
    </div>`).join('');
  }

  function addMountFromBrowser() {
    const browser = window.Alpine && Alpine.store('fileBrowser');
    if (!browser) return window.alert('Folder browser is not ready.');
    browser.show('folder', path => {
      if (state.mounts.some(item => item.host_path === path)) return;
      const base = String(path).split('/').filter(Boolean).pop() || 'project';
      state.mounts.push({host_path: path, container_path: `/workspace/${base}`, mode: 'rw'});
      renderMounts();
    });
  }

  function updateSummaries() {
    $('dockerfile-summary').textContent = `${$('environment-dockerfile').value.split('\n').filter(Boolean).length} lines · editable worker image`;
    $('network-summary').textContent = `${splitLines($('environment-allow').value).length} allowed · ${splitLines($('environment-deny').value).length} blocked`;
  }

  function payload() {
    const useTemplate = $('environment-source').value === 'template';
    return {
      name: $('environment-name').value.trim(),
      template_name: useTemplate ? $('environment-template').value : null,
      dockerfile: useTemplate ? null : $('environment-dockerfile').value,
      mounts: state.mounts,
      egress_allow: useTemplate ? null : splitLines($('environment-allow').value),
      egress_deny: useTemplate ? null : splitLines($('environment-deny').value),
      start: $('environment-start').checked,
    };
  }

  async function submitEnvironment() {
    $('environment-error').textContent = '';
    try {
      const body = payload();
      if (!body.name) throw new Error('Container name is required.');
      const editing = Boolean(state.editingName);
      const result = await api(editing ? `/api/containers/${encodeURIComponent(state.editingName)}` : '/api/containers', {
        method: editing ? 'PUT' : 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body),
      });
      hide('create-modal');
      beginJob(result.job_id, body.name, editing ? 'Updating' : 'Creating');
    } catch (error) {
      $('environment-error').textContent = String(error.message || error);
    }
  }

  function beginJob(jobId, name, verb = 'Creating') {
    state.lastLogSeq = 0;
    $('progress-title').textContent = `${verb} ${name}`;
    $('progress-message').textContent = 'Queued…'; $('progress-stage').textContent = 'queued'; $('progress-log').textContent = ''; $('close-progress').hidden = true;
    show('progress-modal'); clearInterval(state.jobTimer);
    const poll = async () => {
      try {
        const job = await api(`/api/containers/jobs/${encodeURIComponent(jobId)}?after=${state.lastLogSeq}`);
        $('progress-message').textContent = job.message || '';
        $('progress-stage').textContent = String(job.stage || '').replaceAll('_', ' ');
        for (const entry of job.logs || []) { state.lastLogSeq = Math.max(state.lastLogSeq, Number(entry.seq || 0)); $('progress-log').textContent += `[${entry.stream}] ${entry.text}\n`; }
        $('progress-log').scrollTop = $('progress-log').scrollHeight;
        if (job.state === 'ready' || job.state === 'error') {
          clearInterval(state.jobTimer); state.jobTimer = null; $('close-progress').hidden = false;
          if (job.state === 'error') { $('progress-output').open = true; $('progress-message').textContent = job.detail || job.message; }
          else await load();
        }
      } catch (error) { $('progress-message').textContent = String(error.message || error); }
    };
    poll(); state.jobTimer = setInterval(poll, 700);
  }

  async function action(name, actionName, body = null) {
    const options = {method:'POST'};
    if (body) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    await api(`/api/containers/${encodeURIComponent(name)}/actions/${encodeURIComponent(actionName)}`, options); await load();
  }

  function openShell(name) {
    if (state.shell) state.shell.close(); $('shell-title').textContent = `Shell · ${name}`; $('shell-output').textContent = ''; show('shell-modal');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/api/containers/${encodeURIComponent(name)}/shell`); state.shell = socket;
    socket.onmessage = event => { $('shell-output').textContent += String(event.data).replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g, ''); $('shell-output').scrollTop = $('shell-output').scrollHeight; };
    socket.onclose = () => { $('shell-output').textContent += '\n[shell disconnected]\n'; state.shell = null; };
    socket.onerror = () => { $('shell-output').textContent += '\n[shell connection failed]\n'; };
    setTimeout(() => $('shell-command').focus(), 50);
  }

  function snapshot(name) { $('snapshot-container').value = name; $('snapshot-name').value = name.replace(/^mucli-/, ''); $('snapshot-description').value = ''; $('snapshot-error').textContent = ''; show('snapshot-modal'); }

  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-close]'); if (close) { hide(close.dataset.close); updateSummaries(); return; }
    const removeMount = event.target.closest('[data-remove-mount]'); if (removeMount) { state.mounts.splice(Number(removeMount.dataset.removeMount), 1); renderMounts(); return; }
    const button = event.target.closest('[data-action]');
    if (button) {
      const {action: actionName, name, session} = button.dataset;
      try {
        if (actionName === 'shell') return openShell(name);
        if (actionName === 'edit') return await openEdit(name, false);
        if (actionName === 'clone') return await openEdit(name, true);
        if (actionName === 'snapshot') return snapshot(name);
        if (actionName === 'attach') { const sessionName = window.prompt('Session name to attach'); if (sessionName) await action(name, 'attach', {session_name: sessionName}); return; }
        if (actionName === 'detach') { await action(name, 'detach', {session_name: session}); return; }
        if (actionName === 'remove') { if (!window.confirm(`Remove ${name} and its persistent volumes?`)) return; await api(`/api/containers/${encodeURIComponent(name)}?force=true`, {method:'DELETE'}); await load(); return; }
        await action(name, actionName);
      } catch (error) { window.alert(String(error.message || error)); }
      return;
    }
    const templateButton = event.target.closest('[data-template-action]');
    if (templateButton) {
      const name = templateButton.dataset.name;
      if (templateButton.dataset.templateAction === 'use') return openCreate(name);
      if (window.confirm(`Delete template ${name}?`)) { await api(`/api/container-templates/${encodeURIComponent(name)}`, {method:'DELETE'}); await load(); }
    }
  });

  $('container-manager').addEventListener('input', event => {
    const target = event.target;
    if (target.matches('[data-mount-target]')) state.mounts[Number(target.dataset.mountTarget)].container_path = target.value;
  });
  $('container-manager').addEventListener('change', event => {
    const target = event.target;
    if (target.matches('[data-mount-mode]')) state.mounts[Number(target.dataset.mountMode)].mode = target.value;
  });
  $('create-environment-button').addEventListener('click', () => openCreate());
  $('refresh-containers').addEventListener('click', load);
  $('environment-source').addEventListener('change', syncSource);
  $('add-environment-mount').addEventListener('click', addMountFromBrowser);
  $('edit-dockerfile').addEventListener('click', () => { $('editor-title').textContent='Dockerfile'; $('dockerfile-editor-panel').hidden=false; $('network-editor-panel').hidden=true; show('editor-modal'); });
  $('edit-network').addEventListener('click', () => { $('editor-title').textContent='Network policy'; $('dockerfile-editor-panel').hidden=true; $('network-editor-panel').hidden=false; show('editor-modal'); });
  $('submit-environment').addEventListener('click', submitEnvironment);
  $('close-progress').addEventListener('click', () => hide('progress-modal'));
  $('submit-snapshot').addEventListener('click', async () => {
    $('snapshot-error').textContent = '';
    try { await api(`/api/containers/${encodeURIComponent($('snapshot-container').value)}/snapshot`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template_name:$('snapshot-name').value.trim(),description:$('snapshot-description').value.trim()})}); hide('snapshot-modal'); await load(); }
    catch (error) { $('snapshot-error').textContent = String(error.message || error); }
  });
  $('shell-form').addEventListener('submit', event => { event.preventDefault(); const command=$('shell-command').value; if(state.shell&&state.shell.readyState===WebSocket.OPEN)state.shell.send(command+'\n'); $('shell-command').value=''; });
  $('close-shell').addEventListener('click', () => { if(state.shell)state.shell.close(); hide('shell-modal'); });
  $('environment-dockerfile').addEventListener('input', updateSummaries);
  $('environment-allow').addEventListener('input', updateSummaries);
  $('environment-deny').addEventListener('input', updateSummaries);

  load().catch(error => { $('container-empty').textContent = String(error.message || error); });
})();
