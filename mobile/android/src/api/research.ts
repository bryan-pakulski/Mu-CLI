import { api } from './client';

export interface ResearchSource {
  id: number;
  title: string;
  url: string;
  type: string;
  credibility: number;
  authors: string[];
  date: string | null;
  accessed: string;
  metadata: Record<string, unknown>;
}

export interface ResearchState {
  active: boolean;
  sources: ResearchSource[];
  source_count: number;
  bibliography: string;
  findings: Array<Record<string, unknown>>;
  finding_count: number;
}

export const researchApi = {
  getState: () => api.get<ResearchState>('/api/research/state'),
};