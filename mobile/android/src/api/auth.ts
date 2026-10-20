// Device sign-in (documentation/plans/mobile_parity.md §7a).
//
// A phone signs in by pairing: the computer shows a code (and a QR carrying the GUI address and
// the code); the phone exchanges it for a device token. The token is kept in the OS secure store
// (expo-secure-store), one per server, and sent as `Authorization: Bearer <token>` on every HTTP,
// SSE and WebSocket request. An in-memory cache makes it available synchronously to the API
// client. A 401 with `pairing_required` means this phone isn't (or no longer is) paired.

import * as SecureStore from 'expo-secure-store';

const cache = new Map<string, string | null>();

/** Secure-store keys allow [A-Za-z0-9._-] only. */
export function tokenKey(baseUrl: string): string {
  const clean = normalizeBase(baseUrl).replace(/^https?:\/\//, '').replace(/[^A-Za-z0-9._-]/g, '_');
  return `mucli.device.${clean}`.slice(0, 200);
}

export function normalizeBase(baseUrl: string): string {
  return String(baseUrl || '').trim().replace(/\/+$/, '');
}

/** Load the token for a server into the cache (call on start and when the server changes). */
export async function loadToken(baseUrl: string): Promise<string | null> {
  const base = normalizeBase(baseUrl);
  if (cache.has(base)) return cache.get(base) ?? null;
  let value: string | null = null;
  try { value = await SecureStore.getItemAsync(tokenKey(base)); } catch { value = null; }
  cache.set(base, value);
  return value;
}

export async function saveToken(baseUrl: string, token: string): Promise<void> {
  const base = normalizeBase(baseUrl);
  cache.set(base, token);
  await SecureStore.setItemAsync(tokenKey(base), token);
}

export async function clearToken(baseUrl: string): Promise<void> {
  const base = normalizeBase(baseUrl);
  cache.set(base, null);
  try { await SecureStore.deleteItemAsync(tokenKey(base)); } catch { /* already gone */ }
}

/** The cached token for a server (synchronous; null until loaded or when unpaired). */
export function cachedToken(baseUrl: string): string | null {
  return cache.get(normalizeBase(baseUrl)) ?? null;
}

/** Headers that sign a request to `baseUrl` in (empty when this phone isn't paired with it). */
export function authHeaders(baseUrl: string): Record<string, string> {
  const token = cachedToken(baseUrl);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** True for the server's "pair this device first" answer. */
export function isPairingRequired(status: number, body: unknown): boolean {
  return status === 401 && !!body && typeof body === 'object' && (body as { pairing_required?: unknown }).pairing_required === true;
}

/** Test hook. */
export function resetAuthCache(): void { cache.clear(); }

/** A pairing link (QR / deep link): `mucli://pair?code=K7QMR3XT&url=http://192.168.1.5:30311`. */
export function parsePairingLink(link: string): { code: string; url: string } | null {
  const text = String(link || '').trim();
  const match = /^mucli:\/\/pair\?(.*)$/i.exec(text);
  if (!match) return null;
  const params: Record<string, string> = {};
  for (const part of match[1].split('&')) {
    const [k, v = ''] = part.split('=');
    if (k) { try { params[decodeURIComponent(k)] = decodeURIComponent(v); } catch { params[k] = v; } }
  }
  const code = normalizeCode(params.code || '');
  return code.length === 8 ? { code, url: normalizeBase(params.url || '') } : null;
}

export function normalizeCode(code: string): string {
  return String(code || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}
