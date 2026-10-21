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
  reboot_required?: boolean | null; reboot_reasons?: string[];
}

/** vm.node_presence: right after the master starts, a node not heard from yet is `connecting`. */
export type OrchPresence = 'online' | 'connecting' | 'offline' | string;

/** vm.node_busy: work in flight on a node (the run, its tool, running or queued). */
export interface OrchBusy {
  run_id: string; title: string; tool: string; step_name?: string; state: 'running' | 'queued' | string;
  check_mode?: boolean; since?: number | null; count: number; queued: number;
}

/** node_policy.status: whether policy.yaml can be edited from the master, and the last edit. */
export interface OrchRemotePolicy {
  allowed: boolean; supported?: boolean; reason?: string; text?: string; sha256?: string;
  pending?: { state: 'pending' | 'applied' | 'refused' | 'expired' | string; by?: string; at?: number; applied_at?: number; reason?: string } | null;
}

export interface OrchNode {
  id: string; name: string; labels: Record<string, string>; status: string; style: OrchStyle;
  online: boolean; kind: string; canary: boolean; last_seen: number | null; last_seen_ago: string;
  agent_version: string; address: string; facts_at: number | null; summary: OrchFactsSummary;
  groups: string[]; policy: Record<string, unknown>; sandbox: string; policy_errors: string[];
  actions: OrchAction[]; created_at: number; accepted_at?: number | null; agent?: OrchAgentStatus;
  presence?: OrchPresence; busy?: OrchBusy | null;
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
  remote_policy?: OrchRemotePolicy;
}

/** GET /nodes/{id}/maintenance (mu/orchestration/maintenance.py): the node page's control panel. */
export interface OrchMaintenanceAction {
  key: string; label: string; help: string; tool: string; effect: string; danger: boolean;
  available: boolean; check_available: boolean; installed: boolean; reason: string; needs?: string; params?: Record<string, unknown>;
}
export interface OrchOpenRun extends OrchRunBrief { actions: OrchAction[]; here: string; progress: string }
export interface OrchMaintenance {
  node_id: string; name: string; os?: string; kernel?: string; uptime_days: number | null; installed?: boolean;
  reboot: { required: boolean | null; reasons?: string[] }; pending_updates: { count: number; at: number } | null;
  services: string[]; actions: OrchMaintenanceAction[]; open_runs: OrchOpenRun[]; recent: OrchExecution[];
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
  /** Nodes that report a pending reboot. */
  reboot?: { count: number; nodes: Array<{ node_id: string; name: string; reasons?: string[] }> };
  /** The node endpoint; no `bind` means nodes can't enrol yet. */
  network?: { bind: string; port: number; advertise?: string };
}

export interface OrchApprovals {
  runs: OrchRunDetail[]; paused: Array<OrchRunBrief & { actions?: OrchAction[] }>; reviews: OrchReviewItem[];
  runbook_reviews: Array<OrchReviewItem & { actions?: OrchAction[]; problems?: string[]; steps?: unknown[] }>; nodes: OrchNode[];
}

/* ------------------------------------------------------------ catalog */

export interface OrchNote { text: string; at: number; actor?: string; action?: string; tone?: string }
export interface OrchLint { ok: boolean; current: boolean; stale: boolean; counts: Record<string, number> }
export interface OrchLintFinding { severity: 'error' | 'warning' | 'info' | string; rule: string; message: string; file?: string; line?: number }
export interface OrchTests { count: number; passed: number; last: { passed: boolean; run_id?: string; at?: number } | null }
export interface OrchVersionRef { version: number; ref: string; status: string; style: OrchStyle }

/** A tool version as the catalog lists it (vm.tool_brief). */
export interface OrchToolBrief {
  tool_id: string; version: number; ref: string; title: string; summary: string; category: string; status: string;
  style: OrchStyle; effect: OrchEffect; effect_style: OrchEffectStyle; stage: string; intent: string;
  lint: OrchLint; tests: OrchTests; created_by: string; updated_at: number; session_name?: string; actions: OrchAction[];
}
/** GET /tools/{id}/{v} (vm.tool_detail). */
export interface OrchToolDetail extends OrchToolBrief {
  manifest: Record<string, any>; manifest_yaml: string; files: Record<string, string>; blobs: Record<string, string>;
  lint_result: { findings: OrchLintFinding[]; at?: number; stale?: boolean } | null; review: { headline?: string; risks?: string[]; notes?: string; submitted_by?: string; submitted_at?: number } | null;
  previous: string; diff: string; versions: OrchVersionRef[]; notes: OrchNote[]; usage: OrchRunBrief[];
  run_as: string; check_mode: boolean; idempotent: boolean; rollback: string; secrets: unknown[]; origin: string; created_at: number; published_at: number | null;
}

