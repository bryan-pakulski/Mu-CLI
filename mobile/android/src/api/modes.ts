import { api } from './client';

export interface ModeInfo {
  name: string;
  display_name: string;
  description: string;
  is_current: boolean;
  needs_workspace: boolean;
  disabled: boolean;
}

export interface ViewPanelInfo {
  name: string;
  display_name: string;
  description: string;
  view_only: boolean;
  needs_workspace: boolean;
  disabled: boolean;
  external?: boolean;
  route?: string;
}

export interface ModesResponse {
  current: string | null;
  modes: ModeInfo[];
  views: ViewPanelInfo[];
  has_workspace: boolean;
  has_execution_workspace: boolean;
  has_container: boolean;
  session_type: 'chat' | 'workspace' | 'container';
  execution_boundary: 'host' | 'container';
}

export const modesApi = {
  list: () => api.get<ModesResponse>('/api/modes'),
  set: (name: string) => api.post<Record<string, unknown>>(`/api/modes/${encodeURIComponent(name)}`),
};
