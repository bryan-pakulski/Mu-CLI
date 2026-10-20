// The sub-agent panel says the same as the web (mobile_parity.md §6 Chat): the TS words
// (src/components/chat/subagentModel.ts) and the web's app.js functions agree on the same agents,
// and the panel shows model, context %, iteration progress and the action timeline.
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { SubagentActivityPanel } from '../../src/components/SubagentActivityPanel';
import { subagentStatusText, subagentTitle, subagentToolLabel, summarizeSubagentPanel } from '../../src/components/chat/subagentModel';
import type { LiveSubagent } from '../../src/hooks/useChatSession';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const APP = path.resolve(__dirname, '../../../../mu/gui/static/js/app.js');
const NAMES = ['_fmtTok', 'subagentElapsed', 'subagentTitle', 'subagentToolLabel', 'subagentStatusText', 'summarizeSubagentPanel'];

type Web = Record<string, (...args: unknown[]) => string>;

/** The web's top-level functions, taken from app.js and run in a sandbox. */
function loadWeb(): Web {
  const source = fs.readFileSync(APP, 'utf8');
  const bodies = NAMES.map(name => {
    const start = source.indexOf(`\nfunction ${name}(`);
    if (start < 0) throw new Error(`app.js has no function ${name}`);
    const end = source.indexOf('\n}\n', start);
    return source.slice(start, end + 3);
  });
  const context = vm.createContext({ Date, Number, String, Math, Array, Object });
  vm.runInContext(`${bodies.join('\n')}\nthis.web = { ${NAMES.join(', ')} };`, context);
  return (context as unknown as { web: Web }).web;
}

const web = loadWeb();
const NOW = 1_700_000_000_000;

function agent(over: Partial<LiveSubagent> & Record<string, unknown> = {}): LiveSubagent {
  return {
    task_id: 't1', batch_id: 'b', task: 'look', title: '', depth: 1, model: 'claude-haiku-5-5', specialist_key: 'code_reviewer',
    status: 'running', tool_count: 3, last_tool: 'read_file', elapsed: 4.2, context_pct: 37, iter: 3, max_iter: 12,
    tokens_in: 4500, summary: '', error: null, actions: [], observed_at: NOW, ...over,
  } as LiveSubagent;
}

const CASES: Array<Array<Partial<LiveSubagent> & Record<string, unknown>>> = [
  [{}],
  [{ title: '  Review   the diff ' }, { task_id: 't2', status: 'done', specialist_key: '', last_tool: null, tokens_in: 1_200_000 }],
  [{ status: 'stuck', repeat_count: 4 }, { task_id: 't2', status: 'stall', last_tool: 'web_search' }],
  [{ status: 'killed', kill_reason: 'budget' }, { task_id: 't2', status: 'error' }, { task_id: 't3', status: 'done' }],
  [{ status: 'running', last_tool: 'custom_tool-name', tokens_in: 0, elapsed: 0 }],
];

describe('parity with the web', () => {
  it.each(CASES.map((c, i) => [i, c] as const))('case %i: titles, status words and the summary match', (_i, rows) => {
    const agents = rows.map(r => agent(r));
    const running = agents.some(a => ['running', 'stuck', 'stall'].includes(a.status));
    jest.spyOn(Date, 'now').mockReturnValue(NOW);
    for (const a of agents) {
      expect(subagentTitle(a)).toBe(web.subagentTitle(a));
      expect(subagentStatusText(a)).toBe(web.subagentStatusText(a));
      expect(subagentToolLabel(a.last_tool)).toBe(web.subagentToolLabel(a.last_tool));
    }
    expect(summarizeSubagentPanel(agents, running, NOW)).toBe(web.summarizeSubagentPanel({ agents, running }, 0));
    (Date.now as jest.Mock).mockRestore();
  });
});

describe('SubagentActivityPanel', () => {
  it('shows model, context, iteration progress and the action timeline as tool cards', async () => {
    const a = agent({ actions: [
      { seq: 1, tool: 'read_file', detail: 'src/app.py', status: 'done', elapsed: 0.4, at: 0 },
      { seq: 2, tool: 'bash', detail: 'pytest -q', status: 'running', elapsed: 0, at: 0 },
    ] });
    const ui = render(<ThemeProvider statusBar={false}><SubagentActivityPanel agents={[a]} /></ThemeProvider>);
    expect(ui.getByText('Code Reviewer task')).toBeTruthy();
    expect(ui.getByText('code reviewer · Depth 1 · claude-haiku-5-5')).toBeTruthy();
    expect(ui.getByText('37%')).toBeTruthy();
    expect(ui.getByText('3 / 12 · 25%')).toBeTruthy();
    expect(ui.getByText('Read file')).toBeTruthy();
    expect(ui.queryByTestId('subagent-t1-timeline')).toBeNull();
    await act(async () => { fireEvent.press(ui.getByTestId('subagent-t1-activity')); });
    expect(ui.getByTestId('subagent-t1-timeline')).toBeTruthy();
    expect(ui.getByText('src/app.py')).toBeTruthy();
    expect(ui.getByText('pytest -q')).toBeTruthy();
    ui.unmount();
  });

  it('says it is waiting before the first action, and folds when the header is pressed', async () => {
    const ui = render(<ThemeProvider statusBar={false}><SubagentActivityPanel agents={[agent()]} /></ThemeProvider>);
    await act(async () => { fireEvent.press(ui.getByTestId('subagent-t1-activity')); });
    expect(ui.getByText('Waiting for the first tool action…')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('subagent-panel-header')); });
    expect(ui.queryByTestId('subagent-t1')).toBeNull();
    ui.unmount();
  });
});
