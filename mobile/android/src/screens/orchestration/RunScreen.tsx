// A run (web runs.js run detail): its state in a sentence, the server's actions (approve with typed
// confirmation where policy asks, reject with a reason, continue, pause, roll back, cancel), then
// Nodes (the live grid: one glyph per step; tap a node for its executions, log tail and outputs;
// retry a failed node), Plan (decision, blast radius, batches, excluded nodes, warnings, steps),
// Results (the outputs as one table across nodes) and Notes.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, Share, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAction, type OrchExecution, type OrchRunDetail, type OrchRunNodeRow } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Button, ErrorState, KV, Loading, PageHeader, Row, Rows, Text, useToast, type StatePart } from '../../ui';
import { ResultsTable } from '../../ui/ResultsTable';
import { ExecutionCard, StateGlyph } from '../../components/OrchViews';
import { FleetChatSheet, resultsQuestion } from './AskSheets';
import { OrchActionBar, ago, byAI, cap, errorText, plural, progressWords, useOrchActions } from './parts';
import { useOrchData } from './useOrchData';

export const RETRYABLE = ['failed', 'unreachable', 'timed_out', 'refused', 'cancelled'];
const PROGRESS: Record<string, string> = { ready: 'waiting to start', active: 'running', failed: 'failed', done: 'done', waiting_rollback: 'rolling back' };
const DECISION: Record<string, { sign: string; label: string }> = {
  allow: { sign: 'done', label: 'Allowed' }, confirm: { sign: 'waiting', label: 'Needs your approval' },
  typed: { sign: 'waiting', label: 'Typed confirmation' }, deny: { sign: 'failed', label: 'Denied by policy' },
};

/** Re-run a node's failed step (runs.retry_node): offered on failed nodes of running or paused runs. */
export const retryNodeAction = (): OrchAction => ({ id: 'retryNode', label: 'Retry node', enabled: true, reason: '', primary: false, danger: false, tone: 'warn',
  confirm: 'Retry the failed step on this node?' } as unknown as OrchAction);

export function runState(run: OrchRunDetail): StatePart[] {
  const st = orchState('orch_run_state', run.state, run.style);
  return [
    { text: cap(st.label), tone: st.status === 'waiting' ? 'attention' : st.status === 'failed' ? 'danger' : st.status === 'done' ? 'ok' : '' },
    run.effect_style ? { text: run.effect_style.label, tone: run.effect === 'destructive' ? 'danger' : '' } : null,
    { text: progressWords(run) },
    run.check_mode ? { text: 'check mode' } : null,
    run.kind === 'test' || run.kind === 'runbook_test' ? { text: 'test' } : null,
    byAI(run.proposed_by) ? { text: 'proposed by the AI', tone: 'attention' } : null,
  ].filter(Boolean) as StatePart[];
}

type Tab = 'nodes' | 'plan' | 'results' | 'notes';

