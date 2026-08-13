export type ModeWorkspaceTone = 'neutral' | 'active' | 'good' | 'warn' | 'risk';

export interface ModeWorkspaceMetric {
  id: string;
  label: string;
  value: string | number;
  tone: ModeWorkspaceTone;
  detail: string;
}

export interface ModeWorkspaceView {
  id: string;
  label: string;
  count?: number;
}

export interface ModeWorkspaceQuality {
  id: string;
  label: string;
  state: string;
  detail: string;
  value?: string | number;
}

export interface ModeWorkspaceContract {
  schema_version: number;
  mode: string;
  title: string;
  objective: string;
  status: { label: string; tone: ModeWorkspaceTone };
  views: ModeWorkspaceView[];
  metrics: ModeWorkspaceMetric[];
  quality: ModeWorkspaceQuality[];
  search_placeholder: string;
  provenance: string;
}
