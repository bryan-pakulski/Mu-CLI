// A command the user runs (`/status`, `/mode plan`) becomes one command row: what was run and
// what it returned. It is never a chat turn, and it stays out of an exchange's activity row.
import { commandRun, groupIntermediateTurns, type ChatMessage } from '../../src/hooks/useChatSession';

it('builds the row from the server result', () => {
  expect(commandRun('/status', { ok: true, message: 'idle', data: {} })).toEqual({ input: '/status', running: false, ok: true, message: 'idle', output: '' });
  const shell = commandRun('/sh ls', { ok: true, message: '', data: { stdout: 'a\nb\n' } });
  expect(shell.output).toBe('a\nb');
  expect(commandRun('/mode nope', { ok: false, message: 'unknown mode' }).ok).toBe(false);
});

it('keeps a command out of the activity row', () => {
  const msgs: ChatMessage[] = [
    { id: 'u1', role: 'user', text: 'Go' },
    { id: 't1', role: 'tool', text: '', tool: { id: 'c1', name: 'bash', args: {}, status: 'done' } as never },
    { id: 'cmd1', role: 'command', text: '/status', command: { input: '/status', running: false, ok: true, message: 'idle', output: '' } },
    { id: 'a1', role: 'assistant', text: 'Done.' },
  ];
  const grouped = groupIntermediateTurns(msgs);
  expect(grouped.map(m => m.role)).toContain('command');
  const row = grouped.find(m => m.role === 'collapse');
  expect((row?.childTurns || []).some(m => m.role === 'command')).toBe(false);
});
