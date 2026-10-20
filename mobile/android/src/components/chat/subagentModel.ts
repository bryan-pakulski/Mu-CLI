// The sub-agent panel's words, ported from the web (mu/gui/static/js/app.js subagentTitle,
// subagentToolLabel, subagentStatusText, summarizeSubagentPanel, subagentElapsed; and
// wash/toolcard.js subagentActionCard). A parity test runs the web functions on the same agents.

import type { LiveSubagent, LiveSubagentAction } from '../../hooks/useChatSession';
import type { ToolCallView } from '../../ui/ToolCard';

type AgentLike = Partial<LiveSubagent> & { status?: string; kill_reason?: string | null; repeat_count?: number };

const ACTIVE = ['running', 'stuck', 'stall'];

export function isSubagentActive(status: string | undefined): boolean {
  return ACTIVE.includes(String(status || ''));
}

export function fmtTok(n: unknown): string {
  const value = Number(n) || 0;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

/** Seconds elapsed; live agents keep counting from when the value was observed. */
export function subagentElapsed(a: AgentLike | null | undefined, now = Date.now()): number {
  const elapsed = Number(a?.elapsed) || 0;
  if (!a || !isSubagentActive(a.status)) return elapsed;
  const observedAt = Number(a.observed_at) || now;
  return elapsed + Math.max(0, now - observedAt) / 1000;
}

export function subagentTitle(a: AgentLike | null | undefined): string {
  const title = String(a?.title || '').replace(/\s+/g, ' ').trim();
  if (title) return title;
  const specialist = String(a?.specialist_key || '').replace(/[_-]+/g, ' ').trim();
  if (specialist) return `${specialist.replace(/\b\w/g, c => c.toUpperCase())} task`;
  return 'Delegated task';
}

const TOOL_LABELS: Record<string, string> = {
  apply_patch: 'Edit files',
  bash: 'Run command',
  get_chunk: 'Read source chunk',
  list_dir: 'Inspect directory',
  read_file: 'Read file',
  search_for_string: 'Search code',
  spawn_agent: 'Delegate task',
  web_search: 'Search the web',
};

export function subagentToolLabel(tool: unknown): string {
  const value = String(tool || '').trim();
  if (TOOL_LABELS[value]) return TOOL_LABELS[value];
  if (!value) return 'Working';
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export function subagentStatusText(a: AgentLike): string {
  switch (a.status) {
    case 'running': return a.last_tool ? subagentToolLabel(a.last_tool) : 'Starting';
    case 'stuck': return `Stuck${a.repeat_count ? ` · ${a.repeat_count} repeats` : ''}`;
    case 'killed': return `Stopped${a.kill_reason ? ` · ${a.kill_reason}` : ''}`;
    case 'error': return 'Failed';
    case 'done': return 'Completed';
    default: return String(a.status || '');
  }
}

/** The panel's one-line summary: "2 subagents · 1 running · 12.3s · 4.5k tok". */
export function summarizeSubagentPanel(agents: AgentLike[], running: boolean, now = Date.now()): string {
  if (!agents.length) return 'subagents';
  const n = agents.length;
  const parts = [`${n} subagent${n > 1 ? 's' : ''}`];
  if (running) {
    const active = agents.filter(a => a.status === 'running' || a.status === 'stuck').length;
    parts.push(active === n ? 'running' : `${active} running`);
  } else {
    const bits: string[] = [];
    if (agents.some(a => a.status === 'done')) bits.push('done');
    if (agents.some(a => a.status === 'killed')) bits.push('killed');
    if (agents.some(a => a.status === 'error')) bits.push('errored');
    parts.push(bits.join('/') || 'done');
  }
  const elapsed = Math.max(0, ...agents.map(a => subagentElapsed(a, now)));
  if (elapsed) parts.push(`${elapsed.toFixed(1)}s`);
  const tok = agents.reduce((sum, a) => sum + (a.tokens_in || 0), 0);
  if (tok) parts.push(`${fmtTok(tok)} tok`);
  return parts.join(' · ');
}

/** The status sign for an agent row (the shared vocabulary). */
export function subagentSign(status: string | undefined): string {
  if (status === 'running') return 'active';
  if (status === 'done') return 'done';
  if (status === 'error' || status === 'killed') return 'failed';
  if (status === 'stuck' || status === 'stall') return 'waiting';
  return 'pending';
}

/** One timeline action as a tool card (web WashToolCard.subagentActionCard). */
export function subagentActionView(action: LiveSubagentAction, agent: Pick<LiveSubagent, 'task_id'>): ToolCallView {
  const status = String(action.status || 'running');
  const elapsedMs = Number(action.elapsed) > 0 ? Number(action.elapsed) * 1000 : undefined;
  const at = Number(action.at) > 0 ? Number(action.at) * 1000 : 0;
  return {
    id: `sa:${agent.task_id}:${action.seq}`,
    name: String(action.tool || 'tool'),
    args: action.detail ? { detail: action.detail } : undefined,
    status: status === 'error' ? 'failed' : status === 'done' ? 'done' : 'running',
    startedAt: status === 'done' || status === 'error' ? undefined : at || undefined,
    durationMs: elapsedMs,
  };
}
