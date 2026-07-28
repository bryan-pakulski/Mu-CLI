import { api } from './client';

export interface InspectorWorkspace {
  folders: string[];
  cwd: string;
}

export interface InspectorBrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
}

export interface InspectorBrowseResponse {
  path: string;
  parent: string;
  entries: InspectorBrowseEntry[];
}

export interface InspectorMemoryEntry {
  id: number;
  content: string;
  tags: string[];
  source: string;
  kind: string;
  created_at: number;
  updated_at: number;
  hits: number;
  status: string;
}

export interface InspectorMemoryResponse {
  task_memory: InspectorMemoryEntry[];
  scratchpad: InspectorMemoryEntry[];
}

export interface InspectorStats {
  active: boolean;
  name?: string;
  provider?: string;
  model?: string;
  tokens?: Record<string, number>;
  history_length?: number;
  estimated_cost_usd?: number;
  task_memory_size?: number;
  scratchpad_size?: number;
  memory_status_counts?: Record<string, number>;
  agent_mode?: string;
}

export interface InspectorVariable {
  key: string;
  type: string;
  default: unknown;
  value: unknown;
  is_default: boolean;
  help: string;
  secret?: boolean;
  is_set?: boolean;
}

export interface InspectorVariableGroup {
  name: string;
  variables: InspectorVariable[];
}

export const inspectorApi = {
  getWorkspace: () => api.get<InspectorWorkspace>('/api/workspace'),
  addWorkspace: (path: string) => api.post<{ ok: boolean; added: string; folders: string[] }>('/api/workspace', { path }),
  removeWorkspace: (path: string) => api.delete<{ ok: boolean; removed: string; folders: string[] }>('/api/workspace', { query: { path } }),
  browse: (path?: string) => api.get<InspectorBrowseResponse>('/api/browse', { query: { path } }),
  mkdir: (path: string, name: string) => api.post<{ ok: boolean; path: string }>('/api/browse/mkdir', { path, name }),
  getMemory: () => api.get<InspectorMemoryResponse>('/api/memory'),
  searchMemory: (q: string, limit?: number) =>
    api.get<{ results: InspectorMemoryEntry[] }>('/api/memory/search', { query: { q, limit } }),
  deleteMemory: (entryId: number) => api.delete<{ ok: boolean; removed: number }>(`/api/memory/${entryId}`),
  clearMemory: () => api.post<{ ok: boolean }>('/api/memory/clear'),
  getStats: () => api.get<InspectorStats>('/api/stats'),
  getVariables: () => api.get<{ groups: InspectorVariableGroup[] }>('/api/variables'),
  setVariable: (key: string, value: unknown) => api.post<{ ok: boolean; key: string; value: unknown }>(`/api/variables/${encodeURIComponent(key)}`, { value }),
  unsetVariable: (key: string) => api.delete<{ ok: boolean; key: string; value: unknown }>(`/api/variables/${encodeURIComponent(key)}`),
};