import EventSource from 'react-native-sse';

export interface SSEHandlers {
  onMessage?: (event: { kind: string; [key: string]: unknown }) => void;
  onOpen?: () => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export interface SSESubscription {
  close: () => void;
}

/**
 * Subscribe to the mucli SSE event stream at GET /api/events.
 * Returns a subscription object with a close() method.
 */
export function subscribeToEvents(handlers: SSEHandlers): SSESubscription {
  // Import connection store lazily to avoid circular deps
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { baseUrl, activeSessionName } = require('../store/connection').useConnectionStore.getState();

  let url = `${baseUrl}/api/events`;
  if (activeSessionName) {
    url += `?session_name=${encodeURIComponent(activeSessionName)}`;
  }

  const es = new EventSource(url, {
    headers: { Accept: 'text/event-stream' },
    pollingInterval: 3000,
  });

  es.addEventListener('open', () => {
    handlers.onOpen?.();
  });

  es.addEventListener('message', (e) => {
    if (!e.data) return;
    try {
      const parsed = JSON.parse(e.data);
      handlers.onMessage?.(parsed);
    } catch {
      // Non-JSON message — ignore
    }
  });

  es.addEventListener('error', (e) => {
    const msg = (e as unknown as { message?: string })?.message || 'SSE error';
    handlers.onError?.(new Error(msg));
  });

  es.addEventListener('close', () => {
    handlers.onClose?.();
  });

  return {
    close: () => {
      es.removeAllEventListeners();
      es.close();
    },
  };
}

/**
 * Subscribe to a specific SSE event kind (filters by the 'kind' field).
 */
export function subscribeToKind(kind: string, onEvent: (data: Record<string, unknown>) => void): SSESubscription {
  return subscribeToEvents({
    onMessage: (event) => {
      if (event.kind === kind) {
        onEvent(event);
      }
    },
  });
}