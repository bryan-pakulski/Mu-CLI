// Pure helpers for the job page (ported from the web jobs mode,
// mu/gui/static/js/work/view.js): unified-diff parsing, session transcript
// items, check totals and the default tab. Tested in __tests__/job-views.test.ts.

import type { AuditEvent, CheckRun, CheckSuite, JobPR, JobVM } from '../api/jobCore';
import { runInEnvironmentSummary } from '../api/environments';
import { duration, money, stripAnsi } from '../theme/status';

export type DiffLine = { kind: 'hunk' | 'add' | 'del' | 'ctx' | 'note'; text: string; oldNo?: number; newNo?: number };
export type DiffFile = { path: string; status: 'A' | 'M' | 'D' | 'R'; lines: DiffLine[]; added: number; removed: number; binary: boolean };

export const FILE_STATUS_LABEL: Record<DiffFile['status'], string> = { A: 'added', M: 'modified', D: 'deleted', R: 'renamed' };

export function parsePatch(patch: string | null | undefined): DiffFile[] {
  const files: DiffFile[] = [];
  for (const block of String(patch || '').split(/(?=^diff --git )/m).filter(b => b.trim())) {
    const path = block.match(/^\+\+\+ b\/(.*)$/m)?.[1] || block.match(/^--- a\/(.*)$/m)?.[1] || block.match(/^diff --git a\/(\S+)/)?.[1] || 'diff';
    const status: DiffFile['status'] = /^new file mode/m.test(block) ? 'A' : /^deleted file mode/m.test(block) ? 'D' : /^rename from/m.test(block) ? 'R' : 'M';
    const lines: DiffLine[] = [];
    let oldNo = 0, newNo = 0, added = 0, removed = 0, inHunk = false;
    for (const line of block.split('\n')) {
      const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      if (hunk) { oldNo = Number(hunk[1]); newNo = Number(hunk[2]); inHunk = true; lines.push({ kind: 'hunk', text: line }); continue; }
      if (!inHunk) continue;
      if (line.startsWith('+')) { lines.push({ kind: 'add', text: line.slice(1), newNo: newNo++ }); added++; }
      else if (line.startsWith('-')) { lines.push({ kind: 'del', text: line.slice(1), oldNo: oldNo++ }); removed++; }
      else if (line.startsWith('\\')) lines.push({ kind: 'note', text: line });
      else if (line.startsWith(' ') || line === '') {
        if (line === '' && lines.length && lines[lines.length - 1].kind === 'note') continue;
        lines.push({ kind: 'ctx', text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
      }
    }
    while (lines.length && lines[lines.length - 1].kind === 'ctx' && lines[lines.length - 1].text === '') lines.pop();
    files.push({ path, status, lines, added, removed, binary: /^Binary files/m.test(block) });
  }
  return files;
}

export type SessionItem =
  | { kind: 'assistant' | 'user' | 'sys' | 'error' | 'workbench'; text: string }
  | { kind: 'tool' | 'tool_result'; summary: string; text: string };

/** Transcript items for the live events (stage '') or a stored stage transcript. */
export function sessionItems(session: JobVM['session'] | undefined, stage: string): SessionItem[] {
  const items: SessionItem[] = [];
  if (!session) return items;
  if (stage) {
    for (const m of session.stages?.[stage] || []) for (const p of m.parts || []) {
      if (p.type === 'text' && p.text) items.push({ kind: m.role === 'user' ? 'user' : 'assistant', text: p.text });
      else if (p.type === 'tool_call') {
        const args = typeof p.tool_args === 'string' ? p.tool_args : JSON.stringify(p.tool_args ?? '');
        // Commands in another build environment fold to `› [node-build] npm test`.
        const folded = p.tool_name === 'run_in_environment' ? runInEnvironmentSummary(p.tool_args) : null;
        items.push({ kind: 'tool', summary: folded || `${p.tool_name}(${String(args || '').slice(0, 120)})`, text: `${p.tool_name}\n${args || ''}` });
      } else if (p.type === 'tool_result') {
        const result = typeof p.tool_result === 'string' ? p.tool_result : JSON.stringify(p.tool_result ?? '');
        items.push({ kind: 'tool_result', summary: `${p.tool_name || 'result'}: ${String(result || '').split('\n')[0].slice(0, 160)}`, text: String(result || '') });
      }
    }
    return items;
  }
  for (const e of session.events || []) {
    const payload = (e.payload || {}) as Record<string, any>;
    const inner = (payload.payload || {}) as Record<string, any>;
    const kind = payload.event || e.event_type;
    const st = payload.stage;
    if (e.event_type === 'sandbox_starting') {
      const agent = String(e.payload?.agent || '');
      const model = String(e.payload?.agent_model || '');
      const who = agent && agent !== 'mucli' ? ` · ${agent === 'claude' ? 'Claude Code' : 'Codex'}${model ? ` ${model}` : ''}` : '';
      items.push({ kind: 'workbench', text: `sandbox started${st ? ` · ${st}` : ''}${who}` });
    }
    else if (e.event_type === 'sandbox_stopped') items.push({ kind: 'workbench', text: `sandbox stopped${st ? ` · ${st}` : ''}` });
    else if (e.event_type === 'runtime_error' || kind === 'runtime_error') items.push({ kind: 'error', text: stripAnsi(inner.text || e.reason || 'runtime error') });
    else if (kind === 'agent_message') items.push({ kind: inner.role === 'user' ? 'user' : 'assistant', text: inner.text || '' });
    else if (kind === 'tool_call_ui') {
      const folded = inner.tool_name === 'run_in_environment' ? runInEnvironmentSummary(inner.tool_args) : null;
      items.push({ kind: 'tool', summary: folded || inner.tool_name || 'tool', text: JSON.stringify(inner, null, 2) });
    }
    else if (kind === 'tool_result_ui') items.push({ kind: 'tool_result', summary: String(inner.preview || '').split('\n').find(Boolean)?.slice(0, 160) || 'result', text: String(inner.preview || '') });
    else if (kind === 'runtime_status') continue;
    // Build environments: run_in_environment calls and snapshot preparation.
    else if (e.event_type === 'environment_command_started') items.push({ kind: 'tool', summary: runInEnvironmentSummary(payload) || 'run_in_environment', text: JSON.stringify(payload, null, 2) });
    else if (e.event_type === 'environment_command') {
      const status = payload.timed_out ? 'timed out' : `exit ${payload.return_code ?? '?'}`;
      items.push({ kind: 'tool_result', summary: `[${payload.env || '?'}] ${status}${payload.duration_ms != null ? ` · ${Math.round(Number(payload.duration_ms) / 100) / 10}s` : ''}`, text: JSON.stringify(payload, null, 2) });
    }
    else if (['build_environments_preparing', 'build_environment_progress', 'build_environments_recreated'].includes(e.event_type)) items.push({ kind: 'workbench', text: e.reason || e.event_type.replace(/_/g, ' ') });
    else if (e.event_type === 'build_environment_ready') items.push({ kind: 'workbench', text: `build environment ${payload.binding || ''} ready` });
    else if (e.event_type === 'build_environment_failed') items.push({ kind: 'error', text: `build environment${payload.binding ? ` ${payload.binding}` : ''} failed: ${e.reason || ''}` });
    else if (e.event_type === 'stage_message') items.push({ kind: 'assistant', text: inner.text || payload.text || e.reason || '' });
    else items.push({ kind: 'sys', text: stripAnsi(inner.text || payload.text || e.reason || kind) });
  }
  return items;
}

/** Latest runtime_status line (shown next to "live session"). */
export function runtimeStatus(events: AuditEvent[] | undefined): string {
  const e = [...(events || [])].reverse().find(x => (x.payload as Record<string, any>)?.event === 'runtime_status');
  return stripAnsi(((e?.payload as Record<string, any>)?.payload?.text as string) || '');
}

export type Counts = { passed: number; failed: number; skipped: number; total: number };
export function suiteCounts(s: CheckSuite): Counts {
  const j = s.junit;
  return j ? { passed: j.passed || 0, failed: j.failed || 0, skipped: j.skipped || 0, total: j.total || 0 }
    : { passed: s.passed ? 1 : 0, failed: s.passed ? 0 : 1, skipped: 0, total: 1 };
}
export function runCounts(r: CheckRun): Counts {
  return (r.suites || []).map(suiteCounts).reduce((a, c) => ({ passed: a.passed + c.passed, failed: a.failed + c.failed, skipped: a.skipped + c.skipped, total: a.total + c.total }), { passed: 0, failed: 0, skipped: 0, total: 0 });
}
export const listOf = (x: unknown): string[] => (Array.isArray(x) ? x.map(v => (typeof v === 'string' ? v : JSON.stringify(v))) : []);
export const countOf = (x: unknown): number => (Array.isArray(x) ? x.length : Number(x || 0));

export const JOB_TABS = ['session', 'plan', 'diff', 'review', 'checks', 'diagnostics', 'costs'] as const;
export type JobTab = typeof JOB_TABS[number];
export const JOB_TAB_LABELS: Record<JobTab, string> = { session: 'Session', plan: 'Plan', diff: 'Diff', review: 'Review', checks: 'Checks', diagnostics: 'Diagnostics', costs: 'Cost & audit' };
/** Pipeline step → tab. */
export const STEP_TABS: Record<string, JobTab> = { plan: 'plan', approve: 'plan', implement: 'session', checks: 'checks', review: 'review', push: 'review' };

/** Wash default tab for a job's state. */
export function defaultTab(job: Pick<JobVM, 'state' | 'session'>): JobTab {
  if (job.session?.approval) return 'session';
  if (job.state === 'plan_review') return 'plan';
  if (['review_done', 'pushed', 'done'].includes(job.state)) return 'review';
  if (job.state === 'checking') return 'checks';
  if (job.state === 'failed') return 'diagnostics';
  return 'session';
}

/** Audit event → tone. */
export function eventTone(e: string): 'danger' | 'ok' | 'warn' | 'accent' {
  return /(fail|error|denied|reject|cancel|discard)/i.test(e) ? 'danger' : /(approv|done|pass|push|merged|complete)/i.test(e) ? 'ok' : /(edit|input|wait|needs|human|attention)/i.test(e) ? 'warn' : 'accent';
}


export type BudgetSummary = { cost: string; costLabel: string; time: string; timeLabel: string; tone: 'danger' | 'warn' | '' };

/** Job header: running cost vs the cap ("no cap" when unlimited) and worker-active time. */
export function budgetSummary(job: Pick<JobVM, 'cost_usd' | 'budget' | 'max_cost_usd'>): BudgetSummary {
  const b = job.budget;
  const cost = b ? b.cost_usd : Number(job.cost_usd || 0);
  const cap = b ? b.max_cost_usd : job.max_cost_usd ?? null;
  const fraction = cap ? cost / cap : null;
  const costText = cap ? `${money(cost)} / ${money(cap)}` : `${money(cost)} · no cap`;
  const active = b ? b.active_seconds : null;
  const limit = b?.max_runtime_seconds ?? null;
  const time = active == null ? '' : `active ${duration(active * 1000)}${limit ? ` / ${duration(limit * 1000)}` : ''}`;
  return {
    cost: costText,
    costLabel: cap ? `agent cost ${money(cost)} of ${money(cap)} cap` : `agent cost ${money(cost)}, no cost cap`,
    time,
    timeLabel: time ? `active time ${duration((active || 0) * 1000)}` : '',
    tone: fraction == null ? '' : fraction >= 1 ? 'danger' : fraction >= 0.8 ? 'warn' : '',
  };
}

export type PrPillView = { label: string; tone: 'ok' | 'danger' | 'accent'; url: string; accessibilityLabel: string };

/** PR pill (web view.js header parity): "PR #12", "PR #12 · merged"; merged = ok, closed = danger.
 * Only http(s) URLs are offered as links; null when the job has no PR. */
export function prPill(pr: JobPR | null | undefined): PrPillView | null {
  if (!pr || !(pr.url || pr.number)) return null;
  const state = String(pr.state || '');
  const label = `PR${pr.number ? ` #${pr.number}` : ''}${pr.draft && (!state || state === 'open') ? ' · draft' : ''}${state && state !== 'open' ? ` · ${state}` : ''}`;
  const url = /^https?:\/\//i.test(pr.url || '') ? String(pr.url) : '';
  return {
    label,
    tone: state === 'merged' ? 'ok' : state === 'closed' ? 'danger' : 'accent',
    url,
    accessibilityLabel: `Pull request${pr.number ? ` ${pr.number}` : ''}${state ? `, ${state}` : ''}${url ? ', opens in browser' : ''}`,
  };
}
