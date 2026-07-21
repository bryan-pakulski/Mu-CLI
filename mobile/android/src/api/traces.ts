import { api, baseUrl } from './client';

export interface TraceRun {
  run_id: string;
  session: string;
  model: string;
  provider: string;
  mode: string;
  context_limit: number;
  max_iterations: number;
  iters: number;
  bytes: number;
  file: string;
}

export interface TraceSummary {
  run_id: string;
  session: string;
  model: string;
  provider: string;
  mode: string;
  context_limit: number;
  max_iterations: number;
  iters: number;
  total_in: number;
  total_out: number;
  total_cost: number;
  compaction_count: number;
  compaction_by_kind: Record<string, number>;
  mechanical_fallback_count: number;
  nudge_count: number;
  nudge_by_kind: Record<string, number>;
  nudges_broken: number;
  subagent_iters: number;
  peak_context: number;
  peak_estimated: number;
  peak_drift_abs: number;
  mean_drift: number;
  median_drift: number;
  total_wall_ms: number;
  peak_wall_ms: number;
  mean_wall_ms: number;
  tool_calls: number;
  request_count: number;
  context_artifact_counts: Record<string, number>;
  redundant_reads: number;
  status: string;
  bytes: number;
}

export const tracesApi = {
  list: (session?: string) =>
    api.get<TraceRun[]>('/api/traces', { query: { session } }),
  getSession: (sessionName: string, cols?: number) =>
    api.get(`/api/traces/session/${encodeURIComponent(sessionName)}`, { query: { cols } }),
  getRun: (runId: string, cols?: number) =>
    api.get(`/api/traces/${encodeURIComponent(runId)}`, { query: { cols } }),
  getRaw: async (runId: string): Promise<string> => {
    const res = await fetch(`${baseUrl()}/api/traces/${encodeURIComponent(runId)}/raw`);
    if (!res.ok) throw new Error(`Failed: ${res.status}`);
    return res.text();
  },
  getSummary: (runId: string) =>
    api.get<TraceSummary>(`/api/traces/${encodeURIComponent(runId)}/summary`),
};