(() => {
  const state = {
    containers: [], templates: [], defaults: null, shell: null,
    shellFollow: true, shellHistory: [], shellHistoryIndex: 0, shellHistoryDraft: '',
    shellCompletionSeq: 0, shellContainer: '',
    jobTimer: null, lastLogSeq: 0, mounts: [], devices: [], editingName: null,
    stats: {}, statsTimer: null, // MUCLI_CONTAINER_MONITOR_V1
    hardware: null, // MUCLI_CONTAINER_HARDWARE_V1
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
  const icon = (name, size = '') => `<svg class="w-icon"${size ? ` data-size="${size}"` : ''} aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-${name}"></use></svg>`;
  const toast = (message, tone = 'info') => {
    if (window.WashToast) window.WashToast.show(String(message || ''), tone);
    else console.warn(message);
  };

  // Modals (.w-modal backdrops toggled through `hidden`) use the shared dialog
  // stack in dialogs.js (MuDialog): it remembers the opener, focuses the first
  // control (or a preferred one), traps Tab, routes Esc to `dialog-close` and
  // restores focus on close (G§9.17). The dialog semantics live on the
  // .w-modal__dialog surface, not the backdrop.
  const show = (id, preferred = null) => {
    const modal = $(id);
    if (!modal.hidden && window.MuDialog.isOpen(modal)) return;
    window.MuDialog.open(modal, {focus: preferred ? $(preferred) : null});
  };
  const hide = id => {
    const modal = $(id);
    if (modal.hidden) return;
    window.MuDialog.close(modal);
  };

  // ask(): the small Wash modal that replaces window.confirm / window.prompt.
  // Resolves true / the typed value, or null when cancelled.
  let askResolve = null;
  function ask({title, message = '', okLabel = 'OK', tone = 'primary', input = null}) {
    if (askResolve) askResolve(null);
    $('ask-title').textContent = title;
    $('ask-message').textContent = message;
    $('ask-message').hidden = !message;
    $('ask-field').hidden = !input;
    $('ask-label').textContent = input ? input.label : '';
    $('ask-input').value = input ? (input.value || '') : '';
    const ok = $('ask-ok');
    ok.querySelector('span').textContent = okLabel;
    ok.dataset.variant = tone === 'danger' ? 'danger' : 'primary';
    // Destructive confirms focus Cancel; prompts focus their field.
    show('ask-modal', input ? 'ask-input' : 'ask-cancel');
    return new Promise(resolve => { askResolve = resolve; });
  }
  function settleAsk(ok) {
    if (!askResolve) return;
    const resolve = askResolve; askResolve = null;
    const value = $('ask-field').hidden ? true : $('ask-input').value.trim();
    hide('ask-modal');
    resolve(ok ? (value === '' ? null : value) : null);
  }

  // Container status → shared status vocabulary (glyph + label, never colour alone).
  const STATUS_STYLE = {
    running: {glyph: '●', tone: 'running', label: 'Running'},
    created: {glyph: '○', tone: 'neutral', label: 'Created'},
    exited: {glyph: '○', tone: 'neutral', label: 'Stopped'},
    stopped: {glyph: '○', tone: 'neutral', label: 'Stopped'},
    paused: {glyph: '◆', tone: 'caution', label: 'Paused'},
    restarting: {glyph: '●', tone: 'running', label: 'Restarting'},
    missing: {glyph: '✗', tone: 'danger', label: 'Missing'},
    error: {glyph: '✗', tone: 'danger', label: 'Error'},
    dead: {glyph: '✗', tone: 'danger', label: 'Dead'},
  };
  const statusChip = status => {
    const key = String(status || '').toLowerCase();
    const style = STATUS_STYLE[key] || {glyph: '○', tone: 'neutral', label: key ? key[0].toUpperCase() + key.slice(1) : 'Unknown'};
    return `<span class="w-chip" data-tone="${style.tone}" data-status="${esc(key)}"><span class="w-chip__glyph" aria-hidden="true">${style.glyph}</span><span>${esc(style.label)}</span></span>`;
  };

  // ---------------------------------------------------------------- templates: "Used for jobs"
  // (documentation/plans/job_templates.md §3.2/§4): job setup commands, the network allowlist
  // and "Used by N jobs". Saved through PATCH /api/containers/templates/{name}; jobs that already
  // started keep the snapshot they took.
  const MAX_SETUP = 20;
  const openJobs = new Set();
  const setupDrafts = {};
  function setupRow(name, command, index) {
    return `<li class="w-env-jobs__cmd" data-setup-row="${index}"><span class="w-env-jobs__num" aria-hidden="true">${index + 1}</span>
      <input class="w-input" data-mono type="text" maxlength="2000" data-setup-cmd value="${esc(command)}" placeholder="npm ci" aria-label="Job setup command ${index + 1} of template ${esc(name)}">
      <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-setup-remove="${index}" aria-label="Remove command ${index + 1}" title="Remove command ${index + 1}">${icon('x', 'sm')}</button></li>`;
  }
  function templateJobsSection(item) {
    const name = item.name;
    const setup = setupDrafts[name] || item.job_setup || [];
    const egress = item.egress_allow || [];
    const used = Number(item.jobs_using || 0);
    const meta = [`${setup.length} setup command${setup.length === 1 ? '' : 's'}`, `${egress.length} domain${egress.length === 1 ? '' : 's'}`].join(' · ');
    return `<details class="w-env-jobs" data-template-jobs="${esc(name)}"${openJobs.has(name) ? ' open' : ''}>
      <summary class="w-env-jobs__summary"><span class="w-env-jobs__title">Used for jobs</span>
        <span class="w-chip" data-variant="meta" data-template-used="${used}">Used by ${used} job${used === 1 ? '' : 's'}</span><span class="w-env-jobs__meta">${esc(meta)}</span></summary>
      <form class="w-env-jobs__form" data-template-jobs-form="${esc(name)}" novalidate>
        <div class="w-field"><span class="w-field__label" id="tpl-setup-${esc(name)}">Job setup commands</span>
          <ol class="w-env-jobs__cmds" aria-labelledby="tpl-setup-${esc(name)}">${setup.map((c, i) => setupRow(name, c, i)).join('') || ''}</ol>
          <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-setup-add${setup.length >= MAX_SETUP ? ' disabled' : ''}>${icon('plus', 'sm')}<span>Add command</span></button></div>
          <p class="w-field__hint">Run in order, once per job, in the job's worktree after its image is built and before planning, e.g. <code>npm ci</code>. At most ${MAX_SETUP}.</p></div>
        <div class="w-field"><label class="w-field__label" for="tpl-egress-${esc(name)}">Network allowlist</label>
          <textarea class="w-textarea" data-mono rows="4" id="tpl-egress-${esc(name)}" data-template-egress placeholder="registry.npmjs.org">${esc(egress.join('\n'))}</textarea>
          <p class="w-field__hint">One domain per line (<code>*.example.com</code> allowed). Job setup, implement and checks may reach these; model provider hosts are always dropped.</p></div>
        <p class="w-field__error" data-template-jobs-error role="alert"></p>
        <div class="w-btn-row" data-align="end"><button type="submit" class="w-btn" data-variant="primary" data-size="sm">Save</button></div>
      </form></details>`;
  }
  function readSetup(form) { return [...form.querySelectorAll('[data-setup-cmd]')].map(i => i.value); }
  function bindTemplateJobs() {
    document.querySelectorAll('[data-template-jobs]').forEach(details => {
      const name = details.dataset.templateJobs;
      const form = details.querySelector('form');
      details.addEventListener('toggle', () => { if (details.open) openJobs.add(name); else openJobs.delete(name); });
      const redraw = commands => {
        setupDrafts[name] = commands;
        form.querySelector('.w-env-jobs__cmds').innerHTML = commands.map((c, i) => setupRow(name, c, i)).join('');
        form.querySelector('[data-setup-add]').disabled = commands.length >= MAX_SETUP;
      };
      form.addEventListener('click', event => {
        const remove = event.target.closest('[data-setup-remove]');
        if (remove) { const list = readSetup(form); list.splice(Number(remove.dataset.setupRemove), 1); redraw(list); return; }
        if (event.target.closest('[data-setup-add]')) {
          const list = readSetup(form);
          if (list.length >= MAX_SETUP) return;
          redraw([...list, '']);
          form.querySelector(`[data-setup-row="${list.length}"] [data-setup-cmd]`)?.focus();
        }
      });
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const error = form.querySelector('[data-template-jobs-error]');
        error.textContent = '';
        const job_setup = readSetup(form).map(c => c.trim()).filter(Boolean);
        const egress_allow = splitLines(form.querySelector('[data-template-egress]').value);
        if (job_setup.length > MAX_SETUP) { error.textContent = `At most ${MAX_SETUP} setup commands.`; return; }
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        try {
          await api(`/api/containers/templates/${encodeURIComponent(name)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({job_setup, egress_allow})});
          delete setupDrafts[name];
          openJobs.add(name);
          await load();
          toast(`Saved job settings for template ${name}`, 'ok');
        } catch (err) { error.textContent = String(err.message || err); button.disabled = false; }
      });
    });
  }

  async function load() {
    const [listing, defaults] = await Promise.all([api('/api/containers'), api('/api/container-defaults')]);
    state.containers = listing.containers || [];
    state.templates = listing.templates || [];
    state.defaults = defaults;
    state.hardware = defaults.hardware || null;
    render();
    renderStats();
    startStatsPolling();
  }

  // Top-bar status chips (Wash shell, .w-chip): count + glyph + label.
  function renderChips() {
    const envChip = document.getElementById('container-count-chip');
    if (envChip) {
      const running = state.containers.filter(ref => ref.status === 'running').length;
      const total = state.containers.length;
      envChip.dataset.tone = running ? 'running' : 'neutral';
      envChip.querySelector('[data-chip-glyph]').textContent = running ? '●' : '○';
      envChip.querySelector('[data-chip-text]').textContent = running
        ? `${running} of ${total} running`
        : `${total} environment${total === 1 ? '' : 's'}`;
    }
    const tplChip = document.getElementById('template-count-chip');
    if (tplChip) {
      const count = state.templates.length;
      tplChip.querySelector('[data-chip-text]').textContent = `${count} template${count === 1 ? '' : 's'}`;
    }
  }

  function render() {
    renderChips();
    $('container-empty').hidden = state.containers.length > 0;
    $('container-list').innerHTML = state.containers.map(ref => {
      const sessions = (ref.attached_sessions || []).map(name =>
        `<span class="w-chip" data-variant="meta"><span>${esc(name)}</span><button type="button" class="w-chip__remove" data-action="detach" data-name="${esc(ref.name)}" data-session="${esc(name)}" title="Detach session" aria-label="Detach ${esc(name)}">${icon('x', 'sm')}</button></span>`
      ).join('') || '<span class="w-env-muted">—</span>';
      const running = ref.status === 'running';
      const primary = running ? 'stop' : 'start';
      return `<article class="w-card w-env-card" data-variant="interactive" data-card-action="edit" data-name="${esc(ref.name)}" tabindex="0" role="button" aria-label="Edit environment ${esc(ref.name)}">
        <header class="w-card__head">
          <span class="w-eyebrow" data-tone="ok"><i class="w-eyebrow__swatch"></i>Environment</span>
          <div class="w-env-card__tools">
            <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-action="${primary}" data-name="${esc(ref.name)}" title="${running ? 'Stop' : 'Start'} environment" aria-label="${primary} ${esc(ref.name)}">${icon(running ? 'square' : 'play', 'sm')}</button>
            <details class="w-env-overflow">
              <summary class="w-btn" data-variant="icon" data-size="sm" title="More actions" aria-label="More actions for ${esc(ref.name)}">${icon('more-horizontal', 'sm')}</summary>
              <div class="w-menu w-env-overflow__menu" role="menu" aria-label="Actions for ${esc(ref.name)}">
                <button type="button" class="w-menu__item" role="menuitem" data-action="shell" data-name="${esc(ref.name)}">${icon('terminal')}<span class="w-menu__label">Open shell</span></button>
                <button type="button" class="w-menu__item" role="menuitem" data-action="restart" data-name="${esc(ref.name)}">${icon('rotate-ccw')}<span class="w-menu__label">Restart</span></button>
                <button type="button" class="w-menu__item" role="menuitem" data-action="clone" data-name="${esc(ref.name)}">${icon('copy')}<span class="w-menu__label">Clone</span></button>
                <button type="button" class="w-menu__item" role="menuitem" data-action="attach" data-name="${esc(ref.name)}">${icon('link')}<span class="w-menu__label">Attach session</span></button>
                <button type="button" class="w-menu__item" role="menuitem" data-action="snapshot" data-name="${esc(ref.name)}">${icon('layers')}<span class="w-menu__label">Create template</span></button>
                <hr class="w-menu__separator">
                <button type="button" class="w-menu__item" role="menuitem" data-tone="danger" data-action="remove" data-name="${esc(ref.name)}">${icon('trash-2')}<span class="w-menu__label">Remove environment</span></button>
              </div>
            </details>
          </div>
          <h3 class="w-card__title w-env-card__title">${esc(ref.name)}</h3>
          <div class="w-chip-row">
            ${statusChip(ref.status)}
            ${ref.jobs_using ? `<span class="w-chip w-env-jobs-badge" data-variant="meta" title="Non-finished jobs whose build environments snapshot this environment"><span>Used by ${Number(ref.jobs_using)} job${ref.jobs_using === 1 ? '' : 's'}</span></span>` : ''}
          </div>
        </header>
        <div class="w-card__body">
          ${(ref.job_scan_blocks || []).map(block => `<div class="w-env-scan-warning" role="note"><strong><span aria-hidden="true">◆</span> Secret scan blocked job <a href="/work#/job/${esc(block.job_id)}">#${esc(String(block.job_id).slice(0, 8))}</a></strong> (build environment ${esc(block.binding)}): remove these from the environment or allow them in Jobs → Settings → Build environments.<ul>${(block.paths || []).map(path => `<li><code>${esc(path)}</code></li>`).join('')}</ul></div>`).join('')}
          <dl class="w-env-meta"><dt>Image</dt><dd>${esc(ref.image)}</dd><dt>Template</dt><dd>${esc(ref.template_name || 'custom')}</dd><dt>Sessions</dt><dd class="w-env-meta__chips">${sessions}</dd><dt>Network</dt><dd>${esc(ref.network_name || '—')}</dd><dt>Worker</dt><dd>${esc(ref.worker_port || '—')}</dd></dl>
          <section class="w-env-monitor" data-stats-name="${esc(ref.name)}" aria-label="Live resource monitor for ${esc(ref.name)}">
            <div class="w-env-monitor__waiting"><span class="w-skeleton" style="--w-skeleton-w: 60%"></span><span class="w-skeleton" style="--w-skeleton-w: 40%"></span></div>
          </section>
        </div>
        <footer class="w-card__foot w-env-card__hint"><span>Open configuration</span><span aria-hidden="true">›</span></footer>
      </article>`;
    }).join('');

    $('template-empty').hidden = state.templates.length > 0;
    $('template-list').innerHTML = state.templates.map(item => `<article class="w-card w-env-card w-env-card--template" data-variant="interactive" data-template-action="use" data-name="${esc(item.name)}" id="template-${esc(item.name)}" aria-label="Template ${esc(item.name)}">
      <header class="w-card__head">
        <span class="w-eyebrow" data-tone="plan"><i class="w-eyebrow__swatch"></i>Template</span>
        <div class="w-env-card__tools"><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-template-action="remove" data-name="${esc(item.name)}" title="Delete template" aria-label="Delete template ${esc(item.name)}">${icon('trash-2', 'sm')}</button></div>
        <h3 class="w-card__title w-env-card__title">${esc(item.name)}</h3>
      </header>
      <div class="w-card__body">
        <p class="w-env-card__lede">${esc(item.description || 'Reusable container environment')}</p>
        <dl class="w-env-meta"><dt>Source</dt><dd>${esc(item.source_container)}</dd><dt>Image</dt><dd>${esc(item.image)}</dd></dl>
        ${templateJobsSection(item)}
      </div>
      <footer class="w-card__foot w-env-card__hint"><button type="button" class="w-btn w-env-card__use" data-variant="ghost" data-size="sm" data-template-action="use" data-name="${esc(item.name)}" aria-label="Create environment from template ${esc(item.name)}">Use as a base <span aria-hidden="true">›</span></button></footer>
    </article>`).join('');
    bindTemplateJobs();

    $('environment-template').innerHTML = state.templates.map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');
    const option = $('environment-source').querySelector('option[value="template"]');
    if (option) option.disabled = state.templates.length === 0;
  }

  function formatBytes(value) {
    let amount = Math.max(0, Number(value) || 0);
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let index = 0;
    while (amount >= 1000 && index < units.length - 1) { amount /= 1000; index += 1; }
    return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
  }

  function formatRate(value) { return `${formatBytes(value)}/s`; }

  function formatDuration(value) {
    const seconds = Math.max(0, Number(value) || 0);
    if (!seconds) return '0s';
    if (seconds < 60) return `${Math.floor(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
  }

  function metric(label, value, percent = null, detail = '') {
    const pct = percent == null ? null : Math.max(0, Math.min(100, Number(percent) || 0));
    const tone = pct == null ? '' : pct >= 85 ? 'danger' : pct >= 65 ? 'caution' : 'running';
    return `<div class="w-env-metric">
      <span class="w-env-metric__label">${esc(label)}</span><strong class="w-env-metric__value">${esc(value)}</strong>
      ${detail ? `<small class="w-env-metric__detail">${esc(detail)}</small>` : ''}
      ${pct == null ? '' : `<div class="w-progress" data-tone="${tone}" role="progressbar" aria-label="${esc(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}" style="--w-progress: ${pct}%"><span class="w-progress__bar"></span></div>`}
    </div>`;
  }

  function renderStats() {
    document.querySelectorAll('[data-stats-name]').forEach(root => {
      const name = root.dataset.statsName;
      const value = state.stats[name];
      const ref = state.containers.find(item => item.name === name);
      if (!value) {
        root.innerHTML = '<div class="w-env-monitor__waiting" role="status"><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>Waiting for a resource sample…</div>';
        return;
      }
      const running = value.status === 'running';
      const gpu = value.gpu || {};
      const gpuValue = gpu.requested
        ? gpu.utilization_percent == null ? 'N/A' : `${Number(gpu.utilization_percent).toFixed(1)}%`
        : '—';
      root.innerHTML = `
        <div class="w-env-monitor__head"><div><strong>Live monitor</strong><small>${running ? `${value.pids || 0} processes · up ${formatDuration(value.uptime_seconds)}${value.restart_count ? ` · ${value.restart_count} restarts` : ''}` : `${esc(value.status || ref?.status || 'stopped')} · retained storage ${formatBytes(value.storage_writable_bytes)}`}</small></div>${running ? '<span class="w-inkdrop" data-size="sm" data-tone="ok" title="Live" aria-hidden="true"></span>' : ''}</div>
        <div class="w-env-monitor__grid">
          ${metric('CPU', running ? `${Number(value.cpu_percent || 0).toFixed(1)}%` : '—', running ? value.cpu_percent : null)}
          ${metric('Memory', `${formatBytes(value.memory_used_bytes)} / ${formatBytes(value.memory_limit_bytes)}`, running ? value.memory_percent : null)}
          ${metric('GPU', gpuValue, gpu.utilization_percent, gpu.requested && gpu.memory_total_bytes ? `${formatBytes(gpu.memory_used_bytes)} / ${formatBytes(gpu.memory_total_bytes)}` : '')}
          ${metric('Network', `↓ ${formatRate(value.network_rx_bytes_per_second)} · ↑ ${formatRate(value.network_tx_bytes_per_second)}`, null, `${formatBytes(value.network_rx_bytes)} received · ${formatBytes(value.network_tx_bytes)} sent`)}
          ${metric('Storage', formatBytes(value.storage_writable_bytes), null, `Writable layer · rootfs ${formatBytes(value.storage_rootfs_bytes)}`)}
          ${metric('Block I/O', `R ${formatBytes(value.block_read_bytes)} · W ${formatBytes(value.block_write_bytes)}`)}
        </div>
        ${gpu.requested ? '<p class="w-env-monitor__note">GPU values are totals for the assigned physical device.</p>' : ''}
        ${value.error ? `<p class="w-env-monitor__error" role="note"><span aria-hidden="true">✗</span> ${esc(value.error)}</p>` : ''}
      `;
    });
  }

  async function pollStats() {
    try {
      const response = await api('/api/containers/stats');
      state.stats = response.containers || {};
      renderStats();
      state.statsTimer = setTimeout(pollStats, Math.max(1500, response.poll_after_ms || 2500));
    } catch (_) {
      state.statsTimer = setTimeout(pollStats, 5000);
    }
  }

  function startStatsPolling() {
    if (state.statsTimer) clearTimeout(state.statsTimer);
    state.statsTimer = null;
    pollStats();
  }

  function resetForm() {
    state.editingName = null;
    state.mounts = [];
    state.devices = [];
    $('environment-name').disabled = false;
    $('environment-name').value = '';
    $('environment-source').value = 'dockerfile';
    $('environment-template').value = '';
    $('environment-dockerfile').value = state.defaults?.dockerfile || '';
    $('environment-allow').value = (state.defaults?.egress_allow || []).join('\n');
    $('environment-deny').value = (state.defaults?.egress_deny || []).join('\n');
    $('environment-gpu-mode').value = 'none';
    $('environment-gpu-selection').value = '';
    $('environment-device-host').value = '';
    $('environment-device-target').value = '';
    $('environment-device-permissions').value = 'rwm';
    $('environment-start').checked = true;
    $('environment-error').textContent = '';
    setKicker('New environment');
    $('environment-modal-title').textContent = 'Create environment';
    $('environment-modal-description').textContent = 'Standalone containers are not tied to a session.';
    setSubmitLabel('Create');
    syncSource(); renderMounts(); renderHardware(); updateSummaries(); goStep(0);
  }

  function setKicker(text) { $('environment-kicker').lastChild.textContent = text; }
  function setSubmitLabel(text) { $('submit-environment').querySelector('span').textContent = text; }

  // Stepped create flow (G§9.13 stepper): Basics → Folders and hardware → Network and review.
  const STEPS = 3;
  let step = 0;
  function goStep(next) {
    step = Math.max(0, Math.min(STEPS - 1, next));
    document.querySelectorAll('[data-env-pane]').forEach(pane => { pane.hidden = Number(pane.dataset.envPane) !== step; });
    document.querySelectorAll('#environment-steps .w-stepper__step').forEach((item, index) => {
      const status = index < step ? 'done' : index === step ? 'active' : 'pending';
      item.dataset.status = status;
      item.querySelector('[data-step-glyph]').textContent = status === 'done' ? '✓' : status === 'active' ? '●' : '○';
      if (index === step) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    });
    $('environment-back').hidden = step === 0;
    $('environment-next').hidden = step === STEPS - 1;
    // Editing can save from any step; a new environment is created from the review step.
    $('submit-environment').hidden = step !== STEPS - 1 && !state.editingName;
    $('submit-environment').dataset.variant = step === STEPS - 1 ? 'primary' : 'secondary';
    if (step === STEPS - 1) renderReview();
  }
  function validateBasics() {
    const name = $('environment-name');
    const ok = Boolean(name.value.trim());
    name.setAttribute('aria-invalid', ok ? 'false' : 'true');
    $('environment-error').textContent = ok ? '' : 'Give the environment a name.';
    if (!ok) { goStep(0); name.focus(); }
    return ok;
  }
  function renderReview() {
    const body = payload();
    const rows = [
      ['Name', body.name || '—'],
      ['Base', body.template_name ? `template ${body.template_name}` : 'Dockerfile'],
      ['Folders', `${body.mounts.length} mount${body.mounts.length === 1 ? '' : 's'}`],
      ['Hardware', $('hardware-summary').textContent],
      ['Network', body.template_name ? 'from the template' : $('network-summary').textContent],
    ];
    $('environment-review').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
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
    state.devices = (config.devices || []).map(item => ({...item}));
    const gpuRequest = String(config.gpu_request || '');
    $('environment-gpu-mode').value = gpuRequest === 'all' ? 'all' : (gpuRequest ? 'selected' : 'none');
    $('environment-gpu-selection').value = gpuRequest.replace(/^device=/, '');
    if (editing) {
      setKicker('Managed environment');
      $('environment-modal-title').textContent = `Edit ${sourceName}`;
      $('environment-modal-description').textContent = 'Saving recreates the worker while retaining named volumes and attached sessions.';
      setSubmitLabel('Save and recreate');
    } else if (clone) {
      setKicker('Clone environment');
      $('environment-modal-title').textContent = `Clone ${sourceName}`;
      $('environment-modal-description').textContent = 'Review the copied configuration and choose a new name.';
    }
    syncSource(); renderMounts(); renderHardware(); updateSummaries(); goStep(0); show('create-modal', 'environment-name');
  }

  function openCreate(templateName = '') {
    resetForm();
    if (templateName) {
      $('environment-source').value = 'template';
      $('environment-template').value = templateName;
      syncSource();
    }
    show('create-modal', 'environment-name');
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
      root.innerHTML = '<p class="w-env-rows__empty">No extra folders attached.</p>';
      return;
    }
    root.innerHTML = state.mounts.map((mount, index) => `<div class="w-env-row w-env-row--mount" data-mount-index="${index}">
      ${icon('folder')}
      <div class="w-env-row__text"><strong title="${esc(mount.host_path)}">${esc(mount.host_path)}</strong><small>host folder</small></div>
      <span class="w-env-row__arrow" aria-hidden="true">→</span>
      <input class="w-input" data-mono data-mount-target="${index}" value="${esc(mount.container_path)}" aria-label="Container path for ${esc(mount.host_path)}">
      <span class="w-select"><select class="w-select__control" data-mount-mode="${index}" aria-label="Access mode"><option value="rw" ${mount.mode !== 'ro' ? 'selected' : ''}>rw</option><option value="ro" ${mount.mode === 'ro' ? 'selected' : ''}>ro</option></select></span>
      <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-remove-mount="${index}" aria-label="Remove ${esc(mount.host_path)}" title="Remove folder">${icon('x', 'sm')}</button>
    </div>`).join('');
  }

  function addMountFromBrowser() {
    const browser = window.Alpine && Alpine.store('fileBrowser');
    if (!browser) return toast('The folder browser is still loading. Try again in a moment.', 'danger');
    browser.show('folder', path => {
      if (state.mounts.some(item => item.host_path === path)) return;
      const base = String(path).split('/').filter(Boolean).pop() || 'project';
      state.mounts.push({host_path: path, container_path: `/workspace/${base}`, mode: 'rw'});
      renderMounts();
    });
  }

  function renderHardware() {
    const gpu = (state.hardware && state.hardware.gpu) || {};
    const supported = Boolean(gpu.supported);
    const mode = $('environment-gpu-mode').value;
    $('environment-gpu-mode').querySelectorAll('option').forEach(option => {
      if (option.value !== 'none') option.disabled = !supported;
    });
    if (!supported && mode !== 'none') $('environment-gpu-mode').value = 'none';
    $('environment-gpu-selection-row').hidden = $('environment-gpu-mode').value !== 'selected';
    $('hardware-capability').textContent = gpu.reason || 'GPU capability was not detected.';
    const candidates = (state.hardware && state.hardware.devices) || [];
    $('environment-device-preset').innerHTML = '<option value="">Detected device…</option>' + candidates.map(item =>
      `<option value="${esc(item.host_path)}">${esc(item.name || item.host_path)} · ${esc(item.kind || 'device')}</option>`
    ).join('');
    $('environment-device-list').innerHTML = state.devices.length ? state.devices.map((device, index) =>
      `<div class="w-env-row">${icon('hard-drive')}<div class="w-env-row__text"><strong>${esc(device.container_path)}</strong><small>${esc(device.host_path)} · ${esc(device.permissions || 'rwm')}</small></div><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-remove-device="${index}" aria-label="Remove device ${esc(device.container_path)}" title="Remove device">${icon('x', 'sm')}</button></div>`
    ).join('') : '<p class="w-env-rows__empty">No host devices attached.</p>';
    updateHardwareSummary();
  }

  function updateHardwareSummary() {
    const mode = $('environment-gpu-mode').value;
    const gpuText = mode === 'all' ? 'all GPUs' : mode === 'selected' ? `GPU ${$('environment-gpu-selection').value || 'selection required'}` : 'no GPU';
    $('hardware-summary').textContent = `${gpuText} · ${state.devices.length} device${state.devices.length === 1 ? '' : 's'}`;
  }

  function addDevice() {
    const preset = $('environment-device-preset').value;
    const host = ($('environment-device-host').value.trim() || preset).trim();
    const target = ($('environment-device-target').value.trim() || host).trim();
    if (!host || !target) return;
    if (state.devices.some(item => item.host_path === host || item.container_path === target)) return;
    state.devices.push({host_path: host, container_path: target, permissions: $('environment-device-permissions').value || 'rwm'});
    $('environment-device-host').value = '';
    $('environment-device-target').value = '';
    $('environment-device-preset').value = '';
    renderHardware();
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
      gpu_request: $('environment-gpu-mode').value === 'all' ? 'all' : ($('environment-gpu-mode').value === 'selected' && $('environment-gpu-selection').value.trim() ? `device=${$('environment-gpu-selection').value.trim()}` : ''),
      devices: state.devices,
      egress_allow: useTemplate ? null : splitLines($('environment-allow').value),
      egress_deny: useTemplate ? null : splitLines($('environment-deny').value),
      start: $('environment-start').checked,
    };
  }

  async function submitEnvironment() {
    $('environment-error').textContent = '';
    try {
      const body = payload();
      if (!validateBasics()) return;
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
    $('progress-drop').hidden = false; $('progress-bar').hidden = false; $('progress-output').open = false;
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
          $('progress-drop').hidden = true; $('progress-bar').hidden = true;
          if (job.state === 'error') { $('progress-output').open = true; $('progress-message').textContent = job.detail || job.message; }
          else { toast(`${name} is ready`, 'ok'); await load(); }
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

  // MUCLI_SHELL_QOL_V1
  function shellNearBottom() {
    const output = $('shell-output');
    return output.scrollHeight - output.clientHeight - output.scrollTop < 48;
  }

  function appendShellOutput(text, force = false) {
    if (!text) return;
    const shouldFollow = force || state.shellFollow;
    $('shell-output').textContent += String(text).replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g, '');
    if (shouldFollow) {
      requestAnimationFrame(() => { $('shell-output').scrollTop = $('shell-output').scrollHeight; });
    }
  }

  function loadShellHistory(name) {
    state.shellContainer = name;
    try {
      const value = JSON.parse(localStorage.getItem(`mucli-shell-history:${name}`) || '[]');
      state.shellHistory = Array.isArray(value) ? value.map(String).slice(-200) : [];
    } catch (_) {
      state.shellHistory = [];
    }
    state.shellHistoryIndex = state.shellHistory.length;
    state.shellHistoryDraft = '';
  }

  function recordShellHistory(command) {
    const value = String(command || '').trim();
    if (!value) return;
    state.shellHistory = state.shellHistory.filter(item => item !== value);
    state.shellHistory.push(value);
    state.shellHistory = state.shellHistory.slice(-200);
    state.shellHistoryIndex = state.shellHistory.length;
    state.shellHistoryDraft = '';
    try {
      localStorage.setItem(`mucli-shell-history:${state.shellContainer}`, JSON.stringify(state.shellHistory));
    } catch (_) {}
  }

  function moveShellHistory(direction) {
    if (!state.shellHistory.length) return;
    const input = $('shell-command');
    if (state.shellHistoryIndex === state.shellHistory.length) state.shellHistoryDraft = input.value;
    state.shellHistoryIndex = Math.max(0, Math.min(state.shellHistory.length, state.shellHistoryIndex + direction));
    input.value = state.shellHistoryIndex === state.shellHistory.length
      ? state.shellHistoryDraft
      : state.shellHistory[state.shellHistoryIndex];
  }

  function applyShellCompletion(message) {
    const input = $('shell-command');
    if (String(message.source || '') !== input.value) return;
    const start = Math.max(0, Math.min(Number(message.start) || 0, input.value.length));
    const end = Math.max(start, Math.min(Number(message.end) || input.value.length, input.value.length));
    const replacement = String(message.replacement || '');
    const candidates = Array.isArray(message.candidates) ? message.candidates : [];
    if (replacement) {
      input.value = input.value.slice(0, start) + replacement + input.value.slice(end);
      return;
    }
    if (candidates.length > 1) appendShellOutput(`\n${candidates.join('  ')}\n`);
  }

  function completeShellInput() {
    if (!state.shell || state.shell.readyState !== WebSocket.OPEN) return;
    const line = $('shell-command').value;
    state.shell.send(JSON.stringify({
      type: 'shell_complete',
      request_id: String(++state.shellCompletionSeq),
      line,
      cursor: line.length,
    }));
  }

  function openShell(name) {
    if (state.shell) state.shell.close();
    $('shell-title').textContent = `Shell · ${name}`;
    $('shell-output').textContent = '';
    state.shellFollow = true;
    loadShellHistory(name);
    show('shell-modal');
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/api/containers/${encodeURIComponent(name)}/shell`);
    state.shell = socket;
    socket.onopen = () => appendShellOutput(`Connected to ${name}\n`, true);
    socket.onmessage = event => {
      const raw = String(event.data);
      if (raw.startsWith('{')) {
        try {
          const message = JSON.parse(raw);
          if (message.type === 'shell_completion') {
            applyShellCompletion(message);
            return;
          }
        } catch (_) {}
      }
      appendShellOutput(raw);
    };
    socket.onclose = () => { appendShellOutput('\n[shell disconnected]\n'); state.shell = null; };
    socket.onerror = () => { appendShellOutput('\n[shell connection failed]\n'); };
    setTimeout(() => $('shell-command').focus(), 50);
  }

  function snapshot(name) { $('snapshot-container').value = name; $('snapshot-name').value = name.replace(/^mucli-/, ''); $('snapshot-description').value = ''; $('snapshot-error').textContent = ''; show('snapshot-modal'); }

  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-close]'); if (close) { hide(close.dataset.close); updateSummaries(); if (step === STEPS - 1) renderReview(); return; }
    if (event.target.closest('[data-empty-create] .w-btn')) { openCreate(); return; }
    const stepButton = event.target.closest('[data-env-step]');
    if (stepButton) { const target = Number(stepButton.dataset.envStep); if (target === 0 || validateBasics()) goStep(target); return; }
    // Close an open overflow menu when clicking elsewhere (or on one of its items).
    document.querySelectorAll('.w-env-overflow[open]').forEach(menu => { if (!menu.contains(event.target) || event.target.closest('.w-menu__item')) menu.open = false; });
    const removeMount = event.target.closest('[data-remove-mount]'); if (removeMount) { state.mounts.splice(Number(removeMount.dataset.removeMount), 1); renderMounts(); return; }
    const removeDevice = event.target.closest('[data-remove-device]'); if (removeDevice) { state.devices.splice(Number(removeDevice.dataset.removeDevice), 1); renderHardware(); return; }
    const button = event.target.closest('[data-action]');
    if (button) {
      const {action: actionName, name, session} = button.dataset;
      try {
        if (actionName === 'shell') return openShell(name);
        if (actionName === 'edit') return await openEdit(name, false);
        if (actionName === 'clone') return await openEdit(name, true);
        if (actionName === 'snapshot') return snapshot(name);
        if (actionName === 'attach') {
          const sessionName = await ask({title: `Attach a session to ${name}`, okLabel: 'Attach', input: {label: 'Session name'}});
          if (sessionName) { await action(name, 'attach', {session_name: sessionName}); toast(`Attached ${sessionName}`, 'ok'); }
          return;
        }
        if (actionName === 'detach') { await action(name, 'detach', {session_name: session}); return; }
        if (actionName === 'remove') {
          if (!await ask({title: `Remove environment ${name}?`, message: 'Its persistent volumes are removed too. This cannot be undone.', okLabel: 'Remove', tone: 'danger'})) return;
          await api(`/api/containers/${encodeURIComponent(name)}?force=true`, {method:'DELETE'}); await load(); toast(`Removed ${name}`, 'ok'); return;
        }
        await action(name, actionName);
      } catch (error) { toast(String(error.message || error), 'danger'); }
      return;
    }
    const templateButton = event.target.closest('[data-template-action]');
    if (templateButton && event.target.closest('.w-env-jobs')) return;  // the "Used for jobs" editor
    if (templateButton) {
      const name = templateButton.dataset.name;
      if (templateButton.dataset.templateAction === 'use') return openCreate(name);
      if (!await ask({title: `Delete template ${name}?`, message: 'Environments already created from it keep working.', okLabel: 'Delete', tone: 'danger'})) return;
      try { await api(`/api/container-templates/${encodeURIComponent(name)}`, {method:'DELETE'}); await load(); toast(`Deleted template ${name}`, 'ok'); }
      catch (error) { toast(String(error.message || error), 'danger'); }
    }
  });

  // Esc (dialogs.js dispatches `dialog-close` on the topmost backdrop) closes
  // a dialog through its own close control, so cleanup handlers still run.
  document.querySelectorAll('.manager-modal').forEach(modal => {
    modal.addEventListener('dialog-close', () => {
      const close = modal.querySelector('[data-close], #close-shell, #close-progress:not([hidden]), #ask-cancel');
      if (close) close.click();
    });
  });

  $('container-manager').addEventListener('keydown', async event => {
    const card = event.target.closest('[data-card-action], article[data-template-action="use"]');
    if (!card || !['Enter', ' '].includes(event.key)) return;
    if (event.target.closest('button, summary, details, input, select, textarea, a')) return;
    event.preventDefault();
    if (card.dataset.cardAction === 'edit') await openEdit(card.dataset.name, false);
    else openCreate(card.dataset.name);
  });

  $('container-manager').addEventListener('click', async event => {
    const card = event.target.closest('[data-card-action]');
    if (!card || event.target.closest('button, summary, details, input, select, textarea, a')) return;
    try { await openEdit(card.dataset.name, false); }
    catch (error) { toast(String(error.message || error), 'danger'); }
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

  // Command palette (static/js/wash/palette.js): this page's actions and its
  // environments, read live each time the palette draws.
  if (window.WashPalette) {
    window.WashPalette.register('containers', () => [
      {id: 'env:new', group: 'Actions', label: 'New environment', icon: 'plus', run: () => openCreate()},
      {id: 'env:refresh', group: 'Actions', label: 'Refresh environments', icon: 'refresh-cw', run: () => load()},
      {id: 'env:templates', group: 'Navigate', label: 'Show templates', icon: 'layers', run: () => $('templates-title').scrollIntoView({block: 'start'})},
      ...state.containers.map(ref => ({
        id: `env:open:${ref.name}`, group: 'Environments', label: `Configure ${ref.name}`, icon: 'box',
        keywords: `${ref.status || ''} environment container`, run: () => openEdit(ref.name, false),
      })),
    ], {priority: 10});
  }
  $('environment-source').addEventListener('change', syncSource);
  $('add-environment-mount').addEventListener('click', addMountFromBrowser);
  $('add-environment-device').addEventListener('click', addDevice);
  $('environment-gpu-mode').addEventListener('change', renderHardware);
  $('environment-gpu-selection').addEventListener('input', updateHardwareSummary);
  $('environment-device-preset').addEventListener('change', event => {
    const value = event.target.value;
    if (value) {
      $('environment-device-host').value = value;
      $('environment-device-target').value = value;
    }
  });
  $('edit-dockerfile').addEventListener('click', () => { $('editor-title').textContent='Dockerfile'; $('dockerfile-editor-panel').hidden=false; $('network-editor-panel').hidden=true; show('editor-modal'); });
  $('edit-network').addEventListener('click', () => { $('editor-title').textContent='Network policy'; $('dockerfile-editor-panel').hidden=true; $('network-editor-panel').hidden=false; show('editor-modal'); });
  $('submit-environment').addEventListener('click', submitEnvironment);
  $('environment-next').addEventListener('click', () => { if (step > 0 || validateBasics()) goStep(step + 1); });
  $('environment-back').addEventListener('click', () => goStep(step - 1));
  $('environment-name').addEventListener('input', () => { if ($('environment-name').value.trim()) { $('environment-name').setAttribute('aria-invalid', 'false'); $('environment-error').textContent = ''; } });
  $('ask-ok').addEventListener('click', () => settleAsk(true));
  $('ask-cancel').addEventListener('click', () => settleAsk(false));
  $('ask-input').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); settleAsk(true); } });
  $('close-progress').addEventListener('click', () => hide('progress-modal'));
  $('submit-snapshot').addEventListener('click', async () => {
    $('snapshot-error').textContent = '';
    try { await api(`/api/containers/${encodeURIComponent($('snapshot-container').value)}/snapshot`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template_name:$('snapshot-name').value.trim(),description:$('snapshot-description').value.trim()})}); hide('snapshot-modal'); await load(); toast('Template created', 'ok'); }
    catch (error) { $('snapshot-error').textContent = String(error.message || error); }
  });
  $('shell-form').addEventListener('submit', event => {
    event.preventDefault();
    const command = $('shell-command').value;
    if (state.shell && state.shell.readyState === WebSocket.OPEN) {
      recordShellHistory(command);
      state.shellFollow = true;
      appendShellOutput(`$ ${command}\n`, true);
      state.shell.send(command + '\n');
    }
    $('shell-command').value = '';
  });
  $('shell-command').addEventListener('keydown', event => {
    if (event.key === 'Tab') { event.preventDefault(); completeShellInput(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveShellHistory(-1); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); moveShellHistory(1); }
  });
  $('shell-output').addEventListener('scroll', () => { state.shellFollow = shellNearBottom(); });
  $('close-shell').addEventListener('click', () => { if(state.shell)state.shell.close(); hide('shell-modal'); });
  $('environment-dockerfile').addEventListener('input', updateSummaries);
  $('environment-allow').addEventListener('input', updateSummaries);
  $('environment-deny').addEventListener('input', updateSummaries);

  // pagehide (not beforeunload) keeps the page eligible for the back/forward cache.
  window.addEventListener('pagehide', () => {
    if (state.statsTimer) clearTimeout(state.statsTimer);
    state.statsTimer = null;
  });
  window.addEventListener('pageshow', event => { if (event.persisted) startStatsPolling(); });
  load().catch(error => {
    const empty = $('container-empty');
    empty.hidden = false;
    empty.innerHTML = `<div class="w-empty" data-tone="danger" data-variant="error" role="alert">
      <div class="w-empty__art" aria-hidden="true">${icon('alert-circle')}</div>
      <h3 class="w-empty__title">Couldn't load environments</h3>
      <p class="w-empty__body">${esc(error.message || error)}</p>
      <p class="w-empty__detail">Check that Docker is running, then retry.</p>
      <div class="w-empty__actions"><button type="button" class="w-btn" data-variant="secondary" data-retry-load><span>Retry</span></button></div>
    </div>`;
    empty.querySelector('[data-retry-load]').addEventListener('click', () => window.location.reload());
  });
})();