export interface OrchProblem { path: string; message: string; severity?: 'error' | 'warning' | string; line?: number; column?: number }

export interface OrchRunbookBrief {
  runbook_id: string; version: number; ref: string; title: string; summary: string; status: string; style: OrchStyle;
  effect: OrchEffect; effect_style: OrchEffectStyle; steps: Array<Record<string, any>>; problems: string[]; parses?: boolean;
  created_by?: string; updated_at?: number; actions?: OrchAction[];
}
/** GET /runbooks/{id}/{v} (vm.runbook_vm detail). */
export interface OrchRunbookDetail extends OrchRunbookBrief {
  spec: Record<string, any>; spec_yaml: string; source: string; digest: string; inputs: Record<string, any>;
  problem_details: OrchProblem[]; warnings: unknown[]; actions: OrchAction[]; tests: OrchTests & { runs?: OrchRunBrief[] };
  versions: OrchVersionRef[]; notes: OrchNote[]; usage: OrchRunBrief[]; previous: string; diff: string; parses: boolean;
}

/** validateRunbook: the editor's live checks (nothing is stored). */
export interface OrchRunbookValidation {
  ok: boolean; parses: boolean; yaml_error: { message: string; line?: number; column?: number } | null;
  spec: Record<string, any> | null; yaml: string; problems: OrchProblem[]; effect: OrchEffect | null;
  effect_style?: OrchEffectStyle; decision: { level: string; summary?: string } | null; digest?: string;
  steps_info: Array<{ ok?: boolean; effect?: string; effect_style?: OrchEffectStyle }>;
}

/* ------------------------------------------------------------ results */

export interface OrchResultColumn { key: string; label: string; kind: string; format: string }
export interface OrchResultRow {
  exec_id: string; run_id: string; node: string; node_id: string; state: string;
  labels: Record<string, string>; values: Record<string, unknown>; finished_at?: number;
}
export interface OrchResultTable {
  key: string; label: string; per: 'node' | 'item' | string; columns: OrchResultColumn[];
  metric: string | null; order: 'asc' | 'desc' | string; rows: OrchResultRow[];
}
/** A run's results ({steps}) or a tool's latest results ({tables}). */
export interface OrchResults {
  steps?: Array<{ step: number; step_name: string; tool: string; tables: OrchResultTable[] }>;
  tables?: OrchResultTable[]; oldest?: number;
}

/* ------------------------------------------------------------ settings */

export interface OrchAccessRule { name: string; match: Record<string, unknown>; decision: string | Record<string, unknown> }
export interface OrchSettings {
  network: { bind: string; port: number; advertise?: string };
  endpoint: { bind: string; port: number; running: boolean; error: string; url: string; kind: string; fingerprint: string };
  access_policy: { rules: OrchAccessRule[]; default: boolean; yaml: string; version: number };
  keys: Record<string, { key_id: string; public_key: string }>;
  secrets: { locked: boolean; names: string[]; passphrase: boolean };
  agent_updates: { auto: boolean; current: string };
  defaults: { strategy: Record<string, unknown> };
  schedules: Array<Record<string, any>>; groups: Array<Record<string, any>>;
  trusted_keys: Array<Record<string, any>>; dispatch_paused: boolean;
}
export interface OrchAuditEntry { seq: number; at: number; actor: string; action: string; subject: string }

/** Fleet agent summary on GET /api/orchestration/nodes. */
export interface OrchAgentsSummary { current: string; auto: boolean; outdated: number; updatable: number; open_run: string }
export interface OrchNodesResponse { nodes: OrchNode[]; groups: unknown[]; agents?: OrchAgentsSummary }

/* ------------------------------------------------------------ node groups + the node dashboard */

/** A node's health (vm.node_health): ok | warning | critical | pending, with the reasons. */
export interface OrchHealth { level: 'ok' | 'warning' | 'critical' | 'pending' | string; reasons: string[]; style?: OrchStyle }
export type OrchHealthCounts = { ok: number; warning: number; critical: number; pending: number };

/** One node on the dashboard (vm.fleet_dashboard rows), worst first. */
export interface OrchDashboardNode {
  id: string; name: string; status: string; presence: OrchPresence | string; canary: boolean; style: OrchStyle;
  labels: Record<string, string>; groups: string[]; health: OrchHealth; busy: OrchBusy | null;
  last_seen_ago: string; agent_version: string; memory_used_pct: number | null; max_disk_used_pct: number | null;
  load: number | null; cpus: number | null; os: string; uptime_days: number | null;
}

