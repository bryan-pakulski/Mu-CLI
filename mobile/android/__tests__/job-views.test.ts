import { darkColors, lightColors } from '../src/theme/tokens';
import { parsePatch, sessionItems, defaultTab, runCounts } from '../src/components/jobViews';
import { stateStatus, stateLabel, STATUS } from '../src/theme/status';
import { filterJobs } from '../src/screens/JobsListScreen';
import { appendSamples } from '../src/screens/JobActivityScreen';
import type { JobSummary, JobVM } from '../src/api/jobCore';

const PATCH = [
  'diff --git a/src/a.py b/src/a.py', 'index 1..2 100644', '--- a/src/a.py', '+++ b/src/a.py',
  '@@ -1,3 +1,3 @@', ' keep', '-old', '+new', ' tail',
  'diff --git a/src/b.py b/src/b.py', 'new file mode 100644', '--- /dev/null', '+++ b/src/b.py',
  '@@ -0,0 +1,2 @@', '+one', '+two', '',
].join('\n');

it('parses a unified diff into files with status letters, counts and line numbers', () => {
  const files = parsePatch(PATCH);
  expect(files.map(f => [f.path, f.status, f.added, f.removed])).toEqual([['src/a.py', 'M', 1, 1], ['src/b.py', 'A', 2, 0]]);
  expect(files[0].lines.find(l => l.kind === 'add')).toMatchObject({ text: 'new', newNo: 2 });
  expect(parsePatch(null)).toEqual([]);
});

it('folds tool calls to one summary line and keeps stage transcripts', () => {
  const session = {
    live: true, approval: null,
    events: [
      { id: 1, event_type: 'sandbox_event', reason: '', created_at: 1, payload: { event: 'tool_call_ui', payload: { tool_name: 'read_file', path: 'a' } } },
      { id: 2, event_type: 'sandbox_event', reason: '', created_at: 2, payload: { event: 'runtime_status', payload: { text: 'thinking' } } },
      { id: 3, event_type: 'sandbox_event', reason: '', created_at: 3, payload: { event: 'agent_message', payload: { role: 'assistant', text: 'Done' } } },
    ],
    stages: { plan: [{ role: 'user', parts: [{ type: 'text', text: 'Plan it' }, { type: 'tool_call', tool_name: 'grep', tool_args: '{"q":"x"}' }] }] },
  } as unknown as JobVM['session'];
  const live = sessionItems(session, '');
  expect(live.map(i => i.kind)).toEqual(['tool', 'assistant']);
  expect(live[0]).toMatchObject({ summary: 'read_file' });
  const plan = sessionItems(session, 'plan');
  expect(plan[0]).toEqual({ kind: 'user', text: 'Plan it' });
  expect(plan[1]).toMatchObject({ kind: 'tool', summary: 'grep({"q":"x"})' });
});

it('maps job states onto the planos status vocabulary and default tabs', () => {
  expect(stateStatus('implementing')).toBe('active');
  expect(stateStatus('plan_review')).toBe('waiting');
  expect(STATUS[stateStatus('failed')].icon).toBe('✗');
  expect(stateLabel('review_done')).toBe('review done');
  expect(defaultTab({ state: 'plan_review' } as JobVM)).toBe('plan');
  expect(defaultTab({ state: 'checking' } as JobVM)).toBe('checks');
  expect(defaultTab({ state: 'failed' } as JobVM)).toBe('diagnostics');
  expect(defaultTab({ state: 'implementing', session: { approval: { tool_name: 'x', tool_args: {} } } } as unknown as JobVM)).toBe('session');
  expect(runCounts({ run: 1, passed: false, head_sha: '', new_failures: 0, preexisting_failures: 0, suites: [
    { suite: 'u', command: '', passed: false, stdout: '', stderr: '', preexisting_failures: [], junit: { passed: 3, failed: 1, skipped: 1, total: 5 } },
    { suite: 'lint', command: '', passed: true, stdout: '', stderr: '', preexisting_failures: [] },
  ] })).toEqual({ passed: 4, failed: 1, skipped: 1, total: 6 });
});

