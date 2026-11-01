// Threads are not sessions of their own in the sidebar: each nests beneath the session it was
// started from (the server's thread_parent), the filter keeps a matching thread with its
// parent, and a family collapses: collapsed by default except the one holding the open
// thread, the person's choice wins and persists, and a collapsed count shows hidden activity.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = resolve(__dirname, '../../..');
const script = name => readFileSync(resolve(root, 'mu/gui/static', name), 'utf8');

async function sessionsStore(t, list) {
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
    for (const name of ['app.js', 'web_shell.js', 'history_hydration.js']) window.eval(script(`js/${name}`));
    window.document.dispatchEvent(new window.Event('alpine:init'));
    await Promise.resolve();
    const sessions = stores.get('sessions');
    sessions.list = list;
    return { sessions, threads: stores.get('threads'), chat: stores.get('chat'), prompts: stores.get('prompts'), window };
}

const LIST = [
    { name: 'alpha', thread_parent: '' },
    { name: 'docs', thread_parent: 'parent', thread_title: 'Write docs', thread_created_at: 20 },
    { name: 'parent', thread_parent: '' },
    { name: 'parser-fix', thread_parent: 'parent', thread_title: 'Fix parser', thread_created_at: 10 },
];

test('threads list beneath their parent session, oldest first, never as top-level rows', async t => {
    const { sessions } = await sessionsStore(t, LIST);
    assert.deepEqual(Array.from(sessions.tree.map(s => s.name)), ['alpha', 'parent']);
    assert.deepEqual(Array.from(sessions.rows().map(s => s.name)), ['alpha', 'parent', 'parser-fix', 'docs']);
});

test('filtering by a thread title keeps that thread under its parent', async t => {
    const { sessions } = await sessionsStore(t, LIST);
    assert.deepEqual(Array.from(sessions.rows('docs').map(s => s.name)), ['parent', 'docs']);
    assert.deepEqual(Array.from(sessions.rows('parent').map(s => s.name)), ['parent', 'parser-fix', 'docs']);
    assert.deepEqual(Array.from(sessions.rows('nothing').map(s => s.name)), []);
});

test('a thread whose parent is not listed shows as a session', async t => {
    const { sessions } = await sessionsStore(t, [{ name: 'orphan', thread_parent: 'gone', thread_title: 'Orphan' }]);
    assert.deepEqual(Array.from(sessions.rows().map(s => s.name)), ['orphan']);
});

test('a thread row shows its unread peer messages from the current group roster', async t => {
    const { threads } = await sessionsStore(t, LIST);
    threads.roster = [{ session_name: 'docs', unread_count: 3 }];
    assert.equal(threads.unread('docs'), 3);
    assert.equal(threads.unread('alpha'), 0);
});

test('families start collapsed except the one holding the open thread', async t => {
    const { sessions, chat } = await sessionsStore(t, LIST);
    const family = () => sessions.families().find(f => f.name === 'parent');
    chat.currentName = 'alpha';
    assert.equal(sessions.isExpanded(family()), false);
    chat.currentName = 'docs';
    assert.equal(sessions.isExpanded(family()), true);
    // A filter opens every matching family.
    chat.currentName = 'alpha';
    assert.equal(sessions.isExpanded(family(), 'fix'), true);
});

test('the person\'s choice wins, persists, and opening a hidden thread re-opens its family', async t => {
    const { sessions, chat, window } = await sessionsStore(t, LIST);
    const family = () => sessions.families().find(f => f.name === 'parent');
    chat.currentName = 'docs';
    sessions.toggleFamily(family());
    assert.equal(sessions.isExpanded(family()), false);
    assert.deepEqual(JSON.parse(window.localStorage.getItem('mucli-sessions-expanded')), { parent: false });
    sessions.toggleFamily(family());
    assert.equal(sessions.isExpanded(family()), true);
    sessions.setExpanded('parent', false);
    window.fetch = async () => ({ ok: false, json: async () => ({}) });
    sessions.loaded = ['parser-fix'];
    await sessions.switchTo('parser-fix');
    assert.equal(sessions.expanded.parent, true);
});

test('a collapsed family\'s count shows when a hidden thread runs or needs you', async t => {
    const { sessions, prompts } = await sessionsStore(t, LIST);
    const family = () => sessions.families().find(f => f.name === 'parent');
    assert.equal(sessions.familyTone(family()), null);
    sessions.busy = ['docs'];
    assert.equal(sessions.familyTone(family()), 'running');
    prompts.queue = [{ id: 'p', session_name: 'parser-fix' }];
    assert.equal(sessions.familyTone(family()), 'attention');
});
