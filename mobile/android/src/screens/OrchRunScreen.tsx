// Run detail: state, decision, per-node rows with step glyphs from the
// orchestration vocabulary; tap a node row for its executions (log tail and
// output summary). Actions are the run's ActionVMs plus retry on failed nodes.

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { orchestrationApi, type OrchRunDetail, type OrchRunNodeRow } from '../api/orchestration';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fonts, fontWeights, spacing } from '../theme/tokens';
import { STATUS, when } from '../theme/status';
import { OrchActionBar, retryNodeAction, useOrchCommands } from '../components/OrchActions';
import { EffectChip, ExecutionCard, OrchStateChip, StateGlyph, useOrchLive } from '../components/OrchViews';
import { Banner, Button, Empty, ErrorState, KV, Loading, Panel, Pressable, Text } from '../ui';

export type OrchRunScreenProps = NativeStackScreenProps<RootStackParamList, 'OrchRun'>;

/** Run commands offered on the phone. */
export const RUN_ACTIONS = ['approveRun', 'rejectRun', 'continueRun', 'pauseRun', 'rollbackRun', 'cancelRun'];

const PROGRESS_LABEL: Record<string, string> = {
  ready: 'waiting to start', active: 'running', failed: 'failed', done: 'done', waiting_rollback: 'rolling back',
};

export function OrchRunScreen({ route, navigation }: OrchRunScreenProps) {
  const { colors } = useTheme();
  const runId = route.params.runId;
  const [run, setRun] = useState<OrchRunDetail | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await orchestrationApi.run(runId);
      setRun(next); setError('');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setRefreshing(false); }
  }, [runId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (run?.title) navigation.setOptions({ title: run.title }); }, [navigation, run?.title]);
  useOrchLive(() => { void load(); }, change => change.scope === 'run' && (change.id === runId || change.id === ''));
  const { invoke, busy, sheets } = useOrchCommands({ onDone: () => load() });

  if (!run) {
    return error ? <ErrorState message={error} onRetry={() => { void load(); }} /> : <View style={styles.content}><Loading label="Loading run…" /></View>;
  }

  const n = run.summary?.nodes;
  const canRetry = run.state === 'running' || run.state === 'paused';
  const typed = run.actions?.find(a => a.id === 'approveRun' && a.enabled)?.input?.expect;

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        <Panel testID="orch-run-header">
          <View style={styles.rowTop}>
            <OrchStateChip table="orch_run_state" value={run.state} style={run.style} testID="orch-run-state" />
            <EffectChip effect={run.effect} style={run.effect_style} />
            {run.check_mode ? <Text variant="xs" style={{ color: colors.ink3 }}>check mode</Text> : null}
          </View>
          <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{run.title}</Text>
          <KV label="Target"><Text variant="caption" style={{ fontFamily: fonts.mono }} selectable>{run.target}</Text></KV>
          {run.blast_radius ? <KV label="Blast radius">{run.blast_radius}</KV> : null}
          <KV label="Nodes">{`${run.nodes}${n ? ` · ${n.done} done · ${n.active} running · ${n.failed} failed · ${n.pending} waiting` : ''}`}</KV>
          {run.proposed_by ? <KV label="Proposed by">{run.proposed_by}</KV> : null}
          {run.approved_by ? <KV label="Approved by">{run.approved_by}</KV> : null}
          {run.created_at ? <KV label="Created">{when(run.created_at)}</KV> : null}
          {run.finished_at ? <KV label="Finished">{when(run.finished_at)}</KV> : null}
          {run.paused_reason ? <Text variant="xs" style={{ color: colors.attention.ink }}>Paused: {run.paused_reason}</Text> : null}
          {run.reason ? <Text variant="xs" style={{ color: colors.ink2 }}>{run.reason}</Text> : null}
        </Panel>

        {run.decision?.level ? (
          <Panel title="Policy decision" testID="orch-run-decision">
            <KV label="Level">{run.decision.level}</KV>
            {run.decision.rule ? <KV label="Rule">{run.decision.rule}</KV> : null}
            {run.decision.summary ? <Text variant="xs" style={{ color: colors.ink2 }}>{run.decision.summary}</Text> : null}
            {typed ? <Text variant="xs" style={{ color: colors.danger.ink }}>Approving asks you to type {typed}.</Text> : null}
          </Panel>
        ) : null}

        {(run.warnings || []).length ? (
          <Banner tone="warn" glyph={STATUS.waiting.icon} title="Warnings">
            {run.warnings.map((w, i) => <Text key={i} variant="xs">{w}</Text>)}
          </Banner>
        ) : null}

        <Panel title="Actions" testID="orch-run-actions">
          <OrchActionBar actions={run.actions || []} only={RUN_ACTIONS} busy={busy} onInvoke={a => invoke(a, { run_id: run.id })} />
        </Panel>

        <Panel title="Nodes" badge={run.grid.length} flush testID="orch-run-grid">
          {run.steps.length ? (
            <Text variant="xs" style={[styles.legend, { color: colors.ink3 }]} numberOfLines={2}>
              Steps: {run.steps.map((s, i) => `${i + 1}. ${s.name || `step ${i + 1}`}`).join('  ')}
            </Text>
          ) : null}
          {run.grid.length ? run.grid.map(row => (
            <NodeRow key={row.node_id} row={row} expanded={open === row.node_id} busy={busy}
              onToggle={() => setOpen(cur => (cur === row.node_id ? null : row.node_id))}
              onOpenNode={() => navigation.navigate('OrchNode', { nodeId: row.node_id })}
              onRetry={canRetry && row.progress === 'failed' ? () => invoke(retryNodeAction(), { run_id: run.id, node_id: row.node_id }) : undefined} />
          )) : <View style={{ paddingHorizontal: spacing.base }}><Empty>No nodes in this run.</Empty></View>}
        </Panel>

        {run.excluded?.length ? (
          <Panel title="Excluded nodes" badge={run.excluded.length} testID="orch-run-excluded">
            {run.excluded.map(x => (
              <KV key={x.node_id} label={x.name || x.node_id}>{x.reason}</KV>
            ))}
          </Panel>
        ) : null}
      </ScrollView>
      {sheets}
    </View>
  );
}

