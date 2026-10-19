// Node health: every enrolled node with its status chip, online state, last
// seen, OS, max disk % and memory %, and labels. Tap for the node detail.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { orchestrationApi, type OrchAgentsSummary, type OrchNode } from '../api/orchestration';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { spacing } from '../theme/tokens';
import { Banner, Empty, Loading, Panel, useToast } from '../ui';
import { NodeHealthRow, useOrchLive } from '../components/OrchViews';

export type OrchNodesScreenProps = NativeStackScreenProps<RootStackParamList, 'OrchNodes'>;

/** Needs-you first (pending, offline, quarantined), then active, then revoked; by name within. */
const ORDER: Record<string, number> = { pending: 0, offline: 1, quarantined: 2, active: 3, revoked: 4 };

export function sortNodes(nodes: OrchNode[]): OrchNode[] {
  const key = (n: OrchNode) => (n.status === 'active' && !n.online ? 'offline' : n.status);
  return [...nodes].sort((a, b) => (ORDER[key(a)] ?? 5) - (ORDER[key(b)] ?? 5) || (a.name || a.id).localeCompare(b.name || b.id));
}

export function OrchNodesScreen({ navigation }: OrchNodesScreenProps) {
  const { colors } = useTheme();
  const [nodes, setNodes] = useState<OrchNode[] | null>(null);
  const [agents, setAgents] = useState<OrchAgentsSummary | null>(null);
  const toast = useToast();
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await orchestrationApi.nodes();
      setNodes(res.nodes || []); setAgents(res.agents || null); setError('');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setRefreshing(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useOrchLive(() => { void load(); }, change => change.scope === 'node');
  const sorted = useMemo(() => sortNodes(nodes || []), [nodes]);
  const visible = sorted.filter(n => n.status !== 'revoked');
  const revoked = sorted.length - visible.length;

  return (
    <ScrollView contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={colors.ink3} />}>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!nodes && !error ? <Loading label="Loading nodes…" /> : null}
      <AgentBanner agents={agents}
        onOpenRun={runId => navigation.navigate('OrchRun', { runId })}
        onUpdate={async () => {
          try {
            const res = await orchestrationApi.proposeAgentUpdate();
            const run = res.data.run as { id: string; state: string };
            toast(run.state === 'proposed' ? 'Update job proposed: approve it to start.' : 'Update job started.', 'ok');
            navigation.navigate('OrchRun', { runId: run.id });
          } catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
        }} />
      {nodes ? (
        <Panel title="Nodes" badge={visible.length} flush testID="orch-nodes">
          {visible.length ? visible.map(node => (
            <NodeHealthRow key={node.id} node={node} onPress={() => navigation.navigate('OrchNode', { nodeId: node.id })} />
          )) : <View style={{ paddingHorizontal: spacing.base }}><Empty>No nodes enrolled. Create a join token on the web Orchestration page.</Empty></View>}
          {revoked ? <View style={{ paddingHorizontal: spacing.base }}><Empty status="skipped">{`${revoked} revoked node${revoked === 1 ? '' : 's'} hidden.`}</Empty></View> : null}
        </Panel>
      ) : null}
    </ScrollView>
  );
}

/** Nodes running an older agent than the master: an update job, or the open one. */
export function AgentBanner({ agents, onUpdate, onOpenRun }: { agents: OrchAgentsSummary | null; onUpdate: () => void; onOpenRun: (runId: string) => void }) {
  if (!agents?.outdated) return null;
  if (agents.open_run) {
    return <Banner tone="info" glyph="●" testID="orch-agents-open" title={`An agent update job is open (agent ${agents.current}).`}
      action={{ label: 'Open the update job', onPress: () => onOpenRun(agents.open_run), testID: 'orch-agents-open-run' }} />;
  }
  const manual = agents.outdated - agents.updatable;
  const title = `${agents.outdated} node${agents.outdated === 1 ? ' runs' : 's run'} an older agent than the master (${agents.current}).`
    + (manual ? ` ${manual} need a manual update or allow_agent_updates: true in policy.yaml.` : '');
  return <Banner tone="warn" glyph="◆" testID="orch-agents" title={title}
    action={agents.updatable ? { label: 'Update agents', onPress: onUpdate, testID: 'orch-agents-update' } : undefined} />;
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing.xl * 2 },
});
