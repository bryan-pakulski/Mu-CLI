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
// The exchange's tool calls, thinking and interim answers live in its one activity row.
const activityTrace = slot => slot.turns[1].childTurns.find(turn => turn.role === 'trace');
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
    assert.deepEqual(roles(slot.turns), ['user', 'collapse']);
    assert.equal(slot.turns[1].open, false);
    const trace = activityTrace(slot);
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
        const restored = activityTrace(slot);
        chat._ensureBusyTrace(slot);
        chat._ensureBusyTrace(slot); // Repeated sessions snapshots are idempotent.
        assert.deepEqual(roles(slot.turns), ['user', 'collapse']);
        assert.deepEqual(roles(slot.turns[1].childTurns), ['trace']);
        assert.equal(chat._activeTrace(slot), restored);
        assert.equal(slot.turns[1].open, false);
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
    const restored = activityTrace(slot);
    slot.turns[1].open = true;
    chat.addThinking('Continue inspecting.', 'mailroom');
    assert.equal(chat._activeTrace(slot), restored);
    assert.equal(slot.turns[1].open, true);
    assert.equal(slot.turns.length, 2);
    assert.equal(restored.events.length, 7);
});

test('grouped history keeps each exchange as prompt, activity, outputs, then the final answer', async t => {
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
    assert.deepEqual(roles(slot.turns), ['user', 'collapse', 'visualization', 'subagent_panel', 'assistant', 'user', 'collapse']);
    assert.equal(slot.turns[4].text, 'Final answer');
    // Inside the row everything stays chronological, the interim answer included.
    const activity = slot.turns[1].childTurns;
    assert.deepEqual(roles(activity), ['trace', 'assistant', 'trace', 'trace', 'trace']);
    assert.equal(activity[1].text, 'Progress update');
    assert.equal(activity[3].events[0].text, 'After the chart');
    assert.equal(activity[4].events[0].text, 'After the panel');
    const flattened = chat._flattenTimeline(slot.turns);
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
    const restored = activityTrace(slot);
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

test('enabling YOLO leaves human-only security approvals pending', async t => {
    const { window } = await harness(t);
    const prompts = window.Alpine.store('prompts');
    const answered = [];
    prompts.answer = id => answered.push(id);
    prompts.queue = [
        { id: 'poc', session_name: 'mailroom', prompt: { shape: 'tool_approval', approval_policy: 'always_human', tool_name: 'verify_security_proof' } },
        { id: 'desktop', session_name: 'mailroom', prompt: { shape: 'tool_approval', requires_desktop: true } },
        { id: 'ordinary', session_name: 'mailroom', prompt: { shape: 'tool_approval', tool_name: 'write_file' } },
    ];
    window.Alpine.store('yolo')._autoApprovePending();
    assert.deepEqual(answered, ['ordinary']);
});

test('a live turn keeps its latest answer last and reports time, tokens and cost', async t => {
    const { window, chat, slot } = await harness(t, []);
    await chat.loadHistory('mailroom');
    chat.addUser('Fix it', 'mailroom');
    chat.addToolCall('read_file', { path: 'a.py' }, 'mailroom');
    chat.startAssistant('a1', 'mailroom');
    chat.appendDelta('a1', 'Found the bug.', 'mailroom');
    chat.endAssistant('a1', 'mailroom');
    chat.addToolCall('write_file', { path: 'a.py' }, 'mailroom');
    chat.addToolResult('write_file', 'ok', 'mailroom');
    // New tool activity joins the row; the last answer is still the last row.
    assert.deepEqual(roles(slot.turns), ['user', 'collapse', 'assistant']);
    chat.startAssistant('a2', 'mailroom');
    chat.appendDelta('a2', 'Fixed and tested.', 'mailroom');
    chat.endAssistant('a2', 'mailroom');
    slot.busy = false;
    chat.finishTurn('mailroom');
    assert.deepEqual(roles(slot.turns), ['user', 'collapse', 'assistant']);
    assert.equal(slot.turns[2].text, 'Fixed and tested.');
    const activity = slot.turns[1];
    assert.deepEqual(roles(activity.childTurns), ['trace', 'assistant', 'trace']);
    assert.equal(activity.childTurns[1].text, 'Found the bug.');
    slot.turns[0].stats = { input: 12000, output: 400, cost: 0.0312, wall_seconds: 75, api_seconds: 40, tool_calls: 2 };
    chat._groupIntermediateTurns(slot);
    assert.equal(window.summarizeCollapse(slot.turns[1]), 'Worked for 1m 15s · 2 steps · 12.4k tokens · $0.031');
});

test('live events keep the newest answer last and every step in the activity row', async t => {
    const { chat, slot } = await harness(t, []);
    await chat.loadHistory('mailroom');
    chat.addUser('Fix it', 'mailroom');
    chat.startAssistant('a1', 'mailroom');
    chat.appendDelta('a1', 'Looking first.', 'mailroom');
    chat.addToolCall('read_file', { path: 'x' }, 'mailroom');
    chat.addToolResult('read_file', 'ok', 'mailroom');
    chat.addPromptResolved({ auto: true, shape: 'tool_approval', toolName: 'bash' }, 'mailroom');
    chat.startAssistant('a2', 'mailroom');
    chat.appendDelta('a2', 'Done.', 'mailroom');
    chat.endAssistant('a2', 'mailroom');
    assert.deepEqual(roles(slot.turns), ['user', 'collapse', 'assistant']);
    assert.equal(slot.turns[2].text, 'Done.');
    assert.deepEqual(roles(slot.turns[1].childTurns), ['assistant', 'trace', 'prompt_resolved']);
    assert.equal(slot.turns[1].childTurns[0].text, 'Looking first.');
});

test('the activity row lists every finished step instead of folding them', async t => {
    const { window } = await harness(t, []);
    window.eval(script('js/wash/toolcard.js'));
    const events = [
        { id: 'c1', kind: 'tool_call', name: 'bash', args: { command: 'ls' }, at: 1 },
        { id: 'r1', kind: 'tool_result', name: 'bash', rawText: 'ok', at: 2 },
        { id: 'c2', kind: 'tool_call', name: 'read_file', args: { path: 'a.py' }, at: 3 },
        { id: 'r2', kind: 'tool_result', name: 'read_file', rawText: 'ok', at: 4 },
    ];
    const host = window.document.createElement('div');
    host.innerHTML = window.renderToolCards(events, { running: false });
    assert(host.querySelector('[data-summary]'), 'a finished trace on its own folds into one line');
    host.innerHTML = window.renderToolCards(events, { running: false, expanded: true });
    assert.equal(host.querySelector('[data-summary]'), null);
    assert.equal(host.querySelectorAll('.w-toolcard').length >= 2, true);
});

test('only the newest text of a running turn is the live status', async t => {
    const { chat, slot } = await harness(t, []);
    chat.addUser('Go', 'mailroom');
    chat.startAssistant('a1', 'mailroom');
    chat.appendDelta('a1', 'Checking the MFA step.', 'mailroom');
    chat.endAssistant('a1', 'mailroom');
    slot.busy = true;
    const update = chat._findById(slot, 'a1');
    assert.equal(chat.isLiveStatus(update), true);
    assert.equal(chat.isLiveStatus(slot.turns[0]), false, 'your message is never a status');
    slot.busy = false;
    assert.equal(chat.isLiveStatus(update), false, 'a finished turn shows its answer plainly');
});

test('live text shows `inline code` as a code chip, escaped', async t => {
    const { window } = await harness(t, []);
    const host = window.document.createElement('div');
    host.innerHTML = window.renderLiveText('The two `dmesg-ramoops` dumps <b>x</b>');
    assert.equal(host.querySelector('code').textContent, 'dmesg-ramoops');
    assert.equal(host.textContent.includes('`'), false);
    assert.equal(host.querySelector('b'), null, 'markup in the text stays text');
});
