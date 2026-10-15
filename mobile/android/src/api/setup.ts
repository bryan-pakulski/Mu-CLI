// Jobs Setup and Doctor (Wash-style; backend mu/jobs/setup.py). Setup is a live checklist:
// every step checks the host's actual state and offers the actions that fix it. Doctor is the
// quick pass/warn/fail report of everything job runs depend on. Secrets typed into a setup
// prompt go to the host once and are never sent back.

import { api } from './client';
import type { Status } from '../theme/status';

export type SetupState = 'ok' | 'todo' | 'warn' | 'skipped' | 'optional' | '';
export type SetupActionKind = 'run' | 'prompts' | 'job' | 'terminal' | 'nav';

export interface SetupPrompt { label: string; initial?: string; secret?: boolean }

export interface SetupAction {
  id: string; label: string; kind: SetupActionKind; hint?: string; command?: string;
  prompts?: SetupPrompt[]; nav?: string; confirm?: string;
}

export interface SetupStep {
  id: string; title: string; why: string; optional: boolean; checking: boolean;
  state: SetupState; detail: string; actions: SetupAction[];
}

export interface SetupJob { step: string; action: string; title: string; running: boolean; ok: boolean | null; lines: string[] }

export interface SetupVM {
  steps: SetupStep[]; done: number; total: number; all_checked: boolean; blocking: string[];
  can_finish: boolean; completed_at?: number | null; job: SetupJob | null; notice?: string;
}

export interface SetupActionResult { ok: boolean; message?: string; nav?: string; job?: SetupJob; setup: SetupVM }

export type DoctorStatus = 'ok' | 'warn' | 'fail';
export interface DoctorCheck { name: string; status: DoctorStatus; detail: string }
export interface DoctorReport { checks: DoctorCheck[]; failed: number; warnings: number; needs_setup: boolean }

/** Checks run docker/git on the host; a deep doctor starts real containers (~10-20s). */
const CHECK_TIMEOUT_MS = 90_000;

export const setupApi = {
  get: (recheck = false) => api.get<SetupVM>('/api/jobs/setup', { query: recheck ? { recheck: 1 } : undefined, timeoutMs: CHECK_TIMEOUT_MS }),
  recheck: (steps?: string[]) => api.post<SetupVM>('/api/jobs/setup/recheck', steps ? { steps } : {}, { timeoutMs: CHECK_TIMEOUT_MS }),
  action: (step: string, action: string, inputs: string[] = []) =>
    api.post<SetupActionResult>('/api/jobs/setup/action', { step, action, inputs }, { timeoutMs: CHECK_TIMEOUT_MS }),
  finish: () => api.post<SetupVM>('/api/jobs/setup/finish', {}),
  doctor: (deep = false) => api.get<DoctorReport>('/api/jobs/doctor', { query: deep ? { deep: 1 } : undefined, timeoutMs: deep ? 180_000 : CHECK_TIMEOUT_MS }),
};

export const fetchSetup = setupApi.get;
export const recheckSetup = setupApi.recheck;
export const runSetupAction = setupApi.action;
export const finishSetup = setupApi.finish;
export const fetchDoctor = setupApi.doctor;

/** Wash status vocabulary for a setup step (glyph + tone + label). */
export function stepStatus(step: Pick<SetupStep, 'state' | 'checking'>): Status {
  if (step.checking) return 'active';
  return ({ ok: 'done', todo: 'waiting', warn: 'waiting', skipped: 'skipped', optional: 'pending', '': 'pending' } as const)[step.state] || 'pending';
}

export function doctorStatus(status: DoctorStatus): Status {
  return status === 'ok' ? 'done' : status === 'warn' ? 'waiting' : 'failed';
}

/** The step to show first: the first one that needs attention (once everything is checked). */
export function firstAttention(vm: SetupVM | null | undefined): SetupStep | undefined {
  if (!vm?.steps.length) return undefined;
  return (vm.all_checked ? vm.steps.find(s => !s.checking && (s.state === 'todo' || s.state === 'warn')) : undefined) ?? vm.steps[0];
}

/** Initial values for a prompts action: secrets are never prefilled. */
export function promptInitials(action: SetupAction): string[] {
  return (action.prompts || []).map(p => (p.secret ? '' : p.initial || ''));
}

export function doctorSummary(report: DoctorReport | null | undefined): string {
  if (!report) return '';
  const ok = report.checks.length - report.failed - report.warnings;
  return `${ok}/${report.checks.length} ok` + (report.failed ? ` · ${report.failed} failing` : '') + (report.warnings ? ` · ${report.warnings} warnings` : '');
}
