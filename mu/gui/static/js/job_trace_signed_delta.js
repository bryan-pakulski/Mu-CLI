(() => {
    'use strict';

    const TIME_LABELS = new Set(['Wall time', 'Active time', 'Waiting time']);

    function parseDuration(text) {
        const value = String(text || '').replace(/^vs\s+/i, '').trim();
        if (!value || value === '—') return null;
        let seconds = 0;
        let matched = false;
        const hours = value.match(/(-?\d+(?:\.\d+)?)h/);
        const minutes = value.match(/(-?\d+(?:\.\d+)?)m(?!s)/);
        const secs = value.match(/(-?\d+(?:\.\d+)?)s/);
        const millis = value.match(/(-?\d+(?:\.\d+)?)ms/);
        if (hours) { seconds += Number(hours[1]) * 3600; matched = true; }
        if (minutes) { seconds += Number(minutes[1]) * 60; matched = true; }
        if (secs) { seconds += Number(secs[1]); matched = true; }
        if (millis) { seconds += Number(millis[1]) / 1000; matched = true; }
        return matched && Number.isFinite(seconds) ? seconds : null;
    }

    function formatSigned(seconds) {
        if (!Number.isFinite(seconds)) return '—';
        const sign = seconds > 0 ? '+' : seconds < 0 ? '−' : '';
        let value = Math.abs(seconds);
        if (value < 1) return `${sign}${Math.round(value * 1000)}ms`;
        if (value < 60) return `${sign}${value.toFixed(value < 10 ? 1 : 0)}s`;
        const hours = Math.floor(value / 3600);
        value -= hours * 3600;
        const minutes = Math.floor(value / 60);
        const secs = Math.round(value - minutes * 60);
        return hours
            ? `${sign}${hours}h ${minutes}m ${secs}s`
            : `${sign}${minutes}m ${secs}s`;
    }

    function correct() {
        document.querySelectorAll('.jt-compare-metric').forEach(row => {
            const label = row.querySelector('.jt-compare-label')?.textContent?.trim() || '';
            if (!TIME_LABELS.has(label)) return;
            const primary = parseDuration(row.querySelector('.jt-compare-primary')?.textContent);
            const reference = parseDuration(row.querySelector('.jt-compare-ref')?.textContent);
            const delta = row.querySelector('.jt-compare-delta');
            if (primary == null || reference == null || !delta) return;
            delta.textContent = formatSigned(primary - reference);
        });
    }

    const host = document.getElementById('jt-comparison');
    if (!host) return;
    new MutationObserver(correct).observe(host, { childList: true, subtree: true });
    correct();
})();
