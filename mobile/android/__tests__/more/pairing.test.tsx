// Device sign-in by pairing (documentation/plans/mobile_parity.md §7a): the phone exchanges a
// pairing code for a token, keeps it in the secure store per server, signs every request with it,
// and a 401 "pairing required" puts the app back into the pairing state.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import * as SecureStore from 'expo-secure-store';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();
let mockParams: Record<string, string> | undefined;
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate, goBack: mockGoBack, canGoBack: () => true }),
  useRoute: () => ({ params: mockParams }),
}));

import {
  authHeaders, cachedToken, isPairingRequired, loadToken, normalizeCode, parsePairingLink, resetAuthCache, tokenKey,
} from '../../src/api/auth';
import { api } from '../../src/api/client';
import { devicesApi } from '../../src/api/devices';
import { PairScreen } from '../../src/screens/PairScreen';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const BASE = 'http://192.168.1.20:30311';
const store = (SecureStore as unknown as { __store: Map<string, string> }).__store;

function respond(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

const fetchMock = jest.fn();
beforeEach(() => {
  fetchMock.mockReset();
  (global as unknown as { fetch: unknown }).fetch = fetchMock;
  store.clear();
  resetAuthCache();
  mockParams = undefined;
  mockNavigate.mockReset();
  mockGoBack.mockReset();
  useConnectionStore.setState({ baseUrl: BASE, activeSessionName: null, pairingRequired: false, isConnected: false });
});

describe('pairing links and codes', () => {
  it('reads the QR payload the computer shows', () => {
    expect(parsePairingLink(`mucli://pair?code=K7QM-3XRT&url=${encodeURIComponent(BASE + '/')}`)).toEqual({ code: 'K7QM3XRT', url: BASE });
    expect(parsePairingLink('mucli://pair?code=SHORT')).toBeNull();
    expect(parsePairingLink('https://example.com/?code=K7QM3XRT')).toBeNull();
    expect(normalizeCode(' k7qm-3xrt ')).toBe('K7QM3XRT');
  });

  it('keeps one secure-store key per server, using only allowed characters', () => {
    expect(tokenKey(BASE)).toMatch(/^[A-Za-z0-9._-]+$/);
    expect(tokenKey(BASE)).not.toBe(tokenKey('http://192.168.1.21:30311'));
    expect(tokenKey(BASE + '/')).toBe(tokenKey(BASE));
  });

  it('recognises only the server\'s pairing-required answer', () => {
    expect(isPairingRequired(401, { detail: 'Pair this device', pairing_required: true })).toBe(true);
    expect(isPairingRequired(401, { detail: 'nope' })).toBe(false);
    expect(isPairingRequired(403, { pairing_required: true })).toBe(false);
  });
});

describe('devicesApi', () => {
  it('pairs, stores the token securely and signs later requests with it', async () => {
    fetchMock.mockImplementationOnce(() => respond(200, { token: 'tok-1', device: { id: 'd1', name: 'Pixel 8', created_at: 1, last_seen: 1, address: '' } }));
    const device = await devicesApi.pair(BASE, 'k7qm-3xrt', 'Pixel 8');
    expect(device.name).toBe('Pixel 8');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/devices/pair`);
    expect(JSON.parse(init.body)).toEqual({ code: 'K7QM3XRT', device_name: 'Pixel 8' });
    expect(store.get(tokenKey(BASE))).toBe('tok-1');
    expect(authHeaders(BASE)).toEqual({ Authorization: 'Bearer tok-1' });
    expect(authHeaders('http://other:30311')).toEqual({});

    fetchMock.mockImplementationOnce(() => respond(200, { ok: true }));
    await api.get('/api/sessions');
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer tok-1');
  });

  it('loads a saved token after a restart', async () => {
    store.set(tokenKey(BASE), 'tok-saved');
    expect(cachedToken(BASE)).toBeNull();
    await loadToken(BASE);
    expect(cachedToken(BASE)).toBe('tok-saved');
  });

  it('a wrong code surfaces the server message and stores nothing', async () => {
    fetchMock.mockImplementationOnce(() => respond(403, { detail: 'That pairing code is wrong or has expired.' }));
    await expect(devicesApi.pair(BASE, 'AAAA-BBBB', 'Phone')).rejects.toThrow('That pairing code is wrong or has expired.');
    expect(store.size).toBe(0);
  });

  it('signing out revokes on the server and forgets the token even if the server is gone', async () => {
    store.set(tokenKey(BASE), 'tok-1');
    await loadToken(BASE);
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error('offline')));
    await expect(devicesApi.signOut(BASE)).rejects.toThrow('offline');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'DELETE', headers: { Authorization: 'Bearer tok-1' } });
    expect(cachedToken(BASE)).toBeNull();
    expect(store.has(tokenKey(BASE))).toBe(false);
  });
});

describe('a revoked or unpaired phone', () => {
  it('goes back to the pairing state on a 401 pairing_required', async () => {
    fetchMock.mockImplementationOnce(() => respond(401, { detail: 'Pair this device with mucli first.', pairing_required: true }));
    await expect(api.get('/api/sessions')).rejects.toThrow('Pair this device with mucli first.');
    expect(useConnectionStore.getState().pairingRequired).toBe(true);
  });

  it('other errors leave the pairing state alone', async () => {
    fetchMock.mockImplementationOnce(() => respond(401, { detail: 'Bad session' }));
    await expect(api.get('/api/sessions')).rejects.toThrow();
    expect(useConnectionStore.getState().pairingRequired).toBe(false);
  });
});

describe('PairScreen', () => {
  const renderScreen = () => render(<ThemeProvider statusBar={false}><PairScreen /></ThemeProvider>);

  function routeFetch(pairStatus = 200) {
    fetchMock.mockImplementation((url: string) => {
      if (url.endsWith('/healthz')) return respond(200, { ok: true });
      if (url.endsWith('/api/devices/self')) return respond(401, { detail: 'Pair', pairing_required: true });
      if (url.endsWith('/api/devices/pair')) {
        return pairStatus === 200
          ? respond(200, { token: 'tok-2', device: { id: 'd2', name: 'My phone', created_at: 1, last_seen: 1, address: '' } })
          : respond(pairStatus, { detail: 'That pairing code is wrong or has expired.' });
      }
      return respond(200, {});
    });
  }

  it('pairs from a typed address and code, then returns', async () => {
    routeFetch();
    useConnectionStore.setState({ pairingRequired: true });
    const ui = renderScreen();
    fireEvent.changeText(ui.getByTestId('pair-url'), BASE);
    fireEvent.changeText(ui.getByTestId('pair-code'), 'k7qm-3xrt');
    fireEvent.changeText(ui.getByTestId('pair-name'), 'My phone');
    await act(async () => { fireEvent.press(ui.getByTestId('pair-submit')); });
    await waitFor(() => expect(mockGoBack).toHaveBeenCalled());
    expect(store.get(tokenKey(BASE))).toBe('tok-2');
    expect(useConnectionStore.getState().pairingRequired).toBe(false);
    expect(useConnectionStore.getState().baseUrl).toBe(BASE);
  });

  it('rejects a short code before calling the server', async () => {
    routeFetch();
    const ui = renderScreen();
    fireEvent.changeText(ui.getByTestId('pair-code'), 'K7QM');
    await act(async () => { fireEvent.press(ui.getByTestId('pair-submit')); });
    expect(ui.getByTestId('pair-error')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/api/devices/pair'))).toBe(false);
  });

  it('shows the server\'s reason when the code is wrong', async () => {
    routeFetch(403);
    const ui = renderScreen();
    fireEvent.changeText(ui.getByTestId('pair-code'), 'AAAABBBB');
    await act(async () => { fireEvent.press(ui.getByTestId('pair-submit')); });
    await waitFor(() => expect(ui.getByText('That pairing code is wrong or has expired.')).toBeTruthy());
    expect(mockGoBack).not.toHaveBeenCalled();
  });

  it('pairs straight from a scanned QR code', async () => {
    routeFetch();
    const ui = renderScreen();
    await act(async () => { fireEvent.press(ui.getByTestId('pair-scan')); });
    const camera = ui.getByTestId('camera-view');
    await act(async () => { camera.props.onBarcodeScanned({ data: `mucli://pair?code=K7QM3XRT&url=${encodeURIComponent(BASE)}` }); });
    await waitFor(() => expect(store.get(tokenKey(BASE))).toBe('tok-2'));
  });

  it('fills the fields from a mucli://pair deep link', async () => {
    routeFetch();
    mockParams = { url: BASE, code: 'K7QM3XRT' };
    const ui = renderScreen();
    expect(ui.getByTestId('pair-url').props.value).toBe(BASE);
    expect(ui.getByTestId('pair-code').props.value).toBe('K7QM3XRT');
    await act(async () => undefined);
  });
});
