// The Mu page kit (src/ui/Mu.tsx; mobile_parity.md §5): header anatomy, rows, boards, and the
// server-action bar with confirm, input and typed confirmation.
import React from 'react';

import { act, fireEvent, render } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ActionBar, Board, BoardSection, PageHeader, Row, Rows, StateSentence, useServerActions, type ServerAction } from '../../src/ui';

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

describe('PageHeader', () => {
  it('shows the title, the state sentence without empty parts, and text tabs', async () => {
    const onTab = jest.fn();
    const ui = wrap(<PageHeader testID="ph" title="Work" state={[{ text: '2 need you', tone: 'attention' }, null, false, { text: '3 running' }]}
      tabs={[{ key: 'now', label: 'Now' }, { key: 'jobs', label: 'Jobs', count: 4 }]} tab="jobs" onTab={onTab} />);
    expect(ui.getByRole('header').props.children).toBe('Work');
    expect(ui.getByText('2 need you', { exact: false })).toBeTruthy();
    expect(ui.getByText('3 running', { exact: false })).toBeTruthy();
    expect(ui.getByTestId('ph-tabs-jobs').props.accessibilityState).toMatchObject({ selected: true });
    fireEvent.press(ui.getByTestId('ph-tabs-now'));
    expect(onTab).toHaveBeenCalledWith('now');
    await act(async () => undefined);
  });

  it('puts secondary actions under the ⋯ menu', async () => {
    const onPress = jest.fn();
    const ui = wrap(<PageHeader testID="ph" title="Work" menu={[{ key: 'activity', label: 'Activity', onPress }]} />);
    fireEvent.press(ui.getByTestId('ph-menu'));
    fireEvent.press(ui.getByText('Activity'));
    expect(onPress).toHaveBeenCalled();
    await act(async () => undefined);
  });

  it('renders nothing for an empty state sentence', () => {
    const ui = wrap(<StateSentence testID="s" parts={[null, false]} />);
    expect(ui.queryByTestId('s')).toBeNull();
  });
});

describe('Rows and boards', () => {
  it('labels the status sign in words and opens the row', async () => {
    const onPress = jest.fn();
    const ui = wrap(<Rows><Row testID="r" status="needs_you" title="Fix the build" sub="job 12" when="2m" onPress={onPress} /></Rows>);
    expect(ui.getByText('Fix the build')).toBeTruthy();
    expect(ui.getByText('2m')).toBeTruthy();
    fireEvent.press(ui.getByText('Fix the build'));
    expect(onPress).toHaveBeenCalled();
    await act(async () => undefined);
  });

  it('says so when a list is empty, and hides empty board sections without a message', () => {
    const ui = wrap(<Board><Rows empty="No jobs yet" /><BoardSection title="Running" testID="running" /><BoardSection title="Needs you" empty="Nothing needs you" testID="needs" /></Board>);
    expect(ui.getByText('No jobs yet')).toBeTruthy();
    expect(ui.queryByTestId('running')).toBeNull();
    expect(ui.getByText('Nothing needs you')).toBeTruthy();
  });
});

describe('ActionBar with server actions', () => {
  function Harness({ actions, onRun }: { actions: ServerAction[]; onRun: (id: string, args: Record<string, unknown>) => void }) {
    const { ask, element } = useServerActions();
    return (
      <>
        <ActionBar testID="bar" actions={actions} onAction={async a => { const args = await ask(a); if (args) onRun(a.id, args); }} />
        {element}
      </>
    );
  }

  const actions: ServerAction[] = [
    { id: 'approve', label: 'Approve', primary: true, args: { decision: 'approve' } },
    { id: 'cancel', label: 'Cancel run', danger: true, confirm: 'Cancel this run?' },
    { id: 'discard', label: 'Discard', danger: true, input: { arg: 'confirm', label: 'Run name', expect: 'nightly' } },
    { id: 'push', label: 'Push', enabled: false, reason: 'Checks have not passed' },
    { id: 'secret', label: 'Hidden', hidden: true },
  ];

  it('runs a primary action straight away with its args', async () => {
    const onRun = jest.fn();
    const ui = wrap(<Harness actions={actions} onRun={onRun} />);
    await act(async () => { fireEvent.press(ui.getByTestId('bar-approve')); });
    expect(onRun).toHaveBeenCalledWith('approve', { decision: 'approve' });
  });

  it('keeps the rest under More, with disabled ones explaining why and hidden ones absent', async () => {
    const ui = wrap(<Harness actions={actions} onRun={jest.fn()} />);
    fireEvent.press(ui.getByTestId('bar-more'));
    expect(ui.getByText('Cancel run')).toBeTruthy();
    expect(ui.getByText('Checks have not passed')).toBeTruthy();
    expect(ui.queryByText('Hidden')).toBeNull();
    await act(async () => undefined);
  });

  it('asks before a confirmed action and does nothing when cancelled', async () => {
    const onRun = jest.fn();
    const ui = wrap(<Harness actions={actions} onRun={onRun} />);
    fireEvent.press(ui.getByTestId('bar-more'));
    await act(async () => { fireEvent.press(ui.getByText('Cancel run')); });
    expect(ui.getByText('Cancel this run?')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByText('Cancel')); });
    expect(onRun).not.toHaveBeenCalled();
  });

  it('needs the exact typed confirmation before a destructive action', async () => {
    const onRun = jest.fn();
    const ui = wrap(<Harness actions={actions} onRun={onRun} />);
    fireEvent.press(ui.getByTestId('bar-more'));
    await act(async () => { fireEvent.press(ui.getByText('Discard')); });
    expect(ui.getByText('Type nightly to confirm.')).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('server-action-input'), 'night');
    expect(ui.getByTestId('server-action-ok').props.accessibilityState).toMatchObject({ disabled: true });
    fireEvent.changeText(ui.getByTestId('server-action-input'), 'nightly');
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(onRun).toHaveBeenCalledWith('discard', { confirm: 'nightly' });
  });
});

