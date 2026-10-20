// Model pricing (Insights › Costs; web /models/costs, mu/gui/routers/providers.py):
//   GET  /api/providers/pricing        the live registry
//   PUT  /api/providers/pricing        validate and save the operator override
//   POST /api/providers/pricing/reset  back to the packaged defaults

import { api } from './client';
import type { ModelPricingCatalog, ModelPricingInfo } from './providers';

export type PricingModel = ModelPricingInfo;
export type PricingCatalog = ModelPricingCatalog;

export const BILLING_MODES = ['token', 'estimated_token', 'local', 'unknown'] as const;

export const costsApi = {
  get: () => api.get<PricingCatalog>('/api/providers/pricing'),
  save: (catalog: Pick<PricingCatalog, 'currency' | 'unit'>, models: PricingModel[]) =>
    api.put<PricingCatalog>('/api/providers/pricing', {
      version: new Date().toISOString(),
      currency: catalog.currency || 'USD',
      unit: catalog.unit || 'per_million_tokens',
      models,
    }),
  reset: () => api.post<PricingCatalog>('/api/providers/pricing/reset'),
};
