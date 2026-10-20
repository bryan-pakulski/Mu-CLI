// Context fill for the composer's ring (G§9.14). The SSE `context_snapshot` event, reported by the
// chat session hook, is the source; /api/memory/state is polled only until the first snapshot for
// the open session arrives (for example a freshly opened, idle session).

import { useEffect, useState } from 'react';
import { memoryApi } from '../../api/memory';
import { normaliseFill, useContextFillStore } from '../../store/contextFill';

export const CONTEXT_POLL_MS = 20_000;

export function useContextFill(sessionName: string | null, isConnected: boolean): number | null {
  const [polledFill, setPolledFill] = useState<number | null>(null);
  const snapshotFill = useContextFillStore(s => (sessionName && s.sessionName === sessionName ? s.fill : null));
  const hasSnapshot = snapshotFill != null;
  useEffect(() => { setPolledFill(null); }, [sessionName]);
  useEffect(() => {
    if (!sessionName || !isConnected || hasSnapshot) return undefined;
    let cancelled = false;
    const load = () => {
      memoryApi.getState(8, 4)
        .then(state => {
          if (cancelled || !state || !state.active) return;
          const fill = normaliseFill(state.fill_pct);
          if (fill != null) setPolledFill(fill);
        })
        .catch(() => undefined);
    };
    load();
    const timer = setInterval(load, CONTEXT_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [hasSnapshot, isConnected, sessionName]);
  if (!sessionName || !isConnected) return null;
  return snapshotFill ?? polledFill;
}
