// Session files on the phone: downloads go through the app with the device token (to the share
// sheet), never to the browser, which has no token and would be asked to pair; a failed delete
// says why; the visualization viewer sends the token too.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('expo-file-system', () => ({
  cacheDirectory: 'file:///cache/',
  makeDirectoryAsync: jest.fn(async () => undefined),
  deleteAsync: jest.fn(async () => undefined),
  downloadAsync: jest.fn(async (_url: string, dest: string) => ({ status: 200, uri: dest })),
}));
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: jest.fn(), goBack: jest.fn(), canGoBack: () => true }),
  useRoute: () => ({ params: undefined }),
  useFocusEffect: jest.fn(),
  useIsFocused: () => true,
}));
jest.mock('react-native-webview', () => {
  const React = require('react');
  const { View } = require('react-native');
  return { WebView: (props: Record<string, unknown>) => React.createElement(View, { testID: 'webview', ...props }) };
});
jest.mock('../../src/api/urlSafety', () => ({ ...jest.requireActual('../../src/api/urlSafety'), openExternalUrl: jest.fn() }));
jest.mock('../../src/api/artifacts', () => {
  const actual = jest.requireActual('../../src/api/artifacts');
  return { ...actual, artifactsApi: { ...actual.artifactsApi, list: jest.fn(), remove: jest.fn() } };
});
jest.mock('../../src/api/attachments', () => {
  const actual = jest.requireActual('../../src/api/attachments');
  return { ...actual, attachmentsApi: { ...actual.attachmentsApi, list: jest.fn(async () => ({ attachments: [] })), remove: jest.fn() } };
});

import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';
import { ApiError } from '../../src/api/client';
import { artifactsApi } from '../../src/api/artifacts';
import { resetAuthCache, saveToken } from '../../src/api/auth';
import { openExternalUrl } from '../../src/api/urlSafety';
import { ArtifactsScreen } from '../../src/screens/ArtifactsScreen';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

const BASE = 'http://192.168.1.20:30311';
const APK = { artifact_id: 'a1', name: 'mucli-mobile-0.6.0.apk', kind: 'file', size: 90_000_000, mime_type: 'application/vnd.android.package-archive', created_at: 1 };
const VIZ = { artifact_id: 'v1', name: 'chart.html', title: 'Chart', kind: 'visualization', size: 2000, mime_type: 'text/html', created_at: 2 };

beforeEach(async () => {
  jest.clearAllMocks();
  resetAuthCache();
  await saveToken(BASE, 'tok-1');
  useConnectionStore.setState({ baseUrl: BASE, activeSessionName: 'mucli', isConnected: true });
  (artifactsApi.list as jest.Mock).mockResolvedValue({ artifacts: [APK, VIZ] });
});

const renderScreen = () => render(<ThemeProvider statusBar={false}><ToastHost><ArtifactsScreen /></ToastHost></ThemeProvider>);

it('downloads with the device token to the share sheet, never the browser — whatever the size', async () => {
  const ui = renderScreen();
  await waitFor(() => expect(ui.getByText(APK.name)).toBeTruthy());
  await act(async () => { fireEvent.press(ui.getByText(APK.name)); });
  await waitFor(() => expect(Sharing.shareAsync).toHaveBeenCalled());
  const [url, dest, opts] = (FileSystem.downloadAsync as jest.Mock).mock.calls[0];
  expect(url).toBe(`${BASE}/api/sessions/mucli/artifacts/a1/download`);
  expect(dest).toMatch(/mucli-mobile-0\.6\.0\.apk$/);
  expect(opts.headers).toEqual({ Authorization: 'Bearer tok-1' });
  expect((Sharing.shareAsync as jest.Mock).mock.calls[0][1]).toMatchObject({ mimeType: APK.mime_type });
  expect(openExternalUrl).not.toHaveBeenCalled();
});

it('says why when the download is refused', async () => {
  (FileSystem.downloadAsync as jest.Mock).mockResolvedValueOnce({ status: 401, uri: 'x' });
  const ui = renderScreen();
  await waitFor(() => expect(ui.getByText(APK.name)).toBeTruthy());
  await act(async () => { fireEvent.press(ui.getByText(APK.name)); });
  expect(await ui.findByText(/needs pairing again/)).toBeTruthy();
  expect(Sharing.shareAsync).not.toHaveBeenCalled();
});

it('deletes after confirming, and reports a refused delete instead of doing nothing', async () => {
  (artifactsApi.remove as jest.Mock).mockRejectedValueOnce(new ApiError(500, 'disk full'));
  const ui = renderScreen();
  await waitFor(() => expect(ui.getByText(APK.name)).toBeTruthy());
  await act(async () => { fireEvent.press(ui.getByLabelText('Delete artifact')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(artifactsApi.remove).toHaveBeenCalledWith('mucli', 'a1');
  expect(await ui.findByText(/Couldn’t delete mucli-mobile-0.6.0.apk: disk full/)).toBeTruthy();

  (artifactsApi.remove as jest.Mock).mockResolvedValueOnce({ ok: true, artifact_id: 'a1' });
  (artifactsApi.list as jest.Mock).mockResolvedValue({ artifacts: [VIZ] });
  await act(async () => { fireEvent.press(ui.getByLabelText('Delete artifact')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  await waitFor(() => expect(ui.queryByText(APK.name)).toBeNull());
});

it('the visualization viewer sends the device token', async () => {
  const ui = renderScreen();
  await waitFor(() => expect(ui.getByText('Chart')).toBeTruthy());
  await act(async () => { fireEvent.press(ui.getByText('Chart')); });
  const web = ui.getByTestId('webview');
  expect(web.props.source).toMatchObject({ uri: `${BASE}/api/sessions/mucli/artifacts/v1/view`, headers: { Authorization: 'Bearer tok-1' } });
});
