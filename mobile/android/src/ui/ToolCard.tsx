// The single representation of a tool invocation (G§9.8).
//
// Collapsed: status glyph (or a running ink drop), tool-kind icon + label,
// the primary argument in mono (middle-truncated), duration and a chevron,
// on a `glass-subtle` inset with radius-md, 36 high (44 touch). A failed
// call shows ✗ in danger ink on a danger-wash header tint; there is no
// coloured side rule (G§3.4). Tap to expand: arguments as
// a key/value grid and a result preview (first 20 lines) with "Show all" in a
// bottom sheet. Done shows ✓ in ok ink for 1.2s, then settles to ink-3 so a
// long transcript does not turn into a field of green ticks. Three or more
// consecutive calls of the same kind collapse into "Read 6 files ›".

import React, { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { vocabulary } from '../theme/wash.generated';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Icon, type IconName } from './Icon';
import { InkDrop } from './Progress';
import { Sheet } from './Sheet';
import { DiffView, looksLikeDiff } from './DiffView';
import { useGlass } from './Glass';

export type ToolKind = keyof typeof vocabulary.tool_kind;
export type ToolCallStatus = 'running' | 'done' | 'failed';

export type ToolCallView = {
  id: string;
  name: string;
  args?: unknown;
  status: ToolCallStatus;
  result?: string;
  /** ms epoch when the call started (live calls). */
  startedAt?: number;
  durationMs?: number;
  /** true when this call was observed live (not hydrated from history). */
  live?: boolean;
};

const KIND_PATTERNS: Array<[ToolKind, RegExp]> = [
  ['agent', /(sub_?agent|spawn|delegate|agent)/i],
  ['container', /(container|docker|sandbox)/i],
  ['memory', /(memory|remember|recall|scratchpad)/i],
  ['plan', /(plan|todo|task_list|goal)/i],
  ['web', /(web|http|fetch_url|browse|url)/i],
  ['search', /(search|grep|glob|find|ripgrep|rg\b|list_files|ls\b)/i],
  ['shell', /(shell|bash|exec|run_command|command|terminal|sh\b)/i],
  ['edit', /(write|edit|patch|apply|create_file|replace|insert|delete_file|move|rename)/i],
  ['read', /(read|view|open|cat|get_file|inspect)/i],
];

/** Tool-kind classification shared by chat and the sub-agent panel. */
export function toolKind(name: string): ToolKind {
  for (const [kind, re] of KIND_PATTERNS) if (re.test(name)) return kind;
  return 'other';
}

export function toolKindLabel(kind: ToolKind): string {
  return vocabulary.tool_kind[kind].label;
}

export function toolKindIcon(kind: ToolKind): IconName {
  return vocabulary.tool_kind[kind].icon as IconName;
}

function parseArgs(args: unknown): Record<string, unknown> | null {
  if (args == null) return null;
  if (typeof args === 'string') {
    try { const v = JSON.parse(args); return v && typeof v === 'object' && !Array.isArray(v) ? v : { value: args }; } catch { return args ? { value: args } : null; }
  }
  return typeof args === 'object' && !Array.isArray(args) ? (args as Record<string, unknown>) : { value: args };
}

const PRIMARY_KEYS = ['path', 'file_path', 'filename', 'file', 'command', 'cmd', 'query', 'pattern', 'url', 'name', 'task', 'prompt', 'value'];

/** The primary argument (path, command, query) shown in the collapsed row. */
export function primaryArg(args: unknown): string {
  const a = parseArgs(args);
  if (!a) return '';
  for (const k of PRIMARY_KEYS) {
    const v = a[k];
    if (typeof v === 'string' && v.trim()) return v.trim().split('\n')[0];
    if (Array.isArray(v) && v.length && typeof v[0] === 'string') return v.join(' ');
  }
  const first = Object.values(a).find(v => typeof v === 'string' && v.trim());
  return typeof first === 'string' ? first.split('\n')[0] : '';
}

