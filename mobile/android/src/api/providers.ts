import { api } from './client';

export interface ProviderInfo {
  name: string;
  label?: string;
  configured: boolean;
  requires: string;
  cloud_key_set?: boolean;
}

export interface CurrentProvider {
  provider: string | null;
  model: string | null;
  ollama_api_key_set: boolean;
  local_base_url?: string;
  local_api_key_set?: boolean;
  /** Session effort for agent turns; "" = provider default. */
  effort?: string;
  /** Provider-neutral levels the server accepts (mapped per provider). */
  effort_levels?: string[];
}

export interface ModelPricingInfo {
  provider: string;
  key: string;
  input_per_million: number | null;
  cached_input_per_million: number | null;
  output_per_million: number | null;
  /** Legacy compatibility only. New estimated-token rows use input/output rates. */
  estimated_total_per_million: number | null;
  billing: 'token' | 'estimated_token' | 'local' | 'unknown' | string;
  aliases: string[];
  input_modalities: string[];
  output_modalities: string[];
  capabilities: string[];
  context_window: number | null;
  long_context_cutoff: number | null;
  long_input_per_million: number | null;
  long_cached_input_per_million: number | null;
  long_output_per_million: number | null;
  role: string;
  notes: string;
  source: string;
}

export interface OllamaCatalogInfo extends ModelPricingInfo {}

export interface ModelPricingCatalog {
  version: string;
  currency: string;
  unit: string;
  models: ModelPricingInfo[];
  ollama: OllamaCatalogInfo[];
  provider_notes: Record<string, string>;
  config_path: string;
  active_config_path: string;
  default_config_path: string;
  using_override: boolean;
}

export const providersApi = {
  list: () => api.get<{ providers: ProviderInfo[] }>('/api/providers'),
  pricing: () => api.get<ModelPricingCatalog>('/api/providers/pricing'),
  listModels: (name: string, ollamaMode?: string, ollamaApiKey?: string) =>
    api.get<{ models: string[]; error?: string }>(`/api/providers/${encodeURIComponent(name)}/models`, {
      query: { ollama_mode: ollamaMode, ollama_api_key: ollamaApiKey },
    }),
  discoverLocal: (baseUrl: string, apiKey?: string) => api.post<{ models: string[]; error?: string }>('/api/providers/local/discover', {local_base_url: baseUrl, local_api_key: apiKey || undefined}),
  getCurrent: () => api.get<CurrentProvider>('/api/providers/current'),
  switch: (provider: string, model: string, ollamaHost?: string, ollamaMode?: string, ollamaApiKey?: string, localBaseUrl?: string, localApiKey?: string) =>
    api.post<Record<string, unknown>>('/api/providers/switch', {
      provider, model, ollama_host: ollamaHost, ollama_mode: ollamaMode, ollama_api_key: ollamaApiKey,
      local_base_url: localBaseUrl, local_api_key: localApiKey || undefined,
    }),
};
