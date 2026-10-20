// Load an orchestration view model, keep it live from /api/orchestration/events (debounced), and
// expose pull-to-refresh. `scopes` lists the change scopes (node | tool | run) that make it stale.

import { useCallback, useEffect, useRef, useState } from 'react';
import type { OrchChange } from '../../api/orchestration';
import { isOrchMissing, useOrchLive } from '../../components/OrchViews';

export function useOrchData<T>(load: () => Promise<T>, scopes: Array<OrchChange['scope']> | ((c: OrchChange) => boolean), deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const loadRef = useRef(load);
  loadRef.current = load;
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const reload = useCallback(async () => {
    try {
      const next = await loadRef.current();
      if (!alive.current) return;
      setData(next); setError(''); setMissing(false);
    } catch (err) {
      if (!alive.current) return;
      if (isOrchMissing(err)) setMissing(true);
      else setError(err instanceof Error ? err.message : String(err));
    } finally { if (alive.current) setRefreshing(false); }
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setData(null); void reload(); }, deps);
  useOrchLive(() => { void reload(); }, typeof scopes === 'function' ? scopes : change => scopes.includes(change.scope));
  const refresh = useCallback(() => { setRefreshing(true); void reload(); }, [reload]);
  return { data, error, missing, refreshing, reload, refresh, setData };
}
