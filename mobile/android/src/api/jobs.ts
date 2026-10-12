import { api } from './client';

export type JobStatus = 'queued' | 'preparing' | 'running' | 'needs_human' | 'verifying' | 'planning' | 'reviewing' | 'publishing' | 'ready_for_review' | 'recovering' | 'conflicted' | 'failed' | 'timed_out' | 'budget_exceeded' | 'environment_error' | 'cancelled' | 'merged';
export type AttentionReason = '' | 'question' | 'approval_required' | 'ambiguous_requirement' | 'secret_required' | 'merge_conflict' | 'budget_exceeded' | 'environment_failure' | 'verification_required' | 'test_failure' | 'unsafe_action' | 'worker_lost' | 'provider_error' | 'models_unset' | 'plan_review' | 'no_validation' | 'review_stalled' | 'publish_failed' | string;
export type JobBoardSection = 'needs_you' | 'running' | 'queued' | 'ready' | 'failed' | 'done';

export type JobStageName = 'plan' | 'implement' | 'review';

export const JOB_STAGE_NAMES: JobStageName[] = ['plan', 'implement', 'review'];

export interface JobStageModel {
  provider?: string;
  model?: string;
}

export type JobStageModels = Partial<Record<JobStageName, JobStageModel>>;

export interface JobExecutionProfile {
  provider: string;
  model: string;
  agent_mode: string;
  session_type: 'chat' | 'workspace' | 'container';
  auto_approve_writes: boolean;
  stages?: JobStageModels;
  [key: string]: unknown;
}

/** Keep only stages with a provider or model; blank stages inherit server-side. */
export function compactStageModels(
  draft: Partial<Record<JobStageName, { provider?: string; model?: string }>>,
): JobStageModels {
  const out: JobStageModels = {};
  for (const stage of JOB_STAGE_NAMES) {
    const entry = draft[stage] || {};
    const provider = (entry.provider || '').trim();
    const model = (entry.model || '').trim();
    if (!provider && !model) continue;
    const item: JobStageModel = {};
    if (provider) item.provider = provider;
    if (model) item.model = model;
    out[stage] = item;
  }
  return out;
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

/** Pipeline settings stored server-side in $MUCLI_HOME/jobs/settings.json. */
export interface JobPipelineSettings {
  stage_models: JobStageModels;
  plan_approval: boolean;
  stages?: JobStageName[];
  missing?: JobStageName[];
  configured?: boolean;
}

export interface JobPlan {
  summary?: string;
  steps?: string[];
  acceptance_criteria?: string[];
  validation_commands?: string[];
  model?: { provider?: string; model?: string };
}

/** Plan awaiting review: job metadata wins, attention context is the fallback. */
export function planReviewDraft(job: Pick<EngineeringJob, 'acceptance_criteria' | 'validation_commands'> & { metadata?: Record<string, unknown> }, context: Record<string, unknown> = {}) {
  const meta = (job.metadata || {}) as Record<string, unknown>;
  const plan = ((meta.plan as JobPlan) || (context.plan as JobPlan) || {}) as JobPlan;
  const pick = (primary: unknown, fallback: unknown): string[] => {
    const list = Array.isArray(primary) && primary.length ? primary : (Array.isArray(fallback) ? fallback : []);
    return list.map(item => String(item)).filter(Boolean);
  };
  return {
    plan,
    steps: Array.isArray(plan.steps) ? plan.steps.map(String) : [],
    criteria: pick(job.acceptance_criteria, context.acceptance_criteria ?? plan.acceptance_criteria),
    commands: pick(job.validation_commands, context.validation_commands ?? plan.validation_commands),
  };
}

export function splitLines(value: string): string[] {
  return String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

export function planApprovalBody(criteriaText: string, commandsText: string, detail = ''): JobInteractionResponse {
  return {
    decision: 'approve',
    detail: detail.trim(),
    value: { acceptance_criteria: splitLines(criteriaText), validation_commands: splitLines(commandsText) },
  };
}

export function missingStageModels(models: JobStageModels | undefined): JobStageName[] {
  return JOB_STAGE_NAMES.filter(stage => {
    const entry = models?.[stage];
    return !(entry && String(entry.provider || '').trim() && String(entry.model || '').trim());
  });
}

export type JobDeleteResult = {
  job_id: string;
  deleted: boolean;
  forced?: boolean;
  worktree_removed: boolean;
  artifacts_removed: string[];
  warnings: string[];
  branch_preserved: string;
};

/** Secret-free Jira link view ($MUCLI_HOME/integrations/jira.json). */
export interface JiraBoardPref { id: number; name: string; project?: string; type?: string }
export interface JiraSettings {
  base_url: string;
  email: string;
  mode: string;
  configured: boolean;
  api_token_set: boolean;
  pat_set: boolean;
  writeback: boolean;
  sources: Record<string, string>;
  preferences: { boards: JiraBoardPref[]; default_jql: string; assigned_to_me: boolean };
  error?: string;
}
export interface JiraSettingsUpdate {
  base_url?: string;
  email?: string;
  api_token?: string;
  pat?: string;
  preferences?: Partial<JiraSettings['preferences']>;
}
export interface JiraBoard extends JiraBoardPref { preferred?: boolean; suggested?: boolean }
export interface JiraSprint { id: number; name: string; state: string }
export interface JiraLinkedJob { job_id: string; title: string; status: string; attention_reason: string; live: boolean }
export interface JiraTicket {
  key: string;
  summary: string;
  url: string;
  status: string;
  status_category: string;
  issue_type: string;
  priority: string;
  assignee: string;
  labels: string[];
  job?: JiraLinkedJob | null;
}
export interface JiraStatusCount { name: string; category: string; count: number }
export interface JiraTicketQuery {
  board_id?: number;
  sprint_id?: number;
  jql?: string;
  mine?: boolean;
  backlog?: boolean;
  include_done?: boolean;
  status?: string;
}

/** Drop empty/false query values so the server sees only real filters. */
export function jiraTicketQuery(q: JiraTicketQuery): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(q)) {
    if (value === undefined || value === null || value === '' || value === false || value === 0) continue;
    out[key] = value as string | number | boolean;
  }
  return out;
}

