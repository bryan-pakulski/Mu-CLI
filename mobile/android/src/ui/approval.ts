// Approval and question model, ported from the web's prompt card (static/js/app.js promptModal and
// the resolved-breadcrumb helpers). Pure: the inline card, the off-chat banner and the fleet chat
// read the same headline, risk, preview and payloads as the web.

import { kindOf, primaryArg } from './toolList';

export type PromptLike = {
  id: string;
  shape?: string;
  tool_name?: string;
  tool_args?: unknown;
  risk?: unknown;
  approval_policy?: unknown;
  question?: string;
  message?: string;
  description?: string;
  filename?: string;
  path?: string;
  diff?: string;
  default?: unknown;
  options?: unknown[];
  choices?: unknown[];
  multi_select?: boolean;
  allow_other?: boolean;
  session_name?: string;
  [key: string]: unknown;
};

export function isApproval(p: PromptLike): boolean {
  return p.shape === 'tool_approval' || p.shape === 'diff';
}

export function promptTitle(p: PromptLike): string {
  switch (p.shape) {
    case 'choice':
    case 'choices': return p.question || 'choose';
    case 'input': return 'input';
    case 'confirm': return 'confirm';
    case 'quiz': return 'quiz';
    case 'tool_approval': return 'approve tool call';
    case 'diff': return 'review change';
    default: return 'prompt';
  }
}

export function promptTarget(p: PromptLike): string {
  if (p.shape === 'diff') return String(p.filename || p.path || '');
  return primaryArg(p.tool_args);
}

export function promptKind(p: PromptLike) {
  return kindOf(p.tool_name);
}

/** Server risk, always-human policy, deletes/pushes, and shell commands that reach the network. */
export function isRisky(p: PromptLike): boolean {
  if (!isApproval(p)) return false;
  if (p.risk) return true;
  if (p.approval_policy === 'always_human') return true;
  const name = String(p.tool_name || '').toLowerCase();
  if (/(delete|remove|rm_|drop|push|force|destroy|kill)/.test(name)) return true;
  const command = p.shape === 'tool_approval' && promptKind(p) === 'shell' ? promptTarget(p) : '';
  return /\b(rm\s+-[a-z]*[rf]|git\s+push|curl|wget|ssh|scp|rsync|nc\s|sudo|mkfs|dd\s+if=|chmod\s+-R|docker\s+(rm|push))/i.test(command);
}

export function promptTone(p: PromptLike): 'danger' | 'attention' | 'accent' {
  if (isRisky(p)) return 'danger';
  return isApproval(p) ? 'attention' : 'accent';
}

export function chipLabel(p: PromptLike): string {
  return isApproval(p) ? 'Needs you' : 'Question';
}

/** The human sentence in the heading ("Permission to edit routes.py", "Run `pytest -q`"). */
export function headline(p: PromptLike): string {
  if (p.shape === 'diff') return `Review the change to ${promptTarget(p) || 'a file'}`;
  if (p.shape !== 'tool_approval') {
    if (p.shape === 'choice' || p.shape === 'choices') return p.question || p.message || 'Choose an option';
    if (p.shape === 'quiz') return 'Answer the questions';
    return p.message || p.question || p.description || promptTitle(p);
  }
  const tool = String(p.tool_name || 'a tool');
  const target = promptTarget(p);
  switch (promptKind(p)) {
    case 'edit': return target ? `Permission to edit ${target.split('/').pop()}` : 'Permission to edit files';
    case 'shell': return target ? `Run \`${target.length > 80 ? `${target.slice(0, 79)}…` : target}\`` : 'Permission to run a command';
    case 'web': return 'Permission to reach the web';
    case 'agent': return 'Permission to start a sub-agent';
    default: return `Permission to use ${tool}`;
  }
}