function NodeRow({ row, expanded, busy, onToggle, onOpenNode, onRetry }: { row: OrchRunNodeRow; expanded: boolean; busy?: boolean; onToggle: () => void; onOpenNode: () => void; onRetry?: () => void }) {
  const { colors } = useTheme();
  const execs = [...row.cells.map(c => c.exec).filter(Boolean), ...row.rollbacks] as NonNullable<OrchRunNodeRow['cells'][number]['exec']>[];
  return (
    <View style={[styles.nodeRow, { borderBottomColor: colors.hairline }]} testID={`orch-run-node-${row.node_id}`}>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded }}
        accessibilityLabel={`${row.name}, ${PROGRESS_LABEL[row.progress] || row.progress}${row.online ? '' : ', offline'}. ${expanded ? 'Hide' : 'Show'} executions`}
        onPress={onToggle} style={styles.rowTop}>
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flexShrink: 1 }} numberOfLines={1}>{row.name}</Text>
        {!row.online ? <Text variant="xs" style={{ color: colors.ink3 }}>offline</Text> : null}
        <View style={{ flex: 1 }} />
        <View style={styles.cells}>
          {row.cells.map(cell => (
            <StateGlyph key={cell.step} table="orch_exec_state" value={cell.exec?.state || 'queued'} style={cell.style}
              testID={`orch-cell-${row.node_id}-${cell.step}`} />
          ))}
        </View>
      </Pressable>
      <Text variant="xs" style={{ color: row.failed ? colors.danger.ink : colors.ink3 }}>
        {PROGRESS_LABEL[row.progress] || row.progress}{row.batch ? ` · batch ${row.batch + 1}` : ''}
      </Text>
      {expanded ? (
        <View style={{ gap: spacing.xs }} testID={`orch-run-node-${row.node_id}-execs`}>
          {execs.length ? execs.map(ex => <ExecutionCard key={ex.id} ex={ex} />) : <Empty>No executions yet.</Empty>}
          <View style={styles.wrap}>
            {onRetry ? <Button small tone="warn" label="Retry node" disabled={busy} onPress={onRetry} testID={`orch-retry-${row.node_id}`} /> : null}
            <Button small ghost label="Node health" navigates onPress={onOpenNode} />
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing.xl * 2 },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', minHeight: 32 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  cells: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  legend: { paddingHorizontal: spacing.base, paddingBottom: spacing.sm },
  nodeRow: { paddingHorizontal: spacing.base, paddingVertical: spacing.md, gap: spacing.xs, borderBottomWidth: StyleSheet.hairlineWidth },
});
