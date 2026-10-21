// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// A browser paired from another machine and then revoked drops to the pairing page at once
// (static/js/pairing_guard.js): the whole window reloads (the server then serves the pairing page),
// not just one embedded view. This computer's own browser is never affected.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
const guard = readFileSync(resolve(root, 'mu/gui/static/js/pairing_guard.js'), 'utf8');
const tick = (ms = 0) => new Promise(done => setTimeout(done, ms));

function page({ self }) {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://192.168.1.20:30311/settings' });
    const w = dom.window;
    const calls = [];
    let selfStatus = self.status;
    const json = (status, body) => ({ status, ok: status < 400, url: 'http://192.168.1.20:30311/api/x',
        json: async () => body, clone() { return this; } });
    w.fetch = async (url) => {
        calls.push(String(url));
        if (String(url).includes('/api/devices/self')) return json(selfStatus, selfStatus === 401 ? { pairing_required: true } : self.body);
        return json(200, {});
    };
    class FakeEventSource {
        constructor() { this.listeners = {}; FakeEventSource.last = this; }
        addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
        fire(type) { (this.listeners[type] || []).forEach(fn => fn({})); }
    }
    w.EventSource = FakeEventSource;
    let reloads = 0;
    // window.top === window in a plain page; the guard reloads (window.top || window).location.
    Object.defineProperty(w, 'location', { value: { href: w.document.URL, origin: 'http://192.168.1.20:30311', reload: () => { reloads += 1; } } });
    w.eval(guard);
    return { w, close: () => w.close(), calls, reloads: () => reloads, revoke: () => { selfStatus = 401; }, FakeEventSource };
}

test('a 401 pairing_required from any request reloads the whole window', async () => {
    const p = page({ self: { status: 200, body: { device: { id: 'd1' } } } });
    await tick();
    await p.w.fetch('/api/sessions');
    assert.equal(p.reloads(), 0);
    const original = p.w.fetch;
    p.revoke();
    await p.w.fetch('/api/devices/self');
    await tick();
    assert.equal(p.reloads(), 1);
    await original('/api/devices/self');
    await tick();
    assert.equal(p.reloads(), 1, 'reloads once');
    p.close();
});

test('a dropped live stream checks at once and boots a revoked browser', async () => {
    const p = page({ self: { status: 200, body: { device: { id: 'd1' } } } });
    await tick();
    const stream = new p.w.EventSource('/api/events');
    p.revoke();
    stream.fire('error');
    await tick(700);
    assert.equal(p.reloads(), 1);
    p.close();
});

test("this computer's own browser never polls and never reloads", async () => {
    const p = page({ self: { status: 200, body: { device: null, loopback: true } } });
    await tick(50);
    const before = p.calls.filter(c => c.includes('/api/devices/self')).length;
    await tick(5200);
    assert.equal(p.calls.filter(c => c.includes('/api/devices/self')).length, before, 'no polling on loopback');
    assert.equal(p.reloads(), 0);
    p.close();
});
