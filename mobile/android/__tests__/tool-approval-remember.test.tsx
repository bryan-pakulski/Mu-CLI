import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { ToolApprovalBody } from '../src/components/PromptHost';
import { ThemeProvider } from '../src/theme/ThemeContext';
import type { PendingPrompt } from '../src/api/prompts';

const prompt = {
  id: 'p1',
  shape: 'tool_approval',
  tool_name: 'write_file',
  tool_args: { path: 'src/app.py' },
} as unknown as PendingPrompt;

function renderBody(submit: jest.Mock) {
  return render(
    <ThemeProvider>
      <ToolApprovalBody prompt={prompt} submitting={false} submit={submit} cancel={jest.fn()} queueDepth={1} />
    </ThemeProvider>,
  );
}

describe('tool approval "Always allow" checkbox', () => {
  it('approves once by default', () => {
    const submit = jest.fn();
    const ui = renderBody(submit);
    expect(ui.getByTestId('tool-approval-remember').props.accessibilityState).toEqual({ checked: false });
    fireEvent.press(ui.getByTestId('tool-approval-allow'));
    expect(submit).toHaveBeenCalledWith({ approved: true, remember: false });
  });

  it('sends remember: true when the checkbox is checked', () => {
    const submit = jest.fn();
    const ui = renderBody(submit);
    fireEvent.press(ui.getByTestId('tool-approval-remember'));
    expect(ui.getByTestId('tool-approval-remember').props.accessibilityState).toEqual({ checked: true });
    expect(ui.getByText('Always allow')).toBeTruthy();
    fireEvent.press(ui.getByTestId('tool-approval-allow'));
    expect(submit).toHaveBeenCalledWith({ approved: true, remember: true });
  });

  it('offers a single allow action', () => {
    const ui = renderBody(jest.fn());
    expect(ui.queryByText('Always allow this tool', { exact: true })).toBeTruthy(); // checkbox label only
    expect(ui.getAllByText(/^(Allow once|Always allow)$/)).toHaveLength(1);
  });
});