export function OrchRunScreen({ route, navigation }: { route: { params: { runId: string; tab?: Tab } }; navigation: any }) {
  const { colors } = useTheme();
  const toast = useToast();
  const runId = route.params.runId;
  const [tab, setTab] = useState<Tab>(route.params.tab || 'nodes');
  const [open, setOpen] = useState<string | null>(null);
  const [ask, setAsk] = useState<string | null>(null);
  const { data: run, error, refreshing, refresh, reload } = useOrchData(() => orchestrationApi.run(runId),
    c => c.scope === 'run' && (c.id === runId || c.id === ''), [runId]);
  const results = useOrchData(() => (tab === 'results' ? orchestrationApi.runResults(runId) : Promise.resolve(null)),
    c => c.scope === 'run' && c.id === runId, [runId, tab]);
  const { run: runAction, element, busy } = useOrchActions();
  const back = { label: 'Orchestration', onPress: () => navigation.goBack() };

  if (!run) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title="Run" back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading run…" /></View>}
      </View>
    );
  }
  const canRetry = run.state === 'running' || run.state === 'paused';
  const tabs = [
    { key: 'nodes', label: 'Nodes', count: run.grid.length },
    { key: 'plan', label: 'Plan' },
    { key: 'results', label: 'Results' },
    ...(run.notes?.length ? [{ key: 'notes', label: 'Notes', count: run.notes.length }] : []),
  ];
  const shareCsv = async (step: number, view: string) => {
    try {
      const csv = await orchestrationApi.runResultsCsv(runId, step, view);
      await Share.share({ title: `${run.title} results`, message: String(csv) });
    } catch (err) { toast(`Couldn’t share the CSV: ${errorText(err)}`, 'danger'); }
  };

  return (
    <View style={{ flex: 1 }} testID="orch-run">
      <PageHeader testID="orch-run-head" title={run.title || run.id} back={back} state={runState(run)} tabs={tabs} tab={tab} onTab={k => setTab(k as Tab)} />
      <ScrollView contentContainerStyle={styles.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {run.paused_reason ? <Banner tone="attention" glyph="◆" title={`Paused: ${run.paused_reason}`} /> : null}
        {run.decision?.level === 'deny' ? <Banner tone="danger" glyph="✗" title="Denied by access policy">{run.decision.summary || ''}</Banner> : null}
        <OrchActionBar testID="orch-run-actions" actions={run.actions} run={runAction} ctx={{ args: { run_id: run.id }, done: () => { void reload(); } }} />

        {tab === 'nodes' ? (
          <View testID="orch-run-grid">
            {run.steps.length > 1 ? <Text variant="caption" tone="ink3" style={styles.legend}>Steps: {run.steps.map((s, i) => `${i + 1}. ${s.name || `step ${i + 1}`}`).join('  ')}</Text> : null}
            <Rows empty="No nodes in this run.">
              {run.grid.map(row => (
                <GridRow key={row.node_id} row={row} expanded={open === row.node_id} busy={busy}
                  onToggle={() => setOpen(cur => (cur === row.node_id ? null : row.node_id))}
                  onOpenNode={() => navigation.navigate('OrchNode', { nodeId: row.node_id })}
                  onRetry={canRetry && row.failed ? () => { void runAction(retryNodeAction(), { args: { run_id: run.id, node_id: row.node_id }, done: () => { void reload(); } }); } : undefined} />
              ))}
            </Rows>
          </View>
        ) : null}

        {tab === 'plan' ? <PlanView run={run} openNode={id => navigation.navigate('OrchNode', { nodeId: id })} /> : null}

        {tab === 'results' ? (
          results.data ? <ResultsTable testID="orch-run-results" data={results.data} onAsk={v => setAsk(resultsQuestion({ run: { id: run.id, target: String(run.target || '') } }, v))} empty="No node has returned results for this run yet."
            onShareCsv={(step, view) => { void shareCsv(step, view); }} onRow={row => { setOpen(row.node_id || null); setTab('nodes'); }} />
            : results.error ? <Banner tone="danger" glyph="✗" title={results.error} /> : <Loading label="Loading results…" />
        ) : null}

        {tab === 'notes' ? (
          <Rows testID="orch-run-notes">
            {run.notes.map((n: any, i: number) => <Row key={i} status={n.tone === 'danger' ? 'failed' : n.tone === 'ok' ? 'done' : 'pending'} title={String(n.text || '')} sub={[n.actor, n.action].filter(Boolean).join(' · ')} when={ago(n.at)} />)}
          </Rows>
        ) : null}
      </ScrollView>
      <FleetChatSheet visible={ask !== null} prefill={ask || undefined} onClose={() => setAsk(null)} />
      {element}
    </View>
  );
}

