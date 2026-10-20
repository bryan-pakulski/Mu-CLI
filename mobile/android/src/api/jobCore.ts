import { api, ApiError } from './client';
import { subscribeToEvents } from './sse';
import type { JobBuildEnvironments, JobEnvironmentsSummary } from './environments';
import type { ResolvedStageAgent } from './agents';

export type JobPart = 'state' | 'pipeline' | 'plan' | 'session' | 'diff' | 'review' | 'checks' | 'costs' | 'notes';
export type ActionTone = 'ok' | 'warn' | 'danger' | '';
/** Server-decided command availability (CONTRACT §7 adds tone/confirm/input/dialog/hidden). */
export interface JobAction {
  id: string; label: string; enabled: boolean; reason: string; primary: boolean; danger: boolean;
  shortcut?: string;
  tone?: ActionTone;
  confirm?: string | null;
  input?: JobActionInput | null;
  dialog?: 'push' | null | string;
  hidden?: boolean;
  args?: { requires_override?: boolean; event_id?: number; version?: number; fingerprint?: string; [key: string]: unknown };
}
/**
 * Prompt spec for an action. Newer servers also send `arg` (the args key the
 * typed text goes to — wins over the client's id→arg fallback), `value`
 * (prefill), `hint` (helper text) and `type: 'amount'` (single line; text
 * keyboard so suffixes like "30m" / "5M" can be typed).
 */
