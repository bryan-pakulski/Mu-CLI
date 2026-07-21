import { useConnectionStore } from '../store/connection';

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export function baseUrl(): string {
  return useConnectionStore.getState().baseUrl;
}

function sessionParam(): string {
  const name = useConnectionStore.getState().activeSessionName;
  return name ? `?session_name=${encodeURIComponent(name)}` : '';
}

async function request<T>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  opts?: {
    body?: Record<string, unknown>;
    signal?: AbortSignal;
    query?: Record<string, string | number | boolean | undefined>;
  },
): Promise<T> {
  const base = baseUrl();
  let url = `${base}${path}`;

  // Append session_name query param for session-scoped endpoints
  if (method === 'GET' || method === 'DELETE') {
    const sep = url.includes('?') ? '&' : '?';
    const sn = useConnectionStore.getState().activeSessionName;
    if (sn && !path.includes('session_name')) {
      url += `${sep}session_name=${encodeURIComponent(sn)}`;
    }
  }

  // Append extra query params
 if (opts?.query) {
    for (const [key, value] of Object.entries(opts.query)) {
      if (value !== undefined) {
        const sep = url.includes('?') ? '&' : '?';
        url += `${sep}${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`;
      }
    }
  }

  const headers: Record<string, string> = {};
  let bodyStr: string | undefined;
  if (opts?.body) {
    headers['Content-Type'] = 'application/json';
    bodyStr = JSON.stringify(opts.body);
  }

  const response = await fetch(url, {
    method,
    headers,
    body: bodyStr,
    signal: opts?.signal,
  });

  if (!response.ok) {
    let errorBody: unknown;
    let errorMsg = `HTTP ${response.status}`;
    try {
      errorBody = await response.json();
      if (errorBody && typeof errorBody === 'object' && 'detail' in errorBody) {
        errorMsg = String((errorBody as Record<string, unknown>).detail);
      }
    } catch {
      try {
        errorBody = await response.text();
      } catch {
        // ignore
      }
    }
    throw new ApiError(response.status, errorMsg, errorBody);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json() as Promise<T>;
  }
  return (await response.text()) as unknown as T;
}

export const api = {
  get<T>(path: string, opts?: { signal?: AbortSignal; query?: Record<string, string | number | boolean | undefined> }): Promise<T> {
    return request<T>('GET', path, opts);
  },
  post<T>(path: string, body?: Record<string, unknown>, opts?: { signal?: AbortSignal }): Promise<T> {
    return request<T>('POST', path, { body, signal: opts?.signal });
  },
  put<T>(path: string, body?: Record<string, unknown>, opts?: { signal?: AbortSignal }): Promise<T> {
    return request<T>('PUT', path, { body, signal: opts?.signal });
  },
  delete<T>(path: string, opts?: { signal?: AbortSignal; query?: Record<string, string | number | boolean | undefined> }): Promise<T> {
    return request<T>('DELETE', path, opts);
  },
};

// Health check helper
export async function checkHealth(baseUrl?: string): Promise<boolean> {
  const url = (baseUrl || useConnectionStore.getState().baseUrl) + '/healthz';
  try {
    const resp = await fetch(url, { method: 'GET' });
    return resp.ok;
  } catch {
    return false;
  }
}