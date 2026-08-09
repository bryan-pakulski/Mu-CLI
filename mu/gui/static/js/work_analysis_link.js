(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    let costJobId = '';
    let costInFlight = false;

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
        const components = modelApi.cost_components || {};
        const inputCost = Number(components.input_usd || 0);
        const outputCost = Number(components.output_usd || 0);

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
            const estimated = billing.includes('estimated_token');
            value.textContent = estimated ? `~$${amount.toFixed(2)}` : `$${amount.toFixed(2)}`;
            value.title = estimated
                ? `Configured provider estimate: input ~$${inputCost.toFixed(2)} · output ~$${outputCost.toFixed(2)} · total ~$${amount.toFixed(2)}. Not an invoice.`
                : `Attributed model/API spend: input $${inputCost.toFixed(2)} · output $${outputCost.toFixed(2)} · total $${amount.toFixed(2)}. Workspace compute/storage/network cost is separate.`;
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