export interface JobActionInput {
  label: string; optional?: boolean; multiline?: boolean;
  arg?: string; value?: string; hint?: string; type?: 'amount' | 'text' | string; unit?: string;
}
export type JobLimitKind = 'cost' | 'tokens' | 'time' | 'iterations';
/** Set while a job is paused on a cost/tokens/time/iterations cap (state needs_input, attention limit_reached). */
export interface JobLimit {
  kind: JobLimitKind | string; scope: 'job' | 'stage' | string; stage?: string;
  used: number; limit: number; unit: 'usd' | 'tokens' | 'seconds' | 'iterations' | string;
  message: string; summary: string; suggested?: number; suggested_text?: string;
}
export interface JobStep { id: string; label: string; status: string; glyph: string; elapsed: number; cost_usd: number; runs: number; detail: string; started_at?: number | null }
export interface Finding { id: string; severity: string; title: string; detail: string; suggestion: string; file: string | null; line_start: number | null; disposition: string; category?: string; confidence?: number | string | null }
export interface StageModel { provider?: string; model?: string }
export interface JobSummary {
  id: string; title: string; key: string; state: string; verdict: string; mini: string;
  attention_detail: string; repository: string; cost_usd: number; actions: JobAction[];
  created_at: number; updated_at: number; steps: JobStep[];
  paused_from?: string | null; failed_from?: string | null; primary_action?: string;
  stage_models?: Record<string, StageModel | null>;
  /** Agent per stage (mucli / Claude Code / Codex) and its model. */
  stage_agents?: Record<string, ResolvedStageAgent>;
  /** Saved session the job was created from (metadata.origin_session); '' when unknown. */
  origin_session?: string;
  /** Build environments pill (count 0 = shared runtime image). */
  environments?: JobEnvironmentsSummary;
  /** Job cost cap (`max_cost_usd`); null = unlimited. */
  max_cost_usd?: number | null;
  /** The job's PR once pushed; `state` is set by the merge poller (open | merged | closed). */
  pr?: JobPR | null;
  /** Paused on a job/stage cap; null otherwise (older servers omit it). */
  limit?: JobLimit | null;
  /** Home needs-you rows: one-line attention summary (= limit.summary when paused on a limit). */
  attention_summary?: string;
}
export interface JobPR { url?: string; number?: number; provider?: string; draft?: boolean; state?: 'open' | 'merged' | 'closed' | string; merged_at?: string; checked_at?: number }
/** Header: running cost vs the cap and worker-active time vs the runtime cap. */
export interface JobBudget { cost_usd: number; max_cost_usd: number | null; cost_fraction: number | null; active_seconds: number; max_runtime_seconds: number | null }
/** A repository-declared image/devcontainer the paused job waits on (approveEnvironment / rejectEnvironment). */
export interface EnvironmentApproval {
  repository: string; fingerprint: string; kind: 'image' | 'build' | string; ref: string; label: string;
  origin: string; ignored_features?: string[]; message?: string; event_id?: number;
}
/** Current worktree diff against the base commit while a job is active. */
export interface LiveDiff { patch: string; files: string[]; untracked: string[]; untracked_total?: number; truncated: boolean; base_sha: string; generated_at: number; bytes: number }
/** Why a check could not run (missing tool, project tool resolved globally, …) and the fix. */
export interface CheckUnrunnable { tool: string; cause: string; hint: string }
export interface CheckSuite {
  suite: string; command: string; passed: boolean; stdout: string; stderr: string; preexisting_failures: string[];
  /** `unrunnable` when the check never really ran (job_templates.md §5); never triaged as pre-existing. */
  status?: 'passed' | 'failed' | 'unrunnable' | string;
  unrunnable?: CheckUnrunnable | null;
  duration_ms?: number;
  /** Build environment (binding name) that ran the suite; '' = shared runtime image. */
  environment?: string;
  junit?: { passed: number; failed: number; skipped: number; total: number; cases?: Array<{ id: string; status: string; time?: number; suite?: string; name?: string; message?: string }> };
}
export interface CheckRun {
  run: number; passed: boolean; head_sha: string;
  new_failures: number | string[]; preexisting_failures: number | string[];
  suites: CheckSuite[];
  started_at?: number; duration_ms?: number; trigger?: 'auto' | 'recheck' | 'fix' | 'repair' | string; finished_at?: number;
  /** Verification summary: false when any check was unrunnable (the run is unverified, not failed). */
  verified?: boolean;
  /** Number of unrunnable checks in the run. */
  unrunnable?: number;
}
/** One environment repair attempt (job_templates.md §7): an unattended repair stage made checks runnable. */
export interface EnvironmentRepair {
  attempt: number; binding: string; source?: string; template?: string;
  trigger_suites?: Array<{ suite?: string; command?: string; kind?: string; tool?: string; cause?: string }>;
  commands?: string[]; project_commands?: string[]; image?: string; cost_usd?: number;
  outcome: 'running' | 'committed' | 'repaired' | 'unresolved' | 'failed' | 'timed_out' | 'budget_exhausted' | 'error' | string;
  error?: string; reverted_files?: string[]; agent?: string; transcript?: string; started_at?: number; finished_at?: number | null;
}
/** `suite` is the check-suite name (same key as history); the JUnit classname is in `classname`. */
export interface FailedCase { run: number; suite: string; name: string; id: string; message: string; new: boolean; classname?: string }
export interface SuiteHistoryPoint { run: number; passed: boolean; duration_ms: number | null; finished_at: number | null }
export interface TranscriptPart { type: string; text?: string; tool_name?: string; tool_args?: unknown; tool_result?: unknown }
/** A screenshot, recording or log an agent captured while testing (mu/jobs/evidence.py). */
export interface EvidenceItem { id: string; stage: string; attempt?: number; file: string; name: string; caption: string; kind: 'image' | 'video' | 'text' | string; bytes: number; created_at: number }
/** One plain-English note (mu/jobs/core/notes.py): what happened, oldest first; `tab` links to detail. */
export interface JobNote { id: string; at: number; stage: string; tone: string; title: string; headline: string; bullets: string[]; meta: string; count: number; tab: string }
export interface JobVM extends JobSummary {
  notes?: JobNote[];
  branch: string; worktree: string; pipeline: JobStep[]; fix_attempts?: number;
  plan?: { version: number; latest_version: number; approved_version: number; versions: number[]; markdown: string; draft: string | null; diff_from_previous: string; errors: string[]; error?: string };
  review?: { verdict: string; merge_readiness: number; summary: string; counts: Record<string, number>; findings: Finding[]; plan_coverage: Array<{ criterion_id: string; status: string; evidence: string }>; out_of_scope_changes?: unknown[] } | null;
  diff?: { patch: string | null; viewed?: string[]; live?: LiveDiff | null };
  budget?: JobBudget;
  environment_approval?: EnvironmentApproval | null;
  /** Jobs run unattended: a failed template job setup is a warning (the job continued). */
  setup_warning?: { binding: string; command?: string; index?: number; exit_code?: number | null; template?: string; message?: string; error?: string } | null;
  checks?: { live?: AuditEvent[]; runs: CheckRun[]; evidence?: EvidenceItem[]; failed_cases?: FailedCase[]; history?: Record<string, SuiteHistoryPoint[]>; repairs?: EnvironmentRepair[] };
  session?: { session_name?: string; approval?: { tool_name: string; tool_args: Record<string, unknown> | string; reason?: string; detail?: string } | null; live: boolean; events: AuditEvent[]; stages: Record<string, Array<{ role: string; parts: TranscriptPart[] }>> };
  costs?: { total_usd: number; by_stage: Record<string, number>; events: AuditEvent[] };
  /** Per-binding build environment state (job environments panel). */
  build_environments?: JobBuildEnvironments;
}
export interface AuditEvent { id: number; event_type: string; reason: string; created_at: number; payload: Record<string, unknown>; to_status?: string | null }
export interface HomeVM { needs_you: JobSummary[]; running: JobSummary[]; jobs: JobSummary[]; counts: { needs_you: number; total: number } }
export interface JobChange { scope: 'home' | 'job' | 'board'; id: string; parts: JobPart[]; seq: number }
export interface CommandResult { ok: boolean; parts: JobPart[]; data: { version?: number } }

