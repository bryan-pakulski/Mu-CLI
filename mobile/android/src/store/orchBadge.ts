// The Orchestration tab's attention badge (runs to approve, paused runs, reviews, nodes to
// accept). OrchestrationNotifier keeps it current from GET /api/orchestration/badge, which
// never starts orchestration on a server that hasn't used it.

import { create } from 'zustand';

import type { OrchBadge } from '../api/orchestration';

export interface OrchBadgeState {
  count: number;
  summary: string;
  dispatchPaused: boolean;
  used: boolean;
  set: (badge: OrchBadge | null) => void;
}

export const useOrchBadge = create<OrchBadgeState>(set => ({
  count: 0,
  summary: '',
  dispatchPaused: false,
  used: false,
  set: badge => set(badge
    ? { count: Number(badge.total || 0), summary: badge.summary || '', dispatchPaused: !!badge.dispatch_paused, used: !!badge.used }
    : { count: 0, summary: '', dispatchPaused: false, used: false }),
}));

/** Text for the tab badge: nothing at zero, 99+ above 99. */
export function badgeText(count: number): string | undefined {
  if (!count) return undefined;
  return count > 99 ? '99+' : String(count);
}
