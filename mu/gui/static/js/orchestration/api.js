/* Orchestration API client (documentation/plans/orchestration.md §8).
 *
 * Reads are plain GETs under /api/orchestration/*. Every mutation is
 * POST /api/orchestration/cmd {type, args} (plus /plan, /import and the
 * Studio session helper) carrying the X-MuCLI-CSRF header. The token comes
 * from the page (#orch-app[data-csrf]); on a 403 a fresh one is fetched from
 * /api/orchestration/csrf and the request is retried once. A few GETs carry a
 * live join token (the node setup wizard) and send the header too
 * (getSecure, download). Errors surface as Error(detail) with .status set and
 * .data when the server sent it (a 409 save conflict carries the current
 * draft).
 *
 * Live updates: events(handlers) opens the SSE stream; `hello` carries the
 * attention counts, each message is {scope: node|tool|run, id, parts, seq}.
 */
(() => {
    'use strict';
    const BASE = '/api/orchestration';
    const HEADER = 'X-MuCLI-CSRF';
    let token = document.getElementById('orch-app')?.dataset.csrf || '';

    async function freshToken() {
        const res = await fetch(`${BASE}/csrf`, { credentials: 'same-origin', cache: 'no-store' });
        if (!res.ok) throw new Error(`Couldn't refresh the page token (HTTP ${res.status}). Reload the page.`);
        token = (await res.json()).token || '';
        return token;
    }

    function detailOf(body, status) {
        if (!body) return `HTTP ${status}`;
        const d = body.detail;
        if (typeof d === 'string') return d;
        if (Array.isArray(d)) return d.map(x => x.msg || JSON.stringify(x)).join('; ');
        return d ? JSON.stringify(d) : `HTTP ${status}`;
    }

    async function request(method, path, { json, form, retry = true, raw = false, csrf = false, signal } = {}) {
        const headers = {};
        let body;
        if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
        if (form) body = form;
        const secure = method !== 'GET' || csrf;
        if (secure) headers[HEADER] = token;
        const res = await fetch(path.startsWith('/') ? path : `${BASE}/${path}`, { method, headers, body, credentials: 'same-origin', cache: 'no-store', signal });
        if (res.status === 403 && secure && retry) {
            await freshToken();
            return request(method, path, { json, form, retry: false, raw, csrf, signal });
        }
        if (raw) {
            if (!res.ok) {
                let detail = `HTTP ${res.status}`;
                try { detail = detailOf(JSON.parse(await res.text()), res.status); } catch (_) { /* keep */ }
                const err = new Error(detail);
                err.status = res.status;
                throw err;
            }
            return res;
        }
        let data = null;
        const text = await res.text();
        if (text) { try { data = JSON.parse(text); } catch (_) { data = { detail: text }; } }
        if (!res.ok) {
            const err = new Error(detailOf(data, res.status));
            err.status = res.status;
            if (data && data.data !== undefined) err.data = data.data;
            throw err;
        }
        return data;
    }

    const get = (path, opts) => request('GET', path, opts || {});
    const getSecure = path => request('GET', path, { csrf: true });
    const cmd = (type, args = {}, opts = {}) => request('POST', 'cmd', { json: { type, args }, ...opts });
    /* Fetch a CSRF-protected file and hand it to the browser as a download. */
    async function download(path, filename) {
        const res = await request('GET', path, { csrf: true, raw: true });
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename || path.split('/').pop(); a.hidden = true;
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        return { sha256: res.headers.get('X-Content-SHA256') || '', size: blob.size };
    }
    const plan = args => request('POST', 'plan', { json: args });
    const post = (path, json) => request('POST', path, { json });
    function upload(path, file) {
        const form = new FormData();
        form.append('file', file, file.name || 'bundle');
        return request('POST', path, { form });
    }

    /* The live stream (MuLive.stream, mu_live.js): open only while this view is on screen,
     * self-healing (backoff after any error, a watchdog on the server's pings). Every hello after
     * the first means it reconnected: onResync lets the page catch up on what it missed. */
    function events({ onChange, onHello, onState, onResync } = {}) {
        let hellos = 0;
        const handlers = {
            onMessage: ev => { try { onChange?.(JSON.parse(ev.data)); } catch (_) { /* skip */ } },
            events: {
                hello: ev => {
                    try { onHello?.(JSON.parse(ev.data)); } catch (_) { /* ignore a malformed hello */ }
                    if (hellos++ > 0) onResync?.();
                },
            },
            onState,
        };
        if (window.MuLive) return window.MuLive.stream(`${BASE}/events`, handlers);
        const source = new EventSource(`${BASE}/events`);       // no shell script: plain stream
        source.addEventListener('hello', handlers.events.hello);
        source.onmessage = handlers.onMessage;
        source.onopen = () => onState?.(true);
        source.onerror = () => onState?.(false);
        return { close: () => source.close(), reconnect: () => {} };
    }

    window.OrchAPI = { get, getSecure, cmd, plan, post, upload, download, request, events, freshToken, BASE };
})();