it('filters the jobs list by text and by "only jobs that need me"', () => {
  const row = (id: string, state: string, title: string) => ({ id, key: '', title, state, repository: 'repo', actions: [] } as unknown as JobSummary);
  const rows = [row('a1', 'implementing', 'Fix login'), row('b2', 'plan_review', 'Add cache'), row('c3', 'failed', 'Fix cache')];
  expect(filterJobs(rows, 'cache', false).map(r => r.id)).toEqual(['b2', 'c3']);
  expect(filterJobs(rows, '', true).map(r => r.id)).toEqual(['b2', 'c3']);
  expect(filterJobs(rows, 'login', true)).toEqual([]);
});

it('keeps a bounded CPU/memory sample history per container and treats null as no sample', () => {
  const c = (name: string, cpu: number | null) => ({ name, id: name, image: 'i', status: 'running', stage: '', started_at: null, cpu_percent: cpu, mem_bytes: cpu == null ? null : 100, mem_limit: 1000 });
  let s = appendSamples({}, [c('a', null)], 3);
  s = appendSamples(s, [c('a', 10), c('b', 5)], 3);
  s = appendSamples(s, [c('a', 20)], 3);
  s = appendSamples(s, [c('a', 30)], 3);
  expect(s.a.cpu).toEqual([10, 20, 30]);
  expect(s.b).toBeUndefined();
});

/* WCAG 2.2 contrast of the planos palette (text tokens must reach 4.5:1). */
function lum(hex: string) {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

it.each([['dark', darkColors], ['light', lightColors]] as const)('%s palette text tokens are >= 4.5:1 on bg, surface and raised', (_name, colors) => {
  for (const fg of [colors.text, colors.muted, colors.accent, colors.ok, colors.warn, colors.danger, colors.running]) {
    for (const bg of [colors.bg, colors.surface, colors.raised]) expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  }
  expect(ratio(colors.accentOn, colors.accent)).toBeGreaterThanOrEqual(4.5);
});

it('uses the planos palette from CONTRACT §8', () => {
  expect([darkColors.accent, darkColors.ok, darkColors.warn, darkColors.danger, darkColors.running, darkColors.muted, darkColors.text, darkColors.surface, darkColors.border, darkColors.bg, darkColors.raised, darkColors.hover])
    .toEqual(['#7aa2f7', '#73c991', '#e0af68', '#f7768e', '#7dcfff', '#8b93b8', '#c0caf5', '#1a1b26', '#2f3549', '#16161e', '#1f2335', '#292e42']);
  expect([lightColors.accent, lightColors.ok, lightColors.warn, lightColors.danger, lightColors.running, lightColors.muted, lightColors.text, lightColors.surface, lightColors.border, lightColors.bg, lightColors.raised, lightColors.hover])
    .toEqual(['#2f5bd3', '#1a7f37', '#9a6700', '#cf222e', '#0969da', '#57606a', '#1f2328', '#ffffff', '#d0d7de', '#f6f8fa', '#ffffff', '#eaeef2']);
});

test('session timeline names Claude Code / Codex sandboxes and harness tool results', () => {
  const session = {
    events: [
      { id: 1, event_type: 'sandbox_starting', payload: { stage: 'plan', agent: 'claude', agent_model: 'claude-opus-5-5' } },
      { id: 2, event_type: 'sandbox_starting', payload: { stage: 'review', agent: 'mucli' } },
    ],
    stages: { plan: [{ role: 'tool', parts: [{ type: 'tool_result', tool_call_id: 't1', tool_result: 'ok' }] }] },
  } as unknown as JobVM['session'];
  const live = sessionItems(session, '');
  expect(live.map(i => ('text' in i ? i.text : ''))).toEqual(['sandbox started · plan · Claude Code claude-opus-5-5', 'sandbox started · review']);
  expect(sessionItems(session, 'plan')[0]).toMatchObject({ kind: 'tool_result', summary: 'result: ok' });
});
