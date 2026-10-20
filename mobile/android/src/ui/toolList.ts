// The tool-list model, ported from the web's wash/toolcard.js (buildToolCards, groupCards,
// turnSummary, kindOf, primaryArg). Pure TypeScript, no React: chat, the fleet chat and the
// sub-agent panel fold and group tool calls exactly as the web does. A parity test runs the web
// file and this module over the same events (__tests__/chat/tool-list-parity.test.ts).
//
//   buildToolCards(events, {running})  pair tool_call/tool_result, thinking rows, notes; group 3+
//                                      consecutive finished same-kind calls
//   turnSummary(items)                 "Read 3 files · ran 2 commands" (+ failed count)
//   cardFromCall(call)                 a mobile ToolCallView as a card (already paired)

export type ToolListKind = 'read' | 'edit' | 'shell' | 'search' | 'web' | 'agent' | 'memory' | 'plan' | 'container' | 'tool';

export const TOOL_KINDS: Record<ToolListKind, { icon: string; label: string; noun: [string, string]; verb: string }> = {
  read: { icon: 'file-text', label: 'read', noun: ['file', 'files'], verb: 'Read' },
  edit: { icon: 'pencil', label: 'edit', noun: ['file', 'files'], verb: 'Edited' },
  shell: { icon: 'terminal', label: 'shell', noun: ['command', 'commands'], verb: 'Ran' },
  search: { icon: 'search', label: 'search', noun: ['search', 'searches'], verb: 'Ran' },
  web: { icon: 'globe', label: 'web', noun: ['lookup', 'lookups'], verb: 'Made' },
  agent: { icon: 'bot', label: 'sub-agent', noun: ['task', 'tasks'], verb: 'Delegated' },
  memory: { icon: 'brain', label: 'memory', noun: ['memory call', 'memory calls'], verb: 'Made' },
  plan: { icon: 'list-checks', label: 'plan', noun: ['plan update', 'plan updates'], verb: 'Made' },
  container: { icon: 'box', label: 'container', noun: ['container call', 'container calls'], verb: 'Made' },
  tool: { icon: 'wrench', label: 'tool', noun: ['tool call', 'tool calls'], verb: 'Made' },
};

export function kindOf(name: unknown): ToolListKind {
  const n = String(name || '').toLowerCase();
  if (/^(read_file|read_files|get_chunk|list_dir|list_files|view_file|open_file|cat|tree|read_cached|get_file)/.test(n)) return 'read';
  if (/(write_file|apply_patch|edit_file|replace_in_file|create_file|delete_file|move_file|str_replace|insert_text|patch)/.test(n)) return 'edit';
  if (/^(bash|shell|run_command|run_shell|execute|exec|terminal|run_tests|python)/.test(n)) return 'shell';
  if (/(search_for_string|grep|glob|find_files|search_code|codebase_search|ripgrep|search_files|find_symbol)/.test(n)) return 'search';
  if (/(web_search|fetch_url|web_fetch|browse|http_get|download_url|fetch_page)/.test(n)) return 'web';
  if (/(spawn_agent|delegate|subagent|sub_agent)/.test(n)) return 'agent';
  if (/(memory|remember|recall|forget)/.test(n)) return 'memory';
  if (/(plan|todo|goal|backlog|checklist)/.test(n)) return 'plan';
  if (/(container|docker)/.test(n)) return 'container';
  return 'tool';
}

export function parseArgs(args: unknown): unknown {
  if (args == null) return null;
  if (typeof args === 'object') return args;
  const text = String(args).trim();
  if (text.charAt(0) === '{' || text.charAt(0) === '[') {
    try { return JSON.parse(text); } catch { return text; }
  }
  return text;
}

const PRIMARY_KEYS = ['path', 'file_path', 'filepath', 'filename', 'file', 'paths', 'files', 'command', 'cmd',
  'query', 'pattern', 'search', 'url', 'urls', 'task', 'goal', 'name', 'key', 'title', 'text'];
const SEARCH_KEYS = ['query', 'pattern', 'search', 'regex', 'text'];

