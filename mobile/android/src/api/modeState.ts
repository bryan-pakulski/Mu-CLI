// Shared mode state (modes_v2.md §3; mu/gui/routers/mode_state.py): check runs (the shared row
// and sheet), session blockers, and work items. The app's bearer token makes the server record
// the actor as user:mobile.

import { api } from './client';

/** One harness-run command and what happened (mu/modes/evidence.py CheckRun + predicate_text). */
export interface CheckRun {
  id: string;
  purpose: string;
  command: string;
  cwd: string;
  exit_code: number | null;
  passed: boolean;
  predicate: Record<string, unknown>;
  predicate_text?: string;
  excerpt: string;
  result_ref?: string | null;
  security_output_path?: string;
  started_at?: number;
  duration_s?: number;
  hashes?: Record<string, string>;
  runner?: string;
  label?: string;
  ref?: string;
  timed_out?: boolean;
  tests?: Record<string, unknown> | null;
  source?: string;
  detail?: string;
}

export interface Blocker {
  id: string;
  summary: string;
  needs: string;
  mode: string;
  ref: string;
  details: string;
  questions: string[];
  raised_at: number;
  raised_by: string;
  resolved_at: number | null;
  resolution: string;
  resolved_by: string;
  open: boolean;
}

export interface WorkItem {
  id: string;
  title: string;
  status: 'pending' | 'in_progress' | 'blocked' | 'done' | 'dropped' | string;
  parent_id: string | null;
  priority: number;
  evidence: string[];
  note: string;
  created_at: number;
  updated_at: number;
  mode: string;
}

/** Default-mode turn receipt (mu/modes/receipt.py build_receipt), from the `turn_receipt` event. */
export interface TurnReceiptCheck {
  id: string;
  passed: boolean;
  command: string;
  purpose: string;
  after_edit: boolean;
}

export interface TurnReceipt {
  turn?: number | null;
  at?: number;
  status: 'verified' | 'unverified' | 'no_changes' | string;
  files: string[];
  checks: TurnReceiptCheck[];
  work_items: string[];
  blockers: string[];
  nudged?: boolean;
}

export const modeStateApi = {
  getSecurityOutput: (path: string) => api.get<{ text: string }>(`/api/security/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`),
  getCheck: (id: string) => api.get<CheckRun>(`/api/mode-state/checks/${encodeURIComponent(id)}`),
  getCheckOutput: (id: string) => api.get<{ id: string; output: string }>(`/api/mode-state/checks/${encodeURIComponent(id)}/output`),
  listBlockers: () => api.get<{ open: Blocker[]; resolved: Blocker[] }>('/api/mode-state/blockers'),
  resolveBlocker: (id: string, resolution: string) =>
    api.post<{ ok: boolean; blocker: Blocker }>(`/api/mode-state/blockers/${encodeURIComponent(id)}/resolve`, { resolution }),
  listWork: () => api.get<{ items: WorkItem[]; current: string | null; counts: Record<string, number> }>('/api/mode-state/work'),
};
