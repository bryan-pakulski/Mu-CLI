// Job REST routes that have no view-model / command equivalent (plan §6 Work: one client is
// jobCore — VM + /api/jobs/cmd — for everything else): creating a job, saved sessions, settings
// and run rules, toolchain images, Jira, history/report/archive, diagnostics, attempts and the
// attention count.

import { api } from './client';

export type JobStatus = 'queued' | 'preparing' | 'running' | 'needs_human' | 'verifying' | 'planning' | 'reviewing' | 'publishing' | 'ready_for_review' | 'recovering' | 'conflicted' | 'failed' | 'timed_out' | 'budget_exceeded' | 'environment_error' | 'cancelled' | 'merged';
export type AttentionReason = '' | 'question' | 'approval_required' | 'ambiguous_requirement' | 'secret_required' | 'merge_conflict' | 'budget_exceeded' | 'environment_failure' | 'verification_required' | 'test_failure' | 'unsafe_action' | 'worker_lost' | 'provider_error' | 'models_unset' | 'plan_review' | 'no_validation' | 'review_stalled' | 'publish_failed' | string;

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
  stages?: JobStageModels;
  /** Agent per stage: mucli (default), Claude Code or Codex. */
  stage_agents?: import('./agents').StageAgents;
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

/** Where a new job's code comes from (mu/jobs/sources.py); an alternative to workspace_root/repository. */
export type JobSource =
  | { kind: 'folder'; path: string }
  | { kind: 'repository'; name: string }
  | { kind: 'repository'; url: string }
  | { kind: 'session'; session: string; include_conversation?: boolean };

/** A file given to the job at creation (mu/jobs/attachments.py): an upload or a session's file. */
export type JobAttachmentInput =
  | { name: string; content_base64: string; mime_type?: string }
  | { session: string; attachment_id: string }
  | { session: string; artifact_id: string };

/** Settings → Repositories (`jobs.repositories`): the New job sheet's repository choices. */
export interface ConfiguredRepository { name: string; url: string; branch: string }

export function configuredRepositories(rules: JobRules | null | undefined): ConfiguredRepository[] {
  const raw: unknown = rules?.values?.['jobs.repositories'];
  if (!Array.isArray(raw)) return [];
  return (raw as unknown[])
    .filter((r): r is Record<string, unknown> => !!r && typeof r === 'object')
    .map(r => ({ name: String(r.name || '').trim(), url: String(r.url || '').trim(), branch: String(r.branch || '').trim() }))
    .filter(r => r.name && r.url);
}