/** The primary argument (path, command, "query") shown on the collapsed row. */
export function primaryArg(args: unknown, kind?: ToolListKind): string {
  const parsed = parseArgs(args);
  if (kind === 'search' && parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const rec = parsed as Record<string, unknown>;
    for (const key of SEARCH_KEYS) {
      if (typeof rec[key] === 'string' && rec[key]) return `"${rec[key]}"`;
    }
  }
  if (parsed == null) return '';
  if (typeof parsed !== 'object') return String(parsed);
  if (Array.isArray(parsed)) return parsed.map(String).join(', ');
  const rec = parsed as Record<string, unknown>;
  for (const key of PRIMARY_KEYS) {
    const value = rec[key];
    if (value == null || value === '') continue;
    if (Array.isArray(value)) return value.map(String).join(', ');
    if (typeof value !== 'object') return String(value);
  }
  for (const key of Object.keys(rec)) {
    const v = rec[key];
    if (typeof v === 'string' && v) return v;
  }
  return '';
}

export function secondaryMeta(kind: ToolListKind, args: unknown): string {
  const parsed = parseArgs(args);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '';
  const p = parsed as Record<string, unknown>;
  const start = p.start_line != null ? p.start_line : (p.line_start != null ? p.line_start : p.offset);
  let end: unknown = p.end_line != null ? p.end_line : (p.line_end != null ? p.line_end : null);
  if (end == null && p.limit != null && start != null) end = Number(start) + Number(p.limit);
  if (kind === 'read' && start != null) return `lines ${start}${end != null ? `–${end}` : '+'}`;
  if (kind === 'search' && (p.path || p.directory || p.dir)) return `in ${p.path || p.directory || p.dir}`;
  return '';
}

export function middleTruncate(text: unknown, max: number): string {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  if (s.length <= max) return s;
  const head = Math.ceil((max - 1) * 0.6);
  const tail = max - 1 - head;
  return s.slice(0, head) + '…' + s.slice(s.length - tail);
}

