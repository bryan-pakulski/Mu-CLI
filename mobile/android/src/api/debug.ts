import { api } from './client';
import { ModeWorkspaceContract } from './modeWorkspace';

export interface DebugEntry {
  id: number;
  content: string;
  tags: string[];
  source: string;
  kind: string;
  created_at: number;
  updated_at: number;
}

export interface DebugHypothesis extends DebugEntry {
  status: 'untested' | 'supported' | 'confirmed' | 'disproved' | string;
}

export interface DebugState {
  active: boolean;
  debug_target: string;
  hypotheses: DebugHypothesis[];
  suspects: DebugEntry[];
  notes: DebugEntry[];
  findings: DebugEntry[];
  scratchpad_count: number;
  workspace: ModeWorkspaceContract;
}

export const debugApi = {
  getState: () => api.get<DebugState>('/api/debug/state'),
};
