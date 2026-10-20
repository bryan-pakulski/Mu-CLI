// The phone folds and groups tool calls exactly as the web (mobile_parity.md §8): the TS port
// (src/ui/toolList.ts) and the web's wash/toolcard.js run over the same events and must agree on
// items, kinds, states, targets, groups and the one-line turn summary.
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { buildToolCards, turnSummary, type ToolEvent, type ToolListItem } from '../../src/ui/toolList';

const WEB = path.resolve(__dirname, '../../../../mu/gui/static/js/wash/toolcard.js');

type WebApi = {
  buildToolCards: (events: ToolEvent[], options: { running?: boolean }) => Array<Record<string, any>>;
  renderToolCards: (events: ToolEvent[], options: { running?: boolean }) => string;
};

function loadWeb(): WebApi {
  const window: Record<string, unknown> = {};
  const context = vm.createContext({ window, document: { addEventListener: () => undefined }, Map, JSON, Date, Number, String, Math, Array, Object, isFinite });
  vm.runInContext(fs.readFileSync(WEB, 'utf8'), context);
  return window.WashToolCard as WebApi;
}

const web = loadWeb();

function project(items: Array<Record<string, any>>): unknown[] {
  return items.map(item => {
    if (item.type === 'group') return { type: 'group', kind: item.kind, cards: project(item.cards) };
    if (item.type === 'card') return { type: 'card', id: item.id, name: item.name, kind: item.kind, target: item.target, meta: item.meta, state: item.state };
    if (item.type === 'thinking') return { type: 'thinking', id: item.id, live: !!item.live };
    return { type: item.type, id: item.id };
  });
}

function webSummary(events: ToolEvent[]): string {
  const html = web.renderToolCards(events, { running: false });
  const m = /w-toolsummary__label">([^<]*)</.exec(html);
  return m ? m[1] : '';
}

const call = (id: string, name: string, args: unknown, at = 1000): ToolEvent => ({ kind: 'tool_call', id, name, args, at });
const result = (name: string, text: string, at = 2000, extra: Partial<ToolEvent> = {}): ToolEvent => ({ kind: 'tool_result', name, text, at, ...extra });

const FIXTURES: Record<string, ToolEvent[]> = {
  'three reads group, then a shell run': [
    call('a', 'read_file', { path: 'src/a.py', start_line: 10, limit: 20 }), result('read_file', 'x'),
    call('b', 'read_file', { path: 'src/b.py' }), result('read_file', 'y'),
    call('c', 'read_file', '{"path": "src/c.py"}'), result('read_file', 'z'),
    call('d', 'bash', { command: 'pytest -q' }), result('bash', 'Error: 2 failed'),
  ],
  'searches quote their query and name the directory': [
    call('s1', 'search_for_string', { query: 'TODO', path: 'mu/' }), result('search_for_string', '3 hits'),
    call('s2', 'grep', { pattern: 'def main' }), result('grep', '1 hit'),
  ],
  'a call still waiting while the turn runs': [
    call('w1', 'write_file', { file_path: 'a.txt', content: 'hi' }), result('write_file', '{"ok": false, "error": "denied"}'),
    call('w2', 'web_search', { query: 'expo camera' }),
    { kind: 'thinking', id: 'th', text: 'considering options' },
  ],
  'plan-blocked edits, orphan results and notes': [
    result('apply_patch', 'stale page'),
    call('p1', 'apply_patch', { patch: '--- a\n+++ b' }), result('apply_patch', 'blocked', 2000, { planBlocked: true }),
    { kind: 'info', id: 'n1', text: 'Plan mode is on' },
    call('m1', 'remember', ['a', 'b']), result('remember', 'ok'),
    call('t1', 'custom_tool', 'plain text arg'),
  ],
  'mixed kinds count in order of first appearance': [
    call('1', 'bash', { cmd: 'ls' }), result('bash', 'a'),
    call('2', 'read_file', { path: 'x' }), result('read_file', 'b'),
    call('3', 'bash', { cmd: 'pwd' }), result('bash', 'Traceback (most recent call last)'),
    call('4', 'spawn_agent', { task: 'review' }), result('spawn_agent', 'done'),
    call('5', 'docker_ps', {}), result('docker_ps', 'ok'),
  ],
};

describe.each(Object.entries(FIXTURES))('%s', (_name, events) => {
  it.each([true, false])('builds the same items (running=%s)', running => {
    const ours = buildToolCards(JSON.parse(JSON.stringify(events)), { running }) as unknown as Array<Record<string, any>>;
    const theirs = web.buildToolCards(JSON.parse(JSON.stringify(events)), { running });
    expect(project(ours)).toEqual(project(theirs));
  });

  it('writes the same turn summary', () => {
    const items: ToolListItem[] = buildToolCards(events, { running: false });
    expect(webSummary(events)).not.toBe('');
    expect(turnSummary(items).text).toBe(webSummary(events));
  });
});