function GridRow({ row, expanded, busy, onToggle, onOpenNode, onRetry }: {
  row: OrchRunNodeRow; expanded: boolean; busy?: boolean; onToggle: () => void; onOpenNode: () => void; onRetry?: () => void;
}) {
  const execs = [...row.cells.map(c => c.exec).filter(Boolean), ...row.rollbacks] as OrchExecution[];
  const progress = PROGRESS[row.progress] || row.progress;
  return (
    <View testID={`orch-run-node-${row.node_id}`}>
      <Row status={row.failed ? 'failed' : row.progress === 'done' ? 'done' : row.progress === 'active' ? 'active' : 'pending'}
        title={<View style={styles.gridTitle}>
          <Text variant="body" numberOfLines={1} style={{ flexShrink: 1 }}>{row.name}</Text>
          <View style={styles.cells}>
            {row.cells.map(cell => <StateGlyph key={cell.step} table="orch_exec_state" value={cell.exec?.state || 'queued'} style={cell.style} testID={`orch-cell-${row.node_id}-${cell.step}`} />)}
          </View>
        </View>}
        sub={[progress, row.online ? '' : 'offline', row.batch ? `batch ${row.batch + 1}` : ''].filter(Boolean).join(' · ')}
        onPress={onToggle} accessibilityLabel={`${row.name}, ${progress}${row.online ? '' : ', offline'}. ${expanded ? 'Hide' : 'Show'} executions`} />
      {expanded ? (
        <View style={styles.execs} testID={`orch-run-node-${row.node_id}-execs`}>
          {execs.length ? execs.map(ex => <ExecutionCard key={ex.id} ex={ex} />) : <Text variant="caption" tone="ink3">No executions yet.</Text>}
          <View style={styles.buttons}>
            {onRetry ? <Button size="md" variant="tonal" tone="attention" label="Retry node" disabled={busy} onPress={onRetry} testID={`orch-retry-${row.node_id}`} /> : null}
            <Button size="md" variant="ghost" label="Open node" navigates onPress={onOpenNode} />
          </View>
        </View>
      ) : null}
    </View>
  );
}

/** The plan (web runs.planView): access decision, blast radius, effect, rollback, batches, exclusions, steps. */
function PlanView({ run, openNode }: { run: OrchRunDetail; openNode: (id: string) => void }) {
  const decision = (run.decision_full as Record<string, any> | null) || run.decision || {};
  const ds = DECISION[String(decision.level || '')] || { sign: 'pending', label: String(decision.level || 'Unknown') };
  const names = new Map(run.grid.map(g => [g.node_id, g.name]));
  const strategy = run.strategy as Record<string, any>;
  return (
    <View style={styles.plan} testID="orch-run-plan">
      <Rows>
        <Row status={ds.sign} title={ds.label} sub={String(decision.summary || '')} testID="orch-run-decision" />
      </Rows>
      <View style={styles.kv}>
        <KV label="Blast radius">{run.blast_radius || plural(run.nodes || 0, 'node')}</KV>
        <KV label="Effect">{run.effect_style?.label || run.effect}</KV>
        <KV label="Rollback">{run.rollback_available ? 'Available' : 'Not available'}</KV>
        <KV label="Mode">{run.check_mode ? 'Check mode (dry run)' : 'Real run'}</KV>
        <KV label="Target"><Text variant="monoSm" selectable>{run.target}</Text></KV>
        {run.proposed_by ? <KV label="Proposed by">{run.proposed_by}</KV> : null}
        {run.approved_by ? <KV label="Approved by">{run.approved_by}</KV> : null}
      </View>
      {(run.warnings || []).map((w, i) => <Banner key={i} tone="caution" glyph="◆" title={w} />)}
      {run.secrets?.length ? <Text variant="caption" tone="ink3">Secrets passed to the node: {run.secrets.map(s => (typeof s === 'string' ? s : (s as any).name)).join(', ')}</Text> : null}
      <BoardSection title="Batches" testID="orch-run-batches" empty="No batches.">
        {(run.batches || []).map((b, i) => <Row key={i} title={i === 0 && strategy?.canary ? 'Canary' : `Batch ${i + 1}`} sub={b.map(id => names.get(id) || id).join(', ')} />)}
      </BoardSection>
      <BoardSection title="Excluded" count={run.excluded?.length || 0} testID="orch-run-excluded" empty="No matched node was excluded.">
        {(run.excluded || []).map(x => <Row key={x.node_id} title={x.name || x.node_id} sub={x.reason} onPress={() => openNode(x.node_id)} />)}
      </BoardSection>
      {run.steps.length ? (
        <BoardSection title="Steps" testID="orch-run-steps">
          {run.steps.map((s: Record<string, any>, i) => <Row key={i} title={String(s.title || s.name || `Step ${i + 1}`)}
            sub={[s.version ? `${s.tool_id}@${s.version}` : s.tool_id, s.effect, Object.keys(s.params || {}).length ? JSON.stringify(s.params) : '', `on failure ${s.on_failure || 'stop'}`].filter(Boolean).join(' · ')} />)}
        </BoardSection>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  legend: { marginBottom: spacing.xs },
  gridTitle: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  cells: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  execs: { gap: spacing.xs, paddingBottom: spacing.sm },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  plan: { gap: spacing.md },
  kv: { gap: spacing.xs },
});
