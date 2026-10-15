// Wash component kit (src/ui): interaction rules and signature components.
import React from 'react';
import { Text as RNText } from 'react-native';
import { act, fireEvent, render } from '@testing-library/react-native';
import { ThemeProvider } from '../src/theme/ThemeContext';
import {
  ApprovalCard,
  Button,
  ContextRing,
  EmptyState,
  LayerBar,
  Pressable,
  Sheet,
  StatusChip,
  ToolCard,
  ToolGroup,
  WashBackground,
  contextTone,
  groupToolCalls,
  primaryArg,
  resolveVariant,
  toolKind,
} from '../src/ui';

const wrap = (ui: React.ReactElement) => render(<ThemeProvider statusBar={false}>{ui}</ThemeProvider>);

describe('Pressable', () => {
  it('defaults to a button role and expands small targets to 44dp with hit slop', () => {
    const ui = wrap(<Pressable testID="p" onPress={() => undefined} style={{ width: 20, height: 20 }} />);
    const node = ui.getByTestId('p');
    expect(node.props.accessibilityRole).toBe('button');
    fireEvent(node, 'layout', { nativeEvent: { layout: { width: 20, height: 20, x: 0, y: 0 } } });
    expect(ui.getByTestId('p').props.hitSlop).toEqual({ top: 12, bottom: 12, left: 12, right: 12 });
  });

  it('marks disabled controls in accessibilityState and does not fire', () => {
    const onPress = jest.fn();
    const ui = wrap(<Pressable testID="p" disabled onPress={onPress} />);
    expect(ui.getByTestId('p').props.accessibilityState).toEqual({ disabled: true });
    fireEvent.press(ui.getByTestId('p'));
    expect(onPress).not.toHaveBeenCalled();
  });
});

describe('Button', () => {
  it('maps legacy shorthands onto Wash variants', () => {
    expect(resolveVariant({ primary: true })).toBe('primary');
    expect(resolveVariant({ primary: true, tone: 'danger' })).toBe('danger');
    expect(resolveVariant({ tone: 'warn' })).toBe('tonal');
    expect(resolveVariant({ ghost: true })).toBe('ghost');
    expect(resolveVariant({})).toBe('secondary');
    expect(resolveVariant({ iconOnly: true })).toBe('icon');
  });

  it('renders its label, fires, and reports busy while loading', () => {
    const onPress = jest.fn();
    const ui = wrap(<Button label="Approve" onPress={onPress} testID="b" />);
    fireEvent.press(ui.getByText('Approve'));
    expect(onPress).toHaveBeenCalledTimes(1);
    ui.rerender(<ThemeProvider statusBar={false}><Button label="Approve" onPress={onPress} testID="b" loading /></ThemeProvider>);
    expect(ui.getByTestId('b').props.accessibilityState).toMatchObject({ busy: true, disabled: true });
  });
});

describe('Sheet', () => {
  it('offers a close action when dismissable', () => {
    const close = jest.fn();
    const ui = wrap(<Sheet visible onClose={close} title="Mode"><RNText>Body</RNText></Sheet>);
    fireEvent.press(ui.getByRole('button', { name: 'Close' }));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('has no implicit way out when not dismissable', () => {
    const ui = wrap(<Sheet visible onClose={jest.fn()} dismissable={false} title="Approve"><RNText>Body</RNText></Sheet>);
    expect(ui.queryByRole('button', { name: 'Close' })).toBeNull();
    expect(ui.getByText('Body')).toBeTruthy();
  });
});

describe('tool cards', () => {
  it('classifies tool kinds and picks the primary argument', () => {
    expect(toolKind('read_file')).toBe('read');
    expect(toolKind('write_file')).toBe('edit');
    expect(toolKind('run_shell_command')).toBe('shell');
    expect(toolKind('grep')).toBe('search');
    expect(toolKind('web_fetch')).toBe('web');
    expect(toolKind('mystery')).toBe('other');
    expect(primaryArg({ path: 'src/a.py', limit: 3 })).toBe('src/a.py');
    expect(primaryArg('{"command":"pytest -q"}')).toBe('pytest -q');
  });

  it('groups three or more consecutive calls of the same kind', () => {
    const c = (id: string, name: string) => ({ id, name, status: 'done' as const });
    const out = groupToolCalls([c('1', 'read_file'), c('2', 'read_file'), c('3', 'read_file'), c('4', 'grep'), c('5', 'read_file')]);
    expect(Array.isArray(out[0]) && (out[0] as unknown[]).length).toBe(3);
    expect(out.length).toBe(3);
    const ui = wrap(<ToolGroup calls={[c('1', 'read_file'), c('2', 'read_file'), c('3', 'read_file')]} />);
    expect(ui.getByText('Read 3 files')).toBeTruthy();
  });

  it('expands to show arguments and result', () => {
    const ui = wrap(<ToolCard call={{ id: '1', name: 'read_file', args: { path: 'src/a.py' }, status: 'done', result: 'hello', durationMs: 400 }} />);
    expect(ui.getByText('src/a.py')).toBeTruthy();
    fireEvent.press(ui.getByLabelText(/read src\/a.py, done/));
    expect(ui.getByText('hello')).toBeTruthy();
  });
});

describe('approval card', () => {
  it('approves only on an explicit press and carries the remember choice', () => {
    const approve = jest.fn();
    const remember = jest.fn();
    const ui = wrap(
      <ApprovalCard toolName="write_file" description="Allow write_file to run?" remember={false} onRememberChange={remember}
        onApprove={approve} onDeny={jest.fn()} approveTestID="ok" rememberTestID="rem" />,
    );
    expect(approve).not.toHaveBeenCalled();
    expect(ui.getByText('Needs you')).toBeTruthy();
    fireEvent.press(ui.getByTestId('rem'));
    expect(remember).toHaveBeenCalledWith(true);
    fireEvent.press(ui.getByTestId('ok'));
    expect(approve).toHaveBeenCalledTimes(1);
  });
});

describe('meters, chips, art', () => {
  it('uses the shared caution/danger thresholds', () => {
    expect(contextTone(0.5)).toBe('neutral');
    expect(contextTone(0.65)).toBe('caution');
    expect(contextTone(0.85)).toBe('danger');
    const ui = wrap(<ContextRing fill={0.42} />);
    expect(ui.getByLabelText('Context 42% full')).toBeTruthy();
  });

  it('labels every layer directly', () => {
    const ui = wrap(<LayerBar layers={[{ id: 'L0', tokens: 100 }, { id: 'L5', tokens: 300 }]} limit={1000} />);
    expect(ui.getByLabelText('Context layers: L0 10%, L5 30%')).toBeTruthy();
  });

  it('pairs status glyphs with vocabulary labels', () => {
    const ui = wrap(<StatusChip status="waiting" />);
    expect(ui.getByText('◆')).toBeTruthy();
    expect(ui.getByText('needs you')).toBeTruthy();
  });

  it('renders wash art, or a flat wash when decoration is reduced', async () => {
    const ui = wrap(<WashBackground art="dawn"><EmptyState title="No jobs yet" /></WashBackground>);
    expect(ui.getByTestId('wash-art', { includeHiddenElements: true })).toBeTruthy();
    expect(ui.getByText('No jobs yet')).toBeTruthy();
    await act(async () => undefined);
  });
});
