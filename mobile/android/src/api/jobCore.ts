import { api } from './client';
import { subscribeToEvents } from './sse';

export type JobPart = 'state' | 'pipeline' | 'plan' | 'session' | 'diff' | 'review' | 'checks' | 'costs';
export interface JobAction { id: string; label: string; enabled: boolean; reason: string; primary: boolean; danger: boolean; args?: { requires_override?: boolean; event_id?: number; version?: number } }
export interface JobStep { id: string; label: string; status: string; glyph: string; elapsed: number; cost_usd: number; runs: number; detail: string }
export interface Finding { id: string; severity: string; title: string; detail: string; suggestion: string; file: string | null; line_start: number | null; disposition: string }
export interface JobSummary {
  id: string; title: string; key: string; state: string; verdict: string; mini: string;
  attention_detail: string; repository: string; cost_usd: number; actions: JobAction[];
  created_at: number; updated_at: number; steps: JobStep[];
}
export interface JobVM extends JobSummary {
  branch: string; worktree: string; pipeline: JobStep[];
  plan?: { version: number; latest_version: number; approved_version: number; versions: number[]; markdown: string; draft: string | null; diff_from_previous: string; errors: string[] };
  review?: { verdict: string; merge_readiness: number; summary: string; counts: Record<string, number>; findings: Finding[]; plan_coverage: Array<{ criterion_id: string; status: string; evidence: string }> };
  diff?: { patch: string | null };
  checks?: { live?: AuditEvent[]; runs: Array<{ run: number; passed: boolean; head_sha: string; new_failures: number; preexisting_failures: number; suites: Array<{ suite: string; command: string; passed: boolean; stdout: string; stderr: string; preexisting_failures: string[]; junit?: { passed: number; failed: number; skipped: number; total: number } }> }> };
  session?: { approval?: { tool_name:string; tool_args:Record<string, unknown> } | null; live: boolean; events: AuditEvent[]; stages: Record<string, Array<{ role: string; parts: Array<{ type: string; text?: string }> }>> };
  costs?: { total_usd: number; by_stage: Record<string, number>; events: AuditEvent[] };
}
export interface AuditEvent { id: number; event_type: string; reason: string; created_at: number; payload: Record<string, unknown> }
export interface HomeVM { needs_you: JobSummary[]; running: JobSummary[]; jobs: JobSummary[]; counts: { needs_you: number; total: number } }
export interface JobChange { scope: 'home' | 'job' | 'board'; id: string; parts: JobPart[]; seq: number }
export interface CommandResult { ok: boolean; parts: JobPart[]; data: { version?: number } }

export const jobCoreApi = {
  home: () => api.get<HomeVM>('/api/jobs/vm/home'),
  job: (id: string, parts?: JobPart[], version?: number) => api.get<JobVM>(`/api/jobs/vm/job/${encodeURIComponent(id)}`, { query: { parts: parts?.join(','), plan_version: version } }),
  command: (id: string, type: string, args: Record<string, unknown> = {}) => api.post<CommandResult>('/api/jobs/cmd', { type, job_id: id, args }),
};

export function subscribeToJobChanges(onChange: (change: JobChange) => void, onOpen: () => void) {
  return subscribeToEvents({ onMessage: event => onChange(event as unknown as JobChange), onOpen }, {
    path: '/api/jobs/events', sessionName: null,
    decode: raw => {
      const value = raw as Partial<JobChange> | null;
      return value && ['home', 'job', 'board'].includes(value.scope || '') && Array.isArray(value.parts)
        ? { ...value, kind: 'job_change' } : null;
    },
  });
}
