// Status vocabulary for the Android client. Every glyph, semantic and
// canonical label comes from the shared vocabulary in `wash.generated.ts`
// (generated from `mu/design/vocabulary.json`); this module only adds typed
// accessors. Status is never shown by colour alone: every glyph travels with
// a text label (visible or accessibilityLabel).

import { vocabulary } from './wash.generated';
import type { Semantic, Tone } from './tokens';

export type Status = keyof typeof vocabulary.status;
type StatusRow = { icon: string; tone: Semantic; label: string };

// `unverified` (checks that could not run, job_templates.md §5) and the `unrunnable` check row
// come from the shared vocabulary like every other row.
const statusRows = vocabulary.status as Record<string, { glyph: string; semantic: string; label: string }>;
const jobStates = vocabulary.job_state as Record<string, { status: string; label: string }>;

/** Glyph (`icon`), semantic tone and label per status, read from the vocabulary. */
export const STATUS = Object.fromEntries(
  Object.entries(statusRows).map(([key, row]) => [key, { icon: row.glyph, tone: row.semantic as Semantic, label: row.label }]),
) as Record<Status, StatusRow>;

/** Job states whose status is `active` (work in progress). */
export const ACTIVE_STATES = Object.keys(jobStates).filter(state => jobStates[state].status === 'active');

/** Job states whose status is `waiting` (needs you). */
export const WAITING_STATES = Object.keys(jobStates).filter(state => jobStates[state].status === 'waiting');

export function asStatus(value: string | null | undefined): Status {
  return (value && value in statusRows ? value : 'pending') as Status;
}

export function stateStatus(state: string | null | undefined): Status {
  const row = jobStates[String(state || '')];
  return row ? asStatus(row.status) : 'pending';
}

export function stateLabel(state: string | null | undefined): string {
  const key = String(state || '');
  return jobStates[key]?.label || key.replace(/_/g, ' ');
}

const severityRows = vocabulary.severity as Record<string, { semantic: string; rank: number; label: string }>;

/** Review finding severity → semantic tone (blocker danger, major attention, minor caution, nit neutral). */
export const SEVERITY: Record<string, Semantic> = Object.fromEntries(
  Object.entries(severityRows).map(([key, row]) => [key, row.semantic as Semantic]),
);
export const SEVERITY_ORDER = Object.keys(severityRows).sort((a, b) => severityRows[a].rank - severityRows[b].rank);

const verdictRows = vocabulary.verdict as Record<string, { semantic: string; label: string }>;

/** Review verdict → tone + canonical wording. */
export function verdictStyle(verdict: string | null | undefined): [Tone, string] {
  const v = String(verdict || '');
  const row = verdictRows[v];
  return row ? [row.semantic as Semantic, row.label] : ['neutral', v.replace(/_/g, ' ')];
}

/** Plan-coverage status → status vocabulary. */
export const COVERAGE = vocabulary.coverage as Record<string, Status>;

const repairRows = vocabulary.repair_outcome as Record<string, { status: string; label: string }>;

/** Environment repair outcome (job_templates.md §7) → status vocabulary + canonical label. */
export function repairOutcome(outcome: string | null | undefined): { status: Status; label: string } {
  const row = repairRows[String(outcome || '')];
  return row ? { status: asStatus(row.status), label: row.label } : { status: 'pending', label: String(outcome || '').replace(/_/g, ' ') };
}

const checkRows = vocabulary.check_status as Record<string, { status: string; label: string }>;

/** Build/test run status → status vocabulary. */
export function runStatus(status: string): Status {
  return checkRows[status] ? asStatus(checkRows[status].status) : 'pending';
}

const attentionRows = vocabulary.attention_reason as Record<string, { label: string; action: string }>;

/** Attention reason → canonical label and short action verb. */
export function attentionReason(reason: string | null | undefined): { label: string; action: string } {
  const key = String(reason || '');
  return attentionRows[key] || { label: key.replace(/_/g, ' '), action: '' };
}

/** Action tones for job actions when the server omits `tone` (UI presentation, not status). */
export const ACTION_TONE: Record<string, Tone> = {
  approvePlan: 'ok', rejectPlan: 'danger', approveTool: 'attention', denyTool: 'danger', retry: 'attention',
  restartStage: 'attention', resume: 'attention', push: 'ok', discard: 'danger', delete: 'danger',
};

/** Action wording overrides. */
export const ACTION_LABEL: Record<string, string> = { savePlanDraft: 'Edit plan', reprompt: 'Fix selected findings', message: 'Message agent' };

export function money(value: number | null | undefined): string {
  return `$${Number(value || 0).toFixed(2)}`;
}

/** Milliseconds → "45s", "12m", "3h 5m", "4d". */
export function duration(ms: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(ms || 0) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d`;
}

/** Seconds → compact age. */
export function age(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(Number(seconds || 0)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 172800) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function when(ts: number | null | undefined): string {
  const d = new Date(Number(ts || 0) * 1000);
  return Number.isNaN(d.getTime()) || !ts ? '' : d.toISOString().slice(0, 19).replace('T', ' ');
}

export function bytes(value: number | null | undefined): string {
  const n = Number(value || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

export const stripAnsi = (text: string | null | undefined) => String(text ?? '').replace(/\u001b\[[0-9;]*m/g, '');

// Orchestration vocabulary (orchestration.md §8): node status, run state, execution state and
// tool status map onto the shared status rows; effects carry a semantic tone.
export type OrchTable = 'orch_node_status' | 'orch_node_health' | 'orch_run_state' | 'orch_exec_state' | 'orch_tool_status';
const orchEffectRows = vocabulary.orch_effect as Record<string, { semantic: string; label: string }>;

/**
 * Status + label for an orchestration key. A server `style` (the same vocabulary, sometimes
 * with a contextual label such as "not started") wins; unknown keys fall back to pending.
 */
export function orchState(table: OrchTable, key: string | null | undefined, style?: { status?: string; label?: string } | null): { status: Status; label: string } {
  const rows = vocabulary[table] as Record<string, { status: string; label: string }>;
  const row = rows[String(key || '')];
  const status = style?.status || row?.status;
  const label = style?.label || row?.label || String(key || '').replace(/_/g, ' ');
  return { status: asStatus(status), label };
}

/** Tool/run effect → semantic tone + label (read-only ok, mutating attention, destructive danger). */
export function orchEffect(effect: string | null | undefined, style?: { semantic?: string; label?: string } | null): { tone: Semantic; label: string } {
  const row = orchEffectRows[String(effect || '')];
  // Unknown effects are treated as destructive, like the server's effect_style().
  const semantic = style?.semantic || row?.semantic || 'danger';
  return { tone: semantic as Semantic, label: style?.label || row?.label || String(effect || '').replace(/_/g, ' ') };
}
