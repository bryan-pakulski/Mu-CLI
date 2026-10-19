// Orchestration API (documentation/plans/orchestration.md §8 "Mobile").
//
// Reads are plain GETs on /api/orchestration/*; every mutation is
// POST /api/orchestration/cmd {type, args} carrying the CSRF header the
// server hands out at GET /api/orchestration/csrf. The token is cached per
// server and refetched once when a command answers 403 (the GUI restarted
// and its HMAC key changed). Shapes mirror mu/orchestration/vm.py.

import { api, ApiError, baseUrl } from './client';
import { subscribeToEvents, type SSESubscription } from './sse';
import type { JobAction, JobActionInput } from './jobCore';

/** Server prompt spec; `expect` asks for typed confirmation (the user types exactly this). */
export interface OrchActionInput extends JobActionInput { expect?: string }
/** ActionVM (mu/orchestration/actions.py): availability decided once, on the server. */
export interface OrchAction extends Omit<JobAction, 'input'> { input?: OrchActionInput | null }

/** `table_style(...)` row: shared status vocabulary key + canonical label. */
export interface OrchStyle { status: string; label: string }
/** `effect_style(...)`: semantic tone + label for read_only | mutating | destructive. */
export interface OrchEffectStyle { semantic: string; label: string }
export type OrchEffect = 'read_only' | 'mutating' | 'destructive' | string;

export interface OrchAttention {
  approvals: number; paused_runs: number; reviews: number; pending_nodes: number;
  offline_nodes: number; dispatch_paused: boolean; total: number;
}

/** GET /api/orchestration/badge: the attention counts without starting orchestration (`used` false
 * on a server that never used it). The tab badge and notifier poll this, never /attention. */
export interface OrchBadge extends OrchAttention { used: boolean; summary: string }

/** vm.agent_status: the node's agent against the master's. */
export interface OrchAgentStatus { version: string; current: string; outdated: boolean; updatable: boolean; reason: string }

export interface OrchFactsSummary {
  os: string; kernel: string; arch: string; cpus: number | null; hostname: string;
  memory_gb: number | null; memory_used_pct: number | null; max_disk_used_pct: number | null;
  uptime_days: number | null; load: number | null; systemd: unknown; services: number; provides: string[];
}

export interface OrchNode {
  id: string; name: string; labels: Record<string, string>; status: string; style: OrchStyle;
  online: boolean; kind: string; canary: boolean; last_seen: number | null; last_seen_ago: string;
  agent_version: string; address: string; facts_at: number | null; summary: OrchFactsSummary;
  groups: string[]; policy: Record<string, unknown>; sandbox: string; policy_errors: string[];
  actions: OrchAction[]; created_at: number; accepted_at?: number | null; agent?: OrchAgentStatus;
}

export interface OrchExecution {
  id: string; run_id: string; node_id: string; tool: string; step: number; step_name: string;
  batch: number; state: string; style: OrchStyle; exit_code: number | null; reason: string;
  duration_s: number | null; attempts: number; check_mode: boolean; rollback_of: string;
  output: Record<string, unknown> | null; output_errors: unknown; created_at: number;
  dispatched_at: number | null; started_at: number | null; finished_at: number | null; redactions: number;
  log?: string; log_tail?: string; log_truncated?: boolean; params?: Record<string, unknown>;
}

export interface OrchNodeDetail extends OrchNode {
  facts: Record<string, unknown>;
  recent_executions: OrchExecution[];
}

export interface OrchRunDecision { level: string | null; summary: string | null; rule: string | null }
export interface OrchRunSummary {
  executions?: Record<string, number>;
  nodes?: { done: number; failed: number; active: number; pending: number };
  total_nodes?: number; batches?: number; batch?: number; [key: string]: unknown;
}

export interface OrchRunBrief {
  id: string; title: string; kind: string; target: string; state: string; style: OrchStyle;
  proposed_by: string; approved_by: string; created_at: number; started_at: number | null;
  finished_at: number | null; check_mode: boolean; nodes: number; summary: OrchRunSummary;
  paused_reason: string; reason: string; effect: OrchEffect; effect_style: OrchEffectStyle;
  decision: OrchRunDecision; blast_radius: string;
}

export interface OrchRunCell {
  step: number; name: string | null; exec: OrchExecution | null; style: OrchStyle; attempts: number;
}
export interface OrchRunNodeRow {
  node_id: string; name: string; labels: Record<string, string>; online: boolean; batch: number;
  cells: OrchRunCell[]; rollbacks: OrchExecution[];
  /** scheduler.node_progress: ready | active | failed | done | waiting_rollback. */
  progress: string; failed: boolean;
}
export interface OrchExcluded { node_id: string; name: string; reason: string }

