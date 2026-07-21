import { api } from './client';

// Types
export interface SessionSummary {
  name: string;
  is_current: boolean;
  is_loaded: boolean;
  is_busy: boolean;
  modified_at: string;
  modified_unix: number;
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

// API
export const sessionsApi = {
  list: () => api.get<SessionListResponse>('/api/sessions'),
  getActive: (sessionName?: string) =>
    api.get<Record<string, unknown>>('/api/sessions/active', { query: { session_name: sessionName } }),
  getHistory: (sessionName?: string) =>
    api.get<SessionHistoryResponse>('/api/sessions/current/history', { query: { session_name: sessionName } }),
  create: (name: string, provider: string, model: string, workspace?: string) =>
    api.post<Record<string, unknown>>('/api/sessions', { name, provider, model, activate: true, workspace }),
  load: (name: string, provider?: string, model?: string) =>
    api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/load`, { provider, model }),
  focus: (name: string) =>
    api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/focus`),
  unloadActive: () => api.delete<void>('/api/sessions/active'),
  detachActive: () => api.post<Record<string, unknown>>('/api/sessions/active/detach'),
  unload: (name: string) => api.post<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(name)}/unload`),
  delete: (name: string) => api.delete<void>(`/api/sessions/${encodeURIComponent(name)}`),
};