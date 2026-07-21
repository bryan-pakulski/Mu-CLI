import { api } from './client';

export interface SystemPromptInfo {
  name: string;
  source: string;
  path: string | null;
  version: number | null;
  chars: number;
  has_override: boolean;
}

export interface SystemPromptDetail {
  name: string;
  text: string;
  source: string;
  path: string | null;
  version: number | null;
  chars: number;
  validation: string[];
  raw_file: string | null;
}

export const systemPromptsApi = {
  list: () => api.get<{ prompts_dir: string; items: SystemPromptInfo[] }>('/api/system-prompts'),
  get: (name: string) => api.get<SystemPromptDetail>(`/api/system-prompts/${encodeURIComponent(name)}`),
  put: (name: string, text: string, version?: number) =>
    api.put<{ ok: boolean; name: string; path: string; validation: string[] }>(
      `/api/system-prompts/${encodeURIComponent(name)}`,
      { text, version },
    ),
  reload: () => api.post<{ ok: boolean }>('/api/system-prompts/reload'),
  init: (names?: string[], force?: boolean) =>
    api.post<{ ok: boolean; written: Record<string, string> }>('/api/system-prompts/init', { names, force }),
  reset: (name: string) => api.post<{ ok: boolean; name: string; removed: boolean }>(`/api/system-prompts/${encodeURIComponent(name)}/reset`),
};