// Work's words and groupings, mirroring the web (mu/gui/static/js/work.js paintState and
// work/view.js renderHome): the page's state sentence, the Now board (Needs you with each job's
// decisive action, Running, Recently finished, Cost) and each row's one sentence. Pure: tested.

import type { HomeVM, JobAction, JobSummary, JobsStatusVM } from '../api/jobCore';
import type { StatePart } from '../ui/Mu';
import { STATUS, money, stateLabel, stateStatus, verdictStyle } from '../theme/status';
import { actionLabel } from './JobActions';

export type WorkTab = 'now' | 'jobs' | 'tickets' | 'history';
export const WORK_TABS: Array<{ key: WorkTab; label: string }> = [
  { key: 'now', label: 'Now' }, { key: 'jobs', label: 'Jobs' }, { key: 'tickets', label: 'Tickets' }, { key: 'history', label: 'History' },
];
export const asWorkTab = (value: unknown): WorkTab => (WORK_TABS.some(t => t.key === value) ? value as WorkTab : 'now');

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const repoName = (row: Pick<JobSummary, 'repository'>) => (row.repository ? String(row.repository).split('/').filter(Boolean).pop() || '' : '');
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Cost of the jobs active today (each job's cost lands on its last active day, as the web). */
export function spendToday(jobs: JobSummary[], now = Date.now() / 1000): number {
  const start = Math.floor(now / 86400) * 86400;
  return jobs.reduce((sum, j) => ((j.updated_at || j.created_at || 0) >= start ? sum + (Number(j.cost_usd) || 0) : sum), 0);
}

/** The page's state sentence: needs you · running · checks · spend today (zeros left out). */
export function workStateParts(status: JobsStatusVM | null, home: HomeVM | null, now = Date.now() / 1000): StatePart[] {
  const st = status || (home ? {
    agents: home.running.filter(j => j.state !== 'checking').length,
    checks: home.jobs.filter(j => j.state === 'checking').length,
    needs_you: home.needs_you.length, engine: '',
  } : { agents: 0, checks: 0, needs_you: 0, engine: '' });
  const parts: StatePart[] = [];
  if (st.needs_you) parts.push({ text: `${st.needs_you} need${st.needs_you === 1 ? 's' : ''} you`, tone: 'attention' });
  if (st.agents) parts.push({ text: `${st.agents} running` });
  if (st.checks) parts.push({ text: plural(st.checks, 'check') });
  const spend = home ? spendToday(home.jobs, now) : 0;
  if (spend >= 0.005) parts.push({ text: `${money(spend)} today`, tone: 'quiet' });
  if (st.engine === 'down') parts.push({ text: 'container engine down', tone: 'danger' });
  if (!parts.length && home) parts.push({ text: home.jobs.length ? 'Nothing needs you' : 'No jobs yet', tone: 'quiet' });
  return parts;
}

const PRIMARY_BY_STATE: Record<string, string> = { plan_review: 'approvePlan', review_done: 'push', needs_input: 'resume', failed: 'retry' };

/** The action a waiting job asks for (pinned on its Now row): the server's primary, else by state. */
export function decisiveAction(row: JobSummary): JobAction | undefined {
  const wanted = row.primary_action || PRIMARY_BY_STATE[row.state] || '';
  return (row.actions || []).find(a => a.id === wanted && a.enabled && !a.hidden) || (row.actions || []).find(a => a.primary && a.enabled && !a.hidden);
}
export const decisiveLabel = (row: JobSummary) => { const a = decisiveAction(row); return a ? actionLabel(a) : ''; };

/** Line 2 of a job row: the state in words, then at most two facts that change what you'd do. */
export function rowSentence(row: JobSummary): string {
  const status = stateStatus(row.state);
  const lead = row.limit?.summary ? 'Limit reached' : cap(stateLabel(row.state));
  const facts = [row.limit?.summary || (status === 'waiting' || status === 'failed' ? row.attention_detail : ''),
    row.verdict && status !== 'waiting' ? cap(verdictStyle(row.verdict)[1]) : '', repoName(row)].filter(Boolean).slice(0, 2);
  return [lead, ...facts].join(' · ');
}

/** A Now row: why it waits (limit, failure stage, detail) and the repository. */
export function needsSentence(row: JobSummary): string {
  const failed = stateStatus(row.state) === 'failed';
  const reason = row.limit?.summary || row.attention_summary || '';
  const lead = cap(reason ? 'limit reached' : failed && row.failed_from ? `failed in ${stateLabel(row.failed_from)}` : stateLabel(row.state));
  return [lead, reason || row.attention_detail || '', repoName(row)].filter(Boolean).join(' · ');
}

/** The Now board: needs-you oldest first, running, the 6 most recently finished. */
export function nowBoard(home: HomeVM) {
  const needs = home.needs_you.slice().sort((a, b) => (a.updated_at || 0) - (b.updated_at || 0));
  const busy = new Set([...home.needs_you, ...home.running].map(j => j.id));
  const finished = home.jobs.filter(j => !busy.has(j.id)).sort((a, b) => b.updated_at - a.updated_at).slice(0, 6);
  return { needs, running: home.running, finished };
}

/** Running row: the focus stage, its detail and the cost; with stages done of total. */
export function runningSentence(row: JobSummary): { text: string; done: number; total: number } {
  const steps = row.steps || [];
  const step = steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'active' || s.status === 'waiting');
  const text = [step?.label ? cap(step.label) : cap(stateLabel(row.state)), step?.detail || '', Number(row.cost_usd) ? money(row.cost_usd) : ''].filter(Boolean).join(' · ');
  return { text, done: steps.filter(s => s.status === 'done').length, total: steps.length };
}

/** Status sign for a job (one mark per status, from the vocabulary). */
export const jobSign = (row: Pick<JobSummary, 'state'>) => stateStatus(row.state);
export const jobStatusLabel = (row: Pick<JobSummary, 'state'>) => STATUS[stateStatus(row.state)].label;

/** Cost by day for the last `days` days (each job on its last active day); the board's trend. */
export function spendByDay(jobs: JobSummary[], now = Date.now() / 1000, days = 14): { buckets: number[]; week: number; today: number } {
  const DAY = 86400;
  const start = Math.floor(now / DAY) * DAY - (days - 1) * DAY;
  const buckets = Array.from({ length: days }, () => 0);
  for (const j of jobs) {
    const t = j.updated_at || j.created_at || 0, c = Number(j.cost_usd) || 0;
    if (!c || t < start) continue;
    buckets[Math.min(days - 1, Math.floor((t - start) / DAY))] += c;
  }
  return { buckets, week: buckets.slice(-7).reduce((a, b) => a + b, 0), today: buckets[days - 1] };
}
