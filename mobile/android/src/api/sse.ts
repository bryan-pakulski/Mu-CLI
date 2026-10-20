// One SSE manager for the app's three streams (documentation/plans/mobile_parity.md §7):
//   /api/events?session=NAME     chat, prompts and attention (session-filtered on the server)
//   /api/jobs/events             job changes {scope, id, parts, seq}
//   /api/orchestration/events    orchestration changes {scope, id, parts, seq}
//
// Subscribers share one EventSource per (path, session): the first subscriber opens it, the last
// one closes it. Every connection is signed with the device token (api/auth.ts), resumes from the
// last event id it saw (`Last-Event-ID`, replayed by the server), and reconnects only while the app
// is in the foreground, with backoff. A 401 marks the phone as needing pairing.

import { AppState, type AppStateStatus } from 'react-native';
import EventSource from 'react-native-sse';
import { authHeaders } from './auth';

export const DEFAULT_RECONNECT_DELAY_MS = 2_500;

export type StreamEvent = { kind: string; [key: string]: unknown };

export interface SSEHandlers {
  onMessage?: (event: StreamEvent) => void;
  onOpen?: () => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export interface SSESubscription {
  close: () => void;
}

export interface SSEOptions {
  /** The session to filter on; undefined = the active session, null = no filter. */
  sessionName?: string | null;
  path?: string;
  decode?: (value: unknown) => StreamEvent | null;
  reconnectDelayMs?: number;
}

type Subscriber = { handlers: SSEHandlers; decode?: SSEOptions['decode'] };

type Stream = {
  key: string;
  path: string;
  session: string | null;
  subscribers: Set<Subscriber>;
  source: EventSource | null;
  open: boolean;
  lastEventId: string | null;
  attempt: number;
  timer: ReturnType<typeof setTimeout> | null;
  reconnectDelayMs: number;
};

const streams = new Map<string, Stream>();
let appState: AppStateStatus = AppState.currentState;
let appStateSubscription: { remove: () => void } | null = null;

const isForeground = () => appState !== 'background' && appState !== 'inactive';

function connection() {
  // Lazy: avoids a require cycle with the store, and always reads the newest server.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require('../store/connection').useConnectionStore.getState();
}

/** The stream URL: `?session=` is the server's filter (mu/gui/routers/chat.py stream_events). */
export function streamUrl(baseUrl: string, path: string, session: string | null): string {
  let url = `${baseUrl}${path}`;
  if (session) url += `${url.includes('?') ? '&' : '?'}session=${encodeURIComponent(session)}`;
  return url;
}

function ensureAppStateListener() {
  if (appStateSubscription) return;
  appStateSubscription = AppState.addEventListener('change', next => {
    const was = isForeground();
    appState = next;
    streams.forEach(stream => {
      if (!isForeground()) { clearTimer(stream); destroy(stream); return; }
      if (!was) { stream.attempt = 0; schedule(stream, true); }
    });
  });
}

function clearTimer(stream: Stream) {
  if (stream.timer) { clearTimeout(stream.timer); stream.timer = null; }
}

function destroy(stream: Stream) {
  const current = stream.source;
  stream.source = null;
  stream.open = false;
  if (!current) return;
  current.removeAllEventListeners();
  current.close();
}

function schedule(stream: Stream, immediate = false) {
  if (!streams.has(stream.key) || !isForeground() || stream.timer) return;
  const delay = immediate ? 0 : Math.min(15_000, stream.reconnectDelayMs * (2 ** Math.min(stream.attempt, 3)));
  stream.attempt += 1;
  stream.timer = setTimeout(() => { stream.timer = null; connect(stream); }, delay);
}

function each(stream: Stream, fn: (s: Subscriber) => void) {
  Array.from(stream.subscribers).forEach(sub => { try { fn(sub); } catch (err) { console.warn('SSE subscriber failed', err); } });
}

function connect(stream: Stream) {
  if (!streams.has(stream.key) || !isForeground()) return;
  clearTimer(stream);
  destroy(stream);
  const base = connection().baseUrl as string;
  const headers: Record<string, string> = { Accept: 'text/event-stream', ...authHeaders(base) };
  if (stream.lastEventId) headers['Last-Event-ID'] = stream.lastEventId;
  // pollingInterval 0: our foreground-aware loop owns reconnection (no second, hidden loop).
  const next = new EventSource(streamUrl(base, stream.path, stream.session), { headers, pollingInterval: 0 });
  stream.source = next;

  next.addEventListener('open', () => {
    if (stream.source !== next) return;
    stream.attempt = 0;
    stream.open = true;
    each(stream, sub => sub.handlers.onOpen?.());
  });

  next.addEventListener('message', event => {
    if (stream.source !== next || !event.data) return;
    if (event.lastEventId) stream.lastEventId = String(event.lastEventId);
    let parsed: unknown;
    try {
      parsed = JSON.parse(event.data);
    } catch {
      each(stream, sub => sub.handlers.onError?.(new Error(`SSE: malformed JSON payload: ${String(event.data).slice(0, 120)}`)));
      return;
    }
    each(stream, sub => {
      const value = sub.decode ? sub.decode(parsed) : parsed;
      if (!value || typeof value !== 'object' || typeof (value as { kind?: unknown }).kind !== 'string') {
        if (!sub.decode) sub.handlers.onError?.(new Error('SSE: event missing string "kind" field'));
        return;
      }
      sub.handlers.onMessage?.(value as StreamEvent);
    });
  });

  next.addEventListener('error', event => {
    if (stream.source !== next) return;
    const status = (event as unknown as { xhrStatus?: number }).xhrStatus;
    if (status === 401) connection().setPairingRequired?.(true);
    const msg = (event as unknown as { message?: string })?.message || 'SSE error';
    each(stream, sub => sub.handlers.onError?.(new Error(msg)));
    destroy(stream);
    schedule(stream);
  });

  next.addEventListener('close', () => {
    if (stream.source !== next) return;
    each(stream, sub => sub.handlers.onClose?.());
    destroy(stream);
    schedule(stream);
  });
}

export function subscribeToEvents(handlers: SSEHandlers, options: SSEOptions = {}): SSESubscription {
  const session = options.sessionName === undefined ? (connection().activeSessionName as string | null) : options.sessionName;
  const path = options.path || '/api/events';
  const key = `${path}|${session || ''}`;
  ensureAppStateListener();
  let stream = streams.get(key);
  const subscriber: Subscriber = { handlers, decode: options.decode };
  if (!stream) {
    stream = {
      key, path, session: session || null, subscribers: new Set(), source: null, open: false, lastEventId: null,
      attempt: 0, timer: null, reconnectDelayMs: Math.max(1_000, options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS),
    };
    streams.set(key, stream);
    stream.subscribers.add(subscriber);
    connect(stream);
  } else {
    stream.subscribers.add(subscriber);
    if (stream.open) handlers.onOpen?.();
  }
  const owner = stream;
  let closed = false;
  return {
    close: () => {
      if (closed) return;
      closed = true;
      owner.subscribers.delete(subscriber);
      if (owner.subscribers.size === 0) {
        streams.delete(owner.key);
        clearTimer(owner);
        destroy(owner);
      }
    },
  };
}

export function subscribeToKind(kind: string, onEvent: (data: Record<string, unknown>) => void, options?: SSEOptions): SSESubscription {
  return subscribeToEvents({ onMessage: event => { if (event.kind === kind) onEvent(event); } }, options);
}

/** Reconnect every open stream now (after pairing or a server change). */
export function reconnectAllStreams(): void {
  streams.forEach(stream => { stream.attempt = 0; stream.lastEventId = null; connect(stream); });
}

/** Test hook: the number of live shared streams. */
export function liveStreamCount(): number {
  return streams.size;
}
