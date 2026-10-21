// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Node groups on the Orchestration page (static/js/orchestration/groups.js): dragging a node
// onto a group sends the right command — added, moved when dragged from another group, taken out
// when dropped on Ungrouped — and drops that mean nothing send nothing.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
// Objects from inside the jsdom window: compare plain copies.
const plain = v => JSON.parse(JSON.stringify(v));
const source = readFileSync(resolve(root, 'mu/gui/static/js/orchestration/groups.js'), 'utf8');

function page() {
    const dom = new JSDOM(`<!doctype html><ul>
        <li class="w-row" data-drop-group="web">web</li><li class="w-row" data-drop-group="db">db</li>
        <li class="w-row" data-drop-group="-">Ungrouped</li><li class="w-row" data-group-entry="*">All nodes</li></ul>
        <ul><li class="w-row" draggable="true" data-node-id="n1" data-node-name="web-01">web-01</li></ul>`,
        { runScripts: 'outside-only', url: 'http://127.0.0.1:30311/orchestration' });
    const w = dom.window;
    const sent = [];
    w.OrchAPI = { cmd: async (type, args) => { sent.push({ type, args }); return { data: {} }; }, get: async () => ({}) };
    w.OrchUI = { esc: s => String(s), icon: () => '', plural: (n, a, b) => `${n} ${n === 1 ? a : (b || a + 's')}`, toast: () => {}, busy: (_b, fn) => fn() };
    w.Orch = { routes: {}, refresh: () => {} };
    w.eval(source);
    return { w, sent, G: w.Orch.groups };
}

test('a drop means: add, move from the group being viewed, or take out on Ungrouped', () => {
    const { G } = page();
    assert.deepEqual(plain(G.dropCommand({ nodeIds: ['n1'], from: '*', target: 'web' })), { type: 'addToGroup', args: { name: 'web', node_ids: ['n1'] } });
    assert.deepEqual(plain(G.dropCommand({ nodeIds: ['n1', 'n1'], from: 'db', target: 'web' })), { type: 'addToGroup', args: { name: 'web', node_ids: ['n1'], from: 'db' } });
    assert.deepEqual(plain(G.dropCommand({ nodeIds: ['n1'], from: 'db', target: '-' })), { type: 'removeFromGroup', args: { name: 'db', node_ids: ['n1'] } });
    assert.deepEqual(plain(G.dropCommand({ nodeIds: ['n1'], from: '-', target: 'web' })), { type: 'addToGroup', args: { name: 'web', node_ids: ['n1'] } });
    assert.equal(G.dropCommand({ nodeIds: ['n1'], from: 'web', target: 'web' }), null, 'onto its own group');
    assert.equal(G.dropCommand({ nodeIds: ['n1'], from: '*', target: '-' }), null, 'ungrouping needs a group to leave');
    assert.equal(G.dropCommand({ nodeIds: ['n1'], from: 'web', target: '*' }), null, 'All nodes is not a target');
    assert.equal(G.dropCommand({ nodeIds: [], from: '*', target: 'web' }), null);
});

function drag(w, row, target) {
    const dataTransfer = { data: {}, setData(k, v) { this.data[k] = v; }, getData(k) { return this.data[k]; }, effectAllowed: '', dropEffect: '' };
    const fire = (el, type) => {
        const ev = new w.Event(type, { bubbles: true, cancelable: true });
        Object.defineProperty(ev, 'dataTransfer', { value: dataTransfer });
        el.dispatchEvent(ev);
        return ev;
    };
    fire(row, 'dragstart');
    const over = fire(target, 'dragover');
    const highlighted = target.hasAttribute('data-drop-over');
    fire(target, 'drop');
    fire(row, 'dragend');
    return { accepted: over.defaultPrevented, highlighted };
}

test('dragging a node row onto a group sends addToGroup and highlights the target', async () => {
    const { w, sent } = page();
    const row = w.document.querySelector('[data-node-id="n1"]');
    const { accepted, highlighted } = drag(w, row, w.document.querySelector('[data-drop-group="db"]'));
    await new Promise(r => setTimeout(r, 0));
    assert.ok(accepted && highlighted);
    assert.deepEqual(plain(sent), [{ type: 'addToGroup', args: { name: 'db', node_ids: ['n1'] } }]);
    assert.equal(w.document.querySelector('[data-drop-over]'), null, 'the highlight clears');
    assert.equal(w.document.documentElement.hasAttribute('data-orch-dragging'), false);
});

test('a drop that means nothing is refused and sends nothing', async () => {
    const { w, sent } = page();
    const row = w.document.querySelector('[data-node-id="n1"]');
    const { accepted } = drag(w, row, w.document.querySelector('[data-drop-group="-"]'));   // viewing All nodes
    await new Promise(r => setTimeout(r, 0));
    assert.equal(accepted, false);
    assert.deepEqual(plain(sent), []);
});
