(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    let costJobId = '';
    let costInFlight = false;

    const icons = {
        analyze: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V9"></path><path d="M10 19V5"></path><path d="M16 19v-7"></path><path d="M22 19V3"></path><path d="M2 19h22"></path></svg>',
        pricing: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M16 8.5c-.8-.8-2-1.3-3.5-1.3-2 0-3.5 1-3.5 2.5 0 3.8 7 1.8 7 5.2 0 1.4-1.5 2.4-3.7 2.4-1.6 0-3-.5-4-1.4M12 5v14"></path></svg>',
        manage: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16v13H4z"></path><path d="M3 4h18v3H3z"></path><path d="M9 11h6"></path></svg>',
        refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6v5h-5"></path><path d="M19 11a7.5 7.5 0 1 0 .3 4.3"></path></svg>',
        moon: '<svg data-theme-dark viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 15.2A8.3 8.3 0 0 1 8.8 4 8.5 8.5 0 1 0 20 15.2Z"></path></svg>',
        sun: '<svg data-theme-light viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>',
        plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg>',
        back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"></path><path d="m12 19-7-7 7-7"></path></svg>',
    };

    function installPolishStyles() {
        if (document.getElementById('work-polish-css')) return;
        const link = document.createElement('link');
        link.id = 'work-polish-css';
        link.rel = 'stylesheet';
        link.href = '/static/css/work_polish.css';
        document.head.appendChild(link);
    }

    function syncThemeButton(button) {
        if (!button) return;
        const dark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';
        button.querySelector('[data-theme-dark]')?.toggleAttribute('hidden', !dark);
        button.querySelector('[data-theme-light]')?.toggleAttribute('hidden', dark);
    }

    function toggleTheme(button) {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('mucli-theme', next); } catch (_) {}
        const darkSheet = document.getElementById('hljs-dark');
        const lightSheet = document.getElementById('hljs-light');
        if (darkSheet) darkSheet.disabled = next === 'light';
        if (lightSheet) lightSheet.disabled = next === 'dark';
        syncThemeButton(button);
    }

    function iconify(node, icon, title) {
        if (!node) return;
        node.classList.remove('work-manage-button', 'work-icon-button');
        node.classList.add('product-icon-button', 'work-header-icon');
        node.innerHTML = icon;
        node.title = title;
        node.setAttribute('aria-label', title);
    }

    function enhanceChrome() {
        installPolishStyles();
        const app = document.getElementById('work-app');
        const header = document.querySelector('.work-header');
        if (!app || !header || header.dataset.productChrome === '1') return;
        app.classList.add('product-app', 'work-product-app');
        header.classList.add('header', 'product-header', 'work-product-header');
        header.dataset.productChrome = '1';

        const brand = header.querySelector('.work-brand');
        if (brand) {
            brand.className = 'work-product-brand';
            brand.innerHTML = `${icons.back}<span class="session-title-wrap"><span class="session-title">Engineering work</span><span class="session-subtitle">MuCLI · delegated engineering jobs</span></span>`;
            brand.title = 'Back to MuCLI';
        }

        const actions = header.querySelector('.work-header-actions');
        actions?.classList.add('header-actions', 'work-product-actions');
        const actionLinks = actions ? [...actions.querySelectorAll('a.work-manage-button')] : [];
        iconify(actionLinks.find(link => String(link.getAttribute('href') || '').includes('job_trace')), icons.analyze, 'Analyze jobs');
        iconify(actionLinks.find(link => String(link.getAttribute('href') || '').includes('model_costs')), icons.pricing, 'Model pricing');
        iconify(document.getElementById('work-manage'), icons.manage, 'Manage job history');
        iconify(document.getElementById('work-refresh'), icons.refresh, 'Refresh work queue');

        const newButton = document.getElementById('work-new');
        if (newButton) {
            newButton.classList.remove('work-primary');
            newButton.classList.add('product-header-chip', 'work-new-product');
            newButton.innerHTML = `${icons.plus}<span>New job</span>`;
        }

        if (!document.getElementById('work-theme')) {
            const theme = document.createElement('button');
            theme.id = 'work-theme';
            theme.type = 'button';
            theme.className = 'product-icon-button work-header-icon';
            theme.title = 'Toggle appearance';
            theme.setAttribute('aria-label', 'Toggle appearance');
            theme.innerHTML = `${icons.moon}${icons.sun}`;
            theme.addEventListener('click', () => toggleTheme(theme));
            syncThemeButton(theme);
            if (newButton) actions?.insertBefore(theme, newButton);
            else actions?.appendChild(theme);
        }
    }

    function selectedJobId() {
        return String(document.querySelector('.work-job-card.is-selected[data-job-id]')?.dataset.jobId || '');
    }

    function enhanceAnalysisLink() {
        const jobId = selectedJobId();
        const header = detail.querySelector('.work-detail-header-top');
        if (!jobId || !header) return;
        let link = header.querySelector('.work-job-analyze-link');
        if (!link) {
            link = document.createElement('a');
            link.className = 'work-job-analyze-link';
            link.textContent = 'Analyze trace';
            link.title = 'Open retrospective Job Trace Analyzer';
            const right = header.querySelector('.work-card-cost');
            if (right) {
                const group = document.createElement('span');
                group.className = 'work-detail-head-actions';
                right.replaceWith(group);
                group.appendChild(link);
                group.appendChild(right);
            } else {
                header.appendChild(link);
            }
        }
        link.href = `/static/job_trace.html?job=${encodeURIComponent(jobId)}`;
    }

    function costMetric() {
        return [...detail.querySelectorAll('.work-metric')].find(metric =>
            String(metric.querySelector('.work-metric-label')?.textContent || '').trim() === 'Cost'
        );
    }

    function renderCostSemantics(modelApi) {
        const metric = costMetric();
        const value = metric?.querySelector('.work-metric-value');
        if (!metric || !value || !modelApi) return;

        const status = String(modelApi.status || 'legacy');
        const amount = Number(modelApi.api_cost_usd || 0);
        const unpriced = Number(modelApi.unpriced_attempts || 0);
        const billing = Array.isArray(modelApi.billing_modes) ? modelApi.billing_modes.join(', ') : '';

        metric.dataset.costStatus = status;
        if (status === 'unpriced') {
            value.textContent = 'Unpriced';
            value.title = `No configured/provider estimate is available${billing ? ` (${billing})` : ''}. This is not $0.`;
        } else if (status === 'partial') {
            value.textContent = `$${amount.toFixed(2)} + ?`;
            value.title = `${unpriced} attempt${unpriced === 1 ? '' : 's'} still have unpriced model spend.`;
        } else if (status === 'local_zero') {
            value.textContent = '$0.00 API';
            value.title = 'Local model: $0 attributable provider/API spend. Workspace CPU/GPU cost is separate.';
        } else if (status === 'legacy') {
            value.title = 'Historical/legacy estimate without full pricing provenance.';
        } else {
            value.textContent = `$${amount.toFixed(2)}`;
            value.title = billing.includes('estimated_token')
                ? 'Configured provider estimate based on measured tokens; not an invoice.'
                : 'Attributed model/API spend. Workspace compute/storage/network cost is separate.';
        }
    }

    async function enhanceCost() {
        const jobId = selectedJobId();
        if (!jobId || costInFlight || !costMetric()) return;
        if (costJobId === jobId && costMetric()?.dataset.costSemantics === '1') return;
        costInFlight = true;
        try {
            const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/receipt`);
            if (!response.ok) return;
            const data = await response.json();
            const modelApi = data?.receipt?.usage?.model_api;
            if (!modelApi) return;
            renderCostSemantics(modelApi);
            const metric = costMetric();
            if (metric) metric.dataset.costSemantics = '1';
            costJobId = jobId;
        } catch (_) {
            // Cost provenance is an enhancement; the primary receipt remains authoritative.
        } finally {
            costInFlight = false;
        }
    }

    function enhance() {
        enhanceChrome();
        enhanceAnalysisLink();
        void enhanceCost();
    }

    new MutationObserver(enhance).observe(detail, { childList: true, subtree: true });
    document.addEventListener('click', event => {
        if (event.target.closest('[data-job-id]')) {
            costJobId = '';
            setTimeout(enhance, 40);
        }
    }, true);
    enhance();
})();
