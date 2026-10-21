// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Live streams (static/js/mu_live.js): open only while their page is on screen, self-healing.
// Without this, hidden keep-alive views and background tabs held the browser's few connections
// per host and a reconnecting stream queued forever ("Reconnecting to live updates…").
const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
const source = readFileSync(resolve(root, 'mu/gui/static/js/mu_live.js'), 'utf8');

function page() {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://127.0.0.1:30311/work?embed=1' });
    const w = dom.window;
    const made = [];
    class FakeES {
        constructor(url) { this.url = url; this.readyState = 0; this.listeners = {}; this.closed = false; made.push(this); }
        addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
        close() { this.closed = true; this.readyState = 2; }
        open() { this.readyState = 1; this.onopen && this.onopen(); }
        fail(permanent) { if (permanent) this.readyState = 2; this.onerror && this.onerror(); }
        send(type, data) { const ev = { data: JSON.stringify(data || {}) }; if (type === 'message') this.onmessage(ev); else (this.listeners[type] || []).forEach(f => f(ev)); }
    }
    w.EventSource = FakeES;
    let hidden = false;
    Object.defineProperty(w.document, 'visibilityState', { get: () => (hidden ? 'hidden' : 'visible') });
    w.setTimeout = setTimeout; w.clearTimeout = clearTimeout;
    w.eval(source);
    return {
        w, made,
        setTabHidden(h) { hidden = h; w.document.dispatchEvent(new w.Event('visibilitychange')); },
        // The shell (parent) says whether this view is the active one.
        setViewActive(a) { w.dispatchEvent(new w.MessageEvent('message', { data: { type: 'mu:view', active: a }, origin: 'http://127.0.0.1:30311', source: w.parent })); },
    };
}

test('opens when shown, recovers after a permanent error with backoff, and resyncs', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        const p = page();
        const states = [];
        const hellos = [];
        p.w.MuLive.stream('/api/jobs/events', { onState: s => states.push(s), events: { hello: () => hellos.push(1) } });
        assert.equal(p.made.length, 1);
        p.made[0].open(); p.made[0].send('hello');
        p.made[0].fail(true);                                   // HTTP error: EventSource gives up for good
        assert.equal(states.at(-1), false);
        assert.ok(p.made[0].closed);
        mock.timers.tick(1100);                                  // first retry within ~1 s
        assert.equal(p.made.length, 2);
        p.made[1].open(); p.made[1].send('hello');
        assert.equal(states.at(-1), true);
        assert.equal(hellos.length, 2);                          // the page resyncs on the second hello
    } finally { mock.timers.reset(); }
});

test('a silent stall is caught by the watchdog; pings keep it alive', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        const p = page();
        p.w.MuLive.stream('/api/orchestration/events', {});
        p.made[0].open();
        mock.timers.tick(30000); p.made[0].send('ping');         // the server's 15 s ping
        mock.timers.tick(30000);
        assert.equal(p.made.length, 1, 'still the same stream');
        mock.timers.tick(46000);                                 // nothing at all for 45 s
        assert.ok(p.made[0].closed);
        mock.timers.tick(1100);
        assert.equal(p.made.length, 2);
    } finally { mock.timers.reset(); }
});

test('a hidden view or background tab releases its stream after a grace, and reopens when shown', () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
        const p = page();
        p.w.MuLive.stream('/api/jobs/events', {});
        p.made[0].open();
        p.setViewActive(false);
        mock.timers.tick(10000);
        assert.ok(!p.made[0].closed, 'a quick switch keeps it');
        p.setViewActive(true); p.setViewActive(false);
        mock.timers.tick(31000);
        assert.ok(p.made[0].closed, 'released after the grace');
        assert.equal(JSON.stringify(p.w.MuLive.streams()), JSON.stringify([{ url: '/api/jobs/events', state: 'released' }]));
        p.setViewActive(true);
        assert.equal(p.made.length, 2, 'reopened when shown');
        p.setTabHidden(true);
        mock.timers.tick(31000);
        assert.ok(p.made[1].closed, 'background tab releases too');
        p.made[1].fail(true);                                    // no retry storm while hidden
        mock.timers.tick(60000);
        assert.equal(p.made.length, 2);
        p.setTabHidden(false);
        assert.equal(p.made.length, 3);
    } finally { mock.timers.reset(); }
});