export function formatDuration(ms: number): string {
  if (!(ms >= 0)) return '';
  if (ms < 1000) return (Math.max(ms, 100) / 1000).toFixed(1) + 's';
  const seconds = ms / 1000;
  if (seconds < 60) return seconds.toFixed(1) + 's';
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds % 60)}s`;
}

export function isErrorResult(text: unknown): boolean {
  const head = String(text || '').slice(0, 400);
  return /^\s*(error|failed|traceback)\b/i.test(head) || /"ok"\s*:\s*false/.test(head) || /"error_code"\s*:/.test(head);
}

/** A trace event (web: app.js addToolCall/addToolResult and history hydration). */
export type ToolEvent = {
  kind: 'tool_call' | 'tool_result' | 'thinking' | 'info' | string;
  id?: string;
  name?: string;
  args?: unknown;
  at?: number;
  text?: string;
  rawText?: string;
  planBlocked?: boolean;
  cacheKey?: string;
  [key: string]: unknown;
};

export type CardState = 'running' | 'done' | 'settled' | 'error' | 'blocked';

export type ToolCardItem = {
  type: 'card';
  id: string;
  name: string;
  kind: ToolListKind;
  args: unknown;
  target: string;
  meta: string;
  startedAt: number;
  doneAt?: number;
  duration?: number;
  state: CardState;
  resultText?: string;
  cacheKey?: string;
  hasResult: boolean;
};

export type ToolGroupItem = { type: 'group'; id: string; kind: ToolListKind; cards: ToolCardItem[] };
export type ThinkingItem = { type: 'thinking'; id: string; text: string; live: boolean };
export type NoteItem = { type: 'note'; id: string; text: string };
export type ToolListItem = ToolCardItem | ToolGroupItem | ThinkingItem | NoteItem;

function resultText(ev: ToolEvent): string {
  if (typeof ev.rawText === 'string') return ev.rawText;
  if (typeof ev.text === 'string') return ev.text;
  return '';
}

export function buildToolCards(events: ToolEvent[] | null | undefined, options: { running?: boolean } = {}): ToolListItem[] {
  const running = !!options.running;
  const items: Array<ToolCardItem | ThinkingItem | NoteItem> = [];
  const pending: ToolCardItem[] = [];
  const list = Array.isArray(events) ? events : [];
  list.forEach((ev, i) => {
    if (!ev) return;
    if (ev.kind === 'tool_call') {
      const kind = kindOf(ev.name);
      const card: ToolCardItem = {
        type: 'card', id: String(ev.id || `tc-${i}`), name: ev.name || 'tool', kind, args: ev.args,
        target: primaryArg(ev.args, kind), meta: secondaryMeta(kind, ev.args), startedAt: Number(ev.at) || 0,
        state: 'running', hasResult: false,
      };
      items.push(card);
      pending.push(card);
    } else if (ev.kind === 'tool_result') {
      let match: ToolCardItem | null = null;
      for (let p = 0; p < pending.length; p += 1) {
        if (!ev.name || pending[p].name === ev.name) { match = pending[p]; pending.splice(p, 1); break; }
      }
      if (!match) {
        // Orphan result (history page boundary): its own card.
        match = { type: 'card', id: String(ev.id || `tr-${i}`), name: ev.name || 'tool', kind: kindOf(ev.name), args: null, target: '', meta: '', startedAt: 0, state: 'running', hasResult: false };
        items.push(match);
      }
      const text = resultText(ev);
      match.hasResult = true;
      match.resultText = text;
      match.cacheKey = ev.cacheKey || '';
      match.doneAt = Number(ev.at) || 0;
      match.state = ev.planBlocked ? 'blocked' : (isErrorResult(text) ? 'error' : 'done');
    } else if (ev.kind === 'thinking') {
      items.push({ type: 'thinking', id: String(ev.id || `th-${i}`), text: String(ev.text || ''), live: false });
    } else if (ev.kind === 'info') {
      items.push({ type: 'note', id: String(ev.id || `in-${i}`), text: String(ev.text || '') });
    }
  });
  if (!running) items.forEach(item => { if (item.type === 'card' && item.state === 'running') item.state = 'settled'; });
  items.forEach((item, t) => { if (item.type === 'thinking') item.live = running && t === items.length - 1; });
  return groupCards(items);
}

/** 3+ consecutive finished same-kind cards become one group. */
export function groupCards(items: ToolListItem[]): ToolListItem[] {
  const out: ToolListItem[] = [];
  let run: ToolCardItem[] = [];
  const flush = () => {
    if (run.length >= 3) out.push({ type: 'group', id: `g:${run[0].id}`, kind: run[0].kind, cards: run.slice() });
    else out.push(...run);
    run = [];
  };
  items.forEach(item => {
    const groupable = item.type === 'card' && item.state !== 'running';
    if (groupable && run.length && run[0].kind === (item as ToolCardItem).kind) { run.push(item as ToolCardItem); return; }
    flush();
    if (groupable) run.push(item as ToolCardItem); else out.push(item);
  });
  flush();
  return out;
}

export function groupLabel(group: ToolGroupItem): string {
  const spec = TOOL_KINDS[group.kind] || TOOL_KINDS.tool;
  const n = group.cards.length;
  return `${spec.verb} ${n} ${spec.noun[n === 1 ? 0 : 1]}`;
}

/** One summary line per finished turn (Mu §10.1), kinds in order of first appearance. */
export function turnSummary(items: ToolListItem[]): { text: string; failed: number } {
  const order: ToolListKind[] = [];
  const counts: Partial<Record<ToolListKind, number>> = {};
  let failed = 0;
  const add = (card: ToolCardItem) => {
    if (!counts[card.kind]) { counts[card.kind] = 0; order.push(card.kind); }
    counts[card.kind] = (counts[card.kind] || 0) + 1;
    if (card.state === 'error') failed += 1;
  };
  items.forEach(item => {
    if (item.type === 'card') add(item);
    else if (item.type === 'group') item.cards.forEach(add);
  });
  const parts = order.map((kind, i) => {
    const spec = TOOL_KINDS[kind] || TOOL_KINDS.tool;
    const n = counts[kind] || 0;
    const verb = i === 0 ? spec.verb : spec.verb.toLowerCase();
    return `${verb} ${n} ${spec.noun[n === 1 ? 0 : 1]}`;
  });
  return { text: parts.join(' · '), failed };
}

/** The calls (cards and groups) of a list. */
export function callItems(items: ToolListItem[]): Array<ToolCardItem | ToolGroupItem> {
  return items.filter((item): item is ToolCardItem | ToolGroupItem => item.type === 'card' || item.type === 'group');
}

/** A mobile tool call (already paired with its result) as a card. */
export function cardFromCall(call: { id: string; name: string; args?: unknown; status: 'running' | 'done' | 'failed'; result?: string; startedAt?: number; durationMs?: number }): ToolCardItem {
  const kind = kindOf(call.name);
  return {
    type: 'card', id: call.id, name: call.name || 'tool', kind, args: call.args,
    target: primaryArg(call.args, kind), meta: secondaryMeta(kind, call.args),
    startedAt: call.startedAt || 0, duration: call.durationMs,
    state: call.status === 'running' ? 'running' : call.status === 'failed' ? 'error' : 'done',
    resultText: call.result, hasResult: call.status !== 'running',
  };
}
