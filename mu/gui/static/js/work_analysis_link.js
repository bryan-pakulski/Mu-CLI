(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    function selectedJobId() {
        return String(document.querySelector('.work-job-card.is-selected[data-job-id]')?.dataset.jobId || '');
    }

    function enhance() {
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
                group.style.display = 'inline-flex';
                group.style.alignItems = 'center';
                group.style.gap = '10px';
                right.replaceWith(group);
                group.appendChild(link);
                group.appendChild(right);
            } else {
                header.appendChild(link);
            }
        }
        link.href = `/static/job_trace.html?job=${encodeURIComponent(jobId)}`;
    }

    new MutationObserver(enhance).observe(detail, { childList: true, subtree: true });
    document.addEventListener('click', event => {
        if (event.target.closest('[data-job-id]')) setTimeout(enhance, 40);
    }, true);
    enhance();
})();
