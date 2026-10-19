// Node detail: facts summary, policy summary, recent executions and the
// accept / quarantine / release actions (the rest of node management lives
// on the web).

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { orchestrationApi, type OrchNodeDetail } from '../api/orchestration';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fonts, fontWeights, spacing } from '../theme/tokens';
import { STATUS, when } from '../theme/status';
import { OrchActionBar, useOrchCommands } from '../components/OrchActions';
import { OrchStateChip, labelList, pct, useOrchLive } from '../components/OrchViews';
import { Banner, Chip, Empty, ErrorState, KV, Loading, Panel, Pressable, Text, useToast } from '../ui';

export type OrchNodeScreenProps = NativeStackScreenProps<RootStackParamList, 'OrchNode'>;

/** Node commands offered on the phone. */
export const NODE_ACTIONS = ['acceptNode', 'quarantineNode', 'unquarantineNode'];

/** Scalar policy entries as readable rows (lists joined; nested objects summarised). */
export function policyRows(policy: Record<string, unknown> | null | undefined): Array<[string, string]> {
  return Object.entries(policy || {})
    .filter(([k]) => k !== 'errors')
    .map(([k, v]): [string, string] => {
      if (Array.isArray(v)) return [k, v.length ? v.map(x => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : 'none'];
      if (v && typeof v === 'object') return [k, `${Object.keys(v as object).length} entries`];
      return [k, v === '' || v == null ? '—' : String(v)];
    });
}

export function OrchNodeScreen({ route, navigation }: OrchNodeScreenProps) {
  const { colors } = useTheme();
  const nodeId = route.params.nodeId;
  const [node, setNode] = useState<OrchNodeDetail | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await orchestrationApi.node(nodeId);
      setNode(next); setError('');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setRefreshing(false); }
  }, [nodeId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (node?.name) navigation.setOptions({ title: node.name }); }, [navigation, node?.name]);
  useOrchLive(() => { void load(); }, change => (change.scope === 'node' && change.id === nodeId) || change.scope === 'run');
  const { invoke, busy, sheets } = useOrchCommands({ onDone: () => load() });
  const toast = useToast();
  /** Propose an update job for this node (an ordinary run: approve it on the run screen). */
  const updateAgent = async () => {
    try {
      const res = await orchestrationApi.proposeAgentUpdate([nodeId]);
      const run = res.data.run as { id: string; state: string };
      toast(run.state === 'proposed' ? 'Update job proposed: approve it to start.' : 'Update job started.', 'ok');
      navigation.navigate('OrchRun', { runId: run.id });
    } catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
  };

  if (!node) {
    return error ? <ErrorState message={error} onRetry={() => { void load(); }} /> : <View style={styles.content}><Loading label="Loading node…" /></View>;
  }
  const s = node.summary || ({} as OrchNodeDetail['summary']);
  const labels = labelList(node.labels);
  const policy = policyRows(node.policy);

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        <Panel testID="orch-node-header">
          <View style={styles.rowTop}>
            <OrchStateChip table="orch_node_status" value={node.status} style={node.style} testID="orch-node-status" />
            <Text variant="xs" style={{ color: node.online ? colors.ok.ink : colors.ink3 }}>{node.online ? `${STATUS.done.icon} online` : 'not reporting'}</Text>
            {node.canary ? <Chip variant="meta">canary</Chip> : null}
          </View>
          <KV label="Last seen">{node.last_seen_ago || 'never'}</KV>
          {node.address ? <KV label="Address"><Text variant="caption" style={{ fontFamily: fonts.mono }}>{node.address}</Text></KV> : null}
          {node.agent_version ? <KV label="Agent">{node.agent?.outdated ? `${node.agent_version} (master has ${node.agent.current})` : node.agent_version}</KV> : null}
          {node.agent?.outdated ? (
            <Banner tone="warn" glyph={STATUS.waiting.icon} testID="orch-node-agent"
              title={node.agent.updatable ? 'An agent update is available for this node.' : `Agent is out of date: ${node.agent.reason}`}
              action={node.agent.updatable ? { label: 'Update this node', testID: 'orch-node-update-agent', onPress: () => { void updateAgent(); } } : undefined} />
          ) : null}
          {node.groups?.length ? <KV label="Groups">{node.groups.join(', ')}</KV> : null}
          {labels.length ? <View style={styles.wrap}>{labels.map(l => <Chip key={l} variant="meta">{l}</Chip>)}</View> : null}
          <OrchActionBar actions={node.actions || []} only={NODE_ACTIONS} busy={busy} onInvoke={a => invoke(a, { node_id: node.id })} testID="orch-node-actions" />
        </Panel>

        <Panel title="Facts" testID="orch-node-facts">
          {node.facts_at ? <Text variant="xs" style={{ color: colors.ink3 }}>Reported {when(node.facts_at)}</Text> : null}
          <KV label="OS">{s.os || '—'}</KV>
          <KV label="Kernel">{s.kernel || '—'}</KV>
          <KV label="Arch / CPUs">{`${s.arch || '—'} · ${s.cpus ?? '—'}`}</KV>
          <KV label="Hostname">{s.hostname || '—'}</KV>
          <KV label="Memory">{`${s.memory_gb != null ? `${s.memory_gb} GB` : '—'} · ${pct(s.memory_used_pct)} used`}</KV>
          <KV label="Max disk used">{pct(s.max_disk_used_pct)}</KV>
          <KV label="Uptime">{s.uptime_days != null ? `${s.uptime_days} d` : '—'}</KV>
          <KV label="Load">{s.load != null ? String(s.load) : '—'}</KV>
          <KV label="Services">{String(s.services ?? 0)}</KV>
          {s.provides?.length ? <KV label="Provides">{s.provides.join(', ')}</KV> : null}
        </Panel>

        <Panel title="Policy" testID="orch-node-policy">
          <KV label="Sandbox">{node.sandbox || '—'}</KV>
          {policy.map(([k, v]) => k === 'sandbox' ? null : <KV key={k} label={k}>{v}</KV>)}
          {node.policy_errors?.length ? (
            <Banner tone="danger" glyph={STATUS.failed.icon} title="Policy errors">
              {node.policy_errors.map((e, i) => <Text key={i} variant="xs">{e}</Text>)}
            </Banner>
          ) : null}
          {!policy.length && !node.policy_errors?.length ? <Empty>No policy reported.</Empty> : null}
        </Panel>

        <Panel title="Recent executions" badge={node.recent_executions?.length || 0} flush testID="orch-node-execs">
          {node.recent_executions?.length ? node.recent_executions.map(ex => (
            <Pressable key={ex.id} testID={`orch-node-exec-${ex.id}`} accessibilityRole="button"
              accessibilityLabel={`${ex.tool} ${ex.style?.label || ex.state}. Open run`}
              onPress={() => navigation.navigate('OrchRun', { runId: ex.run_id })}
              style={({ pressed }) => [styles.execRow, { borderBottomColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
              <View style={styles.rowTop}>
                <Text variant="sm" style={{ fontFamily: fonts.mono, fontWeight: fontWeights.medium, flexShrink: 1 }} numberOfLines={1}>{ex.tool}</Text>
                <View style={{ flex: 1 }} />
                <OrchStateChip table="orch_exec_state" value={ex.state} style={ex.style} />
              </View>
              <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>
                {[ex.step_name, when(ex.finished_at || ex.created_at), ex.exit_code != null ? `exit ${ex.exit_code}` : '', ex.reason].filter(Boolean).join(' · ')}
              </Text>
            </Pressable>
          )) : <View style={{ paddingHorizontal: spacing.base }}><Empty>No executions on this node yet.</Empty></View>}
        </Panel>
      </ScrollView>
      {sheets}
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing.xl * 2 },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', minHeight: 28 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xs },
  execRow: { paddingHorizontal: spacing.base, paddingVertical: spacing.md, gap: 2, borderBottomWidth: StyleSheet.hairlineWidth },
});
