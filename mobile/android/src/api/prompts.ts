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

export type PromptShape =
  | 'input'
  | 'confirm'
  | 'choices'
  | 'choice'
  | 'quiz'
  | 'tool_approval'
  | string;

export interface QuizQuestion {
  qid: string;
  prompt: string;
  kind: 'multiple_choice' | 'fill_blank';
  options?: string[];
  correct_index?: number;
  expected_answer?: string;
  explanation?: string;
  case_sensitive?: boolean;
  expected_pattern?: string;
}

export interface PendingPrompt {
  id: string;
  shape: PromptShape;
  session_name?: string;
  message?: string;
  question?: string;
  description?: string;
  default?: unknown;
  choices?: unknown[];
  options?: unknown[];
  multi_select?: boolean;
  allow_other?: boolean;
  tool_name?: string;
  tool_args?: unknown;
  risk?: string;
  /** Quiz shape: array of questions from the server. */
  questions?: QuizQuestion[];
  [key: string]: unknown;
}

export const promptsApi = {
  list: () => api.get<{ prompts_dir: string; items: PromptInfo[] }>('/api/prompts'),
  listPending: () => api.get<{ pending: PendingPrompt[] }>('/api/prompts'),
  answer: (promptId: string, payload: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/api/prompts/${encodeURIComponent(promptId)}/answer`, payload),
  cancel: (promptId: string) =>
    api.post<Record<string, unknown>>(`/api/prompts/${encodeURIComponent(promptId)}/cancel`),
};