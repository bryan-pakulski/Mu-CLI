import { api } from './client';

export interface FeatureTask {
  id: number;
  title: string;
  status: string;
  objectives: string[];
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

export interface FeatureSummary {
  feature_id: string;
  feature_name: string;
  feature_request: string;
  approved: boolean;
  review_status: string;
  overall_status: string;
  task_count: number;
  tasks_completed: boolean;
  phases: Array<Record<string, unknown>>;
  phase_columns: FeaturePhase[];
  event_count: number;
  execution: Record<string, unknown>;
}

export interface FeatureState {
  active: boolean;
  active_feature_id: string | null;
  plan: FeatureSummary | null;
  features: Array<{
    feature_id: string;
    feature_name: string;
    status: string;
    is_active: boolean;
    archived: boolean;
  }>;
  metadata_path: string | null;
}

export const featureApi = {
  getState: () => api.get<FeatureState>('/api/feature/state'),
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