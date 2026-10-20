// The composer kit (src/ui/Composer.tsx): Send ↑ becomes Stop ■ while a turn runs (a slash command
// still sends); the foot shows mode, model, plan and auto-approve only while on, and the context
// ring; the mic records, transcribes and puts the text in the composer; slash items complete.
import React, { useState } from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { Composer, type ComposerProps } from '../../src/ui/Composer';
import { ThemeProvider } from '../../src/theme/ThemeContext';

function Harness(props: Partial<ComposerProps> & { initial?: string }) {
  const [value, setValue] = useState(props.initial || '');
  return <Composer value={value} onChangeText={setValue} onSend={jest.fn()} {...props} />;
}
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

it('sends only with content, and becomes Stop while busy', () => {
  const onSend = jest.fn();
  const onStop = jest.fn();
  const ui = wrap(<Harness onSend={onSend} onStop={onStop} />);
  expect(ui.getByTestId('composer-send').props.accessibilityState).toMatchObject({ disabled: true });
  fireEvent.changeText(ui.getByTestId('composer-input'), 'hello');
  fireEvent.press(ui.getByTestId('composer-send'));
  expect(onSend).toHaveBeenCalledTimes(1);
  ui.rerender(<ThemeProvider statusBar={false}><Harness onSend={onSend} onStop={onStop} busy initial="more" /></ThemeProvider>);
  fireEvent.press(ui.getByTestId('composer-stop'));
  expect(onStop).toHaveBeenCalled();
});

it('still sends a slash command while busy', () => {
  const onSend = jest.fn();
  const ui = wrap(<Harness onSend={onSend} busy initial="/plan on" />);
  expect(ui.queryByTestId('composer-stop')).toBeNull();
  fireEvent.press(ui.getByTestId('composer-send'));
  expect(onSend).toHaveBeenCalled();
});

it('shows the foot facts and hides plan and auto-approve while off', () => {
  const mode = jest.fn();
  const model = jest.fn();
  const autoOff = jest.fn();
  const foot = (on: boolean) => ({
    mode: { name: 'feature', label: 'Feature', onPress: mode },
    model: { label: 'qwen3.8:27b', onPress: model },
    plan: { on, onPress: jest.fn() },
    auto: { on, onPress: autoOff },
    context: { fill: 0.42 },
  });
  const ui = wrap(<Harness foot={foot(false)} />);
  expect(ui.queryByTestId('composer-plan')).toBeNull();
  expect(ui.queryByTestId('composer-auto')).toBeNull();
  fireEvent.press(ui.getByLabelText('Mode: Feature'));
  fireEvent.press(ui.getByText('qwen3.8:27b'));
  expect(mode).toHaveBeenCalled();
  expect(model).toHaveBeenCalled();
  expect(ui.getByLabelText('Context 42% full')).toBeTruthy();
  ui.rerender(<ThemeProvider statusBar={false}><Harness foot={foot(true)} /></ThemeProvider>);
  expect(ui.getByText('Plan mode')).toBeTruthy();
  fireEvent.press(ui.getByTestId('composer-auto'));
  expect(autoOff).toHaveBeenCalled();
  expect(ui.getByTestId('composer-input').props.placeholder).toMatch(/Plan mode/);
});

it('dictates: records, transcribes and appends the text', async () => {
  const transcribe = jest.fn(async () => 'and run the tests');
  const ui = wrap(<Harness transcribe={transcribe} initial="Fix the build" />);
  await act(async () => { fireEvent.press(ui.getByLabelText('Voice input')); });
  expect(ui.getByLabelText('Stop recording')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByLabelText('Stop recording')); });
  expect(transcribe).toHaveBeenCalledWith('file:///tmp/recording.m4a');
  expect(ui.getByTestId('composer-input').props.value).toBe('Fix the build and run the tests');
});

it('says why dictation failed', async () => {
  const ui = wrap(<Harness transcribe={async () => { throw new Error('STT failed: 503'); }} />);
  await act(async () => { fireEvent.press(ui.getByLabelText('Voice input')); });
  await act(async () => { fireEvent.press(ui.getByLabelText('Stop recording')); });
  expect(ui.getByTestId('composer-mic-error').props.children).toMatch(/STT failed: 503/);
});

it('has no mic without a transcriber, and lists slash completions', () => {
  const onSelect = jest.fn();
  const ui = wrap(<Harness slash={{ visible: true, items: [{ label: '/plan', desc: 'Read-only planning', value: '/plan' }], selectedIdx: 0, onSelect }} />);
  expect(ui.queryByLabelText('Voice input')).toBeNull();
  fireEvent.press(ui.getByText('/plan'));
  expect(onSelect).toHaveBeenCalledWith({ label: '/plan', desc: 'Read-only planning', value: '/plan' });
});
