// planos status vocabulary (theme.ts STATUS) — the same glyphs, tones and
// labels in every mucli front end. Status is never shown by colour alone:
// every glyph travels with a text label (visible or accessibilityLabel).

import type { PlanosTone } from './tokens';

export type Status = 'done' | 'active' | 'waiting' | 'failed' | 'skipped' | 'pending';

export const STATUS: Record<Status, { icon: string; tone: PlanosTone; label: string }> = {
  done: { icon: '✓', tone: 'ok', label: 'done' },
  active: { icon: '●', tone: 'running', label: 'in progress' },
  waiting: { icon: '◆', tone: 'warn', label: 'needs you' },
  failed: { icon: '✗', tone: 'danger', label: 'failed' },
  skipped: { icon: '–', tone: 'muted', label: 'skipped' },
  pending: { icon: '○', tone: 'muted', label: 'not started' },
};

const STATE_STATUS: Record<string, Status> = {
  selected: 'pending', provisioning: 'active', planning: 'active', implementing: 'active', checking: 'active',
  reviewing: 'active', pushing: 'active', plan_review: 'waiting', review_done: 'waiting', needs_input: 'waiting',
  pushed: 'done', done: 'done', failed: 'failed', cancelled: 'skipped',
};

const STATE_LABEL: Record<string, string> = {
  selected: 'selected', provisioning: 'preparing', planning: 'planning', plan_review: 'plan to approve',
  implementing: 'implementing', checking: 'checking', reviewing: 'reviewing', review_done: 'review done',
  pushing: 'pushing', pushed: 'pushed', done: 'done', needs_input: 'needs input', failed: 'failed', cancelled: 'cancelled',
};

export const ACTIVE_STATES = ['provisioning', 'planning', 'implementing', 'checking', 'reviewing', 'pushing'];

export function asStatus(value: string | null | undefined): Status {
  return (value && value in STATUS ? value : 'pending') as Status;
}

export function stateStatus(state: string | null | undefined): Status {
  return STATE_STATUS[String(state || '')] || 'pending';
}

export function stateLabel(state: string | null | undefined): string {
  const key = String(state || '');
  return STATE_LABEL[key] || key.replace(/_/g, ' ');
}

/** Review finding severities. */
export const SEVERITY: Record<string, PlanosTone> = { blocker: 'danger', major: 'danger', minor: 'warn', nit: 'muted' };
export const SEVERITY_ORDER = ['blocker', 'major', 'minor', 'nit'] as const;

/** Review verdicts → tone + wording. */
export const VERDICT: Record<string, [PlanosTone, string]> = {
  ready: ['ok', 'ready to merge'], ready_with_nits: ['ok', 'ready with nits'],
  needs_work: ['warn', 'needs work'], do_not_merge: ['danger', 'do not merge'],
};
export function verdictStyle(verdict: string | null | undefined): [PlanosTone, string] {
  const v = String(verdict || '');
  return VERDICT[v] || ['muted', v.replace(/_/g, ' ')];
}

/** Plan-coverage status → status vocabulary. */
export const COVERAGE: Record<string, Status> = { met: 'done', partial: 'waiting', unmet: 'failed', unverifiable: 'skipped' };

/** planos action tones (CONTRACT §7) — used when the server omits `tone`. */
export const ACTION_TONE: Record<string, PlanosTone> = {
  approvePlan: 'ok', rejectPlan: 'danger', approveTool: 'warn', denyTool: 'danger', retry: 'warn',
  restartStage: 'warn', resume: 'warn', push: 'ok', discard: 'danger', delete: 'danger',
};

/** planos action wording overrides. */
export const ACTION_LABEL: Record<string, string> = { savePlanDraft: 'Edit plan', reprompt: 'Fix selected findings', message: 'Message agent' };

/** Build/test run status → status vocabulary. */
export function runStatus(status: string): Status {
  const map: Record<string, Status> = { running: 'active', passed: 'done', failed: 'failed', error: 'failed', cancelled: 'skipped' };
  return map[status] || 'pending';
}

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
