// Job build environments (documentation/plans/job_build_environments.md): per-job
// snapshots of container-mode environments, templates or images. Types for the
// server contract plus the pure helpers the New job field, the job panel, the
// session transcript and Settings share.

import { api } from './client';

export type BindingSource = 'environment' | 'template' | 'image';
export type BindingStatus = 'preparing' | 'ready' | 'failed' | 'cleaned' | '' | string;

/** One binding as resolved (preview) or as stored on the job override. */
export interface EnvironmentBinding {
  name: string; source: BindingSource | string; ref: string; paths: string[];
  primary?: boolean; docker?: boolean; gpus?: string | null; preload_images?: string[]; label?: string;
}

/** A prepared binding on the job (VM `build_environments.bindings`). */
export interface JobEnvironmentBinding extends EnvironmentBinding {
  status: BindingStatus; error: string; image_bytes: number; home_bytes: number; prepared_at: number | null;
  probed_env: string[]; skipped_paths: string[]; secret_paths: string[]; engine: string; job_image: string; label: string;
}

/** VM `build_environments` (job page panel). */
export interface JobBuildEnvironments {
  source: string; kept: boolean; refresh_pending: boolean; bindings: JobEnvironmentBinding[];
  count: number; bytes: number; summary: string;
}

/** VM `environments` (job header pill, dashboard rows). */
export interface JobEnvironmentsSummary { count: number; kept: boolean; source: string; summary: string; status: BindingStatus; bytes: number }

export interface EnvironmentSourceOption { name: string; spec: string; image?: string; status?: string; gpus?: string; size?: string; description?: string; container?: string; sessions?: string[] }
export interface KeptJob { id: string; title: string; bytes: number; bindings: string }
export interface EnvironmentsVM {
  environments: EnvironmentSourceOption[]; templates: EnvironmentSourceOption[]; images: EnvironmentSourceOption[];
  docker_in_docker: { available: boolean; engine: string; message: string; install: string[] };
  gpu: { supported: boolean; reason: string; devices?: unknown[] };
  resolved: ResolvedEnvironments;
  kept_jobs?: KeptJob[];
}

/** What the repository asks to use/build (devcontainer.approval_request). */
export interface RepositoryEnvironmentRequest {
  repository: string; fingerprint: string; kind: 'image' | 'build' | string; ref: string; label?: string; origin?: string; ignored_features?: string[];
}
export interface ResolvedEnvironments {
  source: string; bindings: EnvironmentBinding[]; summary: string; error: string;
  /** Unapproved implicit devcontainer the repository offers. */
  suggestion?: { message: string; approval: RepositoryEnvironmentRequest };
  /** Repository-declared bindings still waiting for approval. */
  approval_required?: Array<{ binding: string; ref: string; fingerprint: string }>;
}

/** Approvals a New job sheet can offer for `repository` (suggestion first, then declared bindings). */
export function pendingEnvironmentApprovals(resolved: ResolvedEnvironments | null | undefined, repository: string): Array<RepositoryEnvironmentRequest & { message?: string }> {
  const out: Array<RepositoryEnvironmentRequest & { message?: string }> = [];
  if (!resolved) return out;
  if (resolved.suggestion?.approval?.fingerprint) out.push({ ...resolved.suggestion.approval, message: resolved.suggestion.message });
  for (const row of resolved.approval_required || []) {
    if (row.fingerprint && !out.some(o => o.fingerprint === row.fingerprint)) {
      out.push({ repository, fingerprint: row.fingerprint, kind: row.fingerprint.startsWith('build:') ? 'build' : 'image', ref: row.ref, label: `${row.binding}: ${row.ref}` });
    }
  }
  return out;
}

export const environmentsApi = {
  /** Sources + the set a new job for this repository/session would resolve to. */
  get: (query: { repository?: string; session?: string; images?: boolean } = {}) =>
    api.get<EnvironmentsVM>('/api/jobs/environments', {
      query: { repository: query.repository || undefined, session: query.session || undefined, images: query.images === false ? 'false' : undefined },
      timeoutMs: 15_000,
    }),
  /** Approve the image/devcontainer the repository declares (explicit human action; server re-checks the fingerprint). */
  approve: (body: { repository: string; fingerprint: string; session?: string }) =>
    api.post<{ approved: string }>('/api/jobs/environments/approve', body),
};

/** Where the resolved set came from (§4 precedence). */
export const SOURCE_LABEL: Record<string, string> = {
  job: 'this job', repo: 'repo config (.mucli/jobs.toml)', session: 'session environment',
  settings: 'Settings → Toolchains', default: 'shared runtime image', error: 'invalid configuration',
};
export function sourceLabel(source: string | undefined): string {
  return SOURCE_LABEL[source || ''] || source || '';
}

