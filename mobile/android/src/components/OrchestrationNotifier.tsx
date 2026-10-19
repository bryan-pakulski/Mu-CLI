// App-wide orchestration watcher. Renders nothing.
//
// - Badge: keeps the Orchestration tab's count current from GET /api/orchestration/badge
//   (every 30s while connected and in the foreground, and right after an attention event).
//   That endpoint never starts orchestration on a server that hasn't used it.
// - Notifications: listens on the global, session-agnostic event bus for
//   `kind: "orchestration_attention"` and posts a local notification (runs awaiting
//   approval, nodes awaiting acceptance, tool reviews, paused or failed runs). Shares the
//   "needs you" notification toggle.

import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

import { attentionNotification, orchestrationApi, subscribeToOrchestrationAttention } from '../api/orchestration';
import { loadNotifyEnabled, notifyText, onNotifyEnabledChange } from '../api/jobNotifications';
import { useConnectionStore } from '../store/connection';
import { useOrchBadge } from '../store/orchBadge';

export const BADGE_POLL_MS = 30000;

export function OrchestrationNotifier() {
  const isConnected = useConnectionStore(state => state.isConnected);
  const baseUrl = useConnectionStore(state => state.baseUrl);
  const setBadge = useOrchBadge(state => state.set);
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    void loadNotifyEnabled().then(on => { if (alive) setEnabled(on); });
    const off = onNotifyEnabledChange(on => setEnabled(on));
    return () => { alive = false; off(); };
  }, []);

  useEffect(() => {
    if (!isConnected) { setBadge(null); return undefined; }
    let alive = true;
    const refresh = () => {
      orchestrationApi.badge().then(b => { if (alive) setBadge(b); }).catch(() => { /* older server or offline */ });
    };
    refresh();
    const timer = setInterval(() => { if (AppState.currentState === 'active') refresh(); }, BADGE_POLL_MS);
    const appState = AppState.addEventListener('change', state => { if (state === 'active') refresh(); });
    const stream = subscribeToOrchestrationAttention(event => {
      refresh();
      if (!enabled) return;
      const note = attentionNotification(event);
      if (note) void notifyText(note.title, note.body, note.data);
    });
    return () => { alive = false; clearInterval(timer); appState.remove(); stream.close(); };
  }, [isConnected, baseUrl, enabled, setBadge]);

  return null;
}