export interface CreateJobInput extends Record<string, unknown> {
  title: string;
  source?: JobSource;
  /** Extra editable sources (mu/jobs/multi_source.py): each at /sources/<name>, its own branch and PR or patch. */
  sources?: JobSource[];
  attachments?: JobAttachmentInput[];
  description?: string;
  session_name?: string;
  /** Workspace-first creation: the repository path (a saved session on it supplies model defaults). */
  workspace_root?: string;
  /** Remote git URL (https/ssh/scp-style): the server clones it instead of using a local workspace. */
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

/** Pipeline settings stored server-side in $MUCLI_HOME/jobs/settings.json. */
export interface JobPipelineSettings {
  stage_models: JobStageModels;
  plan_approval: boolean;
  stages?: JobStageName[];
  missing?: JobStageName[];
  configured?: boolean;
  /** Every configurable run rule: generated-form schema + effective values + user overrides. */
  rules?: JobRules;
}

/* ------------------------------------------------------------ run rules */

export type JobRuleKind = 'int' | 'float' | 'bool' | 'text' | 'select' | 'list' | 'suites' | 'rows';
export interface JobRuleSuite { name: string; command: string; junit?: string }
/** One item of a `rows` rule (e.g. a Settings → Toolchains row), keyed by its field's column keys. */
export type JobRuleRow = Record<string, string>;
/** A column of a `rows` rule: one text input per row. */
export interface JobRuleColumn { key: string; label: string; placeholder?: string; required?: boolean; pattern?: string }
export type JobRuleValue = number | boolean | string | string[] | JobRuleSuite[] | JobRuleRow[] | null;

/** One rule field, mirrored from mu/jobs/job_settings.py RULES_SCHEMA. */
export interface JobRuleField {
  path: string;
  label: string;
  kind: JobRuleKind;
  default: JobRuleValue;
  hint: string;
  min?: number;
  max?: number;
  unit?: string;
  options?: Array<[string, string]>;
  pattern?: string;
  item_pattern?: string;
  optional?: boolean;
  /** `rows` only: the per-row inputs. */
  columns?: JobRuleColumn[];
}
export interface JobRuleSection { id: string; title: string; hint: string; fields: JobRuleField[] }
export interface JobRules {
  schema: JobRuleSection[];
  values: Record<string, JobRuleValue>;
  overrides: Record<string, JobRuleValue>;
}
/** Nested partial settings update (e.g. {sandbox: {stage_timeout_s: {implement: 7200}}}). */
export type JobRulesPatch = Record<string, unknown>;

/** Builds the nested PUT body from dotted paths; `null` resets a rule to its default. */
export function rulesPatch(entries: Record<string, unknown>): JobRulesPatch {
  const out: JobRulesPatch = {};
  for (const [path, value] of Object.entries(entries)) {
    const parts = path.split('.');
    let node = out;
    parts.slice(0, -1).forEach(part => {
      if (!node[part] || typeof node[part] !== 'object') node[part] = {};
      node = node[part] as JobRulesPatch;
    });
    node[parts[parts.length - 1]] = value === undefined ? null : value;
  }
  return out;
}

/** Thousands-grouped numbers without relying on Intl (Hermes builds vary). */
export function groupDigits(value: number): string {
  const [int, frac] = String(value).split('.');
  const sign = int.startsWith('-') ? '-' : '';
  const digits = sign ? int.slice(1) : int;
  return sign + digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (frac ? `.${frac}` : '');
}

/** Human-readable rule value ("50,000,000 tokens", "on", "pypi.org, *.npmjs.org"). */
export function formatRuleValue(field: JobRuleField, value: JobRuleValue | undefined): string {
  if (value === null || value === undefined) return field.optional ? 'not set' : '—';
  switch (field.kind) {
    case 'int':
    case 'float':
      return typeof value === 'number' ? `${groupDigits(value)}${field.unit ? ` ${field.unit}` : ''}` : String(value);
    case 'bool':
      return value ? 'on' : 'off';
    case 'select': {
      const opt = (field.options || []).find(o => o[0] === value);
      return opt ? opt[1] : String(value);
    }
    case 'list':
      return Array.isArray(value) && value.length ? (value as string[]).join(', ') : 'none';
    case 'suites':
      return Array.isArray(value) && value.length ? (value as JobRuleSuite[]).map(s => s.name).join(', ') : 'none';
    case 'rows': {
      if (!Array.isArray(value) || !value.length) return 'none';
      const keys = (field.columns || []).map(c => c.key);
      return (value as JobRuleRow[]).map(row => (keys.length ? keys : Object.keys(row)).map(k => row[k]).filter(Boolean).join(' · ')).join('; ');
    }
    default:
      return String(value) || '""';
  }
}

/* ------------------------------------------------------------ toolchain images */

/** Derived image state of a Settings → Toolchains row (GET /api/jobs/toolchains). */
export type ToolchainStatus = 'built' | 'building' | 'failed' | 'missing';
export interface ToolchainImage {
  repository?: string;
  base_image: string;
  tag?: string;
  status: ToolchainStatus;
  error?: string;
  log_tail?: string;
  started_at?: number;
  finished_at?: number;
}

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
  /** Import tickets into a workspace / git URL (session optional); `force` re-runs tickets whose latest job is done. */
  importKeys: (keys: string[], target?: TicketImportTarget | string | null) => api.post<{ created: Array<{ key: string; job_id: string }>; skipped?: unknown[]; errors?: unknown[] }>(
    '/api/jobs/jira/import', ticketImportBody(keys, target),
  ),
};

/** Where imported tickets run: workspace path or git URL first; the session is optional. */
export interface TicketImportTarget {
  workspace?: string; session?: string | null; force?: boolean;
  /** "Run in" for every imported ticket (`runInExecution`: `{template, remember_template?}` or
   * `{environments}`), sent as `defaults.execution`. */
  execution?: Record<string, unknown>;
}

/** POST /api/jobs/jira/import body: a git URL goes as `defaults.repository` (the server clones
 * it), a path as `defaults.workspace_root`; a bare string is the legacy session name. */
export function ticketImportBody(keys: string[], target?: TicketImportTarget | string | null): Record<string, unknown> {
  const t: TicketImportTarget = typeof target === 'string' ? { session: target } : target || {};
  const body: Record<string, unknown> = { keys };
  const path = (t.workspace || '').trim();
  const defaults: Record<string, unknown> = {};
  if (path) Object.assign(defaults, isRepositoryUrl(path) ? { repository: path } : { workspace_root: path });
  if (t.execution && Object.keys(t.execution).length) defaults.execution = { ...t.execution };
  if (Object.keys(defaults).length) body.defaults = defaults;
  if (t.session) body.session_name = t.session;
  if (t.force) body.force = true;
  return body;
}

/** Skipped import rows whose latest job is done (eligible for "Run again"). */
export function doneSkippedKeys(skipped: unknown[] | undefined): string[] {
  return ((skipped || []) as Array<{ key?: string; reason?: string }>)
    .filter(s => /latest job is done/.test(String(s.reason || '')) && s.key)
    .map(s => String(s.key));
}

