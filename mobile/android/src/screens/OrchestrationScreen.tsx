// Orchestration tab (orchestration.md §8 "Mobile"): what is useful on a
// phone. Attention summary, the global kill switch (with a clear paused
// banner), the approvals inbox (proposed runs with approve/reject, paused
// runs with continue/roll back/cancel, nodes awaiting acceptance, tool
// reviews read-only), recent runs and a link to node health. Live via
// /api/orchestration/events; every button is a server ActionVM.

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import {
  orchestrationApi,
  type OrchAction,
  type OrchApprovals,
  type OrchNode,
  type OrchOverview,
  type OrchReviewItem,
  type OrchRunBrief,
  type OrchRunDetail,
} from '../api/orchestration';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fonts, fontWeights, radii, spacing } from '../theme/tokens';
import { STATUS } from '../theme/status';
import { OrchActionBar, dispatchAction, useOrchCommands } from '../components/OrchActions';
import { EffectChip, OrchStateChip, isOrchMissing, labelList, useOrchLive } from '../components/OrchViews';
import { Banner, Button, Chip, Empty, EmptyState, Loading, Panel, Pressable, StatStrip, Text } from '../ui';
import { useGlass } from '../ui/Glass';

export type OrchestrationScreenProps = NativeStackScreenProps<RootStackParamList, 'Orchestration'>;

/** Commands the inbox offers per subject; the rest live on the web. */
export const INBOX_RUN_ACTIONS = ['approveRun', 'rejectRun'];
export const PAUSED_RUN_ACTIONS = ['continueRun', 'rollbackRun', 'cancelRun'];
export const NODE_INBOX_ACTIONS = ['acceptNode', 'quarantineNode'];
/** Paused runs are listed as briefs; at most this many get their detail (for actions). */
const PAUSED_DETAIL_LIMIT = 10;

type Data = { overview: OrchOverview; approvals: OrchApprovals; paused: Array<OrchRunDetail | OrchRunBrief> };

