import EventSource from 'react-native-sse';

export const DEFAULT_RECONNECT_DELAY_MS = 2_500;

export interface SSEHandlers {
  onMessage?: (event: { kind: string; [key: string]: unknown }) => void;
  onOpen?: () => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export interface SSESubscription {
  close: () => void;
}

export interface SSEOptions {
  sessionName?: string | null;
  reconnectDelayMs?: number;
}

export function subscribeToEvents(
  handlers: SSEHandlers,
  options: SSEOptions = {},
): SSESubscription {
  // Import connection store lazily to avoid circular deps.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const connection = require('../store/connection').useConnectionStore.getState();
  const sessionName = options.sessionName === undefined
    ? connection.activeSessionName
    : options.sessionName;

  let url = `${connection.baseUrl}/api/events`;
  if (sessionName) {
    url += `?session_name=${encodeURIComponent(sessionName)}`;
  }

  const reconnectDelayMs = Math.max(
    1_000,
    options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS,
  );
  let closed = false;
  const es = new EventSource(url, {
    headers: { Accept: 'text/event-stream' },
    // react-native-sse defines pollingInterval as the delay before it
    // reconnects after a drop. Zero disables reconnection entirely.
    pollingInterval: reconnectDelayMs,
  });

  es.addEventListener('open', () => {
    if (!closed) handlers.onOpen?.();
  });

  es.addEventListener('message', (event) => {
    if (closed || !event.data) return;
    try {
      handlers.onMessage?.(JSON.parse(event.data));
    } catch {
      // Non-JSON message — ignore.
    }
  });

  es.addEventListener('error', (event) => {
    if (closed) return;
    const msg = (event as unknown as { message?: string })?.message || 'SSE error';
    handlers.onError?.(new Error(msg));
  });

  es.addEventListener('close', () => {
    if (!closed) handlers.onClose?.();
  });

  return {
    close: () => {
      if (closed) return;
      closed = true;
      es.removeAllEventListeners();
      es.close();
    },
  };
}

export function subscribeToKind(
  kind: string,
  onEvent: (data: Record<string, unknown>) => void,
  options?: SSEOptions,
): SSESubscription {
  return subscribeToEvents({
    onMessage: (event) => {
      if (event.kind === kind) onEvent(event);
    },
  }, options);
}
