// Each exchange reads: prompt, one activity row (tools + interim answers, with the turn's
// stats), produced visualizations, then the latest answer last.
import { groupIntermediateTurns, settleRunningTool, type ChatMessage } from '../../src/hooks/useChatSession';

const stats = { input: 100, output: 20, cost: 0.01, api_seconds: 2, wall_seconds: 3, tool_calls: 2 };
const msg = (id: string, role: ChatMessage['role'], extra: Partial<ChatMessage> = {}): ChatMessage => ({ id, role, text: '', ...extra });

it('folds tools and interim answers into one row and keeps the latest answer last', () => {
  const grouped = groupIntermediateTurns([
    msg('u', 'user', { text: 'go', stats }),
    msg('a1', 'assistant', { text: 'Looking first.' }),
    msg('t1', 'tool', { tool: { id: 't1', name: 'read_file', status: 'done' } }),
    msg('v', 'visualization'),
    msg('t2', 'tool', { tool: { id: 't2', name: 'bash', status: 'done' } }),
    msg('a2', 'assistant', { text: 'Done.' }),
  ]);
  expect(grouped.map(m => m.role)).toEqual(['user', 'collapse', 'visualization', 'assistant']);
  expect(grouped[1].childTurns!.map(m => m.id)).toEqual(['a1', 't1', 't2']);
  expect(grouped[1].stats).toEqual(stats);
  expect(grouped[3].text).toBe('Done.');
});

it('settles a running tool nested in the activity row', () => {
  const grouped = groupIntermediateTurns([
    msg('u', 'user', { text: 'go' }),
    msg('t1', 'tool', { tool: { id: 't1', name: 'bash', status: 'running' } }),
  ]);
  expect(grouped[1].collapseLive).toBe(true);
  const settled = settleRunningTool(grouped, 'ok', false);
  expect(settled[1].childTurns![0].tool!.status).toBe('done');
  expect(settled[1].collapseLive).toBe(false);
});
