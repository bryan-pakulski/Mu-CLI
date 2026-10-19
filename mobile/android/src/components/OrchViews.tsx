// Shared orchestration views for the phone: vocabulary chips, the live
// change subscription, node health rows and execution cards. Status is never
// colour alone: every glyph travels with its vocabulary label.

import React, { useEffect, useRef } from 'react';
import { StyleSheet, View } from 'react-native';

import { ApiError } from '../api/client';
import { subscribeToOrchestrationChanges, type OrchChange, type OrchEffectStyle, type OrchExecution, type OrchNode, type OrchStyle } from '../api/orchestration';
import { useTheme } from '../theme/ThemeContext';
import { fonts, spacing, toneOf } from '../theme/tokens';
import { STATUS, orchEffect, orchState, type OrchTable } from '../theme/status';
import { Chip, KV, Pre, Pressable, StatusChip, Text } from '../ui';

export const ORCH_REFRESH_DEBOUNCE_MS = 300;
export const LOG_TAIL_LINES = 40;

/** True when the server has no orchestration API (older server). */
export function isOrchMissing(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405);
}

/** Refetch on `/api/orchestration/events` changes (debounced); `filter` limits which changes count. */
export function useOrchLive(reload: () => void, filter?: (change: OrchChange) => boolean, debounceMs = ORCH_REFRESH_DEBOUNCE_MS) {
  const reloadRef = useRef(reload);
  reloadRef.current = reload;
  const filterRef = useRef(filter);
  filterRef.current = filter;
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; reloadRef.current(); }, debounceMs);
    };
    const stream = subscribeToOrchestrationChanges(change => {
      if (!filterRef.current || filterRef.current(change)) schedule();
    }, schedule);
    return () => { if (timer) clearTimeout(timer); stream.close(); };
  }, [debounceMs]);
}

export function OrchStateChip({ table, value, style, testID }: { table: OrchTable; value: string; style?: OrchStyle | null; testID?: string }) {
  const s = orchState(table, value, style);
  return <StatusChip status={s.status} label={s.label} testID={testID} />;
}

export function EffectChip({ effect, style, testID }: { effect: string; style?: OrchEffectStyle | null; testID?: string }) {
  const e = orchEffect(effect, style);
  return <Chip tone={e.tone} accessibilityLabel={`effect ${e.label}`} testID={testID}>{e.label}</Chip>;
}

/** Glyph-only state mark with an accessible label (run grid cells). */
export function StateGlyph({ table, value, style, testID }: { table: OrchTable; value: string; style?: OrchStyle | null; testID?: string }) {
  const { colors } = useTheme();
  const s = orchState(table, value, style);
  const row = STATUS[s.status];
  const ink = toneOf(colors, row.tone).ink;
  return (
    <View accessible accessibilityLabel={s.label} testID={testID} style={styles.glyph}>
      <Text variant="body" style={{ color: ink, fontWeight: '600' }}>{row.icon}</Text>
    </View>
  );
}

export function pct(value: number | null | undefined): string {
  return value == null || Number.isNaN(Number(value)) ? '—' : `${Math.round(Number(value))}%`;
}

export function labelList(labels: Record<string, string> | null | undefined): string[] {
  return Object.entries(labels || {}).map(([k, v]) => (v === '' || v == null ? k : `${k}=${v}`));
}

