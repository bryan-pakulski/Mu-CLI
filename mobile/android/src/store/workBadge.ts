// What Work needs from you, for the Work tab badge (the web rail's Work count, GET /api/jobs/attention).

import { create } from 'zustand';

type WorkBadgeState = {
  count: number;
  summary: string;
  set: (count: number, summary?: string) => void;
};

export const useWorkBadge = create<WorkBadgeState>(set => ({
  count: 0,
  summary: '',
  set: (count, summary = '') => set({ count: Math.max(0, count | 0), summary }),
}));

/** Refresh the badge from GET /api/jobs/attention (the web rail's Work count); silent on failure. */
export async function refreshWorkBadge(fetchAttention: () => Promise<{ count: number; jobs?: Array<{ title: string }> }>): Promise<void> {
  try {
    const data = await fetchAttention();
    const count = Number(data.count || 0);
    const first = data.jobs?.[0]?.title;
    useWorkBadge.getState().set(count, count ? `${count} job${count === 1 ? '' : 's'} need${count === 1 ? 's' : ''} you${first ? `: ${first}` : ''}` : '');
  } catch {
    // Offline or jobs not hosted here: keep the last count.
  }
}