/** One saved user session a job can run in (GET /api/jobs/sessions). */
export interface JobSessionOption {
  name: string;
  workspace: string;
  workspaces: string[];
  provider: string;
  model: string;
  agent_mode: string;
  yolo: boolean;
  session_type: string;
  /** Container-mode environment the session is attached to ('' = none). */
  environment?: string;
  loaded: boolean;
  current: boolean;
  updated_at: number;
}
export interface JobSessionsResponse { current: string | null; sessions: JobSessionOption[] }

/** "provider · model" for a session row ('' parts dropped). */
export function sessionModelLabel(s: Pick<JobSessionOption, 'provider' | 'model'>): string {
  return [s.provider, s.model].filter(Boolean).join(' · ');
}

/** Default session for a new job/import: the app's session when it is saved, else none (user must pick). */
export function defaultJobSession(sessions: JobSessionOption[], preferred: string | null | undefined): string | null {
  if (preferred && sessions.some(s => s.name === preferred)) return preferred;
  return null;
}

/** "Title or ticket key": a Jira key imports that ticket instead of creating a titled job. */
export const TICKET_KEY_RE = /^[A-Z][A-Z0-9_]+-\d+$/;
export const isTicketKey = (value: string) => TICKET_KEY_RE.test(value.trim());

const sessionWorkspaces = (s: Pick<JobSessionOption, 'workspace' | 'workspaces'>) =>
  (s.workspaces && s.workspaces.length ? s.workspaces : [s.workspace]).filter(Boolean);
const sameWorkspace = (a: string, b: string) => a.replace(/\/+$/, '') === b.replace(/\/+$/, '');

/** Recent workspaces derived from saved sessions, in session order (current first, then most recent). */
export function recentWorkspaces(sessions: JobSessionOption[], limit = 8): string[] {
  const out: string[] = [];
  for (const s of sessions) for (const w of sessionWorkspaces(s)) if (!out.includes(w)) out.push(w);
  return out.slice(0, limit);
}

/** The saved session working in `workspace` (supplies model defaults, mode and environment), if any. */
export function sessionForWorkspace(sessions: JobSessionOption[], workspace: string): JobSessionOption | undefined {
  const path = workspace.trim();
  return path ? sessions.find(s => sessionWorkspaces(s).some(w => sameWorkspace(w, path))) : undefined;
}

/** Preselected workspace: the app session's, else the current session's, else the most recent one. */
export function defaultWorkspace(sessions: JobSessionOption[], preferredSession: string | null | undefined): string {
  const entry = sessions.find(s => s.name === preferredSession) || sessions.find(s => s.current);
  return entry?.workspace || recentWorkspaces(sessions)[0] || '';
}

/** New-job form prefill from rules.values defaults.* (blank when unset). */
export function newJobDefaults(rules: JobRules | null | undefined): { retries: string; cost: string; runtime: string } {
  const values = rules?.values || {};
  const text = (key: string) => {
    const v = values[key];
    return typeof v === 'number' && Number.isFinite(v) ? String(v) : '';
  };
  return { retries: text('defaults.max_retries'), cost: text('defaults.max_cost_usd'), runtime: text('defaults.max_runtime_seconds') };
}

/** New-job cost field: blank = Settings → Job cost limit (omitted), 0 = explicitly unlimited. */
export function costCapPayload(text: string): number | undefined {
  const raw = String(text ?? '').trim();
  if (!raw) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : undefined;
}

/** Placeholder naming the effective Settings default ("Settings default ($100)"). */
export function costPlaceholder(rules: JobRules | null | undefined): string {
  const v = rules?.values?.['defaults.max_cost_usd'];
  if (typeof v !== 'number' || !Number.isFinite(v)) return 'Settings default ($100)';
  return v > 0 ? `Settings default ($${v})` : 'Settings default (no cap)';
}

/** Job management / history (GET /api/jobs/history). */
export interface JobHistoryQuery {
  q?: string;
  status?: string;
  archive?: 'all' | 'active' | 'archived' | string;
  scope?: 'history' | 'all' | string;
  sort?: 'updated' | 'created' | 'cost' | 'title' | 'status' | string;
  order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}
export interface JobHistoryRow extends EngineeringJob {
  archived: boolean;
  archived_at: number | null;
  archived_reason: string;
  attempt_count: number;
  elapsed_seconds: number;
  manageable: boolean;
}
export interface JobHistoryResponse { jobs: JobHistoryRow[]; total: number; limit: number; offset: number; filters: Record<string, unknown> }
export interface JobReport {
  total_jobs: number; archived_jobs: number; total_cost_usd: number; average_cost_usd: number; average_elapsed_seconds: number;
  attempts: number; failure_count: number; failure_rate: number; verification_runs: number; verification_passed: number;
  verification_pass_rate: number; status_counts: Record<string, number>;
  repositories: Array<{ repository: string; jobs: number; cost_usd: number }>;
}
export interface JobManagementState { job_id: string; archived: boolean; archived_at: number | null; archived_reason: string }

