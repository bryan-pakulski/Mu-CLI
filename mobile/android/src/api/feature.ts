import { api } from './client';
import { ModeWorkspaceContract } from './modeWorkspace';

export interface FeatureTask {
  id: number;
  title: string;
  phase_id?: number | null;
  status: string;
  objectives: string[];
  action_points?: string[];
  exit_criteria: string[];
  verified_exit_criteria: string[];
  blocked_reason: string;
  notes: string;
}

export interface FeaturePhase {
  id: string | number;
  title: string;
  goal: string;
  order: number;
  status: string | null;
  tasks: FeatureTask[];
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
  limitations: string[];
  issues: Array<Record<string, unknown>>;
  created_at: number;
}

export interface FeatureDiffProposal {
  id: string;
  review_id: string;
  task_id: number;
  issue_id: string;
  diff: string;
  status: string;
  decision_reason: string;
  created_at: number;
  decided_at?: number | null;
}

export interface FeatureSummary {
  feature_id: string;
  feature_name: string;
  feature_request: string;
  directory?: string;
  metadata_path?: string;
  approved: boolean;
  review_status: string;
  review_notes?: string;
  overall_status: string;
  task_count: number;
  phase_count?: number;
  tasks_completed: boolean;
  phases: Array<Record<string, unknown>>;
  phase_columns: FeaturePhase[];
  event_count: number;
  review_count?: number;
  diff_proposal_count?: number;
  event_log?: FeatureEvent[];
  review_records?: FeatureReviewRecord[];
  diff_proposals?: FeatureDiffProposal[];
  review_summaries?: Array<Record<string, unknown>>;
  next_task?: FeatureTask | null;
  next_phase?: Record<string, unknown> | null;
  execution: Record<string, unknown>;
  created_at?: number;
  updated_at?: number;
  read_only?: boolean;
  preview_feature_id?: string;
}

export interface FeatureListItem {
  feature_id: string;
  feature_name: string;
  status: string;
  is_active: boolean;
  archived: boolean;
  created_at?: number;
  updated_at?: number | null;
}

export interface FeatureState {
  active: boolean;
  active_feature_id: string | null;
  plan: FeatureSummary | null;
  features: FeatureListItem[];
  metadata_path: string | null;
  read_only?: boolean;
  workspace: ModeWorkspaceContract;
}

export const featureApi = {
  getState: () => api.get<FeatureState>('/api/feature/state'),
  preview: (featureId: string) =>
    api.get<FeatureState>(`/api/feature/${encodeURIComponent(featureId)}/preview`),
  approve: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}/approve`),
  load: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}/load`),
  unload: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}/unload`),
  archive: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}/archive`),
  unarchive: (featureId: string) => api.post<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}/unarchive`),
  delete: (featureId: string) => api.delete<{ ok: boolean }>(`/api/feature/${encodeURIComponent(featureId)}`),
  transitionTask: (taskId: number, toStatus: string, notes?: string, blockedReason?: string) =>
    api.post<{ ok: boolean; task_id: number; to_status: string }>(`/api/feature/tasks/${taskId}/transition`, {
      to_status: toStatus, notes, blocked_reason: blockedReason,
    }),
  toggleExitCriterion: (taskId: number, idx: number) =>
    api.post<{ ok: boolean; task_id: number; criterion_index: number; verified: boolean }>(`/api/feature/tasks/${taskId}/exit-criteria/${idx}/toggle`),
};