/** A group with its selector, explicit members and health counts. */
export interface OrchGroupSummary {
  name: string; description: string; selector: string; members: string[];
  nodes: number; online: number; health: OrchHealthCounts;
}

/** GET /api/orchestration/dashboard (vm.fleet_dashboard). */
export interface OrchDashboard {
  scope: { group: string | null; label: string };
  totals: { nodes: number; presence: Record<string, number>; health: OrchHealthCounts; busy: number; pending: number };
  resources: { memory_used_avg: number | null; memory_used_max: number | null; disk_used_avg: number | null;
    disk_used_max: number | null; load_per_cpu_avg: number | null; load_per_cpu_max: number | null };
  runs_24h: { executions: number; succeeded: number; failed: number;
    recent: Array<{ id: string; title: string; state: string; style: OrchStyle; created_ago: string }> };
  nodes: OrchDashboardNode[];
  groups: OrchGroupSummary[];
  fleet: { nodes: number; online: number; health: OrchHealthCounts };
  ungrouped: { nodes: number; online: number; health: OrchHealthCounts };
  thresholds: Record<string, number>;
}

/** GET /api/orchestration/groups: the groups and every node (for the membership editor). */
export interface OrchGroupsResponse {
  groups: OrchGroupSummary[];
  ungrouped: { nodes: number; online: number; health: OrchHealthCounts };
  fleet: { nodes: number; online: number; health: OrchHealthCounts };
  nodes: Array<{ id: string; name: string; groups: string[]; labels: Record<string, string>; health: OrchHealth; presence: string; status: string; style: OrchStyle }>;
}

/** Node history (mu/orchestration/metrics.py): chart points, one bucket each. */
export type OrchMetricKey = 'cpu_pct' | 'mem_pct' | 'disk_pct' | 'load1' | 'net_rx_bps' | 'net_tx_bps' | 'disk_read_bps' | 'disk_write_bps';
export type OrchMetricUnit = 'percent' | 'number' | 'bytes_per_s';
export type OrchMetricsRange = '1h' | '6h' | '24h' | '7d' | '30d';
export type OrchMetricPoint = { t: number } & Partial<Record<OrchMetricKey, number>>;
export interface OrchMetrics {
  range: OrchMetricsRange; ranges: OrchMetricsRange[]; bucket_s: number; since: number; until: number; nodes: number;
  points: OrchMetricPoint[];
  fields: Array<{ key: OrchMetricKey; label: string; unit: OrchMetricUnit; combine: 'avg' | 'max' | 'sum' }>;
  /** Node names whose agent reports history, and those that need an agent update first. */
  reporting: string[]; needs_update: string[];
  scope?: { group: string | null; label: string };
  /** With per_node: each node's small series for the health rows (net = in + out, io = read + write). */
  per_node?: Record<string, OrchNodeSpark>;
}

export type OrchSparkKey = 'cpu_pct' | 'mem_pct' | 'net_bps' | 'io_bps';
export interface OrchNodeSpark {
  points: Array<{ t: number } & Partial<Record<OrchSparkKey, number>>>;
  latest: Record<OrchSparkKey, number | null>;
  peak: Record<OrchSparkKey, number | null>;
}

/** The dashboard's scopes: null = the whole fleet, '-' = ungrouped nodes, else a group name. */
export type OrchScope = string | null;
export interface OrchCommandResult { ok: boolean; command: string; data: Record<string, unknown> }
export interface OrchChange { scope: 'node' | 'tool' | 'run'; id: string; parts: string[]; seq: number }

/** Commands the phone sends (mu/orchestration/commands.py). */
export type OrchCommand =
  | 'approveRun' | 'rejectRun' | 'continueRun' | 'pauseRun' | 'rollbackRun' | 'cancelRun' | 'retryNode'
  | 'acceptNode' | 'quarantineNode' | 'unquarantineNode' | 'pauseDispatch' | 'resumeDispatch' | 'proposeAgentUpdate'
  | 'putGroup' | 'addToGroup' | 'removeFromGroup' | 'renameGroup' | 'deleteGroup' | 'previewSelector';

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

/** POST to a CSRF-protected orchestration route; one token refetch + retry on 403. */
async function securePost<T>(path: string, body: unknown): Promise<T> {
  let token = await csrfToken();
  try {
    return await api.post<T>(path, body, { headers: { [token.header]: token.token } });
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 403) throw err;
    token = await csrfToken(true);
    return api.post<T>(path, body, { headers: { [token.header]: token.token } });
  }
}

/** POST /api/orchestration/cmd {type, args}. */
function command(type: OrchCommand | string, args: Record<string, unknown> = {}): Promise<OrchCommandResult> {
  return securePost<OrchCommandResult>('/api/orchestration/cmd', { type, args });
}

