import React, { useState } from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { JobPlanEditor } from '../../src/components/JobPlanEditor';

const original = '---\nversion: 3\nauthor: human\n---\n## Summary\nKeep this summary.\n\n## Acceptance criteria\n- [AC-1] Original criterion\n\n## Checks\n- suite: unit   cmd: pytest tests -q\n\n## Risks / out of scope\n- Keep this risk.\n';

function Editor() {
  const [value,setValue] = useState(original);
  return <JobPlanEditor value={value} onChange={setValue} />;
}

it('edits, adds and reorders criteria without losing the rest of the Markdown', () => {
  const screen = render(<Editor />);
  fireEvent.press(screen.getByText('Edit Markdown'));
  fireEvent.changeText(screen.getByLabelText('Acceptance criteria 1 text'),'A saved setting persists');
  fireEvent.press(screen.getByText('Add criterion'));
  fireEvent.changeText(screen.getByLabelText('Acceptance criteria 2 text'),'A reload keeps the setting');
  fireEvent.press(screen.getByLabelText('Move Acceptance criteria 2 up'));
  const markdown = screen.getByLabelText('Plan Markdown').props.value;
  expect(markdown).toContain('- [AC-2] A reload keeps the setting\n- [AC-1] A saved setting persists');
  expect(markdown).toContain('version: 3');
  expect(markdown).toContain('Keep this summary.');
  expect(markdown).toContain('Keep this risk.');
  expect(markdown).toContain('cmd: pytest tests -q');
});

it('keeps check commands and raw Markdown edits in sync, including literal dollar signs', () => {
  const screen = render(<Editor />);
  fireEvent.press(screen.getByText('Edit Markdown'));
  fireEvent.changeText(screen.getByLabelText('Checks 1 text'),'pytest -k "price$"');
  expect(screen.getByLabelText('Plan Markdown').props.value).toContain('cmd: pytest -k "price$"');
  fireEvent.changeText(screen.getByLabelText('Plan Markdown'),original.replace('Original criterion','Edited Markdown'));
  expect(screen.getByLabelText('Acceptance criteria 1 text').props.value).toBe('Edited Markdown');
  fireEvent.press(screen.getByLabelText('Remove Checks 1'));
  expect(screen.getByLabelText('Plan Markdown').props.value).not.toContain('suite: unit');
  expect(screen.getByLabelText('Plan Markdown').props.value).toContain('Keep this risk.');
});
