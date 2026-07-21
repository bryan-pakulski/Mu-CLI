import { api } from './client';

export interface ProviderInfo {
  name: string;
  configured: boolean;
  requires: string;
  cloud_key_set?: boolean;
}

export interface CurrentProvider {
  provider: string | null;
  model: string | null;
  ollama_api_key_set: boolean;
}

export const providersApi = {
  list: () => api.get<{ providers: ProviderInfo[] }>('/api/providers'),
  listModels: (name: string, ollamaMode?: string, ollamaApiKey?: string) =>
    api.get<{ models: string[]; error?: string }>(`/api/providers/${encodeURIComponent(name)}/models`, {
      query: { ollama_mode: ollamaMode, ollama_api_key: ollamaApiKey },
    }),
  getCurrent: () => api.get<CurrentProvider>('/api/providers/current'),
  switch: (provider: string, model: string, ollamaHost?: string, ollamaMode?: string, ollamaApiKey?: string) =>
    api.put<Record<string, unknown>>('/api/providers/switch', {
      provider, model, ollama_host: ollamaHost, ollama_mode: ollamaMode, ollama_api_key: ollamaApiKey,
    }),
};