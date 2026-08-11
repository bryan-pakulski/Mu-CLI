import { api } from './client';
import { useConnectionStore } from '../store/connection';

export interface MemoryLayer {
  id: string;
  name: string;
  tokens: number;
  chars: number;
  max: number;
  fill_pct: number;
  hue: number;
  change_count: number;
  row_start: number;
  row_end: number;
}

export interface MemorySnapshot {
  active: boolean;
  cols: number;
  rows: number;
  layers: MemoryLayer[];
  regions: Array<MemoryLayer & { free?: boolean }>;
  grid: number[][];
  total_tokens: number;
  token_source: string;
  context_limit: number;
  free_tokens: number;
  fill_pct: number;
}

export interface DurableMemoryScope {
  type: 'personal' | 'workspace' | 'repository' | 'branch' | 'feature';
  key: string;
  label: string;
}

export interface DurableMemory {
  id: string;
  version: number;
  statement: string;
  kind: string;
  scope: DurableMemoryScope;
  lifecycle: string;
  pinned: boolean;
  trust: { origin: string; verification: string; confidence: number };
  sensitivity: string;
  egress_policy: string;
  tags: string[];
  source_refs: Array<Record<string, unknown>>;
  relations: Array<Record<string, unknown>>;
  created_at: number;
  updated_at: number;
  last_recalled_at?: number | null;
  recall_count: number;
  etag: string;
}

export interface DurableMemoryList {
  memories: DurableMemory[];
  stats: {
    total: number;
    pinned: number;
    by_lifecycle: Record<string, number>;
    scopes: DurableMemoryScope[];
  };
}

export interface DurableMemoryDetail {
  memory: DurableMemory;
  events: Array<Record<string, unknown>>;
  revisions: Array<Record<string, unknown>>;
  graph: {
    center: string;
    nodes: DurableMemory[];
    edges: Array<{ source: string; target: string; type: string }>;
  };
}

export interface RecallReceipt {
  id: string;
  query: string;
  token_count: number;
  budget_tokens: number;
  included: Array<Record<string, unknown>>;
  excluded: Array<Record<string, unknown>>;
  created_at: number;
}

const sessionQuery = () => ({
  session_name: useConnectionStore.getState().activeSessionName || undefined,
});

export const memoryApi = {
  getState: (cols?: number, rows?: number) =>
    api.get<MemorySnapshot>('/api/memory/state', { query: { cols, rows } }),
  getLayerContent: (layer: string) =>
    api.get<{ layer: string; name: string; hue: number; content: string; tokens: number; chars: number; error: string }>(
      '/api/memory/content', { query: { layer } },
    ),
  getCell: (layer: string, row: number, col: number, cols?: number, rows?: number) =>
    api.get<{ error: string; content: string; chars: number; tokens: number; cell_index: number; cell_count: number }>(
      '/api/memory/cell', { query: { layer, row, col, cols, rows } },
    ),
  listDurable: (q = '', lifecycle?: string) =>
    api.get<DurableMemoryList>('/api/v1/memories', {
      query: { ...sessionQuery(), q: q || undefined, lifecycle, limit: 200 },
    }),
  getDurable: (id: string) =>
    api.get<DurableMemoryDetail>('/api/v1/memories/' + encodeURIComponent(id), {
      query: sessionQuery(),
    }),
  createDurable: (
    statement: string,
    scope = 'auto',
    kind = 'observation',
    pinned = false,
  ) =>
    api.post<{ ok: boolean; created: boolean; memory: DurableMemory }>(
      '/api/v1/memories',
      { statement, scope, kind, pinned },
      { query: sessionQuery() },
    ),
  reviseDurable: (memory: DurableMemory, statement: string) =>
    api.patch<{ ok: boolean; memory: DurableMemory }>(
      '/api/v1/memories/' + encodeURIComponent(memory.id),
      { changes: { statement }, reason: 'Mobile Memory Center edit' },
      { query: sessionQuery(), headers: { 'If-Match': memory.etag } },
    ),
  actionDurable: (
    memory: DurableMemory,
    action: 'pin' | 'unpin' | 'archive' | 'restore' | 'forget',
  ) =>
    api.post<{ ok: boolean; memory: DurableMemory }>(
      '/api/v1/memories/' + encodeURIComponent(memory.id) + '/actions',
      { action, reason: 'Mobile Memory Center ' + action },
      { query: sessionQuery(), headers: { 'If-Match': memory.etag } },
    ),
  getLastRecall: () =>
    api.get<{ receipt: RecallReceipt }>('/api/v1/memory-recalls/last', {
      query: sessionQuery(),
    }),
};
