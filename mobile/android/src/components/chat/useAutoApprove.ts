// Auto-approve (YOLO) for the open session: the composer foot shows it while it is on, the Chat ⋯
// session settings toggle it. Offline toggles join the outbound queue and replay with If-Match on
// reconnect (the optimistic value stays visible); online toggles roll back on failure. The server
// value is re-read whenever the session changes.

import { useCallback, useEffect, useState } from 'react';
import { inspectorApi } from '../../api/inspector';
import { queueIfOffline, useConnectionStore } from '../../store/connection';

export function useAutoApprove() {
  const activeSessionName = useConnectionStore(s => s.activeSessionName);
  const isConnected = useConnectionStore(s => s.isConnected);
  const yolo = useConnectionStore(s => s.yolo);
  const setYolo = useConnectionStore(s => s.setYolo);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!isConnected || !activeSessionName) return;
    try {
      const response = await inspectorApi.getVariables(activeSessionName);
      for (const group of response.groups || []) {
        const variable = (group.variables || []).find(item => item.key === 'yolo');
        if (variable) { setYolo(Boolean(variable.value)); return; }
      }
    } catch {
      // Keep the last known value during a transient disconnect.
    }
  }, [activeSessionName, isConnected, setYolo]);

  useEffect(() => { void refresh(); }, [refresh]);

  const update = useCallback(async (next: boolean) => {
    if (!activeSessionName || syncing) return;
    setError(null);
    if (await queueIfOffline('set_variable', { key: 'yolo', value: next }, {
      sessionName: activeSessionName,
      ifMatch: useConnectionStore.getState().sessionRevision ?? undefined,
    })) {
      setYolo(next);
      return;
    }
    const previous = useConnectionStore.getState().yolo;
    setYolo(next);
    setSyncing(true);
    try {
      const response = await inspectorApi.setVariable('yolo', next, activeSessionName);
      setYolo(Boolean(response.value));
    } catch (cause) {
      setYolo(previous);
      setError(`Couldn't update auto-approve: ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      setSyncing(false);
    }
  }, [activeSessionName, setYolo, syncing]);

  return { yolo, syncing, error, update, refresh, canToggle: Boolean(isConnected && activeSessionName) && !syncing };
}
