// The server's busy list is authoritative both ways: a turn whose turn_complete this tab
// missed (stream released in the background, dropped connection) ends on the next hello or
// session poll, and the saved answer is reloaded.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = resolve(__dirname, '../../..');
const script = name => readFileSync(resolve(root, 'mu/gui/static', name), 'utf8');

async function harness(t) {
    const errors = [];
    const console = new VirtualConsole();
    console.on('jsdomError', error => errors.push(error));
    console.on('error', (...args) => errors.push(args));
    const dom = new JSDOM('', { url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console });
    const { window } = dom;
    t.after(() => { window.close(); assert.deepEqual(errors, []); });
    await new Promise(r => window.addEventListener('load', r));
    const stores = new Map();
    window.Alpine = { store(name, value) {
        if (value !== undefined) stores.set(name, value);
        return stores.get(name);
    } };
    const turns = [
        { index: 0, role: 'user', parts: [{ type: 'text', text: 'Fix it' }] },
        { index: 1, role: 'assistant', parts: [{ type: 'text', text: 'Final answer' }] },
    ];
    const historyRequests = [];
    window.fetch = async url => {
        const u = new window.URL(url, window.location.href);
        assert.equal(u.pathname, '/api/sessions/current/history');
        historyRequests.push(u.searchParams.get('name'));
        return { ok: true, json: async () => ({ name: 'mailroom', turns, start_index: 0, window_end: turns.length, total_turns: turns.length }) };
    };
    for (const name of ['app.js', 'web_shell.js', 'history_hydration.js']) window.eval(script(`js/${name}`));
    window.document.dispatchEvent(new window.Event('alpine:init'));
    await Promise.resolve();
    const chat = stores.get('chat');
    chat.currentName = 'mailroom';
    return { chat, historyRequests };
}

const settle = () => new Promise(r => setTimeout(r, 20));
const texts = slot => Array.from(slot.turns).filter(t => t.role === 'assistant').map(t => String(t.text));

test('a busy slot the server no longer lists ends and shows the saved answer', async t => {
    const { chat, historyRequests } = await harness(t);
    const slot = chat._slot('mailroom');
    slot.busy = true;
    slot.busySince = Date.now() - 60000;       // turn started long ago; its turn_complete was missed
    chat.syncServerBusy([]);
    await settle();
    assert.equal(slot.busy, false);
    assert.equal(historyRequests.length, 1);
    assert.deepEqual(texts(slot), ['Final answer']);
});

test('a session the server lists as busy goes busy', async t => {
    const { chat } = await harness(t);
    chat.syncServerBusy(['mailroom']);
    assert.equal(chat._slot('mailroom').busy, true);
});

test('a turn this tab only just started is not ended before the server sees it', async t => {
    const { chat, historyRequests } = await harness(t);
    const slot = chat._slot('mailroom');
    slot.busy = true;
    slot.busySince = Date.now();
    chat.syncServerBusy([]);
    await settle();
    assert.equal(slot.busy, true);
    assert.equal(historyRequests.length, 0);
});
