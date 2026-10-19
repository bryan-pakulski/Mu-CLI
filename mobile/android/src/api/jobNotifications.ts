// "Needs you" local notifications (W10). The app watches the jobs home VM
// while it runs and posts an Android notification when a job newly needs a
// human (plan review, review done, paused, failed, or a pushed job whose PR
// was closed without merging). Delivery uses expo-notifications (local only,
// no push service); the user can turn it off in Jobs → Settings.

import AsyncStorage from '@react-native-async-storage/async-storage';
import type { HomeVM, JobSummary } from './jobCore';
import { WAITING_STATES } from '../theme/status';

export const NOTIFY_STORAGE_KEY = '@mucli/job-notifications';
/**
 * States that always count as "needs you" even if an older server omits
 * needs_you: every job state whose shared-vocabulary status is `waiting`.
 */
export const NEEDS_YOU_STATES = new Set(WAITING_STATES);

let enabledCache: boolean | null = null;
const listeners = new Set<(on: boolean) => void>();

/** Default on; persisted as '0' / '1'. */
export async function loadNotifyEnabled(): Promise<boolean> {
  if (enabledCache != null) return enabledCache;
  try {
    const raw = await AsyncStorage.getItem(NOTIFY_STORAGE_KEY);
    enabledCache = raw !== '0';
  } catch { enabledCache = true; }
  return enabledCache;
}

export async function setNotifyEnabled(on: boolean): Promise<void> {
  enabledCache = on;
  listeners.forEach(l => l(on));
  try { await AsyncStorage.setItem(NOTIFY_STORAGE_KEY, on ? '1' : '0'); } catch { /* best effort */ }
}

export function onNotifyEnabledChange(listener: (on: boolean) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Test hook: forget the cached toggle. */
export function resetNotifyCache(): void { enabledCache = null; }

/**
 * Rows that need a human, keyed `id:state` so re-entering a gate notifies again.
 * No attention-reason filter: any needs_you row (incl. limit_reached pauses)
 * counts; a limit pause is keyed `id:limit:<kind>` so it alerts even when the
 * job was already waiting in needs_input for another reason.
 */
export function needsYouRows(home: HomeVM | null | undefined): Map<string, JobSummary> {
  const out = new Map<string, JobSummary>();
  if (!home) return out;
  const rows = [...(home.needs_you || []), ...(home.jobs || []).filter(j => NEEDS_YOU_STATES.has(j.state))];
  for (const row of rows) {
    const closed = row.state === 'pushed' && row.pr?.state === 'closed';
    const gate = closed ? 'pr_closed' : row.limit ? `limit:${row.limit.kind}:${row.limit.scope}` : row.state;
    out.set(`${row.id}:${gate}`, row);
  }
  return out;
}

/** Jobs that entered "needs you" since `previous` (null = first load: baseline only, no alerts). */
export function enteredNeedsYou(previous: Set<string> | null, home: HomeVM | null | undefined): { keys: Set<string>; entered: JobSummary[] } {
  const rows = needsYouRows(home);
  const keys = new Set(rows.keys());
  if (previous == null) return { keys, entered: [] };
  const entered: JobSummary[] = [];
  const seen = new Set<string>();
  rows.forEach((row, key) => {
    if (!previous.has(key) && !seen.has(row.id)) { seen.add(row.id); entered.push(row); }
  });
  return { keys, entered };
}

const REASON: Record<string, string> = {
  plan_review: 'Plan ready for your review',
  review_done: 'Review finished — ready to push',
  needs_input: 'Waiting for your input',
  failed: 'Failed — needs a decision',
};

export function notificationText(row: JobSummary): { title: string; body: string } {
  const key = row.key || `#${String(row.id).slice(0, 8)}`;
  const closed = row.state === 'pushed' && row.pr?.state === 'closed';
  const reason = closed ? 'PR closed without merging' : row.limit ? 'Limit reached — approve more?' : REASON[row.state] || 'Needs you';
  // attention_detail already equals limit.message for limit pauses; fall back to it for older rows.
  const text = row.attention_detail || row.limit?.message || '';
  const detail = text && !closed ? ` · ${text}` : '';
  return { title: `${key}: ${reason}`, body: `${row.title}${detail}`.slice(0, 240) };
}

type NotificationsModule = {
  getPermissionsAsync: () => Promise<{ granted?: boolean; status?: string; canAskAgain?: boolean }>;
  requestPermissionsAsync: () => Promise<{ granted?: boolean; status?: string }>;
  scheduleNotificationAsync: (req: { content: { title: string; body: string; data?: Record<string, unknown> }; trigger: null }) => Promise<string>;
  setNotificationHandler?: (handler: unknown) => void;
  setNotificationChannelAsync?: (id: string, channel: Record<string, unknown>) => Promise<unknown>;
  AndroidImportance?: { HIGH?: number; DEFAULT?: number };
};

let moduleCache: NotificationsModule | null | undefined;
let prepared: Promise<boolean> | null = null;

function notificationsModule(): NotificationsModule | null {
  if (moduleCache !== undefined) return moduleCache;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    moduleCache = require('expo-notifications') as NotificationsModule;
  } catch { moduleCache = null; }
  return moduleCache;
}

/** Test hook. */
export function resetNotificationsModule(): void { moduleCache = undefined; prepared = null; }

async function prepare(mod: NotificationsModule): Promise<boolean> {
  try {
    mod.setNotificationHandler?.({
      handleNotification: async () => ({ shouldShowAlert: true, shouldPlaySound: false, shouldSetBadge: false }),
    });
    await mod.setNotificationChannelAsync?.('default', { name: 'Jobs that need you', importance: mod.AndroidImportance?.HIGH ?? 4 });
    const current = await mod.getPermissionsAsync();
    if (current.granted || current.status === 'granted') return true;
    if (current.canAskAgain === false) return false;
    const asked = await mod.requestPermissionsAsync();
    return !!(asked.granted || asked.status === 'granted');
  } catch { return false; }
}

/** Post one local notification per job; resolves to the number posted. Never throws. */
export async function notifyNeedsYou(rows: JobSummary[]): Promise<number> {
  if (!rows.length || !(await loadNotifyEnabled())) return 0;
  const mod = notificationsModule();
  if (!mod) return 0;
  prepared = prepared || prepare(mod);
  if (!(await prepared)) return 0;
  let posted = 0;
  for (const row of rows.slice(0, 5)) {
    const { title, body } = notificationText(row);
    try {
      await mod.scheduleNotificationAsync({ content: { title, body, data: { jobId: row.id } }, trigger: null });
      posted++;
    } catch { /* best effort */ }
  }
  return posted;
}

/** Post one free-form local notification (orchestration attention); resolves to whether it was posted. Never throws. */
export async function notifyText(title: string, body: string, data: Record<string, unknown> = {}): Promise<boolean> {
  if (!(await loadNotifyEnabled())) return false;
  const mod = notificationsModule();
  if (!mod) return false;
  prepared = prepared || prepare(mod);
  if (!(await prepared)) return false;
  try {
    await mod.scheduleNotificationAsync({ content: { title, body, data }, trigger: null });
    return true;
  } catch { return false; }
}