export function OrchestrationScreen({ navigation }: OrchestrationScreenProps) {
  const { colors } = useTheme();
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [missing, setMissing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [overview, approvals] = await Promise.all([orchestrationApi.overview(), orchestrationApi.approvals()]);
      // /approvals lists paused runs as briefs (no actions); fetch their detail for the buttons.
      const paused = await Promise.all((approvals.paused || []).map((brief, i) => (i < PAUSED_DETAIL_LIMIT
        ? orchestrationApi.run(brief.id).catch(() => brief)
        : Promise.resolve(brief))));
      setData({ overview, approvals, paused });
      setError(''); setMissing(false);
    } catch (err) {
      if (isOrchMissing(err)) setMissing(true);
      else setError(err instanceof Error ? err.message : String(err));
    } finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useOrchLive(() => { void load(); }, change => change.scope !== 'tool' || change.parts.includes('status'));
  const { invoke, busy, sheets } = useOrchCommands({ onDone: () => load() });

  const openRun = (runId: string) => navigation.navigate('OrchRun', { runId });
  const openNode = (nodeId: string) => navigation.navigate('OrchNode', { nodeId });

  if (missing) {
    return <EmptyState icon="server" title="Orchestration is not available" message="This server has no orchestration API. Update mucli on the computer running the GUI." testID="orch-missing" />;
  }

  const overview = data?.overview;
  const approvals = data?.approvals;
  const att = overview?.attention;
  const paused = !!(overview?.dispatch_paused ?? att?.dispatch_paused);
  const reviews: OrchReviewItem[] = [...(approvals?.reviews || []), ...(approvals?.runbook_reviews || [])];
  const offline = att?.offline_nodes || 0;
  const kill = dispatchAction(paused);

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={colors.ink3} />}>
        <View style={styles.header}>
          <View style={{ flex: 1, gap: 2 }}>
            <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Overview</Text>
            <Text variant="xs" style={{ color: colors.ink3 }}>
              {overview ? `${overview.nodes.total} node${overview.nodes.total === 1 ? '' : 's'} · ${overview.catalog.published} published tool${overview.catalog.published === 1 ? '' : 's'}` : 'Fleet approvals and health'}
            </Text>
          </View>
          <Button label="Nodes" icon="server" navigates testID="orch-open-nodes" onPress={() => navigation.navigate('OrchNodes')} />
        </View>

        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}

        {paused ? (
          <Banner tone="danger" glyph={STATUS.waiting.icon} role="alert" testID="orch-dispatch-paused"
            title="Dispatch is paused: no new work is sent to any node."
            action={{ label: kill.label, onPress: () => invoke(kill, {}), testID: 'orch-kill-switch' }} />
        ) : null}

        {loading && !data ? <Loading label="Loading orchestration…" /> : null}

        {att ? (
          <StatStrip testID="orch-attention" items={[
            { testID: 'orch-stat-approvals', value: att.approvals, label: 'to approve', tone: att.approvals ? 'attention' : 'neutral', glyph: STATUS.waiting.icon },
            { testID: 'orch-stat-paused', value: att.paused_runs, label: 'paused runs', tone: att.paused_runs ? 'attention' : 'neutral', glyph: STATUS.waiting.icon },
            { testID: 'orch-stat-pending-nodes', value: att.pending_nodes, label: 'new nodes', tone: att.pending_nodes ? 'attention' : 'neutral', glyph: STATUS.waiting.icon, onPress: () => navigation.navigate('OrchNodes') },
            { testID: 'orch-stat-offline', value: offline, label: 'offline', tone: offline ? 'danger' : 'neutral', glyph: STATUS.failed.icon, onPress: () => navigation.navigate('OrchNodes') },
            { testID: 'orch-stat-reviews', value: att.reviews, label: 'in review', tone: att.reviews ? 'attention' : 'neutral', glyph: STATUS.waiting.icon },
          ]} />
        ) : null}

        {data ? (
          <>
            <Panel title="Approvals" glyph="◆" glyphTone="warn" badge={approvals?.runs.length || 0} badgeTone={approvals?.runs.length ? 'warn' : ''} testID="orch-inbox-runs">
              {approvals?.runs.length ? approvals.runs.map(run => (
                <ProposedRunCard key={run.id} run={run} busy={busy} onOpen={() => openRun(run.id)} onInvoke={a => invoke(a, { run_id: run.id })} />
              )) : <Empty status="done">No runs wait for approval.</Empty>}
            </Panel>

            {data.paused.length ? (
              <Panel title="Paused runs" glyph={STATUS.waiting.icon} glyphTone="warn" badge={data.paused.length} badgeTone="warn" testID="orch-inbox-paused">
                {data.paused.map(run => (
                  <PausedRunCard key={run.id} run={run} busy={busy} onOpen={() => openRun(run.id)} onInvoke={a => invoke(a, { run_id: run.id })} />
                ))}
              </Panel>
            ) : null}

            {approvals?.nodes.length ? (
              <Panel title="Nodes awaiting acceptance" glyph={STATUS.waiting.icon} glyphTone="warn" badge={approvals.nodes.length} badgeTone="warn" testID="orch-inbox-nodes">
                {approvals.nodes.map(node => (
                  <PendingNodeCard key={node.id} node={node} busy={busy} onOpen={() => openNode(node.id)} onInvoke={a => invoke(a, { node_id: node.id })} />
                ))}
              </Panel>
            ) : null}

            {reviews.length ? (
              <Panel title="In review" badge={reviews.length} testID="orch-inbox-reviews">
                <Text variant="xs" style={{ color: colors.ink3 }}>Tool and runbook reviews happen on the web Orchestration page, where the diff and lint results are shown.</Text>
                {reviews.map(item => (
                  <View key={item.ref} testID={`orch-review-${item.ref}`} accessible accessibilityLabel={`${item.title}, ${item.ref}, in review on the web`} style={[styles.reviewRow, { borderTopColor: colors.hairline }]}>
                    <View style={styles.rowTop}>
                      <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flexShrink: 1 }} numberOfLines={1}>{item.title}</Text>
                      <View style={{ flex: 1 }} />
                      <EffectChip effect={item.effect} style={item.effect_style} />
                    </View>
                    <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1}>{item.ref}</Text>
                    {item.summary ? <Text variant="xs" style={{ color: colors.ink2 }} numberOfLines={2}>{item.summary}</Text> : null}
                  </View>
                ))}
              </Panel>
            ) : null}

            <Panel title="Recent runs" flush testID="orch-recent">
              {overview?.recent_runs.length ? overview.recent_runs.map(run => <RunRow key={run.id} run={run} onPress={() => openRun(run.id)} />)
                : <View style={{ paddingHorizontal: spacing.base }}><Empty>No runs yet.</Empty></View>}
            </Panel>

            {!paused ? (
              <Panel title="Kill switch" testID="orch-kill-panel">
                <Text variant="xs" style={{ color: colors.ink3 }}>Stops sending new work to every node. Running executions finish; nothing new starts until you resume.</Text>
                <View style={styles.wrap}>
                  <Button tone="danger" label={kill.label} icon="pause" testID="orch-kill-switch" disabled={busy} onPress={() => invoke(kill, {})} />
                </View>
              </Panel>
            ) : null}
          </>
        ) : null}
      </ScrollView>
      {sheets}
    </View>
  );
}

