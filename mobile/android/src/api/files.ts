import { api } from './client';

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  children?: FileEntry[] | null;
}

export interface FileReadResult {
  path: string;
  content: string;
  size: number;
  readonly: boolean;
  why?: string;
  mtime?: number;
}

export const filesApi = {
  getTree: (path?: string) =>
    api.get<{ roots?: FileEntry[]; path?: string; entries?: FileEntry[] }>('/api/files/tree', { query: { path } }),
  readFile: (path: string) =>
    api.get<FileReadResult>('/api/files/read', { query: { path } }),
  save: (path: string, content: string, expectedMtime?: number) =>
    api.post<{ ok: boolean; path: string; size: number; mtime: number }>('/api/files/save', { path, content, expected_mtime: expectedMtime }),
  create: (path: string, isDir: boolean = false) =>
    api.post<{ ok: boolean; path: string; is_dir: boolean }>('/api/files/create', { path, is_dir: isDir }),
  rename: (from: string, to: string) =>
    api.post<{ ok: boolean; from: string; to: string }>('/api/files/rename', { from, to }),
  delete: (path: string, recursive: boolean = false) =>
    api.delete<{ ok: boolean; path: string }>('/api/files', { query: { path, recursive } }),
};