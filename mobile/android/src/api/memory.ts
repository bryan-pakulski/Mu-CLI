import { api } from './client';

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
};