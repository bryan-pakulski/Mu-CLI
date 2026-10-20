// A turn's tool calls on the phone (src/ui/ToolList.tsx): a finished turn folds into the web's
// summary line and expands to rows and groups; a running turn stays open.
import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { ToolList } from '../../src/ui/ToolList';
import { buildToolCards, type ToolEvent } from '../../src/ui/toolList';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const events: ToolEvent[] = [
  { kind: 'tool_call', id: 'a', name: 'read_file', args: { path: 'src/a.py' }, at: 1000 }, { kind: 'tool_result', name: 'read_file', text: 'a', at: 1100 },
  { kind: 'tool_call', id: 'b', name: 'read_file', args: { path: 'src/b.py' }, at: 1200 }, { kind: 'tool_result', name: 'read_file', text: 'b', at: 1300 },
  { kind: 'tool_call', id: 'c', name: 'read_file', args: { path: 'src/c.py' }, at: 1400 }, { kind: 'tool_result', name: 'read_file', text: 'c', at: 1500 },
  { kind: 'tool_call', id: 'd', name: 'bash', args: { command: 'pytest -q' }, at: 1600 }, { kind: 'tool_result', name: 'bash', text: 'Error: 1 failed', at: 2600 },
];
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

it('folds a finished turn into one line with the failed count, and expands to the rows', () => {
  const ui = wrap(<ToolList items={buildToolCards(events)} />);
  expect(ui.getByText('Read 3 files · ran 1 command')).toBeTruthy();
  expect(ui.getByText('1 failed')).toBeTruthy();
  expect(ui.queryByText('Read 3 files')).toBeNull();
  fireEvent.press(ui.getByTestId('tool-list-summary'));
  expect(ui.getByText('Read 3 files')).toBeTruthy();
  expect(ui.getByText('a.py, b.py, c.py')).toBeTruthy();
  expect(ui.getByTestId('tool-card-d')).toBeTruthy();
});

it('keeps a running turn open and says it is working when nothing has happened yet', () => {
  const running = [...events.slice(0, 2), { kind: 'tool_call', id: 'e', name: 'bash', args: { command: 'make' }, at: 3000 }];
  const ui = wrap(<ToolList items={buildToolCards(running, { running: true })} running />);
  expect(ui.queryByTestId('tool-list-summary')).toBeNull();
  expect(ui.getByTestId('tool-card-a')).toBeTruthy();
  expect(ui.getByTestId('tool-card-e')).toBeTruthy();
  const empty = wrap(<ToolList items={[]} running />);
  expect(empty.getByText('Working…')).toBeTruthy();
});
