// Pairing with a mucli GUI (mu/gui/routers/devices.py).

import { ApiError } from './client';
import { clearToken, normalizeBase, normalizeCode, saveToken, authHeaders } from './auth';

export type PairedDevice = { id: string; name: string; created_at: number; last_seen: number; address: string };

async function json<T>(res: Response): Promise<T> {
  const raw = await res.text();
  let body: unknown = raw;
  try { body = raw ? JSON.parse(raw) : {}; } catch { /* text */ }
  if (!res.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? String((body as { detail: unknown }).detail) : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}

export const devicesApi = {
  /** Exchange a pairing code for this phone's token (stored securely for `baseUrl`). */
  async pair(baseUrl: string, code: string, deviceName: string): Promise<PairedDevice> {
    const base = normalizeBase(baseUrl);
    const res = await fetch(`${base}/api/devices/pair`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: normalizeCode(code), device_name: deviceName }),
    });
    const data = await json<{ token: string; device: PairedDevice }>(res);
    await saveToken(base, data.token);
    return data.device;
  },
  /** Who this phone is to `baseUrl` (null device = not paired / loopback). Throws 401 when unpaired. */
  async whoami(baseUrl: string): Promise<PairedDevice | null> {
    const base = normalizeBase(baseUrl);
    const res = await fetch(`${base}/api/devices/self`, { headers: authHeaders(base) });
    return (await json<{ device: PairedDevice | null }>(res)).device;
  },
  /** Sign this phone out of `baseUrl` (revokes its token on the server, forgets it here). */
  async signOut(baseUrl: string): Promise<void> {
    const base = normalizeBase(baseUrl);
    try { await fetch(`${base}/api/devices/self`, { method: 'DELETE', headers: authHeaders(base) }); }
    finally { await clearToken(base); }
  },
};
