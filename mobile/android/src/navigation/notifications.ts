// "Needs you" notifications open the exact item (mobile_parity.md §7): a tap — or the tap that
// launched the app — routes by the notification's data, posted by api/jobNotifications.ts
// ({jobId}) and api/orchestration.ts attentionNotification ({orchRunId} | {orchNodeId} | {orchRef}).

import { useEffect } from 'react';
import { navRef, navigateTo } from './nav';

export type NotificationRoute = { name: string; params?: Record<string, unknown> };

/** Where a notification's data leads; null when it names nothing the app can open. */
export function routeForNotification(data: Record<string, unknown> | null | undefined): NotificationRoute | null {
  if (!data || typeof data !== 'object') return null;
  const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null);
  const job = str(data.jobId);
  if (job) return { name: 'JobDetail', params: { jobId: job } };
  const run = str(data.orchRunId);
  if (run) return { name: 'OrchRun', params: { runId: run } };
  const node = str(data.orchNodeId);
  if (node) return { name: 'OrchNode', params: { nodeId: node } };
  const ref = /^([a-z0-9][a-z0-9_.-]{0,240})@(\d{1,6})$/.exec(str(data.orchRef) || '');
  if (ref) return { name: 'OrchTool', params: { toolId: ref[1], version: Number(ref[2]) } };
  return null;
}

type Response = { notification: { request: { content: { data?: Record<string, unknown> } } } };
type NotificationsModule = {
  addNotificationResponseReceivedListener: (fn: (r: Response) => void) => { remove: () => void };
  getLastNotificationResponseAsync: () => Promise<Response | null>;
};

function load(): NotificationsModule | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    return require('expo-notifications') as NotificationsModule;
  } catch {
    return null;
  }
}

function open(response: Response | null, seen: Set<string>) {
  const data = response?.notification?.request?.content?.data;
  const route = routeForNotification(data);
  if (!route) return;
  const key = JSON.stringify(route);
  if (seen.has(key)) return;
  seen.add(key);
  const go = (tries: number) => {
    if (navRef.current?.isReady()) navigateTo(route.name, route.params);
    else if (tries > 0) setTimeout(() => go(tries - 1), 250);
  };
  go(40);
}

/** Mount once inside the app: routes notification taps (including the one that launched the app). */
export function useNotificationTaps(mod: NotificationsModule | null = load()) {
  useEffect(() => {
    if (!mod) return undefined;
    const seen = new Set<string>();
    let alive = true;
    void mod.getLastNotificationResponseAsync().then(r => { if (alive) open(r, seen); }).catch(() => undefined);
    const sub = mod.addNotificationResponseReceivedListener(r => { seen.clear(); open(r, seen); });
    return () => { alive = false; sub.remove(); };
  }, [mod]);
}
