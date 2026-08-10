(() => {
    'use strict';

    /*
     * Legacy filename, new responsibility: Job Trace is an individual-task
     * breakdown only. Remove the old comparison controls before they can be
     * used, discard stale ?compare= links, and make a direct analyzer visit
     * immediately open the newest available job instead of an empty shell.
     *
     * job_trace.js starts its async job-list request before this deferred script
     * runs. Its captured compare DOM references remain harmless after removal;
     * clearing the query parameter ensures the old comparison fetch path never
     * runs while the list is loading.
     */
    document.getElementById('jt-compare-select')?.remove();
    document.getElementById('jt-compare-section')?.remove();

    const initialUrl = new URL(window.location.href);
    if (initialUrl.searchParams.has('compare')) {
        initialUrl.searchParams.delete('compare');
        history.replaceState(null, '', initialUrl);
    }

    const jobSelect = document.getElementById('jt-job-select');
    if (!jobSelect) return;

    function requestedJob() {
        return String(new URL(window.location.href).searchParams.get('job') || '').trim();
    }

    function autoLoadFirstJob() {
        if (requestedJob()) return true;
        const first = Array.from(jobSelect.options).find(option => String(option.value || '').trim());
        if (!first) return false;
        jobSelect.value = first.value;
        jobSelect.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }

    // The core analyzer populates the select asynchronously. MutationObserver
    // handles the normal path; the short timer is a defensive fallback for
    // browsers that coalesce option mutations during innerHTML replacement.
    const observer = new MutationObserver(() => {
        if (!requestedJob() && jobSelect.options.length > 1) {
            setTimeout(() => {
                if (autoLoadFirstJob()) observer.disconnect();
            }, 0);
        }
    });
    observer.observe(jobSelect, { childList: true });

    let checks = 0;
    const timer = window.setInterval(() => {
        checks += 1;
        if (autoLoadFirstJob() || checks >= 100) {
            clearInterval(timer);
            observer.disconnect();
        }
    }, 50);
})();
