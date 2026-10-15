// "Run in" (documentation/plans/job_templates.md §3.1): which runtime a new job uses. Pure
// helpers for the picker (groups, row summaries, preselection, payload) plus the per-device
// "last used for this workspace" memory.

import AsyncStorage from '@react-native-async-storage/async-storage';

import { bindingsFromDrafts, draftFromSpec, type EnvironmentSourceOption, type EnvironmentsVM, type TemplateSourceOption } from './environments';
import { bytes } from '../theme/status';

/** '' = Default runtime; otherwise an option spec (`template:x` / `environment:x`). */
export type RunInSpec = string;
export const DEFAULT_RUN_IN: RunInSpec = '';

export interface RunInGroups { templates: TemplateSourceOption[]; environments: EnvironmentSourceOption[] }

const specOf = (kind: 'template' | 'environment', o: EnvironmentSourceOption) => o.spec || `${kind}:${o.name}`;

/** Templates and environments, each sorted by name (Default runtime is always first in the UI). */
export function runInGroups(vm: EnvironmentsVM | null | undefined): RunInGroups {
  const byName = <T extends EnvironmentSourceOption>(list: T[] | undefined, kind: 'template' | 'environment') =>
    (list || []).filter(o => o && o.name).map(o => ({ ...o, spec: specOf(kind, o) })).sort((a, b) => a.name.localeCompare(b.name));
  return { templates: byName(vm?.templates, 'template'), environments: byName(vm?.environments, 'environment') };
}

export function specKind(spec: RunInSpec): 'default' | 'template' | 'environment' {
  if (spec.startsWith('template:')) return 'template';
  if (spec.startsWith('environment:')) return 'environment';
  return 'default';
}

export function specName(spec: RunInSpec): string {
  return spec.includes(':') ? spec.slice(spec.indexOf(':') + 1) : spec;
}

/** True when `spec` is Default runtime or names a template/environment in `groups`. */
export function specAvailable(spec: RunInSpec | null | undefined, groups: RunInGroups): boolean {
  if (spec == null) return false;
  if (spec === DEFAULT_RUN_IN) return true;
  return [...groups.templates, ...groups.environments].some(o => o.spec === spec);
}

/**
 * Preselection order (§3.1): the repository's remembered template, then the session's
 * environment, then the last choice for this workspace on this device, then Default runtime.
 * A candidate that is no longer offered is skipped.
 */
export function preselectRunIn(input: {
  vm: EnvironmentsVM | null | undefined; sessionEnvironment?: string | null; lastUsed?: RunInSpec | null;
}): RunInSpec {
  const groups = runInGroups(input.vm);
  const remembered = input.vm?.remembered?.source || '';
  const session = (input.sessionEnvironment || '').trim();
  const candidates = [
    remembered,
    session ? (session.includes(':') ? session : `environment:${session}`) : '',
    input.lastUsed || '',
  ];
  return candidates.find(c => c && specAvailable(c, groups)) || DEFAULT_RUN_IN;
}

/** `node ×2, cargo`: the job setup commands by program. */
export function setupSummary(commands: string[] | undefined): string {
  const counts = new Map<string, number>();
  for (const cmd of commands || []) {
    const program = String(cmd || '').trim().split(/\s+/)[0];
    if (program) counts.set(program, (counts.get(program) || 0) + 1);
  }
  return [...counts].map(([p, n]) => (n > 1 ? `${p} ×${n}` : p)).join(', ');
}

/** Template row detail: description · GPU · setup · size. */
export function templateSummary(t: TemplateSourceOption): string {
  const parts: string[] = [];
  if (t.description) parts.push(t.description);
  if (t.gpus) parts.push('GPU');
  const setup = setupSummary(t.job_setup);
  parts.push(setup ? `setup: ${setup}` : 'no job setup');
  const size = t.size || (t.size_bytes ? bytes(t.size_bytes) : '');
  if (size) parts.push(size);
  return parts.join(' · ');
}

/** Environment row detail: status · description. */
export function environmentSummary(e: EnvironmentSourceOption): string {
  return [e.status, e.description || e.image].filter(Boolean).join(' · ');
}

/** Registries the template can't reach for this workspace (`◆ can't reach …`). */
export function fitWarnings(t: TemplateSourceOption | null | undefined): string[] {
  return [...new Set((t?.fit?.missing_egress || []).map(d => String(d).trim()).filter(Boolean))];
}

/** Allowlist after "Allow": the template's current egress plus `domains` (no duplicates). */
export function allowEgress(t: TemplateSourceOption, domains: string[]): string[] {
  const out = [...(t.egress_allow || [])];
  for (const d of domains) if (!out.includes(d)) out.push(d);
  return out;
}

/**
 * `execution` fields for the choice. A template is sent by name (the server normalises it to
 * `execution.environments`) and may be remembered as the repository default; an environment is
 * sent as one binding; Default runtime sends nothing.
 */
export function runInExecution(spec: RunInSpec, remember = false): Record<string, unknown> {
  const kind = specKind(spec);
  if (kind === 'template') return remember ? { template: specName(spec), remember_template: true } : { template: specName(spec) };
  if (kind === 'environment') return { environments: bindingsFromDrafts([draftFromSpec(spec)]) };
  return {};
}

/* ------------------------------------------------------------ last used per workspace (device) */

export const LAST_RUN_IN_KEY = 'mucli.jobs.runIn.lastUsed';
const workspaceKey = (workspace: string) => workspace.trim().replace(/\/+$/, '');

async function readLastUsed(): Promise<Record<string, RunInSpec>> {
  try {
    const raw = await AsyncStorage.getItem(LAST_RUN_IN_KEY);
    const value = raw ? JSON.parse(raw) : {};
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, RunInSpec> : {};
  } catch {
    return {};
  }
}

export async function loadLastRunIn(workspace: string): Promise<RunInSpec | null> {
  const key = workspaceKey(workspace);
  if (!key) return null;
  const map = await readLastUsed();
  return typeof map[key] === 'string' ? map[key] : null;
}

export async function saveLastRunIn(workspace: string, spec: RunInSpec): Promise<void> {
  const key = workspaceKey(workspace);
  if (!key) return;
  const map = await readLastUsed();
  map[key] = spec;
  try { await AsyncStorage.setItem(LAST_RUN_IN_KEY, JSON.stringify(map)); } catch { /* device storage is best effort */ }
}