/** Node health row: status chip, online, last seen, OS, max disk %, memory %, labels. */
export function NodeHealthRow({ node, onPress }: { node: OrchNode; onPress: () => void }) {
  const { colors } = useTheme();
  const sum = node.summary || ({} as OrchNode['summary']);
  const labels = labelList(node.labels);
  return (
    <Pressable testID={`orch-node-${node.id}`} accessibilityRole="button"
      accessibilityLabel={`${node.name}, ${orchState('orch_node_status', node.status, node.style).label}, ${node.online ? 'online' : 'offline'}`}
      onPress={onPress}
      style={({ pressed }) => [styles.row, { borderBottomColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
      <View style={styles.rowTop}>
        <Text variant="sm" style={{ fontWeight: '600', flexShrink: 1 }} numberOfLines={1}>{node.name || node.id}</Text>
        {node.canary ? <Chip variant="meta">canary</Chip> : null}
        <View style={{ flex: 1 }} />
        <OrchStateChip table="orch_node_status" value={node.status} style={node.style} />
      </View>
      <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>
        {node.online ? 'online' : 'not reporting'} · seen {node.last_seen_ago || 'never'}{sum.os ? ` · ${sum.os}` : ''}
      </Text>
      <Text variant="xs" style={{ color: colors.ink2, fontFamily: fonts.mono }} numberOfLines={1} testID={`orch-node-${node.id}-health`}>
        disk {pct(sum.max_disk_used_pct)} · mem {pct(sum.memory_used_pct)}
      </Text>
      {labels.length ? (
        <View style={styles.wrap}>
          {labels.slice(0, 6).map(l => <Chip key={l} variant="meta">{l}</Chip>)}
          {labels.length > 6 ? <Text variant="xs" style={{ color: colors.ink3 }}>+{labels.length - 6}</Text> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

/** Last `lines` lines of a log. */
export function logTail(text: string | null | undefined, lines = LOG_TAIL_LINES): string {
  const all = String(text ?? '').replace(/\s+$/, '').split('\n');
  return all.length > lines ? all.slice(-lines).join('\n') : all.join('\n');
}

/** Declared outputs as short `key: value` lines (values JSON-encoded and capped). */
export function outputSummary(output: Record<string, unknown> | null | undefined, max = 12): string {
  const entries = Object.entries(output || {});
  if (!entries.length) return '';
  const lines = entries.slice(0, max).map(([k, v]) => {
    const text = typeof v === 'string' ? v : JSON.stringify(v);
    return `${k}: ${String(text).length > 160 ? `${String(text).slice(0, 160)}…` : text}`;
  });
  if (entries.length > max) lines.push(`… ${entries.length - max} more`);
  return lines.join('\n');
}

export function ExecutionCard({ ex, testID }: { ex: OrchExecution; testID?: string }) {
  const { colors } = useTheme();
  const log = logTail(ex.log ?? ex.log_tail);
  const output = outputSummary(ex.output);
  return (
    <View testID={testID || `orch-exec-${ex.id}`} style={[styles.exec, { borderColor: colors.hairline }]}>
      <View style={styles.rowTop}>
        <Text variant="sm" style={{ fontWeight: '600', flexShrink: 1 }} numberOfLines={1}>
          {ex.rollback_of ? 'Rollback · ' : ''}{ex.step_name || `Step ${ex.step + 1}`}
        </Text>
        <View style={{ flex: 1 }} />
        <OrchStateChip table="orch_exec_state" value={ex.state} style={ex.style} />
      </View>
      <KV label="Tool"><Text variant="caption" style={{ fontFamily: fonts.mono }}>{ex.tool}</Text></KV>
      {ex.exit_code != null ? <KV label="Exit code">{String(ex.exit_code)}</KV> : null}
      {ex.duration_s != null ? <KV label="Duration">{`${Number(ex.duration_s).toFixed(1)}s`}</KV> : null}
      {ex.attempts > 1 ? <KV label="Attempts">{String(ex.attempts)}</KV> : null}
      {ex.check_mode ? <KV label="Mode">check (dry run)</KV> : null}
      {ex.reason ? <Text variant="xs" style={{ color: colors.ink2 }}>{ex.reason}</Text> : null}
      {output ? (
        <>
          <Text variant="caption" tone="ink3">Output</Text>
          <Pre testID={`orch-exec-${ex.id}-output`}>{output}</Pre>
        </>
      ) : null}
      {log ? (
        <>
          <Text variant="caption" tone="ink3">Log tail{ex.log_truncated ? ' (truncated on the node)' : ''}</Text>
          <Pre testID={`orch-exec-${ex.id}-log`} numberOfLines={LOG_TAIL_LINES}>{log}</Pre>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: spacing.base, paddingVertical: spacing.md, gap: spacing.xs, borderBottomWidth: StyleSheet.hairlineWidth },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xs },
  glyph: { minWidth: 20, alignItems: 'center' },
  exec: { gap: spacing.xs, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth },
});