export interface OrchRunDetail extends OrchRunBrief {
  params: Record<string, unknown>; selector: Record<string, unknown>; strategy: Record<string, unknown>;
  excluded: OrchExcluded[]; steps: Array<{ name?: string; [key: string]: unknown }>; batches: string[][];
  warnings: string[]; rollback_available: unknown; decision_full: Record<string, unknown> | null;
  grid: OrchRunNodeRow[]; notes: unknown[]; actions: OrchAction[]; secrets: string[];
}

/** Tool/runbook versions awaiting review: read-only on the phone (review happens on the web). */
export interface OrchReviewItem {
  ref: string; title: string; summary: string; version: number; status: string; style: OrchStyle;
  effect: OrchEffect; effect_style: OrchEffectStyle; tool_id?: string; runbook_id?: string;
}

export interface OrchOverview {
  nodes: { total: number; by_status: Record<string, number> };
  catalog: { published: number; drafts: number; in_review: number };
  attention: OrchAttention; recent_runs: OrchRunBrief[]; approvals: OrchRunBrief[];
  paused: OrchRunBrief[]; unreachable: OrchExecution[]; dispatch_paused: boolean; time: number;
}

export interface OrchApprovals {
  runs: OrchRunDetail[]; paused: OrchRunBrief[]; reviews: OrchReviewItem[];
  runbook_reviews: OrchReviewItem[]; nodes: OrchNode[];
}

/** Fleet agent summary on GET /api/orchestration/nodes. */
export interface OrchAgentsSummary { current: string; auto: boolean; outdated: number; updatable: number; open_run: string }
export interface OrchNodesResponse { nodes: OrchNode[]; groups: unknown[]; agents?: OrchAgentsSummary }
export interface OrchCommandResult { ok: boolean; command: string; data: Record<string, unknown> }
export interface OrchChange { scope: 'node' | 'tool' | 'run'; id: string; parts: string[]; seq: number }

/** Commands the phone sends (mu/orchestration/commands.py). */
export type OrchCommand =
  | 'approveRun' | 'rejectRun' | 'continueRun' | 'pauseRun' | 'rollbackRun' | 'cancelRun' | 'retryNode'
  | 'acceptNode' | 'quarantineNode' | 'unquarantineNode' | 'pauseDispatch' | 'resumeDispatch' | 'proposeAgentUpdate';

export const ORCH_CSRF_HEADER = 'X-MuCLI-CSRF';

let csrf: { base: string; token: string; header: string } | null = null;

/** Test hook: forget the cached CSRF token. */
export function resetCsrfCache(): void { csrf = null; }

async function csrfToken(fresh = false): Promise<{ token: string; header: string }> {
  const base = baseUrl();
  if (!fresh && csrf && csrf.base === base) return csrf;
  const res = await api.get<{ token: string; header?: string }>('/api/orchestration/csrf');
  csrf = { base, token: String(res?.token || ''), header: res?.header || ORCH_CSRF_HEADER };
  return csrf;
}

/** POST /api/orchestration/cmd with the CSRF header; one token refetch + retry on 403. */
async function command(type: OrchCommand | string, args: Record<string, unknown> = {}): Promise<OrchCommandResult> {
  const body = { type, args };
  let token = await csrfToken();
  try {
    return await api.post<OrchCommandResult>('/api/orchestration/cmd', body, { headers: { [token.header]: token.token } });
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 403) throw err;
    token = await csrfToken(true);
    return api.post<OrchCommandResult>('/api/orchestration/cmd', body, { headers: { [token.header]: token.token } });
  }
}

const enc = encodeURIComponent;

