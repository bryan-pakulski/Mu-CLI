import { api } from './client';

export type JobStatus = 'queued' | 'preparing' | 'running' | 'needs_human' | 'verifying' | 'ready_for_review' | 'recovering' | 'conflicted' | 'failed' | 'timed_out' | 'budget_exceeded' | 'environment_error' | 'cancelled' | 'merged';
export type AttentionReason = '' | 'question' | 'approval_required' | 'ambiguous_requirement' | 'secret_required' | 'merge_conflict' | 'budget_exceeded' | 'environment_failure' | 'test_failure' | 'unsafe_action' | 'worker_lost' | 'provider_error';

export interface JobExecutionProfile {
  provider: string;
  model: string;
  agent_mode: string;
  session_type: 'chat' | 'workspace' | 'container';
  auto_approve_writes: boolean;
  [key: string]: unknown;
}

export interface EngineeringJob {
  id: string;
  title: string;
  description: string;
  repository: string;
  base_branch: string;
  base_sha: string;
  acceptance_criteria: string[];
  validation_commands: string[];
  status: JobStatus;
  attention_reason: AttentionReason;
  attention_detail: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  completed_at: number | null;
  max_cost_usd: number | null;
  max_runtime_seconds: number | null;
  max_iterations: number | null;
  max_retries: number;
  max_subagents: number | null;
  cost_usd: number;
  branch: string;
  worktree: string;
  environment: Record<string, unknown>;
  execution: JobExecutionProfile;
  metadata: Record<string, unknown>;
  session_name: string;
  worker_id: string;
  lease_expires_at: number | null;
  heartbeat_at: number | null;
  version: number;
  terminal: boolean;
  needs_attention: boolean;
}

export interface JobEvent {
  id: number;
  job_id: string;
  event_type: string;
  from_status: JobStatus | null;
  to_status: JobStatus | null;
  reason: string;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface JobAttempt {
  id: string;
  job_id: string;
  number: number;
  status: string;
  session_name: string;
  worker_id: string;
  started_at: number;
  finished_at: number | null;
  error: string;
  cost_usd: number;
  metadata: Record<string, unknown>;
}

export interface CreateJobInput {
  title: string;
  description?: string;
  repository?: string;
  base_branch?: string;
  base_sha?: string;
  acceptance_criteria?: string[];
  validation_commands?: string[];
  max_cost_usd?: number;
  max_runtime_seconds?: number;
  max_iterations?: number;
  max_retries?: number;
  max_subagents?: number;
  environment?: Record<string, unknown>;
  execution?: Partial<JobExecutionProfile>;
  metadata?: Record<string, unknown>;
}

export const jobsApi = {
  list: () => api.get<{ jobs: EngineeringJob[] }>('/api/jobs'),
  get: (jobId: string) => api.get<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}`),
  create: (input: CreateJobInput) => api.post<{ job: EngineeringJob }>('/api/jobs', input),
  events: (jobId: string, after = 0) => api.get<{ events: JobEvent[] }>(`/api/jobs/${encodeURIComponent(jobId)}/events`, { query: { after } }),
  attempts: (jobId: string) => api.get<{ attempts: JobAttempt[] }>(`/api/jobs/${encodeURIComponent(jobId)}/attempts`),
  cancel: (jobId: string, reason?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { reason }),
  resume: (jobId: string, detail?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/resume`, { detail }),
  retry: (jobId: string, reason?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { reason }),
};