/** Proposed run: title, target, effect, blast radius, decision, excluded nodes; approve/reject. */
export function ProposedRunCard({ run, busy, onOpen, onInvoke }: { run: OrchRunDetail; busy?: boolean; onOpen: () => void; onInvoke: (a: OrchAction) => void }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const destructive = run.effect === 'destructive';
  const excluded = run.excluded?.length || 0;
  const typed = run.actions?.find(a => a.id === 'approveRun')?.input?.expect;
  return (
    <View testID={`orch-proposed-${run.id}`} style={[styles.card, glass.style(1)]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Open run ${run.title}`} onPress={onOpen}
        style={[styles.cardHead, { backgroundColor: glass.tint(destructive ? 'danger' : 'attention') }]}>
        <OrchStateChip table="orch_run_state" value={run.state} style={run.style} />
        <View style={{ flex: 1 }} />
        <EffectChip effect={run.effect} style={run.effect_style} testID={`orch-proposed-${run.id}-effect`} />
      </Pressable>
      <View style={styles.cardBody}>
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold }} numberOfLines={2}>{run.title}</Text>
        <Text variant="xs" style={{ color: colors.ink2, fontFamily: fonts.mono }} numberOfLines={1}>{run.target}{run.check_mode ? '  · check mode' : ''}</Text>
        {run.blast_radius ? <Text variant="xs" testID={`orch-proposed-${run.id}-blast`}>Blast radius: {run.blast_radius}</Text> : null}
        {run.decision?.summary ? <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={3}>Policy: {run.decision.summary}</Text> : null}
        <Text variant="xs" style={{ color: colors.ink3 }}>
          {run.nodes} node{run.nodes === 1 ? '' : 's'}
          {excluded ? <Text variant="xs" style={{ color: colors.attention.ink }} testID={`orch-proposed-${run.id}-excluded`}>{` · ${excluded} excluded`}</Text> : null}
          {run.proposed_by ? ` · proposed by ${run.proposed_by}` : ''}
        </Text>
        {typed ? <Text variant="xs" style={{ color: colors.danger.ink }}>Approval needs typed confirmation of {typed}.</Text> : null}
        {(run.warnings || []).slice(0, 3).map((w, i) => <Text key={i} variant="xs" style={{ color: colors.attention.ink }}>{STATUS.waiting.icon} {w}</Text>)}
        <OrchActionBar actions={run.actions || []} only={INBOX_RUN_ACTIONS} busy={busy} onInvoke={onInvoke} testID={`orch-proposed-${run.id}-actions`} />
      </View>
    </View>
  );
}

function PausedRunCard({ run, busy, onOpen, onInvoke }: { run: OrchRunDetail | OrchRunBrief; busy?: boolean; onOpen: () => void; onInvoke: (a: OrchAction) => void }) {
  const { colors } = useTheme();
  const actions = 'actions' in run ? run.actions : null;
  return (
    <View testID={`orch-paused-${run.id}`} style={[styles.reviewRow, { borderTopColor: colors.hairline }]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Open run ${run.title}`} onPress={onOpen} style={styles.rowTop}>
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flexShrink: 1 }} numberOfLines={1}>{run.title}</Text>
        <View style={{ flex: 1 }} />
        <EffectChip effect={run.effect} style={run.effect_style} />
      </Pressable>
      {run.paused_reason ? <Text variant="xs" style={{ color: colors.attention.ink }}>{run.paused_reason}</Text> : null}
      {actions ? <OrchActionBar actions={actions} only={PAUSED_RUN_ACTIONS} busy={busy} onInvoke={onInvoke} testID={`orch-paused-${run.id}-actions`} />
        : <Button small ghost label="Open run" navigates onPress={onOpen} />}
    </View>
  );
}