export const orchestrationApi = {
  overview: () => api.get<OrchOverview>('/api/orchestration/overview'),
  badge: () => api.get<OrchBadge>('/api/orchestration/badge'),
  attention: () => api.get<OrchAttention>('/api/orchestration/attention'),
  approvals: () => api.get<OrchApprovals>('/api/orchestration/approvals'),
  nodes: () => api.get<OrchNodesResponse>('/api/orchestration/nodes'),
  node: (id: string) => api.get<OrchNodeDetail>(`/api/orchestration/nodes/${enc(id)}`),
  runs: (states?: string[], limit?: number) => api.get<{ runs: OrchRunBrief[] }>('/api/orchestration/runs', {
    query: { state: states?.length ? states.join(',') : undefined, limit },
  }),
  run: (id: string) => api.get<OrchRunDetail>(`/api/orchestration/runs/${enc(id)}`),
  execution: (id: string) => api.get<OrchExecution>(`/api/orchestration/executions/${enc(id)}`),
  command,
  approveRun: (runId: string, typed?: string) => command('approveRun', typed ? { run_id: runId, typed } : { run_id: runId }),
  rejectRun: (runId: string, reason?: string) => command('rejectRun', reason ? { run_id: runId, reason } : { run_id: runId }),
  continueRun: (runId: string) => command('continueRun', { run_id: runId }),
  pauseRun: (runId: string) => command('pauseRun', { run_id: runId }),
  rollbackRun: (runId: string) => command('rollbackRun', { run_id: runId }),
  cancelRun: (runId: string) => command('cancelRun', { run_id: runId }),
  retryNode: (runId: string, nodeId: string) => command('retryNode', { run_id: runId, node_id: nodeId }),
  acceptNode: (nodeId: string) => command('acceptNode', { node_id: nodeId }),
  quarantineNode: (nodeId: string, reason?: string) => command('quarantineNode', reason ? { node_id: nodeId, reason } : { node_id: nodeId }),
  unquarantineNode: (nodeId: string) => command('unquarantineNode', { node_id: nodeId }),
  pauseDispatch: (reason?: string) => command('pauseDispatch', reason ? { reason } : {}),
  resumeDispatch: () => command('resumeDispatch', {}),
  /** An update job for out-of-date agents (all updatable nodes, or `nodes`): an ordinary run to approve. */
  proposeAgentUpdate: (nodes?: string[]) => command('proposeAgentUpdate', nodes?.length ? { nodes } : {}),
};

/** Live changes: `data: {scope, id, parts, seq}` (the `hello` event is named and ignored). */
export function subscribeToOrchestrationChanges(onChange: (change: OrchChange) => void, onOpen?: () => void): SSESubscription {
  return subscribeToEvents({ onMessage: event => onChange(event as unknown as OrchChange), onOpen }, {
    path: '/api/orchestration/events', sessionName: null,
    decode: raw => {
      const value = raw as Partial<OrchChange> | null;
      return value && ['node', 'tool', 'run'].includes(String(value.scope || '')) && Array.isArray(value.parts)
        ? { ...value, kind: 'orchestration_change' } : null;
    },
  });
}

/**
 * Orchestration attention on the global, session-agnostic bus (`/api/events`):
 * `{kind: "orchestration_attention", detail, run_id?, title?, node_id?, ref?, state?, reason?, paused?}`.
 * Listening here never touches /api/orchestration, so servers that never used
 * orchestration are not made to start it.
 */
export interface OrchAttentionEvent {
  kind: 'orchestration_attention'; detail?: string; run_id?: string; title?: string;
  node_id?: string; ref?: string; state?: string; reason?: string; paused?: boolean;
}

export function subscribeToOrchestrationAttention(onEvent: (event: OrchAttentionEvent) => void): SSESubscription {
  return subscribeToEvents({
    onMessage: event => { if (event.kind === 'orchestration_attention') onEvent(event as unknown as OrchAttentionEvent); },
  }, { path: '/api/events', sessionName: null });
}

/** A notification for an attention event, or null when it needs no human (dispatch toggles, routine states). */
export function attentionNotification(event: OrchAttentionEvent): { title: string; body: string; data: Record<string, string> } | null {
  const detail = String(event.detail || '');
  if (detail === 'orchestration_approval' && event.run_id) {
    return { title: 'Orchestration: run awaits approval', body: String(event.title || event.run_id).slice(0, 240), data: { orchRunId: event.run_id } };
  }
  if (detail === 'orchestration_node' && event.node_id) {
    return { title: 'Orchestration: node awaits acceptance', body: `Node ${event.node_id} enrolled and waits for you.`, data: { orchNodeId: event.node_id } };
  }
  if (detail === 'orchestration_review' && event.ref) {
    return { title: 'Orchestration: tool submitted for review', body: `${event.ref} · review it on the web.`, data: { orchRef: event.ref } };
  }
  if (event.run_id && (event.state === 'paused' || event.state === 'failed')) {
    const what = event.state === 'paused' ? 'Run paused' : 'Run failed';
    return { title: `Orchestration: ${what}`, body: String(event.reason || event.run_id).slice(0, 240), data: { orchRunId: event.run_id } };
  }
  return null;
}
