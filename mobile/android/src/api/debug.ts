import { api } from './client';

export interface DebugState {
  active: boolean;
  debug_target: string;
  hypotheses: Array<Record<string, unknown>>;
  suspects: Array<Record<string, unknown>>;
  notes: Array<Record<string, unknown>>;
  findings: Array<Record<string, unknown>>;
  scratchpad_count: number;
}

export const debugApi = {
  getState: () => api.get<DebugState>('/api/debug/state'),
};