export function announcement(p: PromptLike): string {
  if (isApproval(p)) return `Mucli needs your approval: ${headline(p).replace(/`/g, '')}`;
  return `Mucli has a question: ${String(headline(p) || '').slice(0, 160)}`;
}

/** The prompt's own diff, else a patch argument (web previewDiff without the chat diff store). */
export function previewDiff(p: PromptLike, recentDiff?: (target: string) => string | null): string {
  if (p.shape === 'diff') return String(p.diff || '');
  const args = p.tool_args;
  if (args && typeof args === 'object') {
    const rec = args as Record<string, unknown>;
    for (const key of ['patch', 'diff', 'unified_diff']) {
      if (typeof rec[key] === 'string' && /^(---|\+\+\+|@@|diff --git)/m.test(rec[key] as string)) return rec[key] as string;
    }
  }
  return (recentDiff && recentDiff(promptTarget(p))) || '';
}

export function commandPreview(p: PromptLike): string {
  return p.shape === 'tool_approval' && promptKind(p) === 'shell' ? promptTarget(p) : '';
}

export function riskText(p: PromptLike): string {
  if (p.risk) return String(p.risk);
  if (p.approval_policy === 'always_human') return 'Always asks a person';
  if (isRisky(p)) return 'High-risk action';
  if (promptKind(p) === 'edit') return 'Modifies files';
  if (promptKind(p) === 'shell') return 'Runs a shell command';
  return '';
}

export function okLabel(p: PromptLike): string {
  switch (p.shape) {
    case 'tool_approval':
    case 'diff': return 'Approve';
    case 'confirm': return 'Yes';
    default: return 'OK';
  }
}

export function formatArgs(args: unknown): string {
  if (args === undefined || args === null) return '(no arguments)';
  if (typeof args === 'string') return args;
  try { return JSON.stringify(args, null, 2); } catch { return String(args); }
}

export function optLabel(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o && typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (typeof rec.label === 'string') return rec.label;
    if (typeof rec.name === 'string') return rec.name;
  }
  try { return JSON.stringify(o); } catch { return String(o); }
}

export function optValue(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o && typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (rec.value !== undefined) return String(rec.value);
    if (typeof rec.id === 'string') return rec.id;
    if (typeof rec.label === 'string') return rec.label;
  }
  return String(o);
}

/** Quiz options submit the option string, as the TUI and the web do. */
export function quizOptionValue(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o == null) return '';
  if (typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (rec.value !== undefined) return String(rec.value);
    if (rec.label !== undefined) return String(rec.label);
  }
  return String(o);
}

export type AnswerState = { selected?: string[]; otherText?: string; useOther?: boolean; text?: string; answers?: Record<string, string>; remember?: boolean; confirm?: boolean };

/** The POST /api/prompts/{id}/answer payload for a shape (unchanged from the web). */
export function answerPayload(p: PromptLike, s: AnswerState): Record<string, unknown> {
  switch (p.shape) {
    case 'choice':
    case 'choices': {
      const real = (s.selected || []).filter(v => v !== '__other__');
      const other = s.useOther ? (s.otherText || '') : '';
      // `choices` (runtime recovery picker) expects one scalar value.
      return p.shape === 'choices' ? { value: real[0] || other } : { selected: real, other_text: other };
    }
    case 'input': return { value: s.text ?? '' };
    case 'confirm': return { value: s.confirm !== false };
    case 'quiz': return { answers: s.answers || {} };
    case 'tool_approval': return { approved: true, remember: !!s.remember };
    case 'diff': return { approved: true };
    default: return { value: s.text ?? true };
  }
}

export function summarizeAnswer(p: PromptLike, payload: Record<string, unknown>): string {
  switch (p.shape) {
    case 'choice':
    case 'choices': {
      if (p.shape === 'choices') return String(payload.value || '(none)');
      const parts = [...((payload.selected as string[]) || [])];
      if (payload.other_text) parts.push(String(payload.other_text));
      return parts.length ? parts.join(', ') : '(none)';
    }
    case 'input': return String(payload.value || '');
    case 'confirm': return payload.value === false ? 'no' : 'yes';
    case 'quiz': {
      const entries = Object.entries((payload.answers as Record<string, string>) || {});
      return entries.length ? entries.map(([k, v]) => `${k}: ${v}`).join('; ') : '(no answers)';
    }
    case 'tool_approval': return `approved${p.tool_name ? ` ${p.tool_name}` : ''}${payload.remember ? ' (remembered)' : ''}`;
    case 'diff': return 'approved';
    default: return String(payload.value || '');
  }
}

/** A resolved prompt in the transcript (web .w-resolved). */
export type ResolvedPrompt = {
  id: string;
  shape: string;
  toolName: string;
  target: string;
  answer: string;
  cancelled: boolean;
  auto: boolean;
  at: number;
};

export function resolvedRecord(p: PromptLike, outcome: { cancelled?: boolean; auto?: boolean; answer?: string }, at = Date.now()): ResolvedPrompt {
  return {
    id: p.id, shape: p.shape || '', toolName: p.tool_name || '', target: promptTarget(p),
    answer: outcome.answer || '', cancelled: !!outcome.cancelled, auto: !!outcome.auto, at,
  };
}

export function resolvedVerb(r: ResolvedPrompt): string {
  if (r.shape === 'tool_approval' || r.shape === 'diff') {
    if (r.cancelled) return 'Denied';
    return r.auto ? 'Auto-approved' : 'Approved';
  }
  return r.cancelled ? 'Cancelled' : 'Answered';
}

export function resolvedSubject(r: ResolvedPrompt): string {
  if (r.shape === 'tool_approval' || r.shape === 'diff') {
    const target = String(r.target || '').split('/').pop();
    return [r.toolName || (r.shape === 'diff' ? 'change' : 'tool'), target].filter(Boolean).join(' ');
  }
  return r.answer;
}
