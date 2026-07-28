import { api } from './client';

export type SessionType = 'chat' | 'workspace' | 'container';

export interface ContainerMount {
  host_path: string;
  container_path: string;
  mode: 'ro' | 'rw';
}

export interface ContainerCreateOptions {
  containerName: string;
  dockerfile?: string;
  mounts?: ContainerMount[];
  egressAllow?: string[];
  egressDeny?: string[];
}

export interface ContainerDefaultsResponse {
  dockerfile: string;
  egress_allow: string[];
  egress_deny: string[];
}

// Types
export interface SessionSummary {
  name: string;
  is_current: boolean;
  is_loaded: boolean;
  is_busy: boolean;
  modified_at: string;
  modified_unix: number;
  session_type?: SessionType;
  container_name?: string | null;
}

export interface SessionListResponse {
  current: string | null;
  active: boolean;
  loaded: string[];
  busy: string[];
  sessions: SessionSummary[];
}

export interface SessionHistoryTurn {
  index: number;
  role: string;
  parts: Array<Record<string, unknown>>;
}

export interface SessionHistoryResponse {
  name: string;
  turns: SessionHistoryTurn[];
}

export interface CreateSessionOptions {
  sessionType?: SessionType;
  ollamaMode?: 'local' | 'cloud';
  ollamaHost?: string;
  ollamaApiKey?: string;
  container?: ContainerCreateOptions;
}

export interface WorkspaceSuggestionResponse {
  query: string;
  resolved_path: string;
  exists: boolean;
  suggestions: string[];
}

export interface WorkspaceDetailsResponse {
  name: string;
  workspaces: string[];
}

// API
export const sessionsApi = {
  getContainerDefaults: () => api.get<ContainerDefaultsResponse>('/api/container-defaults'),
  list: () => api.get<SessionListResponse>('/api/sessions'),
  getActive: (sessionName?: string) =>
    api.get<Record<string, unknown>>('/api/sessions/active', { query: { session_name: sessionName } }),
  getHistory: (sessionName?: string) =>
    api.get<SessionHistoryResponse>('/api/sessions/current/history', { query: { session_name: sessionName } }),
  suggestWorkspaces: (path: string, limit: number = 12) =>
    api.get<WorkspaceSuggestionResponse>('/api/sessions/workspaces/suggest', { query: { path, limit } }),
  getWorkspace: (name: string) =>
    api.get<WorkspaceDetailsResponse>(`/api/sessions/${encodeURIComponent(name)}/workspace`),
  updateWorkspace: (name: string, workspaces: string[]) =>
    api.put<WorkspaceDetailsResponse>(`/api/sessions/${encodeURIComponent(name)}/workspace`, { workspaces }),
  create: (name: string, provider: string, model: string, workspace?: string, options?: CreateSessionOptions) =>
    api.post<Record<string, unknown>>('/api/sessions', {
      name,
      provider,
      model,
      activate: true,
      session_type: options?.sessionType || 'workspace',
      workspace,
      ollama_mode: options?.ollamaMode,
      ollama_host: options?.ollamaHost,
      ollama_api_key: options?.ollamaApiKey,
      container_name: options?.container?.containerName,
      dockerfile: options?.container?.dockerfile,
      mounts: options?.container?.mounts,
      egress_allow: options?.container?.egressAllow,
      egress_deny: options?.container?.egressDeny,
    }),
  load: (name: string, provider?: string, model?: string) =>
    api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/load`, { provider, model }),
  focus: (name: string) =>
    api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/focus`),
  unloadActive: () => api.delete<void>('/api/sessions/active'),
  detachActive: () => api.post<Record<string, unknown>>('/api/sessions/active/detach'),
  unload: (name: string) => api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/unload`),
  delete: (name: string) => api.delete<void>(`/api/sessions/${encodeURIComponent(name)}`),
  getContainer: (name: string) =>
    api.get<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/container`),
  addContainerMount: (name: string, mount: ContainerMount) =>
    api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/container/mount`, mount),
};
