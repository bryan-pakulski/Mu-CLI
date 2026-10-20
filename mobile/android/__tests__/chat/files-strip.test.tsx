// The session files strip above the composer collapses into a small Files button at the right
// edge (with the count) and remembers that on the phone; the button brings it back.
import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../src/api/artifacts', () => {
  const actual = jest.requireActual('../../src/api/artifacts');
  return { ...actual, artifactsApi: { ...actual.artifactsApi, list: jest.fn() } };
});

import { artifactsApi } from '../../src/api/artifacts';
import { ArtifactStrip, FILES_STRIP_COLLAPSED_KEY } from '../../src/components/ArtifactStrip';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

const getItem = AsyncStorage.getItem as jest.Mock;
const setItem = AsyncStorage.setItem as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  (artifactsApi.list as jest.Mock).mockResolvedValue({ artifacts: [
    { artifact_id: 'a1', name: 'report.pdf', kind: 'file', size: 2048, created_at: 1 },
    { artifact_id: 'v1', name: 'chart.html', title: 'Chart', kind: 'visualization', size: 900, created_at: 2 },
  ] });
});

const renderStrip = () => render(<ThemeProvider statusBar={false}><ToastHost><ArtifactStrip sessionName="mucli" /></ToastHost></ThemeProvider>);

it('collapses into a Files button with the count, remembers it, and expands again', async () => {
  const ui = renderStrip();
  await waitFor(() => expect(ui.getByTestId('files-strip')).toBeTruthy());
  expect(ui.getByText('report.pdf')).toBeTruthy();

  await act(async () => { fireEvent.press(ui.getByTestId('files-strip-collapse')); });
  expect(ui.queryByText('report.pdf')).toBeNull();
  expect(ui.getByLabelText('Show session files, 2')).toBeTruthy();
  expect(setItem).toHaveBeenCalledWith(FILES_STRIP_COLLAPSED_KEY, '1');

  await act(async () => { fireEvent.press(ui.getByTestId('files-strip-expand')); });
  expect(ui.getByText('report.pdf')).toBeTruthy();
  expect(setItem).toHaveBeenLastCalledWith(FILES_STRIP_COLLAPSED_KEY, '0');
});

it('opens collapsed when it was collapsed last time', async () => {
  getItem.mockImplementation(async (key: string) => (key === FILES_STRIP_COLLAPSED_KEY ? '1' : null));
  const ui = renderStrip();
  await waitFor(() => expect(ui.getByTestId('files-strip-collapsed')).toBeTruthy());
  expect(ui.queryByText('report.pdf')).toBeNull();
  getItem.mockImplementation(async () => null);
});
