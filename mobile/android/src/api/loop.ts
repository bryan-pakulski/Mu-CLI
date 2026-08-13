import { api } from './client';
import { ModeWorkspaceContract } from './modeWorkspace';

export interface LoopEntry {
  id: number;
  content: string;
  tags: string[];
  source: string;
  kind: string;
  created_at: number;
  updated_at: number;
  status?: string;
}

export interface LoopState {
  active: boolean;
  loop_goal: string;
  loop_active: boolean;
  loop_features: unknown[];
  backlog: LoopEntry[];
  memory: LoopEntry[];
  workspace: ModeWorkspaceContract;
}

export const loopApi = {
  getState: () => api.get<LoopState>('/api/loop/state'),
  setActive: (active: boolean, goal: string) => api.post<{ ok: boolean; loop_active: boolean; loop_goal: string }>('/api/loop/control', { active, goal }),
};
