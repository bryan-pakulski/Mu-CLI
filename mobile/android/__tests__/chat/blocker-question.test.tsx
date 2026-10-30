/**
 * A loop pause is visible in the chat: the agent's raise_blocker call renders as the full
 * question below the folded turn summary, never as a collapsed tool row.
 */
import React from 'react';
import { render } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToolList } from '../../src/ui/ToolList';
import { buildToolCards, type ToolEvent } from '../../src/ui/toolList';

const long = 'The next matmul speedup needs fp16 inputs, which changes results slightly versus fp32 reference outputs.';
const events: ToolEvent[] = [
  { kind: 'tool_call', id: 'a', name: 'read_file', args: { path: 'a.py' }, at: 1 },
  { kind: 'tool_result', name: 'read_file', text: 'x', at: 2 },
  { kind: 'tool_call', id: 'q', name: 'raise_blocker', at: 3,
    args: { summary: long, requested_input: 'Accept fp16?', details: 'int8 lost accuracy', questions: ['Keep fp32 fallback?'] } },
  { kind: 'tool_result', name: 'raise_blocker', text: '{"ok": true}', at: 4 },
];

test('a finished turn shows its question in full beside the folded summary', () => {
  const items = buildToolCards(events, { running: false });
  const screen = render(<ThemeProvider><ToolList items={items} /></ThemeProvider>);
  expect(screen.getByTestId('tool-list-summary')).toBeTruthy();
  expect(screen.getByText('Read 1 file')).toBeTruthy();
  expect(screen.getByTestId('tool-question-q')).toBeTruthy();
  for (const text of [long, 'int8 lost accuracy']) expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getByText(/Accept fp16\?/)).toBeTruthy();
  expect(screen.getByText(/Keep fp32 fallback\?/)).toBeTruthy();
});