/** CONTRACT §2 — what `push` would send as the PR/MR. */
export interface PushPreview {
  title: string; body: string; branch: string; base: string; mode: string; remote: string; draft: boolean;
  requires_override: boolean; verdict: string; head_sha: string; reason: string;
  /** PR/MR token the push will use (never the value); source '' means it is not set. */
  token?: { provider: string; variable: string; source: '' | 'environment' | 'stored' } | null;
  provider?: string;
}
/** CONTRACT §2 push args. */
export interface PushArgs { pr_title?: string; pr_body?: string; draft?: boolean; override_reason?: string }

/** CONTRACT §5 — activity (agents, checks, containers). */
export interface ActivityContainer {
  /** Docker State (running/exited…); `status_text` is the human Docker status line. */
  name: string; id: string; image: string; status: string; status_text?: string; stage: string; started_at: number | null;
  cpu_percent: number | null; mem_bytes: number | null; mem_limit: number | null;
  /** stage = agent/check container · engine = the job's Docker engine · broker = run_in_environment sibling. */
  role?: 'stage' | 'engine' | 'broker' | string; binding?: string; gpus?: string;
}
export interface ActivityJob {
  id: string; key: string; title: string; state: string; stage: string;
  worker_id: string | null; heartbeat_at: number | null; lease_expires_at: number | null; since: number;
  cost_usd: number; kind: 'agent' | 'checks' | 'provision' | 'push' | 'waiting' | string; last: string;
  containers: ActivityContainer[];
}
export interface ActivityVM {
  /** 'off' = no docker CLI, 'down' = CLI present but `docker ps` failed. */
  generated_at: number; engine: 'up' | 'down' | 'off' | string;
  counts: { agents: number; checks: number; containers: number; needs_you: number };
  jobs: ActivityJob[]; orphans: ActivityContainer[];
}
export interface ActivityLog { text: string; truncated: boolean; source: 'worker' | 'container' | string }
export interface JobsStatusVM { agents: number; checks: number; needs_you: number; engine: string }

/** True when the server is older than the contract endpoint (404/405). */
export function isMissingEndpoint(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405);
}

/** Resolve to `null` instead of throwing when an endpoint does not exist yet. */
export async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try { return await promise; } catch (err) { if (isMissingEndpoint(err)) return null; throw err; }
}

const enc = encodeURIComponent;

export const jobCoreApi = {
  home: () => api.get<HomeVM>('/api/jobs/vm/home'),
  jobs: () => api.get<{ count: number; jobs: JobSummary[] }>('/api/jobs/vm/jobs'),
  job: (id: string, parts?: JobPart[], version?: number) => api.get<JobVM>(`/api/jobs/vm/job/${enc(id)}`, { query: { parts: parts?.join(','), plan_version: version } }),
  command: (id: string, type: string, args: Record<string, unknown> = {}) => api.post<CommandResult>('/api/jobs/cmd', { type, job_id: id, args }),
  /** §2 push dialog prefill. */
  pushPreview: (id: string) => api.get<PushPreview>(`/api/jobs/vm/job/${enc(id)}/push-preview`),
  /** §4 server-side viewed mark for one diff file. */
  markViewed: (id: string, path: string, viewed: boolean) => api.post<CommandResult>('/api/jobs/cmd', { type: 'markViewed', job_id: id, args: { path, viewed } }),
  /** §5 activity tree. */
  activity: () => api.get<ActivityVM>('/api/jobs/vm/activity', { timeoutMs: 8_000 }),
  activityLogs: (jobId: string, tailBytes = 65536) => api.get<ActivityLog>(`/api/jobs/vm/activity/logs/${enc(jobId)}`, { query: { tail_bytes: tailBytes } }),
  removeContainer: (name: string) => api.post<{ ok: boolean }>(`/api/jobs/activity/containers/${enc(name)}/remove`, {}),
  /** §5 cheap header chips. */
  status: () => api.get<JobsStatusVM>('/api/jobs/vm/status', { timeoutMs: 5_000 }),
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