/** `rust-build → a, b · compose-it ⬢ docker` when the server sent no label. */
export function bindingLabel(b: EnvironmentBinding): string {
  if (b.label) return b.label;
  let text = b.name;
  if (b.paths?.length) text += ` → ${b.paths.join(', ')}`;
  if (b.docker) text += ' ⬢ docker';
  if (b.gpus) text += ` · gpus ${b.gpus}`;
  return text;
}

/** Header pill text: `⬢ 3 environments · kept` (null when the job uses the shared runtime). */
export function environmentsPillLabel(s: Partial<JobEnvironmentsSummary> | null | undefined): string | null {
  const count = Number(s?.count || 0);
  if (!count) return null;
  const parts = [`⬢ ${count} environment${count === 1 ? '' : 's'}`];
  if (s?.status && s.status !== 'ready') parts.push(String(s.status));
  if (s?.kept) parts.push('kept');
  return parts.join(' · ');
}

export function statusTone(status: BindingStatus): 'ok' | 'running' | 'danger' | 'muted' {
  return status === 'ready' ? 'ok' : status === 'preparing' ? 'running' : status === 'failed' ? 'danger' : 'muted';
}

/** Editable row of the Build environments editor. `gpus`: '' inherit · 'none' · 'all' · '0,1'. */
export interface EnvironmentDraft { source: BindingSource; ref: string; paths: string; docker: boolean; gpus: string }

export function emptyDraft(): EnvironmentDraft {
  return { source: 'environment', ref: '', paths: '', docker: false, gpus: '' };
}

export function draftFromBinding(b: EnvironmentBinding): EnvironmentDraft {
  const source = (['environment', 'template', 'image'].includes(b.source) ? b.source : 'environment') as BindingSource;
  const gpus = b.gpus == null ? '' : b.gpus === '' ? 'none' : b.gpus.replace(/^device=/, '');
  return { source, ref: b.ref || b.name, paths: (b.paths || []).join(', '), docker: !!b.docker, gpus };
}

/** Option spec (`environment:x` / `template:x` / `image:x`) → draft source + ref. */
export function draftFromSpec(spec: string, base: EnvironmentDraft = emptyDraft()): EnvironmentDraft {
  const [prefix, ...rest] = spec.split(':');
  const source = (['environment', 'template', 'image'].includes(prefix) ? prefix : 'environment') as BindingSource;
  return { ...base, source, ref: rest.length ? rest.join(':') : spec };
}

/** Drafts → `execution.environments` / `setEnvironments` binding objects (blank rows dropped). */
export function bindingsFromDrafts(drafts: EnvironmentDraft[]): Array<Record<string, unknown>> {
  return drafts.filter(d => d.ref.trim()).map(d => {
    const item: Record<string, unknown> = {
      env: `${d.source}:${d.ref.trim()}`,
      paths: d.paths.split(/[,\n]/).map(p => p.trim()).filter(Boolean),
      docker: d.docker,
    };
    const gpus = d.gpus.trim();
    if (gpus) item.gpus = gpus;
    return item;
  });
}

/** Local check before sending (the server re-validates and names the bad value). */
export function draftError(d: EnvironmentDraft): string | null {
  if (!d.ref.trim()) return null;
  if (d.source === 'image' ? !/^[\w./:@-]+$/.test(d.ref.trim()) || d.ref.trim().startsWith('-') : !/^[A-Za-z0-9][\w.-]*$/.test(d.ref.trim())) {
    return `Invalid ${d.source} name: ${d.ref.trim()}`;
  }
  for (const p of d.paths.split(/[,\n]/).map(x => x.trim()).filter(Boolean)) {
    if (p.startsWith('/') || p.split('/').includes('..')) return `Paths must be repository-relative: ${p}`;
  }
  if (d.gpus.trim() && !/^(all|none|inherit|[\w.:-]+(,[\w.:-]+)*)$/i.test(d.gpus.trim())) return `Invalid GPU request: ${d.gpus.trim()}`;
  return null;
}

/** `run_in_environment` tool calls fold to `[env] command` (planos: `› [node-build] npm test`). */
export function runInEnvironmentSummary(args: unknown): string | null {
  let value = args;
  if (typeof value === 'string') {
    try { value = JSON.parse(value); } catch { return null; }
  }
  if (!value || typeof value !== 'object') return null;
  const a = value as Record<string, unknown>;
  const env = String(a.env || '').trim();
  const command = String(a.command || '').trim();
  if (!env && !command) return null;
  const cwd = String(a.cwd || '').trim();
  return `[${env || '?'}] ${command}${cwd && cwd !== '.' ? `  (in ${cwd})` : ''}`;
}
