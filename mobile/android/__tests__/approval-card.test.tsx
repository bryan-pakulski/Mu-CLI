// PromptHost's tool approval on the Wash ApprovalCard (G§9.9): attention
// state card, explicit Cancel/Allow, unchanged /api/prompts payloads.
import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { ToolApprovalBody } from '../src/components/PromptHost';
import { ThemeProvider } from '../src/theme/ThemeContext';
import type { PendingPrompt } from '../src/api/prompts';

const prompt = {
  id: 'p1', shape: 'tool_approval', tool_name: 'run_shell_command', tool_args: { command: 'pytest -q' }, risk: 'runs a command',
} as unknown as PendingPrompt;

function renderBody(submit = jest.fn(), cancel = jest.fn(), queueDepth = 1) {
  const ui = render(
    <ThemeProvider statusBar={false}>
      <ToolApprovalBody prompt={prompt} submitting={false} submit={submit} cancel={cancel} queueDepth={queueDepth} />
    </ThemeProvider>,
  );
  return { ui, submit, cancel };
}

it('shows the needs-you state, the target and the risk', () => {
  const { ui } = renderBody();
  expect(ui.getByText('Needs you')).toBeTruthy();
  expect(ui.getByText('Permission to run run_shell_command')).toBeTruthy();
  expect(ui.getByText('pytest -q')).toBeTruthy();
  expect(ui.getByText('Risk: runs a command')).toBeTruthy();
});

it('cancels through the prompt-cancel path and never approves on render', () => {
  const { ui, submit, cancel } = renderBody();
  expect(submit).not.toHaveBeenCalled();
  fireEvent.press(ui.getByText('Cancel'));
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(submit).not.toHaveBeenCalled();
});

it('shows the queue position when several prompts wait', () => {
  const { ui } = renderBody(jest.fn(), jest.fn(), 3);
  expect(ui.getByText('1 of 3')).toBeTruthy();
});
