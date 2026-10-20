// App-wide watcher for "needs you" jobs: keeps the Work tab badge current from
// GET /api/jobs/attention, and (when notifications are on) refetches the jobs home
// VM on the change stream (and every 30 s as a fallback) and posts a local
// notification for every job that newly needs a human. Renders nothing.

import { useEffect, useRef, useState } from 'react';

import { jobCoreApi, subscribeToJobChanges } from '../api/jobCore';
import { enteredNeedsYou, loadNotifyEnabled, notifyNeedsYou, onNotifyEnabledChange } from '../api/jobNotifications';
import { jobsApi } from '../api/jobs';
import { useConnectionStore } from '../store/connection';
import { refreshWorkBadge, useWorkBadge } from '../store/workBadge';

export const NEEDS_YOU_POLL_MS = 30_000;

export function NeedsYouNotifier() {
  const isConnected = useConnectionStore(state => state.isConnected);
  const baseUrl = useConnectionStore(state => state.baseUrl);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const previous = useRef<Set<string> | null>(null);

  useEffect(() => {
    let alive = true;
    void loadNotifyEnabled().then(on => { if (alive) setEnabled(on); });
    const off = onNotifyEnabledChange(on => setEnabled(on));
    return () => { alive = false; off(); };
  }, []);

  // The Work badge, on the web rail's schedule plus every job change.
  useEffect(() => {
    if (!isConnected) { useWorkBadge.getState().set(0); return undefined; }
    const refresh = () => { void refreshWorkBadge(jobsApi.attention); };
    refresh();
    const timer = setInterval(refresh, NEEDS_YOU_POLL_MS);
    const stream = subscribeToJobChanges(change => { if (change.scope !== 'board') refresh(); }, refresh);
    return () => { clearInterval(timer); stream.close(); };
  }, [isConnected, baseUrl]);

  useEffect(() => {
    previous.current = null; // new server or reconnect: re-baseline, never replay the backlog
    if (!isConnected || !enabled) return undefined;
    let alive = true;
    let busy = false;
    const check = async () => {
      if (busy) return;
      busy = true;
      try {
        const home = await jobCoreApi.home();
        if (!alive) return;
        const { keys, entered } = enteredNeedsYou(previous.current, home);
        previous.current = keys;
        if (entered.length) await notifyNeedsYou(entered);
      } catch { /* offline or old server: try again later */ }
      finally { busy = false; }
    };
    void check();
    const timer = setInterval(() => { void check(); }, NEEDS_YOU_POLL_MS);
    const stream = subscribeToJobChanges(change => { if (change.scope !== 'board') void check(); }, () => { void check(); });
    return () => { alive = false; clearInterval(timer); stream.close(); };
  }, [isConnected, baseUrl, enabled]);

  return null;
}
