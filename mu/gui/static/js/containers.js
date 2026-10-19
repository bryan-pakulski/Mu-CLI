(() => {
  const state = {
    containers: [], templates: [], defaults: null, shell: null,
    shellFollow: true, shellHistory: [], shellHistoryIndex: 0, shellHistoryDraft: '',
    shellCompletionSeq: 0, shellContainer: '',
    jobTimer: null, lastLogSeq: 0, mounts: [], devices: [], editingName: null,
    stats: {}, statsTimer: null, // MUCLI_CONTAINER_MONITOR_V1
    hardware: null, // MUCLI_CONTAINER_HARDWARE_V1
    selected: null, mainKey: '', history: {}, // Mu §11b: the chosen environment / template; live samples per environment
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
  // Mu §5.4: one sign (.w-sign, keyed by the vocabulary `status` group) plus the
  // word in the row's second line; colour only for running, needs-you and failed.
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
  // Container status → the vocabulary `status` group its sign uses.
  const STATUS_SIGN = {running: 'active', restarting: 'active', paused: 'waiting', missing: 'failed', error: 'failed', dead: 'failed'};
  const statusStyle = status => {
    const key = String(status || '').toLowerCase();
    const style = STATUS_STYLE[key] || {glyph: '○', tone: 'neutral', label: key ? key[0].toUpperCase() + key.slice(1) : 'Unknown'};
    return {...style, sign: STATUS_SIGN[key] || 'pending'};
  };
  const statusSign = status => {
    const style = statusStyle(status);
    return `<span class="w-sign" data-status="${style.sign}" aria-hidden="true">${style.glyph}</span>`;
  };
  // The status word: attention / danger ink only when it asks for your eyes.
  const statusWord = status => {
    const style = statusStyle(status);
    const tone = style.sign === 'failed' ? ' data-tone="danger"' : style.sign === 'waiting' ? ' data-tone="attention"' : '';
    return `<span class="w-env-status"${tone}>${esc(style.label)}</span>`;
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
        <span class="w-env-jobs__used" data-template-used="${used}">Used by ${used} job${used === 1 ? '' : 's'}</span><span class="w-env-jobs__meta">${esc(meta)}</span></summary>
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
    if (!state.opened) {
      state.opened = true;
      if (!selectFromUrl()) autoSelect();
    } else if (!selectedExists()) {
      state.selected = null;
      autoSelect();
    }
    render();
    startStatsPolling();
  }

  window.addEventListener('hashchange', () => { if ((location.hash || '').startsWith('#template-')) { selectFromUrl(); render(); } });

  // ---------------------------------------------------------------- selection (Mu §11b)
  // One thing is chosen at a time: an environment or a template. MAIN shows it,
  // ASIDE its context. A row you pick goes in ?open= (a reload reopens it); the
  // first environment is chosen on arrival without touching the URL.
  const panes = () => $('env-panes');
  function openParam() { try { return new URL(location.href).searchParams.get('open'); } catch (_) { return null; } }
  function setOpenParam(value) {
    try {
      const url = new URL(location.href);
      if (value) url.searchParams.set('open', value); else url.searchParams.delete('open');
      history.replaceState(history.state, '', url.pathname + url.search + url.hash);
    } catch (_) { /* no URL support */ }
  }
  function selectedExists() {
    const sel = state.selected;
    if (!sel) return false;
    return sel.kind === 'template' ? state.templates.some(item => item.name === sel.name) : state.containers.some(item => item.name === sel.name);
  }
  // ?open=<name> | ?open=template:<name> | #template-<name> (the job page's link).
  function selectFromUrl() {
    const param = openParam();
    const hash = decodeURIComponent((location.hash || '').replace(/^#/, ''));
    if (param && param.startsWith('template:')) {
      const name = param.slice('template:'.length);
      if (state.templates.some(item => item.name === name)) { state.selected = {kind: 'template', name}; return true; }
    } else if (param && state.containers.some(item => item.name === param)) {
      state.selected = {kind: 'env', name: param}; return true;
    } else if (hash.startsWith('template-')) {
      const name = hash.slice('template-'.length);
      if (state.templates.some(item => item.name === name)) { state.selected = {kind: 'template', name}; return true; }
    }
    return false;
  }
  function autoSelect() {
    if (state.containers.length) state.selected = {kind: 'env', name: state.containers[0].name};
    else if (state.templates.length) state.selected = {kind: 'template', name: state.templates[0].name};
    else state.selected = null;
  }
  // `pick`: the user chose it (URL + narrow screens show the main pane).
  function select(kind, name, {pick = false, show = true} = {}) {
    state.selected = {kind, name};
    if (pick) {
      setOpenParam(kind === 'template' ? `template:${name}` : name);
      if (show) panes().setAttribute('data-show', 'main');
    }
    renderMain();
    renderAside();
    markSelected();
  }

  // The state sentence under the page title (Mu §4.2): "2 running · 1 stopped ·
  // 1 template", zeros left out. Written through WashHead when the frame has it.
  function renderState() {
    const running = state.containers.filter(ref => ref.status === 'running').length;
    const other = state.containers.length - running;
    const templates = state.templates.length;
    const parts = [
      running && {text: `${running} running`},
      other && {text: `${other} stopped`, tone: 'quiet'},
      templates && {text: `${templates} template${templates === 1 ? '' : 's'}`},
    ].filter(Boolean);
    if (window.WashHead) { window.WashHead.set({state: parts}); return; }
    const el = $('environment-state');
    if (el) el.innerHTML = parts.map(part => `<span class="w-state__part"${part.tone ? ` data-tone="${part.tone}"` : ''}>${esc(part.text)}</span>`).join('');
  }

  // A list row's second line (Mu §5.1): the state in words, then a live CPU meter
  // and memory once a sample exists — never a "waiting for a sample" placeholder.
  function rowSub(ref) {
    const parts = [statusWord(ref.status)];
    if ((ref.job_scan_blocks || []).length) parts.push('<span data-tone="attention">Secret scan blocked a job</span>');
    const live = state.stats[ref.name];
    if (live && live.status === 'running') {
      const cpu = Number(live.cpu_percent || 0);
      parts.push(`<span class="w-env-rowmeter"><span class="w-bar" style="--v: ${Math.min(100, cpu).toFixed(0)}%" data-tone="${cpu >= 85 ? 'danger' : 'running'}" role="img" aria-label="CPU ${cpu.toFixed(0)}%"></span>${cpu.toFixed(0)}%</span>`);
      parts.push(formatBytes(live.memory_used_bytes));
    } else if (live && live.storage_writable_bytes) {
      parts.push(`${formatBytes(live.storage_writable_bytes)} retained`);
    } else {
      const sessions = (ref.attached_sessions || []).length;
      if (sessions) parts.push(`${sessions} session${sessions === 1 ? '' : 's'}`);
    }
    return parts.join(' · ');
  }

  function envRow(ref) {
    const blocked = (ref.job_scan_blocks || []).length > 0;
    const selected = state.selected && state.selected.kind === 'env' && state.selected.name === ref.name;
    return `<li class="w-row w-env-item" data-name="${esc(ref.name)}"${selected ? ' data-selected' : ''}>
        ${blocked ? '<span class="w-sign" data-status="waiting" aria-hidden="true">◆</span>' : statusSign(ref.status)}
        <button type="button" class="w-row__main" data-card-action="open" data-name="${esc(ref.name)}" aria-label="Open environment ${esc(ref.name)}"${selected ? ' aria-current="true"' : ''}>
          <span class="w-row__title">${esc(ref.name)}</span>
          <span class="w-row__sub" data-stats-line="${esc(ref.name)}">${rowSub(ref)}</span>
        </button>
      </li>`;
  }

  // The ⋯ menu on the main pane's head: the actions that aren't buttons there (Mu §7.3).
  function overflowMenu(ref) {
    return `<details class="w-env-overflow">
          <summary class="w-btn" data-variant="icon" data-size="sm" title="More actions" aria-label="More actions for ${esc(ref.name)}">${icon('more-horizontal', 'sm')}</summary>
          <div class="w-menu w-env-overflow__menu" role="menu" aria-label="Actions for ${esc(ref.name)}">
            <button type="button" class="w-menu__item" role="menuitem" data-action="edit" data-name="${esc(ref.name)}">${icon('sliders-horizontal')}<span class="w-menu__label">Configure</span></button>
            <button type="button" class="w-menu__item" role="menuitem" data-action="restart" data-name="${esc(ref.name)}">${icon('rotate-ccw')}<span class="w-menu__label">Restart</span></button>
            <button type="button" class="w-menu__item" role="menuitem" data-action="clone" data-name="${esc(ref.name)}">${icon('copy')}<span class="w-menu__label">Clone</span></button>
            <button type="button" class="w-menu__item" role="menuitem" data-action="attach" data-name="${esc(ref.name)}">${icon('link')}<span class="w-menu__label">Attach session</span></button>
            <button type="button" class="w-menu__item" role="menuitem" data-action="snapshot" data-name="${esc(ref.name)}">${icon('layers')}<span class="w-menu__label">Create template</span></button>
            <hr class="w-menu__separator">
            <button type="button" class="w-menu__item" role="menuitem" data-tone="danger" data-action="remove" data-name="${esc(ref.name)}">${icon('trash-2')}<span class="w-menu__label">Remove environment</span></button>
          </div>
        </details>`;
  }

  function templateRow(item) {
    const used = Number(item.jobs_using || 0);
    const sub = [used ? `used by ${used} job${used === 1 ? '' : 's'}` : '', item.source_container ? `from ${esc(item.source_container)}` : esc(item.description || '')].filter(Boolean).join(' · ');
    const selected = state.selected && state.selected.kind === 'template' && state.selected.name === item.name;
    return `<li class="w-row w-env-item w-env-item--template" id="template-${esc(item.name)}" data-name="${esc(item.name)}"${selected ? ' data-selected' : ''}>
        <span class="w-sign" data-status="pending" aria-hidden="true">○</span>
        <button type="button" class="w-row__main" data-card-action="template" data-name="${esc(item.name)}" aria-label="Open template ${esc(item.name)}"${selected ? ' aria-current="true"' : ''}>
          <span class="w-row__title">${esc(item.name)}</span>
          <span class="w-row__sub">${sub || 'Template'}</span>
        </button>
      </li>`;
  }

  function render() {
    renderState();
    $('container-list-empty').hidden = state.containers.length > 0;
    $('container-list').hidden = state.containers.length === 0;
    $('container-list').innerHTML = state.containers.map(envRow).join('');
    $('template-empty').hidden = state.templates.length > 0;
    $('template-list').hidden = state.templates.length === 0;
    $('template-list').innerHTML = state.templates.map(templateRow).join('');
    renderMain();
    renderAside();

    $('environment-template').innerHTML = state.templates.map(item => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('');
    const option = $('environment-source').querySelector('option[value="template"]');
    if (option) option.disabled = state.templates.length === 0;
  }

  function markSelected() {
    document.querySelectorAll('#container-manager .w-env-item').forEach(row => {
      const kind = row.classList.contains('w-env-item--template') ? 'template' : 'env';
      const on = !!(state.selected && state.selected.kind === kind && state.selected.name === row.dataset.name);
      row.toggleAttribute('data-selected', on);
      const main = row.querySelector('.w-row__main');
      if (main) { if (on) main.setAttribute('aria-current', 'true'); else main.removeAttribute('aria-current'); }
    });
  }

  // ---------------------------------------------------------------- MAIN: the chosen thing
  const CHARTS = [
    {key: 'cpu', label: 'CPU'},
    {key: 'mem', label: 'Memory'},
    {key: 'net', label: 'Network', legend: [['rx', '↓ in'], ['tx', '↑ out']]},
    {key: 'io', label: 'Disk I/O', legend: [['r', 'read'], ['w', 'write']]},
  ];
  function envHead(ref) {
    const running = ref.status === 'running';
    return `<header class="w-env-head">
        <button type="button" class="w-textbtn w-env-back" data-env-back>${icon('arrow-left', 'sm')}<span>Environments</span></button>
        <div class="w-env-head__title">
          <h2 class="w-env-title">${esc(ref.name)}</h2>
          <p class="w-state" id="env-main-state">${envState(ref)}</p>
        </div>
        <div class="w-env-head__acts">
          <button type="button" class="w-textbtn" data-action="shell" data-name="${esc(ref.name)}">${icon('terminal', 'sm')}<span>Terminal</span></button>
          <button type="button" class="w-textbtn" data-action="${running ? 'stop' : 'start'}" data-name="${esc(ref.name)}">${running ? 'Stop' : 'Start'}</button>
          <button type="button" class="w-textbtn" data-action="edit" data-name="${esc(ref.name)}">Configure</button>
          ${overflowMenu(ref)}
          <button type="button" class="w-textbtn w-env-aside-toggle" data-tone="quiet" data-env-aside-toggle aria-controls="env-aside" aria-expanded="false">Details</button>
        </div>
      </header>`;
  }
  function envState(ref) {
    const live = state.stats[ref.name];
    const parts = [`<span class="w-state__part">${statusWord(ref.status)}</span>`];
    if (live && live.status === 'running' && live.uptime_seconds) parts.push(`<span class="w-state__part">up ${esc(formatDuration(live.uptime_seconds))}</span>`);
    const users = (ref.attached_sessions || []).length;
    if (users) parts.push(`<span class="w-state__part">used by ${esc(ref.attached_sessions.slice(0, 2).join(', '))}${users > 2 ? ` +${users - 2}` : ''}</span>`);
    if (ref.jobs_using) parts.push(`<span class="w-state__part w-env-jobs-badge" title="Non-finished jobs whose build environments snapshot this environment">used by ${Number(ref.jobs_using)} job${ref.jobs_using === 1 ? '' : 's'}</span>`);
    return parts.join('');
  }
  function envMain(ref) {
    return `${envHead(ref)}
      ${(ref.job_scan_blocks || []).map(block => `<div class="w-decision w-env-scan-warning" role="note"><p class="w-decision__q">Secret scan blocked job <a href="/work#/job/${esc(block.job_id)}">#${esc(String(block.job_id).slice(0, 8))}</a></p><p class="w-decision__r">Build environment ${esc(block.binding)}: remove these from the environment, or allow them in Work › Settings › Build environments.</p><pre>${(block.paths || []).map(path => esc(path)).join('\n')}</pre></div>`).join('')}
      <section class="w-env-monitor" data-stats-name="${esc(ref.name)}" aria-label="Live resource use for ${esc(ref.name)}">
        <div class="w-env-charts">${CHARTS.map(chart => `
          <figure class="w-env-chart" data-chart="${chart.key}">
            <figcaption class="w-env-chart__cap"><span class="w-env-chart__label">${chart.label}</span><strong class="w-env-chart__value" data-chart-value></strong></figcaption>
            ${chart.legend ? `<p class="w-env-chart__legend">${chart.legend.map(([series, text]) => `<span data-series="${series}"><i aria-hidden="true"></i>${text}</span>`).join('')}</p>` : ''}
            <div class="w-env-chart__plot" data-chart-plot></div>
          </figure>`).join('')}
        </div>
        <dl class="w-facts w-env-monitor__grid" data-monitor-facts></dl>
        <p class="w-env-monitor__note" data-monitor-note hidden></p>
      </section>`;
  }
  function templateMain(item) {
    openJobs.add(item.name);  // in the main pane the editor is the point of the page: expanded
    return `<header class="w-env-head">
        <button type="button" class="w-textbtn w-env-back" data-env-back>${icon('arrow-left', 'sm')}<span>Environments</span></button>
        <div class="w-env-head__title">
          <h2 class="w-env-title">${esc(item.name)}</h2>
          <p class="w-state"><span class="w-state__part">Template</span>${item.description ? `<span class="w-state__part">${esc(item.description)}</span>` : ''}</p>
        </div>
        <div class="w-env-head__acts">
          <button type="button" class="w-textbtn" data-template-action="use" data-name="${esc(item.name)}">Use as a base</button>
          <button type="button" class="w-textbtn w-env-aside-toggle" data-tone="quiet" data-env-aside-toggle aria-controls="env-aside" aria-expanded="false">Details</button>
        </div>
      </header>
      <div class="w-env-template-jobs">${templateJobsSection(item)}</div>`;
  }
  function selectedRef() {
    const sel = state.selected;
    if (!sel) return null;
    return sel.kind === 'template' ? state.templates.find(item => item.name === sel.name) : state.containers.find(item => item.name === sel.name);
  }
  // MAIN is rebuilt only when the chosen thing (or its shape) changes, so the live
  // charts, an open ⋯ menu and the template editor's draft survive polling. The
  // head holds every action (Terminal · Start/Stop · Configure · ⋯); the aside is
  // context only (sessions, configuration), so each action has one home.
  function renderMain() {
    const body = $('env-main-body');
    const ref = selectedRef();
    const empty = !state.containers.length && !state.templates.length;
    $('container-empty').hidden = !empty;
    body.hidden = !ref;
    if (!ref) { body.innerHTML = ''; state.mainKey = ''; syncTerminal(); return; }
    const sel = state.selected;
    const key = sel.kind === 'template'
      ? `t:${ref.name}:${ref.jobs_using || 0}:${JSON.stringify(ref.job_setup || [])}:${JSON.stringify(ref.egress_allow || [])}`
      : `e:${ref.name}:${ref.status}:${JSON.stringify(ref.job_scan_blocks || [])}`;
    if (key !== state.mainKey) {
      state.mainKey = key;
      body.innerHTML = sel.kind === 'template' ? templateMain(ref) : envMain(ref);
      if (sel.kind === 'template') bindTemplateJobs();
      else renderStats();
    } else if (sel.kind === 'env') {
      const stateEl = $('env-main-state');
      if (stateEl) stateEl.innerHTML = envState(ref);
    }
    syncTerminal();
  }

  // ---------------------------------------------------------------- ASIDE: context
  function renderAside() {
    const aside = $('env-aside');
    const ref = selectedRef();
    if (!ref) { aside.innerHTML = ''; return; }
    if (state.selected.kind === 'template') {
      const used = Number(ref.jobs_using || 0);
      aside.innerHTML = `<header class="w-pane__head"><h2 class="w-pane__title">Template</h2></header>
        <dl class="w-facts w-env-meta"><dt>Source</dt><dd class="w-mono">${esc(ref.source_container || '—')}</dd><dt>Image</dt><dd class="w-mono">${esc(ref.image || '—')}</dd><dt>Jobs</dt><dd>${used ? `used by ${used} job${used === 1 ? '' : 's'}` : 'not used by a job'}</dd></dl>
        <div class="w-env-aside__acts">
          <button type="button" class="w-textbtn" data-template-action="use" data-name="${esc(ref.name)}">Create an environment from it</button>
          <button type="button" class="w-textbtn" data-tone="danger" data-template-action="remove" data-name="${esc(ref.name)}">Delete template</button>
        </div>`;
      return;
    }
    const sessions = (ref.attached_sessions || []).map(name =>
      `<li class="w-env-session"><span>${esc(name)}</span><button type="button" class="w-textbtn" data-tone="quiet" data-action="detach" data-name="${esc(ref.name)}" data-session="${esc(name)}" title="Detach session" aria-label="Detach ${esc(name)}">Detach</button></li>`
    ).join('');
    aside.innerHTML = `
      <section aria-labelledby="env-aside-sessions">
        <header class="w-pane__head"><h2 class="w-pane__title" id="env-aside-sessions">Sessions</h2></header>
        ${sessions ? `<ul class="w-env-sessions">${sessions}</ul>` : '<p class="w-group__empty">No sessions attached.</p>'}
        <button type="button" class="w-textbtn" data-action="attach" data-name="${esc(ref.name)}">Attach a session</button>
      </section>
      <section aria-labelledby="env-aside-config">
        <h2 class="w-pane__title w-env-aside__title" id="env-aside-config">Configuration</h2>
        <dl class="w-facts w-env-meta"><dt>Template</dt><dd>${esc(ref.template_name || 'custom')}</dd><dt>Worker</dt><dd class="w-mono">${esc(ref.worker_port || '—')}</dd>${ref.jobs_using ? `<dt>Jobs</dt><dd>used by ${Number(ref.jobs_using)}</dd>` : ''}</dl>
        <details class="w-disclose"><summary>Details</summary>
          <dl class="w-facts w-env-meta"><dt>Image</dt><dd class="w-mono">${esc(ref.image)}</dd><dt>Network</dt><dd class="w-mono">${esc(ref.network_name || '—')}</dd></dl>
        </details>
      </section>`;
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

  // ---------------------------------------------------------------- live samples
  // A rolling buffer per environment (the last ~3 minutes of /api/containers/stats
  // polls) feeds the charts; disk I/O is the rate between cumulative samples.
  const HISTORY = 72;
  function recordSamples() {
    const now = Date.now();
    Object.entries(state.stats).forEach(([name, value]) => {
      if (!value || value.status !== 'running') { delete state.history[name]; return; }
      const series = state.history[name] || (state.history[name] = []);
      const prev = series[series.length - 1];
      const br = Number(value.block_read_bytes || 0), bw = Number(value.block_write_bytes || 0);
      const dt = prev ? Math.max(0.5, (now - prev.t) / 1000) : 0;
      series.push({
        t: now,
        cpu: Number(value.cpu_percent || 0),
        mem: Number(value.memory_used_bytes || 0),
        memLimit: Number(value.memory_limit_bytes || 0),
        rx: Number(value.network_rx_bytes_per_second || 0),
        tx: Number(value.network_tx_bytes_per_second || 0),
        br, bw,
        r: prev && dt ? Math.max(0, (br - prev.br) / dt) : 0,
        w: prev && dt ? Math.max(0, (bw - prev.bw) / dt) : 0,
      });
      if (series.length > HISTORY) series.splice(0, series.length - HISTORY);
    });
  }
  const CHART_W = 320, CHART_H = 96;
  // One chart = one axis (0 → its own max), thin 2px lines, a faint area under a
  // single series, the latest value as text. Hover reads any sample back.
  function chartSvg(points, lines, max) {
    const n = points.length;
    const x = i => n <= 1 ? CHART_W : (i / (n - 1)) * CHART_W;
    const y = v => CHART_H - 2 - (max > 0 ? (v / max) * (CHART_H - 6) : 0);
    const paths = lines.map(({key}) => points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(''));
    const area = lines.length === 1 && n > 1 ? `<path class="w-env-chart__area" d="${paths[0]}L${x(n - 1).toFixed(1)},${CHART_H}L${x(0).toFixed(1)},${CHART_H}Z"></path>` : '';
    return `<svg viewBox="0 0 ${CHART_W} ${CHART_H}" preserveAspectRatio="none" role="img" aria-hidden="true">
      <line class="w-env-chart__base" x1="0" y1="${CHART_H - 1}" x2="${CHART_W}" y2="${CHART_H - 1}"></line>
      ${area}${lines.map((line, i) => `<path class="w-env-chart__line" data-series="${line.key}" d="${paths[i]}"></path>`).join('')}
      <line class="w-env-chart__cursor" x1="0" x2="0" y1="0" y2="${CHART_H}" hidden></line>
    </svg>`;
  }
  function chartModel(key, series, value) {
    const last = series[series.length - 1] || {};
    if (key === 'cpu') {
      const max = Math.max(100, ...series.map(p => p.cpu));
      return {lines: [{key: 'cpu'}], max, text: p => `${p.cpu.toFixed(1)}%`, value: `${Number(value.cpu_percent || 0).toFixed(1)}%`};
    }
    if (key === 'mem') {
      const limit = last.memLimit || Math.max(...series.map(p => p.mem), 1);
      return {lines: [{key: 'mem'}], max: limit, text: p => formatBytes(p.mem), value: `${formatBytes(value.memory_used_bytes)}${value.memory_limit_bytes ? ` of ${formatBytes(value.memory_limit_bytes)}` : ''}`};
    }
    if (key === 'net') {
      const max = Math.max(1, ...series.map(p => Math.max(p.rx, p.tx)));
      return {lines: [{key: 'rx'}, {key: 'tx'}], max, text: p => `↓ ${formatRate(p.rx)} · ↑ ${formatRate(p.tx)}`, value: `↓ ${formatRate(value.network_rx_bytes_per_second)} · ↑ ${formatRate(value.network_tx_bytes_per_second)}`};
    }
    const max = Math.max(1, ...series.map(p => Math.max(p.r, p.w)));
    return {lines: [{key: 'r'}, {key: 'w'}], max, text: p => `read ${formatRate(p.r)} · write ${formatRate(p.w)}`, value: `read ${formatBytes(value.block_read_bytes)} · written ${formatBytes(value.block_write_bytes)}`};
  }

  // Live numbers (Mu §5.5): list rows, the chosen environment's state line, charts
  // and facts. Before the first sample nothing is drawn.
  function renderStats() {
    document.querySelectorAll('[data-stats-line]').forEach(line => {
      const ref = state.containers.find(item => item.name === line.dataset.statsLine);
      if (ref) line.innerHTML = rowSub(ref);
    });
    const ref = selectedRef();
    if (ref && state.selected.kind === 'env' && $('env-main-state')) $('env-main-state').innerHTML = envState(ref);
    document.querySelectorAll('[data-stats-name]').forEach(root => {
      const name = root.dataset.statsName;
      const value = state.stats[name];
      root.hidden = !value;
      if (!value) return;
      const running = value.status === 'running';
      const series = state.history[name] || [];
      const charts = root.querySelector('.w-env-charts');
      charts.hidden = !running;
      if (running) {
        root.querySelectorAll('[data-chart]').forEach(fig => {
          const model = chartModel(fig.dataset.chart, series, value);
          fig._model = model;
          fig._series = series;
          if (!fig._hover) fig.querySelector('[data-chart-value]').textContent = model.value;
          fig.querySelector('[data-chart-plot]').innerHTML = series.length > 1 ? chartSvg(series, model.lines, model.max) : '<p class="w-env-chart__wait">Collecting samples…</p>';
        });
      }
      const gpu = value.gpu || {};
      const gpuValue = gpu.requested
        ? gpu.utilization_percent == null ? 'N/A' : `${Number(gpu.utilization_percent).toFixed(1)}%`
        : '';
      const rows = running ? [
        ['Processes', `${value.pids || 0}`],
        value.uptime_seconds ? ['Uptime', formatDuration(value.uptime_seconds)] : null,
        value.restart_count ? ['Restarts', `${value.restart_count}`] : null,
        gpuValue && ['GPU', `${gpuValue}${gpu.memory_total_bytes ? ` · ${formatBytes(gpu.memory_used_bytes)} of ${formatBytes(gpu.memory_total_bytes)}` : ''}`],
        ['Storage', `${formatBytes(value.storage_writable_bytes)} writable`],
      ] : [
        ['Storage', `${formatBytes(value.storage_writable_bytes)} retained`],
      ];
      root.querySelector('[data-monitor-facts]').innerHTML = rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
      const note = root.querySelector('[data-monitor-note]');
      const notes = [gpu.requested ? 'GPU values are totals for the assigned physical device.' : '', value.error ? `✗ ${value.error}` : ''].filter(Boolean);
      note.hidden = !notes.length;
      note.textContent = notes.join(' ');
      note.classList.toggle('w-env-monitor__error', !!value.error);
    });
  }

  // Hover reads a sample back into the chart's value (crosshair + text; Mu §9).
  document.addEventListener('pointermove', event => {
    const plot = event.target.closest && event.target.closest('[data-chart-plot]');
    document.querySelectorAll('.w-env-chart').forEach(fig => {
      if (plot && fig.contains(plot)) return;
      if (fig._hover) { fig._hover = false; const cursor = fig.querySelector('.w-env-chart__cursor'); if (cursor) cursor.setAttribute('hidden', ''); if (fig._model) fig.querySelector('[data-chart-value]').textContent = fig._model.value; }
    });
    if (!plot) return;
    const fig = plot.closest('.w-env-chart');
    const series = fig._series || [];
    if (series.length < 2 || !fig._model) return;
    const box = plot.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const index = Math.max(0, Math.min(series.length - 1, Math.round(frac * (series.length - 1))));
    const sample = series[index];
    fig._hover = true;
    const age = Math.round((Date.now() - sample.t) / 1000);
    fig.querySelector('[data-chart-value]').textContent = `${fig._model.text(sample)} · ${age ? `${age}s ago` : 'now'}`;
    const cursor = fig.querySelector('.w-env-chart__cursor');
    if (cursor) {
      const xPos = (index / (series.length - 1)) * CHART_W;
      cursor.setAttribute('x1', xPos); cursor.setAttribute('x2', xPos); cursor.removeAttribute('hidden');
    }
  });

  async function pollStats() {
    try {
      const response = await api('/api/containers/stats');
      state.stats = response.containers || {};
      recordSamples();
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
    $('environment-modal-title').textContent = 'Create environment';
    $('environment-modal-description').textContent = 'Standalone containers are not tied to a session.';
    setSubmitLabel('Create');
    syncSource(); renderMounts(); renderHardware(); updateSummaries(); goStep(0);
  }

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
      ['Folders', body.mounts.length ? `${body.mounts.length} folder${body.mounts.length === 1 ? '' : 's'}` : 'none'],
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
      $('environment-modal-title').textContent = `Edit ${sourceName}`;
      $('environment-modal-description').textContent = 'Saving recreates the worker while retaining named volumes and attached sessions.';
      setSubmitLabel('Save and recreate');
    } else if (clone) {
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
    // Zeros are left out (Mu §5.5): "No GPU", "All GPUs · 2 devices".
    const devices = state.devices.length ? ` · ${state.devices.length} device${state.devices.length === 1 ? '' : 's'}` : '';
    $('hardware-summary').textContent = `${gpuText.charAt(0).toUpperCase()}${gpuText.slice(1)}${devices}`;
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
    const allowed = splitLines($('environment-allow').value).length;
    const blocked = splitLines($('environment-deny').value).length;
    $('network-summary').textContent = [allowed && `${allowed} allowed`, blocked && `${blocked} blocked`].filter(Boolean).join(' · ') || 'No outbound domains';
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

  // The terminal: inline in the main pane when it is showing beside the list (from
  // 840px), the #shell-modal dialog on phones. One shell body moves between them,
  // so its listeners, history and output come along.
  const inlineShell = () => !window.matchMedia || window.matchMedia('(width >= 840px)').matches;
  function placeShellBody(inline) {
    const body = document.querySelector('.w-env-shell__body');
    const target = inline ? $('env-terminal-host') : $('shell-modal').querySelector('.w-modal__dialog');
    if (body && body.parentElement !== target) target.appendChild(body);
  }
  // Show the inline terminal only under the environment it is connected to.
  function syncTerminal() {
    const host = $('env-terminal-host');
    const on = state.shellInline && state.shellContainer && state.selected && state.selected.kind === 'env' && state.selected.name === state.shellContainer;
    host.hidden = !on;
  }
  function closeShell() {
    if (state.shell) state.shell.close();
    state.shellInline = false;
    syncTerminal();
    placeShellBody(false);
  }
  function openShell(name) {
    if (state.shell) state.shell.close();
    $('shell-title').textContent = `Shell · ${name}`;
    $('env-terminal-title').textContent = `Terminal · ${name}`;
    $('shell-output').textContent = '';
    state.shellFollow = true;
    loadShellHistory(name);
    state.shellInline = inlineShell();
    placeShellBody(state.shellInline);
    if (state.shellInline) {
      if (!state.selected || state.selected.kind !== 'env' || state.selected.name !== name) select('env', name, {pick: true});
      syncTerminal();
      $('env-terminal-host').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    } else {
      show('shell-modal');
    }
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

  // Below 1200px the aside opens over the main pane on demand.
  function toggleAside(force) {
    const open = force == null ? panes().getAttribute('data-aside') !== 'open' : force;
    if (open) panes().setAttribute('data-aside', 'open'); else panes().removeAttribute('data-aside');
    document.querySelectorAll('[data-env-aside-toggle]').forEach(button => button.setAttribute('aria-expanded', String(open)));
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
    const opener = event.target.closest('[data-card-action]');
    if (opener) { select(opener.dataset.cardAction === 'template' ? 'template' : 'env', opener.dataset.name, {pick: true}); return; }
    if (event.target.closest('[data-env-back]')) { panes().setAttribute('data-show', 'list'); return; }
    const asideToggle = event.target.closest('[data-env-aside-toggle]');
    if (asideToggle) { toggleAside(); return; }
    if (panes().getAttribute('data-aside') === 'open' && !event.target.closest('#env-aside')) toggleAside(false);
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

  // j / k move between rows (Mu §9.6) and choose the focused one; Enter opens it on
  // narrow screens. The dialogs carry aria-modal statically, so only a visible one
  // blocks the keys.
  const modalOpen = () => [...document.querySelectorAll('[aria-modal="true"]')].some(el => el.getClientRects().length > 0);
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key !== 'j' && event.key !== 'k') return;
    if (event.target.closest('input, textarea, select, [contenteditable="true"]') || modalOpen()) return;
    const rows = [...document.querySelectorAll('#env-panes [data-pane="list"] .w-row__main')];
    if (!rows.length) return;
    const at = rows.indexOf(document.activeElement);
    const next = event.key === 'j' ? Math.min(rows.length - 1, at + 1) : Math.max(0, at < 0 ? 0 : at - 1);
    event.preventDefault();
    rows[next].focus();
    select(rows[next].dataset.cardAction === 'template' ? 'template' : 'env', rows[next].dataset.name, {pick: true, show: false});
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
      {id: 'env:templates', group: 'Navigate', label: 'Show templates', icon: 'layers', run: () => { panes().setAttribute('data-show', 'list'); $('templates-title').scrollIntoView({block: 'start'}); }},
      ...state.containers.map(ref => ({
        id: `env:open:${ref.name}`, group: 'Environments', label: ref.name, icon: 'box',
        keywords: `${ref.status || ''} environment container configure`, run: () => select('env', ref.name, {pick: true}),
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
  $('env-terminal-close').addEventListener('click', closeShell);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && panes().getAttribute('data-aside') === 'open' && !modalOpen()) toggleAside(false); });
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
