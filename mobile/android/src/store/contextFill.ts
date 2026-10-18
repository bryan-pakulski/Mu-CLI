// Header context-ring fill (G§9.14). The chat session hook reports every
// SSE `context_snapshot` (published by the server's pre_provider_call hook)
// here; the header reads it and only polls /api/memory/state while no
// snapshot has arrived for the open session.

import { create } from 'zustand';

export type ContextFillState = {
  /** Session the last snapshot belongs to. */
  sessionName: string | null;
  /** Fill as a 0..1 fraction, or null before any snapshot. */
  fill: number | null;
  report: (sessionName: string | null, fillPct: unknown) => void;
};

/** Normalise a server `fill_pct` (0..100, or a 0..1 fraction) to 0..1. */
export function normaliseFill(value: unknown): number | null {
  const pct = Number(value);
  if (value == null || value === '' || !Number.isFinite(pct)) return null;
  return pct > 1 ? pct / 100 : pct;
}

export const useContextFillStore = create<ContextFillState>(set => ({
  sessionName: null,
  fill: null,
  report: (sessionName, fillPct) => {
    const fill = normaliseFill(fillPct);
    if (fill == null || !sessionName) return;
    set({ sessionName, fill });
  },
}));
