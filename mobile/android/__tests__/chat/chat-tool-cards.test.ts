// Mobile chat shows tool calls (G§9.8 M; spec §5.3 M3) — client-only: the
// SSE tool_call/tool_result events and the history tool parts become ToolCards.
import { historyToMessages, settleRunningTool, type ChatMessage } from '../../src/hooks/useChatSession';
import type { SessionHistoryTurn } from '../../src/api/sessions';

it('turns history tool_call parts into settled tool cards with their result preview', () => {
  const turns = [
    { index: 0, role: 'user', parts: [{ type: 'text', text: 'Read the file' }] },
    { index: 1, role: 'assistant', parts: [{ type: 'text', text: 'Reading.' }, { type: 'tool_call', tool_name: 'read_file', tool_args: { path: 'a.py' } }] },
    { index: 2, role: 'tool', parts: [{ type: 'tool_result', tool_name: 'read_file', preview: 'print(1)' }] },
    { index: 3, role: 'assistant', parts: [{ type: 'text', text: 'Done.' }] },
  ] as unknown as SessionHistoryTurn[];
  const messages = historyToMessages(turns);
  expect(messages.map(m => m.role)).toEqual(['user', 'assistant', 'tool', 'assistant']);
  const tool = messages[2].tool!;
  expect(tool).toMatchObject({ name: 'read_file', args: { path: 'a.py' }, status: 'done', result: 'print(1)' });
});

it('settles the oldest running live tool with its result and duration', () => {
  const running = (id: string): ChatMessage => ({ id, role: 'tool', text: '', tool: { id, name: 'grep', status: 'running', startedAt: Date.now() - 500, live: true } });
  const next = settleRunningTool([running('a'), running('b')], 'found 3', false);
  expect(next[0].tool).toMatchObject({ status: 'done', result: 'found 3' });
  expect(next[0].tool!.durationMs).toBeGreaterThanOrEqual(0);
  expect(next[1].tool!.status).toBe('running');
  const blocked = settleRunningTool(next, 'blocked by plan mode', true);
  expect(blocked[1].tool!.status).toBe('failed');
  expect(settleRunningTool([], 'x', false)).toEqual([]);
});
