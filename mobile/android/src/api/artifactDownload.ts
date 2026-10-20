// Save a check artifact or an evidence file onto the phone (the web's download link). The file is
// fetched with this phone's device token into the app cache, then written into a folder the user
// picks through Android's Storage Access Framework. Nothing is opened or rendered by the app:
// HTML reports and binaries come from code under test and are only stored.

import * as FileSystem from 'expo-file-system';
import { baseUrl } from './client';
import { authHeaders } from './auth';

/** Larger files are left to the computer: the write goes through memory as base64. */
export const MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024;

export type SaveOutcome = { saved: true; name: string } | { saved: false; reason: 'cancelled' | 'too-large' };

const enc = encodeURIComponent;

export function checkArtifactUrl(base: string, jobId: string, run: number, path: string): string {
  return `${base}/api/jobs/${enc(jobId)}/checks/${enc(String(run))}/files/${path.split('/').map(enc).join('/')}`;
}

export function evidenceFileUrl(base: string, jobId: string, file: string): string {
  return `${base}/api/jobs/${enc(jobId)}/evidence/${file.split('/').map(enc).join('/')}`;
}

/** A file name safe for the cache and the picked folder (as the server's Content-Disposition). */
export function safeFileName(name: string): string {
  const clean = String(name || '').split('/').pop()!.replace(/[^A-Za-z0-9._-]/g, '_').replace(/^\.+/, '');
  return clean || 'artifact';
}

async function save(url: string, name: string, bytes?: number): Promise<SaveOutcome> {
  if (bytes != null && bytes > MAX_DOWNLOAD_BYTES) return { saved: false, reason: 'too-large' };
  const fileName = safeFileName(name);
  const dir = `${FileSystem.cacheDirectory}artifacts/`;
  await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => undefined);
  const tmp = `${dir}${Date.now()}-${fileName}`;
  try {
    const base = baseUrl();
    const res = await FileSystem.downloadAsync(url, tmp, { headers: authHeaders(base) });
    if (res.status !== 200) throw new Error(res.status === 401 ? 'This phone needs pairing again.' : `The server answered ${res.status}.`);
    const saf = FileSystem.StorageAccessFramework;
    const perm = await saf.requestDirectoryPermissionsAsync();
    if (!perm.granted) return { saved: false, reason: 'cancelled' };
    const dest = await saf.createFileAsync(perm.directoryUri, fileName, 'application/octet-stream');
    const data = await FileSystem.readAsStringAsync(tmp, { encoding: FileSystem.EncodingType.Base64 });
    await saf.writeAsStringAsync(dest, data, { encoding: FileSystem.EncodingType.Base64 });
    return { saved: true, name: fileName };
  } finally {
    await FileSystem.deleteAsync(tmp, { idempotent: true }).catch(() => undefined);
  }
}

export function saveCheckArtifact(jobId: string, run: number, file: { path: string; name?: string | null; bytes?: number }): Promise<SaveOutcome> {
  return save(checkArtifactUrl(baseUrl(), jobId, run, file.path), file.name || file.path, file.bytes);
}

export function saveEvidenceFile(jobId: string, item: { file: string; name?: string; bytes?: number }): Promise<SaveOutcome> {
  return save(evidenceFileUrl(baseUrl(), jobId, item.file), item.name || item.file, item.bytes);
}