function fmtDuration(ms: number | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '';
  return ms < 1000 ? `${(ms / 1000).toFixed(1)}s` : ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

function valueText(v: unknown): string {
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch { return String(v); }
}

const PREVIEW_LINES = 20;

export function ToolCard({ call, testID }: { call: ToolCallView; testID?: string }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const kind = toolKind(call.name);
  const target = primaryArg(call.args);
  const [now, setNow] = useState(Date.now());
  // Done glyph settles to neutral after 1.2s (only for calls seen live).
  const [fresh, setFresh] = useState(false);
  const prevStatus = useRef(call.status);
  useEffect(() => {
    if (prevStatus.current === 'running' && call.status === 'done') {
      setFresh(true);
      const t = setTimeout(() => setFresh(false), 1200);
      prevStatus.current = call.status;
      return () => clearTimeout(t);
    }
    prevStatus.current = call.status;
    return undefined;
  }, [call.status]);
  useEffect(() => {
    if (call.status !== 'running' || !call.startedAt) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [call.status, call.startedAt]);

  const elapsed = call.status === 'running' && call.startedAt ? now - call.startedAt : call.durationMs;
  const failed = call.status === 'failed';
  const glyph = call.status === 'running' ? null : failed ? '✗' : '✓';
  const glyphColor = failed ? colors.danger.ink : fresh ? colors.ok.ink : colors.ink3;
  const args = parseArgs(call.args);
  const resultLines = (call.result || '').split('\n');
  const statusWord = call.status === 'running' ? 'running' : failed ? 'failed' : 'done';

  return (
    <View testID={testID} style={[glass.style('subtle'), { borderRadius: radii.md, overflow: 'hidden' }]}>
      <Pressable
        accessibilityLabel={`${toolKindLabel(kind)} ${target || call.name}, ${statusWord}${elapsed ? `, ${fmtDuration(elapsed)}` : ''}`}
        accessibilityState={{ expanded: open, busy: call.status === 'running' || undefined }}
        accessibilityHint={open ? 'Collapses the tool details' : 'Shows the tool arguments and result'}
        scale={false}
        onPress={() => setOpen(o => !o)}
        testID={testID ? `${testID}-row` : undefined}
        style={({ pressed }) => ({ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, backgroundColor: pressed ? colors.hairline : failed ? glass.tint('danger') : 'transparent' })}
      >
        <View style={{ width: 14, alignItems: 'center' }}>
          {glyph ? <Text variant="label" importantForAccessibility="no" style={{ color: glyphColor }}>{glyph}</Text> : <InkDrop tone="running" size={8} />}
        </View>
        <Icon name={toolKindIcon(kind)} size="sm" color={colors.ink3} />
        <Text variant="label" style={{ color: colors.ink }}>{toolKindLabel(kind)}</Text>
        <Text variant="monoSm" tone="ink2" numberOfLines={1} ellipsizeMode="middle" style={{ flex: 1 }}>{target || call.name}</Text>
        {elapsed ? <Text variant="caption" numeric tone="ink3">{fmtDuration(elapsed)}</Text> : null}
        <Text variant="caption" tone="ink3" importantForAccessibility="no">{open ? '▾' : '›'}</Text>
      </Pressable>
      {open ? (
        <View style={{ paddingHorizontal: spacing.md, paddingBottom: spacing.md, gap: spacing.sm }}>
          <Text variant="caption" tone="ink3">{call.name}</Text>
          {args ? (
            <View style={{ gap: 2 }}>
              {Object.entries(args).map(([k, v]) => (
                <View key={k} style={{ flexDirection: 'row', gap: spacing.sm }}>
                  <Text variant="monoSm" tone="ink3" style={{ minWidth: 72 }}>{k}</Text>
                  <Text variant="monoSm" selectable numberOfLines={6} style={{ flex: 1 }}>{valueText(v)}</Text>
                </View>
              ))}
            </View>
          ) : null}
          {call.result ? (
            looksLikeDiff(call.result) ? <DiffView patch={call.result} maxLines={PREVIEW_LINES} /> : (
              <Text variant="monoSm" selectable style={{ backgroundColor: colors.code, borderRadius: radii.sm, padding: spacing.sm, color: failed ? colors.danger.ink : colors.ink }}>
                {resultLines.slice(0, PREVIEW_LINES).join('\n')}
              </Text>
            )
          ) : null}
          {resultLines.length > PREVIEW_LINES ? (
            <Pressable accessibilityLabel={`Show all ${resultLines.length} lines`} onPress={() => setAll(true)} style={{ minHeight: 36, justifyContent: 'center' }}>
              <Text variant="label" accent>Show all ({resultLines.length} lines) ›</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      <Sheet visible={all} onClose={() => setAll(false)} title={`${call.name} result`}>
        <Text variant="monoSm" selectable>{call.result}</Text>
      </Sheet>
    </View>
  );
}

const GROUP_VERBS: Partial<Record<ToolKind, [string, string]>> = {
  read: ['Read', 'files'], edit: ['Edited', 'files'], search: ['Ran', 'searches'], shell: ['Ran', 'commands'], web: ['Fetched', 'pages'],
};

export function groupSummary(kind: ToolKind, count: number): string {
  const [verb, noun] = GROUP_VERBS[kind] || ['Ran', `${toolKindLabel(kind)} calls`];
  return `${verb} ${count} ${noun}`;
}

/** Three or more consecutive same-kind calls: "Read 6 files ›". */
export function ToolGroup({ calls, testID }: { calls: ToolCallView[]; testID?: string }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const [open, setOpen] = useState(false);
  const kind = toolKind(calls[0]?.name || '');
  const running = calls.some(c => c.status === 'running');
  const failed = calls.filter(c => c.status === 'failed').length;
  const label = groupSummary(kind, calls.length) + (failed ? ` · ${failed} failed` : '');
  return (
    <View testID={testID} style={{ gap: spacing.xs }}>
      <Pressable accessibilityLabel={label} accessibilityState={{ expanded: open }} scale={false} onPress={() => setOpen(o => !o)}
        style={({ pressed }) => ({ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.md, ...glass.style('subtle'), ...(pressed ? { backgroundColor: colors.hairline } : null) })}>
        <View style={{ width: 14, alignItems: 'center' }}>
          {running ? <InkDrop tone="running" size={8} /> : <Text variant="label" style={{ color: failed ? colors.danger.ink : colors.ink3 }}>{failed ? '✗' : '✓'}</Text>}
        </View>
        <Icon name={toolKindIcon(kind)} size="sm" color={colors.ink3} />
        <Text variant="label" style={{ flex: 1 }}>{label}</Text>
        <Text variant="caption" tone="ink3" importantForAccessibility="no">{open ? '▾' : '›'}</Text>
      </Pressable>
      {open ? calls.map(c => <ToolCard key={c.id} call={c} />) : null}
    </View>
  );
}

/** Split a run of calls into cards and same-kind groups (3+). */
export function groupToolCalls(calls: ToolCallView[]): Array<ToolCallView | ToolCallView[]> {
  const out: Array<ToolCallView | ToolCallView[]> = [];
  let run: ToolCallView[] = [];
  const flush = () => {
    if (run.length >= 3) out.push(run);
    else out.push(...run);
    run = [];
  };
  for (const c of calls) {
    if (run.length && toolKind(run[0].name) !== toolKind(c.name)) flush();
    run.push(c);
  }
  flush();
  return out;
}
