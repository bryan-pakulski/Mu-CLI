// NODE_PATH=mobile/android/node_modules node --test tests/frontend/*.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = resolve(__dirname, '../../..');
const script = name => readFileSync(resolve(root, 'mu/gui/static', name), 'utf8');
const settle = window => new Promise(resolve => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve)));
const roles = turns => Array.from(turns, turn => turn.role);
const history = parts => parts.map(([role, ...parts], index) => ({ index, role, parts }));
const text = value => ({ type: 'text', text: value });
const thinking = value => ({ type: 'thinking', text: value });
const call = name => ({ type: 'tool_call', tool_name: name, tool_args: { path: name } });
const result = name => ({ type: 'tool_result', tool_name: name, preview: `result: ${name}` });
const runningHistory = () => history([
    ['user', text('Review the workspace')],
    ['assistant', thinking('Inspect the files.'), call('list_files')],
    ['tool', result('list_files')],
    ['assistant', thinking('Read the relevant file.'), call('read_file')],
    ['tool', result('read_file')],
]);

async function harness(t, turns = runningHistory()) {
    const errors = [];
    const console = new VirtualConsole();
    console.on('jsdomError', error => errors.push(error));
    console.on('error', (...args) => errors.push(args));
    const dom = new JSDOM('', { url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console });
    const { window } = dom;
    t.after(() => { window.close(); assert.deepEqual(errors, []); });
    await new Promise(resolve => window.addEventListener('load', resolve));
    // Register the production stores without starting unrelated settings,
    // polling, or SSE connections. History fetching and its wrappers run intact.
    const stores = new Map();
    window.Alpine = { store(name, value) {
        if (value !== undefined) stores.set(name, value);
        return stores.get(name);
    } };
    let requests = 0;
    window.fetch = async url => {
        assert.equal(new window.URL(url, window.location.href).pathname, '/api/sessions/current/history');
        requests += 1;
        return { ok: true, json: async () => ({ name: 'mailroom', turns, start_index: 0, window_end: turns.length, total_turns: turns.length }) };
    };
    for (const name of ['app.js', 'web_shell.js', 'history_hydration.js']) window.eval(script(`js/${name}`));
    window.document.dispatchEvent(new window.Event('alpine:init'));
    await Promise.resolve(); // Install the production hydration wrappers.
    const chat = stores.get('chat');
    chat.currentName = 'mailroom';
    return { window, chat, slot: chat.current(), requests: () => requests };
}

test('reload groups provider thinking, calls, and results in chronological order', async t => {
    const { chat, slot } = await harness(t);
    await chat.loadHistory('mailroom');
    assert(slot.historyHydrated);
    assert.deepEqual(roles(slot.turns), ['user', 'trace']);
    const trace = slot.turns[1];
    assert.equal(trace.open, false);
    assert.equal(trace.running, false);
    assert.deepEqual(Array.from(trace.events, event => event.kind), ['thinking', 'tool_call', 'tool_result', 'thinking', 'tool_call', 'tool_result']);
    assert.deepEqual(Array.from(trace.events, event => event.id), ['h-ev-1-0', 'h-ev-1-1', 'h-ev-2-0', 'h-ev-3-0', 'h-ev-3-1', 'h-ev-4-0']);
    assert.equal(trace.events[2].rawText, 'result: list_files');
    assert.equal(trace.events[3].text, 'Read the relevant file.');
});

for (const busyBeforeHistory of [true, false]) {
    test(`running reload resumes one trace when busy arrives ${busyBeforeHistory ? 'before' : 'after'} history`, async t => {
        const { chat, slot, requests } = await harness(t);
        if (busyBeforeHistory) {
            slot.busy = true;
            chat._ensureBusyTrace(slot); // Empty placeholder from hello/sessions.
        }
        await chat.loadHistory('mailroom');
        slot.busy = true;
        const restored = slot.turns[1];
        chat._ensureBusyTrace(slot);
        chat._ensureBusyTrace(slot); // Repeated sessions snapshots are idempotent.
        assert.deepEqual(roles(slot.turns), ['user', 'trace']);
        assert.equal(chat._activeTrace(slot), restored);
        assert.equal(restored.open, false);
        chat.addThinking('Check the tests.', 'mailroom');
        chat.addToolCall('run_tests', {}, 'mailroom');
        chat.addToolResult('run_tests', 'passed', 'mailroom');
        assert.equal(restored.events.length, 9);
        assert.equal(slot.turns.length, 2);
        // A reconnect must not overwrite newly streamed events with saved history.
        await chat.loadHistory('mailroom');
        assert.equal(requests(), 1);
        assert.equal(slot.pendingReload, true);
        assert.equal(restored.events.length, 9);
    });
}

test('a live event can resume the restored trace before the next busy snapshot', async t => {
    const { chat, slot } = await harness(t);
    await chat.loadHistory('mailroom');
    const restored = slot.turns[1];
    restored.open = true;
    chat.addThinking('Continue inspecting.', 'mailroom');
    assert.equal(chat._activeTrace(slot), restored);
    assert.equal(restored.open, true);
    assert.equal(slot.turns.length, 2);
    assert.equal(restored.events.length, 7);
});

test('grouped history preserves message, visualization, and subagent boundaries', async t => {
    const artifact = { kind: 'visualization', artifact_id: 'chart', title: 'Results' };
    const turns = history([
        ['user', text('First prompt')],
        ['assistant', call('first')],
        ['tool', result('first')],
        ['assistant', text('Progress update'), thinking('Next step'), call('chart')],
        ['tool', { ...result('chart'), artifact }, thinking('After the chart')],
        ['assistant', { type: 'subagent_panel', batch_id: 'batch', agents: [{ task_id: 'worker', status: 'done' }] }, thinking('After the panel')],
        ['assistant', text('Final answer')],
        ['user', text('Second prompt')],
        ['assistant', call('second')],
        ['tool', result('second')],
    ]);
    const { chat, slot } = await harness(t, turns);
    await chat.loadHistory('mailroom');
    const flattened = chat._flattenTimeline(slot.turns);
    assert.deepEqual(roles(flattened), ['user', 'trace', 'assistant', 'trace', 'visualization', 'trace', 'subagent_panel', 'trace', 'assistant', 'user', 'trace']);
    assert.equal(flattened[5].events[0].text, 'After the chart');
    assert.equal(flattened[7].events[0].text, 'After the panel');
    assert(slot.turns.some(turn => turn.role === 'assistant' && turn.text === 'Final answer'));
    slot.busy = true;
    chat._ensureBusyTrace(slot);
    assert.equal(flattened.filter(turn => turn.running).length, 1);
    assert.equal(chat._activeTrace(slot), flattened[10]);
});

test('finishing and starting another prompt keeps each exchange separate', async t => {
    const { chat, slot } = await harness(t);
    await chat.loadHistory('mailroom');
    slot.busy = true;
    chat._ensureBusyTrace(slot);
    const restored = slot.turns[1];
    chat.startAssistant('answer', 'mailroom');
    chat.appendDelta('answer', 'All done.', 'mailroom');
    chat.endAssistant('answer', 'mailroom');
    slot.busy = false;
    chat.finishTurn('mailroom');
    assert.equal(restored.running, false);
    assert.equal(slot.turns.at(-1).text, 'All done.');
    chat.addUser('Next task', 'mailroom');
    chat.addToolCall('next', {}, 'mailroom');
    assert.notEqual(chat._activeTrace(slot), restored);
    assert.equal(restored.events.length, 6);
    assert.equal(chat._activeTrace(slot).events.length, 1);
});
