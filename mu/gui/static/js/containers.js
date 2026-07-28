(() => {
  const state = {
    containers: [], templates: [], defaults: null, shell: null,
    jobTimer: null, lastLogSeq: 0,
  };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const lines = value => String(value || "").split(/[\n,]/).map(v => v.trim()).filter(Boolean);
  const api = async (url, options = {}) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
    return data;
  };
  const show = id => { $(id).hidden = false; };
  const hide = id => { $(id).hidden = true; };

  async function load() {
    const [listing, defaults] = await Promise.all([
      api('/api/containers'), api('/api/container-defaults'),
    ]);
    state.containers = listing.containers || [];
    state.templates = listing.templates || [];
    state.defaults = defaults;
    render();
    fillDefaults();
  }

  function render() {
    const containerList = $('container-list');
    $('container-empty').hidden = state.containers.length > 0;
    containerList.innerHTML = state.containers.map(ref => {
      const sessions = (ref.attached_sessions || []).map(name =>
        `<span class="session-chip">${esc(name)}<button data-action="detach" data-name="${esc(ref.name)}" data-session="${esc(name)}" title="Detach">×</button></span>`
      ).join('') || '—';
      return `<article class="manager-card">
        <div class="manager-card-head"><h3>${esc(ref.name)}</h3><span class="manager-status ${esc(ref.status)}">${esc(ref.status)}</span></div>
        <dl class="manager-meta"><dt>image</dt><dd>${esc(ref.image)}</dd><dt>template</dt><dd>${esc(ref.template_name || '—')}</dd><dt>sessions</dt><dd>${sessions}</dd><dt>network</dt><dd>${esc(ref.network_name || '—')}</dd></dl>
        <div class="manager-actions">
          <button data-action="start" data-name="${esc(ref.name)}">start</button>
          <button data-action="stop" data-name="${esc(ref.name)}">stop</button>
          <button data-action="restart" data-name="${esc(ref.name)}">restart</button>
          <button data-action="shell" data-name="${esc(ref.name)}">shell</button>
          <button data-action="attach" data-name="${esc(ref.name)}">attach session</button>
          <button data-action="snapshot" data-name="${esc(ref.name)}">snapshot</button>
          <button data-action="remove" data-name="${esc(ref.name)}">remove</button>
        </div>
      </article>`;
    }).join('');

    const templateList = $('template-list');
    $('template-empty').hidden = state.templates.length > 0;
    templateList.innerHTML = state.templates.map(item => `<article class="manager-card">
      <div class="manager-card-head"><h3>${esc(item.name)}</h3><span class="manager-status">template</span></div>
      <dl class="manager-meta"><dt>source</dt><dd>${esc(item.source_container)}</dd><dt>image</dt><dd>${esc(item.image)}</dd><dt>description</dt><dd>${esc(item.description || '—')}</dd></dl>
      <div class="manager-actions"><button data-template-action="use" data-name="${esc(item.name)}">use template</button><button data-template-action="remove" data-name="${esc(item.name)}">delete</button></div>
    </article>`).join('');

    $('environment-template').innerHTML = state.templates.map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');
    const templateOption = $('environment-source').querySelector('option[value="template"]');
    if (templateOption) templateOption.disabled = state.templates.length === 0;
    if (state.templates.length === 0 && $('environment-source').value === 'template') {
      $('environment-source').value = 'dockerfile';
      syncSource();
    }
  }

  function fillDefaults() {
    if (!state.defaults) return;
    if (!$('environment-dockerfile').value) $('environment-dockerfile').value = state.defaults.dockerfile || '';
    if (!$('environment-allow').value) $('environment-allow').value = (state.defaults.egress_allow || []).join('\n');
    updateSummaries();
  }

  function updateSummaries() {
    $('dockerfile-summary').textContent = `${$('environment-dockerfile').value.split('\n').length} lines · editable worker image`;
    $('network-summary').textContent = `${lines($('environment-allow').value).length} allowed · ${lines($('environment-deny').value).length} blocked`;
  }

  function openCreate(templateName = '') {
    $('environment-error').textContent = '';
    $('environment-name').value = '';
    $('environment-source').value = templateName ? 'template' : 'dockerfile';
    $('environment-template').value = templateName;
    syncSource();
    show('create-modal');
  }

  function syncSource() {
    const useTemplate = $('environment-source').value === 'template';
    $('template-select-row').hidden = !useTemplate;
    $('dockerfile-row').hidden = useTemplate;
  }

  function parseMounts() {
    return String($('environment-mounts').value || '').split('\n').map(v => v.trim()).filter(Boolean).map(line => {
      const parts = line.split(':');
      const mode = ['ro', 'rw'].includes(parts.at(-1)) ? parts.pop() : 'rw';
      const host_path = parts.shift();
      const container_path = parts.join(':');
      if (!host_path || !container_path.startsWith('/')) throw new Error(`Invalid mount: ${line}`);
      return {host_path, container_path, mode};
    });
  }

  async function createEnvironment() {
    $('environment-error').textContent = '';
    try {
      const useTemplate = $('environment-source').value === 'template';
      const payload = {
        name: $('environment-name').value.trim(),
        template_name: useTemplate ? $('environment-template').value : null,
        dockerfile: useTemplate ? null : $('environment-dockerfile').value,
        mounts: parseMounts(),
        egress_allow: useTemplate ? null : lines($('environment-allow').value),
        egress_deny: useTemplate ? null : lines($('environment-deny').value),
        start: $('environment-start').checked,
      };
      if (!payload.name) throw new Error('Container name is required.');
      const result = await api('/api/containers', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      hide('create-modal');
      beginJob(result.job_id, payload.name);
    } catch (error) {
      $('environment-error').textContent = String(error.message || error);
    }
  }

  function beginJob(jobId, name) {
    state.lastLogSeq = 0;
    $('progress-title').textContent = `Creating ${name}`;
    $('progress-message').textContent = 'Queued…';
    $('progress-stage').textContent = 'queued';
    $('progress-log').textContent = '';
    $('close-progress').hidden = true;
    show('progress-modal');
    clearInterval(state.jobTimer);
    const poll = async () => {
      try {
        const job = await api(`/api/containers/jobs/${encodeURIComponent(jobId)}?after=${state.lastLogSeq}`);
        $('progress-message').textContent = job.message || '';
        $('progress-stage').textContent = String(job.stage || '').replaceAll('_', ' ');
        for (const entry of job.logs || []) {
          state.lastLogSeq = Math.max(state.lastLogSeq, Number(entry.seq || 0));
          $('progress-log').textContent += `[${entry.stream}] ${entry.text}\n`;
        }
        $('progress-log').scrollTop = $('progress-log').scrollHeight;
        if (job.state === 'ready' || job.state === 'error') {
          clearInterval(state.jobTimer);
          state.jobTimer = null;
          $('close-progress').hidden = false;
          if (job.state === 'error') {
            $('progress-output').open = true;
            $('progress-message').textContent = job.detail || job.message;
          } else {
            await load();
          }
        }
      } catch (error) {
        $('progress-message').textContent = String(error.message || error);
      }
    };
    poll();
    state.jobTimer = setInterval(poll, 700);
  }

  async function action(name, actionName, payload = null) {
    const options = {method:'POST'};
    if (payload) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(payload); }
    await api(`/api/containers/${encodeURIComponent(name)}/actions/${encodeURIComponent(actionName)}`, options);
    await load();
  }

  function openShell(name) {
    if (state.shell) state.shell.close();
    $('shell-title').textContent = `Shell · ${name}`;
    $('shell-output').textContent = '';
    show('shell-modal');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/api/containers/${encodeURIComponent(name)}/shell`);
    state.shell = socket;
    socket.onmessage = event => {
      const clean = String(event.data).replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g, '');
      $('shell-output').textContent += clean;
      $('shell-output').scrollTop = $('shell-output').scrollHeight;
    };
    socket.onclose = () => { $('shell-output').textContent += '\n[shell disconnected]\n'; state.shell = null; };
    socket.onerror = () => { $('shell-output').textContent += '\n[shell connection failed]\n'; };
    setTimeout(() => $('shell-command').focus(), 50);
  }

  async function snapshot(name) {
    $('snapshot-container').value = name;
    $('snapshot-name').value = name.replace(/^mucli-/, '');
    $('snapshot-description').value = '';
    $('snapshot-error').textContent = '';
    show('snapshot-modal');
  }

  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-close]');
    if (close) { hide(close.dataset.close); updateSummaries(); return; }
    const button = event.target.closest('[data-action]');
    if (button) {
      const {action: actionName, name, session} = button.dataset;
      try {
        if (actionName === 'shell') return openShell(name);
        if (actionName === 'snapshot') return snapshot(name);
        if (actionName === 'attach') {
          const sessionName = window.prompt('Session name to attach');
          if (sessionName) await action(name, 'attach', {session_name: sessionName});
          return;
        }
        if (actionName === 'detach') {
          await action(name, 'detach', {session_name: session});
          return;
        }
        if (actionName === 'remove') {
          if (!window.confirm(`Remove ${name} and its persistent volumes?`)) return;
          await api(`/api/containers/${encodeURIComponent(name)}?force=true`, {method:'DELETE'});
          await load();
          return;
        }
        await action(name, actionName);
      } catch (error) { window.alert(String(error.message || error)); }
      return;
    }
    const templateButton = event.target.closest('[data-template-action]');
    if (templateButton) {
      const name = templateButton.dataset.name;
      if (templateButton.dataset.templateAction === 'use') return openCreate(name);
      if (window.confirm(`Delete template ${name}?`)) {
        await api(`/api/container-templates/${encodeURIComponent(name)}`, {method:'DELETE'});
        await load();
      }
    }
  });

  $('create-environment-button').addEventListener('click', () => openCreate());
  $('refresh-containers').addEventListener('click', load);
  $('environment-source').addEventListener('change', syncSource);
  $('edit-dockerfile').addEventListener('click', () => { $('editor-title').textContent='Dockerfile'; $('dockerfile-editor-panel').hidden=false; $('network-editor-panel').hidden=true; show('editor-modal'); });
  $('edit-network').addEventListener('click', () => { $('editor-title').textContent='Network policy'; $('dockerfile-editor-panel').hidden=true; $('network-editor-panel').hidden=false; show('editor-modal'); });
  $('submit-environment').addEventListener('click', createEnvironment);
  $('close-progress').addEventListener('click', () => hide('progress-modal'));
  $('submit-snapshot').addEventListener('click', async () => {
    $('snapshot-error').textContent = '';
    try {
      await api(`/api/containers/${encodeURIComponent($('snapshot-container').value)}/snapshot`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({template_name:$('snapshot-name').value.trim(), description:$('snapshot-description').value.trim()})});
      hide('snapshot-modal'); await load();
    } catch (error) { $('snapshot-error').textContent = String(error.message || error); }
  });
  $('shell-form').addEventListener('submit', event => {
    event.preventDefault();
    const command = $('shell-command').value;
    if (state.shell && state.shell.readyState === WebSocket.OPEN) state.shell.send(command + '\n');
    $('shell-command').value = '';
  });
  $('close-shell').addEventListener('click', () => { if (state.shell) state.shell.close(); hide('shell-modal'); });
  $('environment-dockerfile').addEventListener('input', updateSummaries);
  $('environment-allow').addEventListener('input', updateSummaries);
  $('environment-deny').addEventListener('input', updateSummaries);

  load().catch(error => { $('container-empty').textContent = String(error.message || error); });
})();
