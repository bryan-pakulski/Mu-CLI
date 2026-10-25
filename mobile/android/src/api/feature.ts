// Feature mode (modes_v2.md §4.3; mu/gui/routers/feature.py): the active plan (plan_view —
// status draft → approved → executing → review → done, approval, tasks with criteria and their
// evidence, reviews, diff proposals, completion gaps, events), the session's features, open
// blockers and the check runs the plan cites. Every mutation goes through the engine's gates.

import { api } from './client';
import type { Blocker, CheckRun } from './modeState';

export type FeaturePlanStatus = 'draft' | 'approved' | 'executing' | 'review' | 'done';
export type FeatureTaskStatus = 'not_started' | 'in_progress' | 'blocked' | 'completed' | 'archived';

export interface FeatureCriterionEvidence {
  kind: 'run' | 'file' | 'attested' | string;
  ref?: string;
  justification?: string;
  actor?: string;
  at?: number;
}

export interface FeatureCriterion {
  id: string;
  text: string;
  check: { command?: string; predicate?: Record<string, unknown> } | null;
  met: boolean;
  evidence: FeatureCriterionEvidence[];
}

export interface FeatureTask {
  id: number;
  title: string;
  phase_id: number | null;
  objective: string;
  actions: string[];
  criteria: FeatureCriterion[];
  status: FeatureTaskStatus | string;
  notes: string;
  origin: string;
  started_at: number | null;
  completed_at: number | null;
  diff_stat: string;
  reviewed: boolean;
  override: { reason?: string; actor?: string; at?: number; [key: string]: unknown } | null;
  unmet: string[];
}

export interface FeaturePhase {
  id: number;
  title: string;
  goal: string;
  order: number;
}

export interface FeatureEvent {
  id: string;
  kind: string;
  entity: string;
  entity_id: string | number;
  payload: Record<string, unknown>;
  actor: string;
  created_at: number;
}

export interface FeatureReviewRecord {
  id: string;
  task_id: number;
  summary: string;
  issues: Array<Record<string, unknown>>;
  diff_stat: string;
  check_run: string;
  actor: string;
  created_at: number;
}

export interface FeatureDiffProposal {
  id: string;
  task_id: number;
  diff: string;
  rationale: string;
  status: 'pending' | 'approved' | 'rejected' | string;
  decision_reason: string;
  decided_by: string;
  created_at: number;
  decided_at: number | null;
}

export interface FeaturePlan {
  feature_id: string;
  feature_name: string;
  feature_request: string;
  directory: string;
  metadata_path: string;
  status: FeaturePlanStatus | string;
  revision: number;
  approval: { actor?: string; at?: number; plan_hash?: string; [key: string]: unknown } | null;
  approval_history: Array<Record<string, unknown>>;
  tasks: FeatureTask[];
  phases_meta: FeaturePhase[];
  event_log: FeatureEvent[];
  review_records: FeatureReviewRecord[];
  diff_proposals: FeatureDiffProposal[];
  current_task_id: number | null;
  completion_requested: boolean;
  completion: Record<string, unknown> | null;
  created_at: number;
  updated_at: number;
  hash: string;
  approved: boolean;
  approval_current: boolean;
  current_task: number | null;
  next_task: number | null;
  counts: Partial<Record<FeatureTaskStatus, number>>;
  unreviewed: number[];
  completion_gaps?: string[];
}

export interface FeatureListItem {
  feature_id: string;
  feature_name: string;
  status: string | null;
  task_count: number;
  completed_count: number;
  is_active: boolean;
  archived: boolean;
  created_at?: number;
  updated_at?: number | null;
}

export interface FeatureState {
  active: boolean;
  active_feature_id: string | null;
  plan: FeaturePlan | null;
  features: FeatureListItem[];
  blockers: Blocker[];
  runs: CheckRun[];
  read_only: boolean;
}

const fid = (featureId: string) => encodeURIComponent(featureId);

export const featureApi = {
  getState: () => api.get<FeatureState>('/api/feature/state'),
  preview: (featureId: string) => api.get<FeatureState>(`/api/feature/${fid(featureId)}/preview`),
  /** Approve the active draft (user-only). */
  approve: () => api.post<FeatureState>('/api/feature/approve'),
  startTask: (taskId: number) => api.post<FeatureState>(`/api/feature/tasks/${taskId}/start`),
  transitionTask: (taskId: number, toStatus: string, reason = '') =>
    api.post<FeatureState>(`/api/feature/tasks/${taskId}/transition`, { to_status: toStatus, reason }),
  attestCriterion: (taskId: number, criterionId: string, note: string) =>
    api.post<FeatureState>(`/api/feature/tasks/${taskId}/criteria/${encodeURIComponent(criterionId)}/attest`, { note }),
  completeTask: (taskId: number, overrideReason = '') =>
    api.post<FeatureState>(`/api/feature/tasks/${taskId}/complete`, { override_reason: overrideReason }),
  decideDiff: (proposalId: string, decision: 'approved' | 'rejected', reason = '') =>
    api.post<FeatureState>(`/api/feature/diffs/${encodeURIComponent(proposalId)}/decide`, { decision, reason }),
  /** Mark the feature complete (the engine enforces the preconditions). */
  complete: () => api.post<FeatureState>('/api/feature/complete'),
  load: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${fid(featureId)}/load`),
  unload: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${fid(featureId)}/unload`),
  archive: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${fid(featureId)}/archive`),
  unarchive: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${fid(featureId)}/unarchive`),
  delete: (featureId: string) => api.delete<{ ok: boolean }>(`/api/feature/${fid(featureId)}`),
};