/** Only send a token field when the user typed one (blank keeps the stored secret). */
export function jiraSettingsBody(form: { base_url: string; email: string; token: string; mode: 'cloud' | 'dc' }): JiraSettingsUpdate {
  const body: JiraSettingsUpdate = { base_url: form.base_url.trim(), email: form.email.trim() };
  const token = form.token.trim();
  if (token) {
    if (form.mode === 'dc') body.pat = token;
    else body.api_token = token;
  }
  return body;
}

export const jiraApi = {
  settings: () => api.get<JiraSettings>('/api/jobs/jira/settings'),
  save: (body: JiraSettingsUpdate) => api.put<JiraSettings>('/api/jobs/jira/settings', body),
  unlink: () => api.delete<JiraSettings>('/api/jobs/jira/settings'),
  test: () => api.post<{ ok: boolean; user: { displayName?: string; emailAddress?: string } }>('/api/jobs/jira/test', {}),
  boards: (query = '') => api.get<{ boards: JiraBoard[]; my_projects: string[] }>('/api/jobs/jira/boards', { query: query ? { query } : {} }),
  sprints: (boardId: number) => api.get<{ sprints: JiraSprint[] }>(`/api/jobs/jira/boards/${boardId}/sprints`),
  tickets: (q: JiraTicketQuery) => api.get<{ tickets: JiraTicket[]; statuses: JiraStatusCount[]; jql: string }>(
    '/api/jobs/jira/tickets', { query: jiraTicketQuery(q) },
  ),
  importKeys: (keys: string[], sessionName?: string | null) => api.post<{ created: Array<{ key: string; job_id: string }>; skipped?: unknown[]; errors?: unknown[] }>(
    '/api/jobs/jira/import', { keys, session_name: sessionName || undefined },
  ),
};

export const jobsApi = {
  list: () => api.get<{ jobs: EngineeringJob[] }>('/api/jobs'),
  getSettings: () => api.get<JobPipelineSettings>('/api/jobs/settings'),
  saveSettings: (settings: Partial<Pick<JobPipelineSettings, 'stage_models' | 'plan_approval'>>) => api.put<JobPipelineSettings>('/api/jobs/settings', settings),
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
  /** Single-step delete: cancels running work, archives, then purges record/telemetry/evidence. Git branch preserved. */
  delete: (jobId: string, force = true) => api.delete<JobDeleteResult>(`/api/jobs/${encodeURIComponent(jobId)}?force=${force ? 1 : 0}`),
};
