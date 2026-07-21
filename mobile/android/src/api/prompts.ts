import { api } from './client';

export interface PromptInfo {
  name: string;
  source: string;
  path: string | null;
  version: number | null;
  chars: number;
  has_override: boolean;
}

export interface PromptDetail {
  name: string;
  text: string;
  source: string;
  path: string | null;
  version: number | null;
  chars: number;
  validation: string[];
  raw_file: string | null;
}

export const promptsApi = {
  list: () => api.get<{ prompts_dir: string; items: PromptInfo[] }>('/api/prompts'),
  listPending: () => api.get<{ pending: Array<Record<string, unknown>> }>('/api/prompts'),
  answer: (promptId: string, payload: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/api/prompts/${encodeURIComponent(promptId)}/answer`, payload),
  cancel: (promptId: string) =>
    api.post<Record<string, unknown>>(`/api/prompts/${encodeURIComponent(promptId)}/cancel`),
};