export function jobHistoryQuery(q: JobHistoryQuery): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(q)) {
    if (value === undefined || value === null || value === '') continue;
    out[key] = value as string | number;
  }
  return out;
}

export const jobsApi = {
  /** Every saved user session (job workers excluded), current first then most recent. */
  sessions: () => api.get<JobSessionsResponse>('/api/jobs/sessions'),
  getSettings: () => api.get<JobPipelineSettings>('/api/jobs/settings'),
  saveSettings: (settings: Partial<Pick<JobPipelineSettings, 'stage_models' | 'plan_approval'>>) => api.put<JobPipelineSettings>('/api/jobs/settings', settings),
  /** Partial run-rule update (nested object from `rulesPatch`); 400 `detail` names the bad field. */
  saveRules: (patch: JobRulesPatch) => api.put<JobPipelineSettings>('/api/jobs/settings', patch),
  /** Saved Settings → Toolchains rows with their derived image status. */
  toolchains: () => api.get<{ toolchains: ToolchainImage[] }>('/api/jobs/toolchains'),
  /** Starts a background build of a saved row's image (400 when the base image is not in a saved row). */
  buildToolchain: (baseImage: string) => api.post<ToolchainImage>('/api/jobs/toolchains/build', { base_image: baseImage }),
  create: (input: CreateJobInput) => api.post<{ job: EngineeringJob }>('/api/jobs', input),
  /** New job sheet with a ticket key: import that Jira ticket with the sheet's workspace/execution as defaults. */
  importTicket: (key: string, sessionName: string | null | undefined, defaults: Record<string, unknown>, force = false) =>
    api.post<{ created: Array<{ key: string; job_id: string; title?: string }>; skipped?: Array<{ key: string; job_id: string; reason?: string }>; errors?: Array<{ key: string; error: string }> }>(
      '/api/jobs/jira/import', { keys: [key], session_name: sessionName || undefined, defaults, ...(force ? { force: true } : {}) },
    ),
  /** Worker log, failure and diagnostic events (the Diagnostics tab; no VM equivalent). */
  diagnostics: (jobId: string) => api.get<{ diagnostics: JobDiagnosticsSnapshot }>(`/api/jobs/${encodeURIComponent(jobId)}/diagnostics`),
  /** Jobs waiting on a human (the Work tab badge, as the web rail). */
  attention: () => api.get<{ count: number; jobs: Array<{ id: string; title: string; status: string; attention_reason: string; attention_detail: string; updated_at: number }> }>('/api/jobs/attention', { timeoutMs: 6_000 }),
  /** Attempts per stage run (Cost & audit tab; no VM equivalent). */
  attempts: (jobId: string) => api.get<{ attempts: JobAttempt[] }>(`/api/jobs/${encodeURIComponent(jobId)}/attempts`),
  /** History / management list (archived and terminal jobs included). */
  history: (q: JobHistoryQuery = {}) => api.get<JobHistoryResponse>('/api/jobs/history', { query: jobHistoryQuery(q) }),
  /** Aggregate report for the same filters as history. */
  report: (q: Omit<JobHistoryQuery, 'sort' | 'order' | 'limit' | 'offset'> = {}) => api.get<{ report: JobReport }>('/api/jobs/report', { query: jobHistoryQuery(q) }),
  archive: (jobId: string, reason = '') => api.post<JobManagementState>(`/api/jobs/${encodeURIComponent(jobId)}/archive`, { reason }),
  restore: (jobId: string) => api.post<JobManagementState>(`/api/jobs/${encodeURIComponent(jobId)}/restore`, {}),
  bulk: (action: 'archive' | 'restore' | 'delete', jobIds: string[], opts: { reason?: string; force?: boolean } = {}) =>
    api.post<{ action: string; requested: number; succeeded: number; failed: number; results: Array<{ job_id: string; ok: boolean; error?: string }> }>(
      '/api/jobs/manage/bulk', { action, job_ids: jobIds, reason: opts.reason || '', force: !!opts.force },
    ),
};


/** True for a remote git URL (https://, http://, ssh://, git://, or scp-style git@host:path) rather than a local path. */
export function isRepositoryUrl(text: string): boolean {
  const value = String(text || '').trim();
  if (!value || /\s/.test(value)) return false;
  if (/^(https?|ssh|git):\/\/[^/\s]+\/.+/i.test(value)) return true;
  return /^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:(?!\/\/)[^\s]+$/.test(value);
}
