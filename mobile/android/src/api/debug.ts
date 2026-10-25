// Debug mode (modes_v2.md §4.2; mu/gui/routers/debug.py): the current case climbing
// open → reproduced → root_caused → fixed → verified → closed (or unreproducible), its
// hypotheses with evidence, suspects, and the case's check runs.

import { api } from './client';
import type { CheckRun } from './modeState';

export type DebugCaseStatus = 'open' | 'reproduced' | 'root_caused' | 'fixed' | 'verified' | 'closed' | 'unreproducible';

export interface DebugEvidence {
  kind: 'run' | 'location' | 'search' | 'note' | string;
  ref: string;
  stance: 'supports' | 'refutes' | string;
  note: string;
}

export interface DebugHypothesis {
  id: string;
  statement: string;
  status: 'untested' | 'supported' | 'refuted' | 'confirmed' | string;
  evidence: DebugEvidence[];
  created_at: number;
  updated_at: number;
}

export interface DebugSuspect {
  path: string;
  reason: string;
  line: number | null;
  symbol: string;
}

export interface DebugRootCause {
  signature?: string;
  cause?: string;
  fix?: string;
  files?: string[];
}

export interface DebugCase {
  id: string;
  title: string;
  symptom: string;
  status: DebugCaseStatus | string;
  repro: { command?: string; predicate?: Record<string, unknown>; expect?: string; runs?: string[]; [key: string]: unknown };
  hypotheses: DebugHypothesis[];
  suspects: DebugSuspect[];
  fix: { files?: string[]; summary?: string; diff_stat?: string; [key: string]: unknown };
  verification: Record<string, unknown>;
  regression_test: Record<string, unknown> | null;
  root_cause: DebugRootCause | null;
  bisect: Record<string, unknown> | null;
  unreproducible_reason: string;
  memory_id: string;
  created_at: number;
  updated_at: number;
}

export interface DebugCaseSummary {
  id: string;
  title: string;
  status: string;
  hypotheses: number;
  suspects: number;
  repro: string;
  updated_at: number;
}

export interface PastRootCause {
  id: string;
  statement: string;
  root_cause: DebugRootCause | null;
  case: string;
}

export interface DebugState {
  active: boolean;
  case: DebugCase | null;
  cases: DebugCaseSummary[];
  runs: CheckRun[];
  past_root_causes: PastRootCause[];
  plan_mode: boolean;
}

export const debugApi = {
  getState: () => api.get<DebugState>('/api/debug/state'),
  selectCase: (caseId: string) => api.post<DebugState>(`/api/debug/cases/${encodeURIComponent(caseId)}/select`),
  /** The user closes the verified current case with its root cause. */
  close: (body: { cause: string; signature?: string; unreproducible_reason?: string }) => api.post<DebugState>('/api/debug/close', body),
};
