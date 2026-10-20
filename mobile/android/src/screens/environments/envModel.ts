// Environments: the words and numbers the web page shows (mu/gui/static/js/containers.js), as pure
// functions so the list, the detail page and the tests agree.

import type { ContainerStats, ManagedContainer, ContainerTemplateSummary } from '../../api/containers';
import type { Status } from '../../theme/status';

/** Docker state → status sign and word (containers.js STATUS). */
const STATES: Record<string, { status: Status; word: string }> = {
  running: { status: 'active', word: 'Running' },
  restarting: { status: 'active', word: 'Restarting' },
  created: { status: 'pending', word: 'Created' },
  exited: { status: 'pending', word: 'Stopped' },
  stopped: { status: 'pending', word: 'Stopped' },
  paused: { status: 'waiting', word: 'Paused' },
  missing: { status: 'failed', word: 'Missing' },
  error: { status: 'failed', word: 'Error' },
  dead: { status: 'failed', word: 'Dead' },
};

export function envStatus(ref: Pick<ManagedContainer, 'status' | 'job_scan_blocks'>): Status {
  if ((ref.job_scan_blocks || []).length) return 'waiting';
  return STATES[ref.status]?.status || 'pending';
}

export function statusWord(status: string): string {
  return STATES[status]?.word || (status ? status[0].toUpperCase() + status.slice(1) : 'Unknown');
}

export function formatBytes(value: number | null | undefined): string {
  let n = Math.max(0, Number(value || 0));
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

export const formatRate = (value: number | null | undefined) => `${formatBytes(value)}/s`;

export function formatUptime(seconds: number | null | undefined): string {
  let s = Math.max(0, Math.round(Number(seconds || 0)));
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** A list row's second line: the state in words, then live CPU and memory, never a placeholder. */
export function envRowSub(ref: ManagedContainer, live?: ContainerStats | null): string {
  const parts = [statusWord(ref.status)];
  if ((ref.job_scan_blocks || []).length) parts.push('Secret scan blocked a job');
  if (live && live.status === 'running') {
    parts.push(`CPU ${Math.round(Number(live.cpu_percent || 0))}%`);
    parts.push(formatBytes(live.memory_used_bytes));
  } else if (live && live.storage_writable_bytes) {
    parts.push(`${formatBytes(live.storage_writable_bytes)} retained`);
  } else if (ref.attached_sessions.length) {
    parts.push(plural(ref.attached_sessions.length, 'session'));
  }
  return parts.join(' · ');
}

export function templateRowSub(item: ContainerTemplateSummary): string {
  const used = Number(item.jobs_using || 0);
  return [used ? `used by ${plural(used, 'job')}` : '', item.source_container ? `from ${item.source_container}` : item.description || ''].filter(Boolean).join(' · ') || 'Template';
}

/** The detail page's state sentence parts. */
export function envState(ref: ManagedContainer, live?: ContainerStats | null): string[] {
  const parts = [statusWord(ref.status)];
  if (live && live.status === 'running' && live.uptime_seconds) parts.push(`up ${formatUptime(live.uptime_seconds)}`);
  const users = ref.attached_sessions.length;
  if (users) parts.push(`used by ${ref.attached_sessions.slice(0, 2).join(', ')}${users > 2 ? ` +${users - 2}` : ''}`);
  if (ref.jobs_using) parts.push(`used by ${plural(Number(ref.jobs_using), 'job')}`);
  return parts;
}

export function listState(containers: ManagedContainer[], templates: ContainerTemplateSummary[]): string[] {
  const running = containers.filter(c => c.status === 'running').length;
  const other = containers.length - running;
  return [running ? `${running} running` : '', other ? `${other} stopped` : '', templates.length ? plural(templates.length, 'template') : ''].filter(Boolean);
}

/** One live sample per poll, newest last (the detail page's charts). */
export type Sample = { cpu: number; mem: number; rx: number; tx: number; r: number; w: number };
export const MAX_SAMPLES = 60;

export function appendSample(samples: Sample[], live: ContainerStats, previous?: ContainerStats | null): Sample[] {
  const dt = previous ? Math.max(0.001, live.sampled_at - previous.sampled_at) : 0;
  const rate = (now: number, before?: number) => (previous && dt ? Math.max(0, (now - Number(before || 0)) / dt) : 0);
  const next: Sample = {
    cpu: Number(live.cpu_percent || 0),
    mem: Number(live.memory_percent || 0),
    rx: Number(live.network_rx_bytes_per_second || 0),
    tx: Number(live.network_tx_bytes_per_second || 0),
    r: rate(live.block_read_bytes, previous?.block_read_bytes),
    w: rate(live.block_write_bytes, previous?.block_write_bytes),
  };
  return [...samples, next].slice(-MAX_SAMPLES);
}

/** Lines → list (allowlists, job setup), blanks dropped. */
export const splitLines = (text: string) => text.split('\n').map(line => line.trim()).filter(Boolean);
