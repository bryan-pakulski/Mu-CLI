import { api } from './client';
import { ModeWorkspaceContract } from './modeWorkspace';

export interface ResearchSource {
  id: number;
  title: string;
  url: string;
  source_type: string;
  credibility_score: number;
  authors: string[];
  date: string | null;
  accessed_date: string;
  metadata: Record<string, unknown>;
}

export interface ResearchState {
  active: boolean;
  sources: ResearchSource[];
  source_count: number;
  bibliography: string;
  findings: ResearchFinding[];
  finding_count: number;
  workspace: ModeWorkspaceContract;
}

export interface ResearchFinding {
  id: number;
  content: string;
  tags: string[];
  source: string;
  kind: string;
  record_type: 'claim' | 'legacy_note';
  evidence_state: 'source_linked' | 'evidence_gap';
  created_at: number;
  updated_at: number;
  hits: number;
}

export const researchApi = {
  getState: () => api.get<ResearchState>('/api/research/state'),
};
