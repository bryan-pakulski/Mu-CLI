// The live status update renders `inline code` as a chip with its text, never raw backticks.
import React from 'react';
import { render } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { StatusSheen, statusRuns } from '../../src/ui/StatusSheen';

it('splits prose from inline code', () => {
  expect(statusRuns('The two `dmesg-ramoops` dumps')).toEqual([
    { code: false, text: 'The two ' }, { code: true, text: 'dmesg-ramoops' }, { code: false, text: ' dumps' },
  ]);
  expect(statusRuns('a ``` b')).toEqual([{ code: false, text: 'a ``` b' }]);
});

it('shows the code text without backticks', () => {
  const ui = render(<ThemeProvider statusBar={false}><StatusSheen text="Reading `dmesg-ramoops` next." testID="s" /></ThemeProvider>);
  expect(ui.getAllByText('dmesg-ramoops').length).toBeGreaterThan(0);
  expect(ui.queryByText(/`/)).toBeNull();
});