function PendingNodeCard({ node, busy, onOpen, onInvoke }: { node: OrchNode; busy?: boolean; onOpen: () => void; onInvoke: (a: OrchAction) => void }) {
  const { colors } = useTheme();
  const labels = labelList(node.labels);
  const sum = node.summary;
  return (
    <View testID={`orch-pending-node-${node.id}`} style={[styles.reviewRow, { borderTopColor: colors.hairline }]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Open node ${node.name}`} onPress={onOpen} style={styles.rowTop}>
        <Text variant="sm" style={{ fontWeight: fontWeights.semibold, flexShrink: 1 }} numberOfLines={1}>{node.name || node.id}</Text>
        <View style={{ flex: 1 }} />
        <OrchStateChip table="orch_node_status" value={node.status} style={node.style} />
      </Pressable>
      <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={2}>
        {[sum?.hostname, sum?.os, sum?.arch, node.address].filter(Boolean).join(' · ') || 'No facts reported yet'}
      </Text>
      {labels.length ? <View style={styles.wrap}>{labels.slice(0, 6).map(l => <Chip key={l} variant="meta">{l}</Chip>)}</View> : null}
      <OrchActionBar actions={node.actions || []} only={NODE_INBOX_ACTIONS} busy={busy} onInvoke={onInvoke} testID={`orch-pending-node-${node.id}-actions`} />
    </View>
  );
}

export function RunRow({ run, onPress }: { run: OrchRunBrief; onPress: () => void }) {
  const { colors } = useTheme();
  const n = run.summary?.nodes;
  return (
    <Pressable testID={`orch-run-row-${run.id}`} accessibilityRole="button" accessibilityLabel={`${run.title}, ${run.style?.label || run.state}`} onPress={onPress}
      style={({ pressed }) => [styles.runRow, { borderBottomColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
      <View style={styles.rowTop}>
        <Text variant="sm" numberOfLines={1} style={{ flexShrink: 1 }}>{run.title}</Text>
        <View style={{ flex: 1 }} />
        <OrchStateChip table="orch_run_state" value={run.state} style={run.style} />
      </View>
      <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1}>
        {run.target} · {run.nodes} node{run.nodes === 1 ? '' : 's'}{n ? ` · ${n.done} done${n.failed ? `, ${n.failed} failed` : ''}` : ''}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing.xl * 2 },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 32 },
  card: { borderRadius: radii.control, overflow: 'hidden' },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: 44 },
  cardBody: { padding: spacing.md, gap: spacing.xs },
  reviewRow: { gap: spacing.xs, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth },
  runRow: { paddingHorizontal: spacing.base, paddingVertical: spacing.md, gap: 2, borderBottomWidth: StyleSheet.hairlineWidth },
});
