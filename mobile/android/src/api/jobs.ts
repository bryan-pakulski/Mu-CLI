import { api } from './client';

export type JobStatus = 'queued' | 'preparing' | 'running' | 'needs_human' | 'verifying' | 'ready_for_review' | 'recovering' | 'conflicted' | 'failed' | 'timed_out' | 'budget_exceeded' | 'environment_error' | 'cancelled' | 'merged';
export type AttentionReason = '' | 'question' | 'approval_required' | 'ambiguous_requirement' | 'secret_required' | 'merge_conflict' | 'budget_exceeded' | 'environment_failure' | 'verification_required' | 'test_failure' | 'unsafe_action' | 'worker_lost' | 'provider_error';
export type JobBoardSection = 'needs_you' | 'running' | 'queued' | 'ready' | 'failed' | 'done';

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

export interface VerificationCheck {
  command: string;
  passed: boolean;
  return_code: number | null;
  timed_out: boolean;
  duration_seconds: number;
  stdout: string;
  stderr: string;
  error: string;
}

export interface VerificationRun {
  id: string;
  job_id: string;
  status: string;
  passed: boolean;
  started_at: number;
  completed_at: number;
  checks: VerificationCheck[];
  base_sha: string;
  head_sha: string;
  changed_files: string[];
  additions: number;
  deletions: number;
  diff_stat: string;
  dirty: boolean;
  dirty_status: string;
  manifest_path: string;
  summary: Record<string, unknown>;
}

export interface WorkReceipt {
  schema_version: number;
  generated_at: number;
  job: {
    id: string;
    title: string;
    description: string;
    status: JobStatus;
    needs_attention: boolean;
    attention_reason: AttentionReason;
    attention_detail: string;
  };
  outcome: {
    ready_for_review: boolean;
    terminal: boolean;
    attempts: number;
    elapsed_seconds: number;
    cost_usd: number;
  };
  ticket: {
    acceptance_criteria: string[];
    validation_commands: string[];
  };
  git: {
    repository: string;
    repository_id?: string | null;
    base_branch: string;
    base_sha: string;
    branch: string;
    worktree: string;
    head_sha: string;
    changed_files: string[];
    additions: number;
    deletions: number;
    diff_stat: string;
    dirty: boolean | null;
  };
  verification: VerificationRun | null;
  attempts: JobAttempt[];
  usage: {
    cost_usd: number;
    tokens: Record<string, number>;
  };
  activity: {
    events: number;
    agent_messages: number;
    tool_calls: number;
    human_responses: number;
    checkpoints: number;
    verification_runs: number;
  };
}

export interface JobDiff {
  job_id: string;
  base_sha: string;
  head_sha: string;
  branch: string;
  files: string[];
  stat: string;
  patch: string;
  truncated: boolean;
}

export interface JobDiagnosticsSnapshot {
  job_id: string;
  status: JobStatus;
  attention_reason: AttentionReason;
  repository: string;
  base_branch: string;
  base_sha: string;
  branch: string;
  worktree: string;
  worker_id: string;
  heartbeat_at: number | null;
  lease_expires_at: number | null;
  diagnostics: JobEvent[];
  latest_failure: JobEvent | null;
  worker_log_path: string;
  worker_log_exists: boolean;
  worker_log_size: number;
  worker_log_tail: string;
  worker_log_truncated: boolean;
}

export interface JobBoardResponse {
  counts: Record<JobBoardSection, number>;
  sections: Record<JobBoardSection, EngineeringJob[]>;
}

export interface CreateJobInput extends Record<string, unknown> {
  title: string;
  description?: string;
  repository?: string;
  session_name?: string;
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

export interface JobInteractionResponse extends Record<string, unknown> {
  detail?: string;
  decision?: 'approve' | 'deny' | 'explain' | string;
  value?: unknown;
  selected?: unknown[];
}

export const jobsApi = {
  list: () => api.get<{ jobs: EngineeringJob[] }>('/api/jobs'),
  board: () => api.get<JobBoardResponse>('/api/jobs/board'),
  get: (jobId: string) => api.get<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}`),
  create: (input: CreateJobInput) => api.post<{ job: EngineeringJob }>('/api/jobs', input),
  receipt: (jobId: string) => api.get<{ receipt: WorkReceipt }>(`/api/jobs/${encodeURIComponent(jobId)}/receipt`),
  diagnostics: (jobId: string) => api.get<{ diagnostics: JobDiagnosticsSnapshot }>(`/api/jobs/${encodeURIComponent(jobId)}/diagnostics`),
  diff: (jobId: string) => api.get<{ diff: JobDiff }>(`/api/jobs/${encodeURIComponent(jobId)}/diff`),
  events: (jobId: string, after = 0) => api.get<{ events: JobEvent[] }>(`/api/jobs/${encodeURIComponent(jobId)}/events`, { query: { after } }),
  attempts: (jobId: string) => api.get<{ attempts: JobAttempt[] }>(`/api/jobs/${encodeURIComponent(jobId)}/attempts`),
  verifications: (jobId: string) => api.get<{ verifications: VerificationRun[] }>(`/api/jobs/${encodeURIComponent(jobId)}/verifications`),
  respond: (jobId: string, response: JobInteractionResponse) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/respond`, response),
  requestChanges: (jobId: string, feedback: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/request-changes`, { feedback }),
  continue: (jobId: string, detail?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/continue`, { detail }),
  discard: (jobId: string, reason?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/discard`, { reason }),
  cancel: (jobId: string, reason?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { reason }),
  resume: (jobId: string, detail?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/resume`, { detail }),
  retry: (jobId: string, reason?: string) => api.post<{ job: EngineeringJob }>(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { reason }),
};
