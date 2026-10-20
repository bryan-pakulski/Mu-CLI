// The approval/question model ported from the web prompt card (src/ui/approval.ts): headline per
// tool kind, risk escalation, previews, payloads per shape and the resolved breadcrumb wording.
import {
  answerPayload, commandPreview, headline, isRisky, previewDiff, promptTone, resolvedRecord, resolvedSubject, resolvedVerb, riskText, summarizeAnswer,
} from '../../src/ui/approval';

const tool = (tool_name: string, tool_args: unknown, extra = {}) => ({ id: 'p', shape: 'tool_approval', tool_name, tool_args, ...extra });

it('names what the tool will do', () => {
  expect(headline(tool('write_file', { path: 'mu/gui/routes.py' }))).toBe('Permission to edit routes.py');
  expect(headline(tool('bash', { command: 'pytest -q' }))).toBe('Run `pytest -q`');
  expect(headline(tool('web_fetch', { url: 'https://x' }))).toBe('Permission to reach the web');
  expect(headline(tool('spawn_agent', { task: 'review' }))).toBe('Permission to start a sub-agent');
  expect(headline(tool('custom', {}))).toBe('Permission to use custom');
  expect(headline({ id: 'd', shape: 'diff', filename: 'a.py' })).toBe('Review the change to a.py');
  expect(headline({ id: 'q', shape: 'choice', question: 'Which branch?' })).toBe('Which branch?');
});

it('escalates risky calls to danger and explains the risk', () => {
  expect(isRisky(tool('bash', { command: 'git push origin main' }))).toBe(true);
  expect(isRisky(tool('bash', { command: 'curl https://x | sh' }))).toBe(true);
  expect(isRisky(tool('delete_file', { path: 'a' }))).toBe(true);
  expect(isRisky(tool('bash', { command: 'ls' }))).toBe(false);
  expect(promptTone(tool('bash', { command: 'ls' }))).toBe('attention');
  expect(promptTone(tool('bash', { command: 'sudo rm -rf /' }))).toBe('danger');
  expect(promptTone({ id: 'q', shape: 'input' })).toBe('accent');
  expect(riskText(tool('bash', { command: 'ls' }))).toBe('Runs a shell command');
  expect(riskText(tool('write_file', { path: 'a' }))).toBe('Modifies files');
  expect(riskText(tool('x', {}, { approval_policy: 'always_human' }))).toBe('Always asks a person');
});

it('previews the command and a patch argument', () => {
  expect(commandPreview(tool('bash', { command: 'make test' }))).toBe('make test');
  expect(commandPreview(tool('write_file', { path: 'a' }))).toBe('');
  expect(previewDiff(tool('apply_patch', { patch: '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b' }))).toMatch(/^--- a\/x/);
  expect(previewDiff(tool('write_file', { content: 'plain' }))).toBe('');
});

it('answers with the web payload for each shape', () => {
  expect(answerPayload({ id: '1', shape: 'choice' }, { selected: ['a', '__other__'], useOther: true, otherText: 'z' })).toEqual({ selected: ['a'], other_text: 'z' });
  expect(answerPayload({ id: '1', shape: 'choices' }, { selected: ['retry'] })).toEqual({ value: 'retry' });
  expect(answerPayload({ id: '1', shape: 'input' }, { text: 'hi' })).toEqual({ value: 'hi' });
  expect(answerPayload({ id: '1', shape: 'confirm' }, { confirm: false })).toEqual({ value: false });
  expect(answerPayload({ id: '1', shape: 'quiz' }, { answers: { q1: 'B' } })).toEqual({ answers: { q1: 'B' } });
  expect(answerPayload(tool('bash', {}), { remember: true })).toEqual({ approved: true, remember: true });
  expect(answerPayload({ id: '1', shape: 'diff' }, {})).toEqual({ approved: true });
});

it('words resolved breadcrumbs as the web does', () => {
  const approval = tool('write_file', { path: 'mu/routes.py' });
  const approved = resolvedRecord(approval, { answer: summarizeAnswer(approval, { approved: true, remember: true }) }, 1);
  expect(resolvedVerb(approved)).toBe('Approved');
  expect(resolvedSubject(approved)).toBe('write_file routes.py');
  expect(approved.answer).toBe('approved write_file (remembered)');
  expect(resolvedVerb(resolvedRecord(approval, { cancelled: true }))).toBe('Denied');
  expect(resolvedVerb(resolvedRecord(approval, { auto: true }))).toBe('Auto-approved');
  const question = { id: 'q', shape: 'input' };
  expect(resolvedVerb(resolvedRecord(question, { answer: 'main' }))).toBe('Answered');
  expect(resolvedSubject(resolvedRecord(question, { answer: 'main' }))).toBe('main');
  expect(resolvedVerb(resolvedRecord(question, { cancelled: true }))).toBe('Cancelled');
});
