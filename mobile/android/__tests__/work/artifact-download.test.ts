// Saving an artifact onto the phone: fetched with the device token, written into a folder the user
// picks (Storage Access Framework), the cached copy removed; nothing is opened.
jest.mock('expo-file-system', () => ({
  cacheDirectory: 'file:///cache/',
  EncodingType: { Base64: 'base64' },
  makeDirectoryAsync: jest.fn(async () => undefined),
  downloadAsync: jest.fn(async () => ({ status: 200 })),
  readAsStringAsync: jest.fn(async () => 'QUJD'),
  deleteAsync: jest.fn(async () => undefined),
  StorageAccessFramework: {
    requestDirectoryPermissionsAsync: jest.fn(async () => ({ granted: true, directoryUri: 'content://tree/Downloads' })),
    createFileAsync: jest.fn(async () => 'content://tree/Downloads/report.html'),
    writeAsStringAsync: jest.fn(async () => undefined),
  },
}));

import * as FileSystem from 'expo-file-system';
import { saveToken, resetAuthCache } from '../../src/api/auth';
import { MAX_DOWNLOAD_BYTES, safeFileName, saveCheckArtifact, saveEvidenceFile } from '../../src/api/artifactDownload';
import { useConnectionStore } from '../../src/store/connection';

const fs = FileSystem as unknown as Record<string, jest.Mock> & { StorageAccessFramework: Record<string, jest.Mock> };
const BASE = 'http://192.168.1.20:30311';

beforeEach(async () => {
  jest.clearAllMocks();
  resetAuthCache();
  await saveToken(BASE, 'tok-1');
  useConnectionStore.setState({ baseUrl: BASE });
});

it('downloads with the device token and stores the bytes in the picked folder', async () => {
  const out = await saveCheckArtifact('job 1', 2, { path: 'reports/cov report.html', name: 'cov report.html', bytes: 10 });
  expect(out).toEqual({ saved: true, name: 'cov_report.html' });
  const [url, , opts] = fs.downloadAsync.mock.calls[0];
  expect(url).toBe(`${BASE}/api/jobs/job%201/checks/2/files/reports/cov%20report.html`);
  expect(opts.headers.Authorization).toBe('Bearer tok-1');
  // Stored as an opaque file under its safe name; never rendered.
  expect(fs.StorageAccessFramework.createFileAsync).toHaveBeenCalledWith('content://tree/Downloads', 'cov_report.html', 'application/octet-stream');
  expect(fs.StorageAccessFramework.writeAsStringAsync).toHaveBeenCalledWith('content://tree/Downloads/report.html', 'QUJD', { encoding: 'base64' });
  expect(fs.deleteAsync).toHaveBeenCalled();
});

it('does nothing when the user cancels the folder picker, and still cleans up', async () => {
  fs.StorageAccessFramework.requestDirectoryPermissionsAsync.mockResolvedValueOnce({ granted: false });
  expect(await saveEvidenceFile('j', { file: 'capture.pcap', name: 'capture.pcap' })).toEqual({ saved: false, reason: 'cancelled' });
  expect(fs.downloadAsync.mock.calls[0][0]).toBe(`${BASE}/api/jobs/j/evidence/capture.pcap`);
  expect(fs.StorageAccessFramework.writeAsStringAsync).not.toHaveBeenCalled();
  expect(fs.deleteAsync).toHaveBeenCalled();
});

it('reports a server refusal instead of saving an error page', async () => {
  fs.downloadAsync.mockResolvedValueOnce({ status: 401 });
  await expect(saveCheckArtifact('j', 1, { path: 'a.bin' })).rejects.toThrow('pairing');
  expect(fs.StorageAccessFramework.createFileAsync).not.toHaveBeenCalled();
});

it('leaves very large files to the computer', async () => {
  expect(await saveCheckArtifact('j', 1, { path: 'big.tar', bytes: MAX_DOWNLOAD_BYTES + 1 })).toEqual({ saved: false, reason: 'too-large' });
  expect(fs.downloadAsync).not.toHaveBeenCalled();
});

it('never lets a name escape the folder', () => {
  expect(safeFileName('../../etc/passwd')).toBe('passwd');
  expect(safeFileName('..hidden')).toBe('hidden');
  expect(safeFileName('')).toBe('artifact');
});