/** A chat session the orchestration routes open (fleet chat, discovery chat); 400 = choose a model. */
export interface OrchChatSession { session_name: string; provider?: string; model?: string; busy?: boolean; url?: string }

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
  maintenance: (nodeId: string) => api.get<OrchMaintenance>(`/api/orchestration/nodes/${enc(nodeId)}/maintenance`),
  runResults: (runId: string) => api.get<OrchResults>(`/api/orchestration/runs/${enc(runId)}/results`),
  runResultsCsv: (runId: string, step: number, view: string) => api.get<string>(`/api/orchestration/runs/${enc(runId)}/results.csv`, { query: { step, view } }),
  tools: () => api.get<{ tools: OrchToolBrief[]; runbooks: OrchRunbookBrief[] }>('/api/orchestration/tools', { query: { all: true } }),
  tool: (toolId: string, version: number | string) => api.get<OrchToolDetail>(`/api/orchestration/tools/${enc(toolId)}/${enc(String(version))}`),
  toolResults: (toolId: string) => api.get<OrchResults>(`/api/orchestration/tools/${enc(toolId)}/results`),
  toolResultsCsv: (toolId: string, view: string) => api.get<string>(`/api/orchestration/tools/${enc(toolId)}/results.csv`, { query: { view } }),
  runbook: (runbookId: string, version: number | string) => api.get<OrchRunbookDetail>(`/api/orchestration/runbooks/${enc(runbookId)}/${enc(String(version))}`),
  settings: () => api.get<OrchSettings>('/api/orchestration/settings'),
  /** The node dashboard: `group` null = the fleet, '-' = ungrouped, else one group. */
  dashboard: (group: OrchScope = null) => api.get<OrchDashboard>('/api/orchestration/dashboard', { query: { group: group ?? undefined } }),
  groups: () => api.get<OrchGroupsResponse>('/api/orchestration/groups'),
  /** One node's history charts (agent 1.3.0 or newer reports them). */
  nodeMetrics: (nodeId: string, range: OrchMetricsRange = '6h') => api.get<OrchMetrics>(`/api/orchestration/nodes/${enc(nodeId)}/metrics`, { query: { range } }),
  /** History for a dashboard scope: null = the fleet, '-' = ungrouped, else one group. */
  dashboardMetrics: (group: OrchScope = null, range: OrchMetricsRange = '6h', perNode = false) =>
    api.get<OrchMetrics>('/api/orchestration/dashboard/metrics', { query: { group: group ?? undefined, range, per_node: perNode ? 1 : undefined } }),
  /** Create or update a group; `members` replaces the explicit member list when given. */
  putGroup: (args: { name: string; selector?: string; description?: string; members?: string[] }) => command('putGroup', args),
  /** Put nodes in a group; with `from`, they leave that group (a move). */
  addToGroup: (name: string, nodeIds: string[], from?: string) => command('addToGroup', from ? { name, node_ids: nodeIds, from } : { name, node_ids: nodeIds }),
  removeFromGroup: (name: string, nodeIds: string[]) => command('removeFromGroup', { name, node_ids: nodeIds }),
  renameGroup: (name: string, newName: string) => command('renameGroup', { name, new_name: newName }),
  deleteGroup: (name: string) => command('deleteGroup', { name }),
  /** The nodes a label selector reaches now (read-only; validates it). */
  previewSelector: (labels: string) => command('previewSelector', { selector: { labels } }),
  audit: (limit = 10) => api.get<{ entries: OrchAuditEntry[] }>('/api/orchestration/audit', { query: { limit } }),
  command,
  /** The fleet chat (mu/gui/routers/orchestration.py fleet_chat_session): `fresh` starts over and deletes the old one. */
  chatSession: (body: { fresh?: boolean; provider?: string; model?: string } = {}) => securePost<OrchChatSession>('/api/orchestration/chat/session', body),
  /** The Studio discovery chat for a draft ('' = a new tool). */
  studioSession: (body: { ref: string; provider?: string; model?: string }) => securePost<OrchChatSession>('/api/orchestration/studio/session', body),
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
    return { title: 'Orchestration: tool submitted for review', body: `${event.ref} waits for your review.`, data: { orchRef: event.ref } };
  }
  if (event.run_id && (event.state === 'paused' || event.state === 'failed')) {
    const what = event.state === 'paused' ? 'Run paused' : 'Run failed';
    return { title: `Orchestration: ${what}`, body: String(event.reason || event.run_id).slice(0, 240), data: { orchRunId: event.run_id } };
  }
  return null;
}
