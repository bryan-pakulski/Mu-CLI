// Loop mode (modes_v2.md §4.4; mu/gui/routers/loop.py): the mission (goal, status, budgets,
// done-when criteria, increments), the backlog as work items, open blockers and evidence runs.

import { api } from './client';
import type { Blocker, CheckRun, WorkItem } from './modeState';

export type LoopMissionStatus = 'running' | 'paused' | 'stopped' | 'done' | 'blocked';

export interface LoopCriterion {
  id: string;
  text: string;
  check: { command?: string; predicate?: Record<string, unknown> } | null;
  met: boolean;
  evidence: string[];
  attested_by: string;
  note: string;
}

export interface LoopIncrement {
  n: number;
  objective: string;
  result: 'passed' | 'failed' | 'partial' | string;
  decision: string;
  next: string;
  work_item: string;
  actions: string[];
  evidence: string[];
  delegations: string[];
  remediation_item: string;
  at: number;
}

export type LoopBudget = Partial<Record<'max_increments' | 'max_cost_usd' | 'max_runtime_s' | 'max_turns', number>>;
export type LoopSpent = Partial<Record<'increments' | 'cost_usd' | 'runtime_s' | 'turns', number>>;

export interface LoopMission {
  id: string;
  goal: string;
  status: LoopMissionStatus | string;
  status_reason: string;
  goal_history: Array<Record<string, unknown>>;
  done_when: LoopCriterion[];
  budget: LoopBudget;
  spent: LoopSpent;
  increments: LoopIncrement[];
  started_at: number;
  ended_at: number | null;
}

/** The budget closest to its cap (mu/loop/engine.py tightest_budget). */
export interface LoopBudgetMeter {
  kind: 'increments' | 'cost_usd' | 'runtime_s' | 'turns' | string;
  used: number;
  cap: number;
  ratio: number;
}

export interface LoopState {
  active: boolean;
  mission: LoopMission | null;
  budget_meter: LoopBudgetMeter | null;
  backlog: WorkItem[];
  current_item: string | null;
  blockers: Blocker[];
  runs: CheckRun[];
  past: Array<{ id: string; goal: string; status: string }>;
}

export const loopApi = {
  getState: () => api.get<LoopState>('/api/loop/state'),
  control: (action: 'pause' | 'resume' | 'stop', reason = '') => api.post<LoopState>('/api/loop/control', { action, reason }),
  start: (goal: string, doneWhen: unknown[] = [], budget: LoopBudget = {}) =>
    api.post<LoopState>('/api/loop/start', { goal, done_when: doneWhen, budget }),
  setGoal: (goal: string) => api.post<LoopState>('/api/loop/goal', { goal }),
  setBudget: (budget: LoopBudget) => api.post<LoopState>('/api/loop/budget', budget),
  attest: (criterionId: string, note: string) => api.post<LoopState>(`/api/loop/criteria/${encodeURIComponent(criterionId)}/attest`, { note }),
  addBacklogItem: (content: string, status = 'pending') => api.post<LoopState>('/api/loop/backlog', { content, status }),
};
