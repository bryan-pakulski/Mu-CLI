// Nodes (web nodes.js renderInventory + the fleet line): where nodes connect, agent drift, then every
// node as a row — presence (connecting / online / offline), what it's running, meters, reboot and
// agent flags — searchable and filtered by state. Pending nodes are accepted inline, after a check
// of their name and labels.

import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchAction, type OrchAgentsSummary, type OrchNode } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, Button, Loading, Rows, Text, TextField, useToast } from '../../ui';
import { NodeRow, cap, errorText, nodeStateKey, plural, useOrchActions } from './parts';
import { useOrchData } from './useOrchData';

export const NODE_STATES = ['active', 'connecting', 'offline', 'pending', 'quarantined', 'revoked'];

type Endpoint = { bind?: string; port?: number; running?: boolean; error?: string; kind?: string };

/** Accept, with the web's check: the name and labels must match the machine you enrolled. */
export function acceptWithCheck(action: OrchAction, n: OrchNode): OrchAction {
  const labels = Object.entries(n.labels || {}).map(([k, v]) => `${k}=${v}`).join(', ') || 'no labels';
  return { ...action, label: 'Accept node', confirm: `${n.name} will receive work that targets its labels (${labels}). Check the name and labels match the machine you enrolled.` };
}

export function NodesView({ openNode, openRun }: { openNode: (id: string) => void; openRun: (id: string) => void }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [filter, setFilter] = useState('');
  const [q, setQ] = useState('');
  const { data, error, refreshing, refresh, reload } = useOrchData(
    () => orchestrationApi.nodes() as Promise<{ nodes: OrchNode[]; agents?: OrchAgentsSummary; endpoint?: Endpoint }>, ['node', 'run']);
  const { run, element } = useOrchActions();
  const nodes = data?.nodes || [];
  const counts = useMemo(() => nodes.reduce<Record<string, number>>((acc, n) => { const k = nodeStateKey(n); acc[k] = (acc[k] || 0) + 1; return acc; }, {}), [nodes]);
  const needle = q.trim().toLowerCase();
  const shown = nodes.filter(n => (!filter || nodeStateKey(n) === filter)
    && (!needle || [n.name, n.id, n.address, Object.entries(n.labels || {}).map(([k, v]) => `${k}=${v}`).join(' ')].join(' ').toLowerCase().includes(needle)));

  const updateAgents = async () => {
    try {
      const res = await orchestrationApi.proposeAgentUpdate();
      const r = res.data.run as { id: string; state: string };
      toast(r.state === 'proposed' ? 'Update job proposed. Review and approve it to start.' : 'Update job started', 'ok');
      openRun(r.id);
    } catch (err) { toast(errorText(err), 'danger'); }
  };

  return (
    <ScrollView contentContainerStyle={styles.pad} testID="orch-nodes" keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!data && !error ? <Loading label="Loading nodes…" /> : null}
      {data ? (
        <>
          <EndpointLine ep={data.endpoint} />
          <AgentBanner agents={data.agents || null} onUpdate={() => { void updateAgents(); }} onOpenRun={openRun} />
          <TextField value={q} onChangeText={setQ} placeholder="Search name, id or label" accessibilityLabel="Search nodes" autoCapitalize="none" autoCorrect={false} testID="orch-nodes-search" />
          {Object.keys(counts).length > 1 ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters} accessibilityLabel="Filter by state">
              <Button size="md" variant={!filter ? 'tonal' : 'ghost'} selected={!filter} label={`All ${nodes.length}`} onPress={() => setFilter('')} testID="orch-nodes-filter-all" />
              {NODE_STATES.filter(s => counts[s]).map(s => (
                <Button key={s} size="md" variant={filter === s ? 'tonal' : 'ghost'} selected={filter === s}
                  label={`${cap(s === 'connecting' || s === 'offline' ? s : orchState('orch_node_status', s).label)} ${counts[s]}`}
                  onPress={() => setFilter(s)} testID={`orch-nodes-filter-${s}`} />
              ))}
            </ScrollView>
          ) : null}
          <Rows label="Nodes" empty={nodes.length ? 'No nodes match this filter.' : 'No nodes enrolled yet. Set one up from Orchestration › Nodes on the computer.'}>
            {shown.map(n => {
              const accept = (n.actions || []).find(x => x.id === 'acceptNode' && x.enabled);
              return <NodeRow key={n.id} node={n} onPress={() => openNode(n.id)}
                actions={accept ? <Button size="md" variant="tonal" tone="ok" label="Accept" testID={`orch-node-${n.id}-accept`}
                  onPress={() => { void run(acceptWithCheck(accept, n), { args: { node_id: n.id }, done: () => { void reload(); } }); }} /> : undefined} />;
            })}
          </Rows>
        </>
      ) : null}
      {element}
    </ScrollView>
  );
}

/** Where nodes connect, in one line (web nodes.endpointLine). */
function EndpointLine({ ep }: { ep?: Endpoint }) {
  if (!ep) return null;
  const head = ep.running ? `Listening on ${ep.bind}:${ep.port}` : ep.error ? 'Node endpoint not running' : 'Node endpoint not configured';
  return (
    <View testID="orch-endpoint" style={styles.endpoint}>
      <Text variant="caption" tone={ep.running ? 'ink2' : ep.error ? 'danger' : 'attention'}>{ep.running ? '✓' : ep.error ? '✗' : '◆'} {head}{ep.kind ? ` · ${ep.kind}` : ''}</Text>
      {ep.error ? <Text variant="caption" tone="danger">{ep.error}</Text> : null}
      {!ep.bind ? <Text variant="caption" tone="ink3">Nodes reach the master on a private address you choose (Settings › Network on the computer).</Text> : null}
    </View>
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
  const title = `${plural(agents.outdated, 'node runs', 'nodes run')} an older agent than the master (${agents.current}).`
    + (manual ? ` ${manual} need a manual update or allow_agent_updates: true in policy.yaml.` : '') + (agents.auto ? ' Automatic updates are on.' : '');
  return <Banner tone="attention" glyph="◆" testID="orch-agents" title={title}
    action={agents.updatable ? { label: 'Update agents', onPress: onUpdate, testID: 'orch-agents-update' } : undefined} />;
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.sm },
  filters: { gap: spacing.xs },
  endpoint: { gap: spacing.xs },
});
