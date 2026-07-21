import { api } from './client';

export interface LoopState {
  active: boolean;
  loop_goal: string;
  loop_active: boolean;
  loop_features: string[];
  backlog: Array<Record<string, unknown>>;
  memory: Array<Record<string, unknown>>;
}

export const loopApi = {
  getState: () => api.get<LoopState>('/api/loop/state'),
};