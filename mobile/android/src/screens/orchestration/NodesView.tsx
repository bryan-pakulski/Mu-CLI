// Nodes: the node dashboard (web nodes.js + the Nodes dashboard; vm.fleet_dashboard). On top, the
// scope — All nodes, each group with its health, Ungrouped, + New group (groups.tsx). Then, for that
// scope: what's true in one sentence, resource meters (memory, disk, load per CPU — average and
// worst), the last day's runs, and every node worst-first with the reasons in words, what it's
// running and its state. Long-press a node (or its ⋯) to move it between groups; "Run on this
// group" proposes a run aimed at the group. For the whole fleet it also shows where nodes connect
// and agent drift, and pending nodes are accepted inline after a check of their name and labels.
// Each node row carries its own mini charts (CPU, memory, network — from the same metrics poll as
// the History block), and the rows sort by health or by what's working hardest now.

import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import {
  orchestrationApi, type OrchAction, type OrchAgentsSummary, type OrchDashboard, type OrchDashboardNode, type OrchMetrics, type OrchNode,
  type OrchNodeSpark, type OrchScope,
} from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import {
  Banner, Button, IconButton, KV, Loading, Meter, MiniChart, PrimaryPill, Row, Rows, Segmented, StateSentence, Text, TextField, formatMetric,
  useToast, type StatePart,
} from '../../ui';
import { GroupPicker, GroupRunSheet, GroupSheet, scopeLabel, useGroupMove } from './groups';
import { ScopeHistory, useScopeHistory } from './history';
import { NodeRow, RunRow, ago, busyWords, cap, errorText, nodeStateKey, plural, useOrchActions } from './parts';
import { useOrchData } from './useOrchData';

export const NODE_STATES = ['active', 'connecting', 'offline', 'pending', 'quarantined', 'revoked'];

type Endpoint = { bind?: string; port?: number; running?: boolean; error?: string; kind?: string };

/** Accept, with the web's check: the name and labels must match the machine you enrolled. */
export function acceptWithCheck(action: OrchAction, n: OrchNode): OrchAction {
  const labels = Object.entries(n.labels || {}).map(([k, v]) => `${k}=${v}`).join(', ') || 'no labels';
  return { ...action, label: 'Accept node', confirm: `${n.name} will receive work that targets its labels (${labels}). Check the name and labels match the machine you enrolled.` };
}

/** The scope in one sentence: nodes, presence, health, live work (zeros left out). */
export function dashboardState(d: OrchDashboard | null): StatePart[] {
  if (!d) return [];
  const t = d.totals;
  if (!t.nodes) return [{ text: d.scope.group === null ? 'No nodes yet' : 'No nodes in this group' }];
  const parts: StatePart[] = [{ text: plural(t.nodes, 'node') }];
  if (t.presence.offline) parts.push({ text: `${t.presence.offline} offline`, tone: 'danger' });
  if (t.presence.connecting) parts.push({ text: `${t.presence.connecting} connecting`, tone: 'quiet' });
  if (t.health.critical) parts.push({ text: `${t.health.critical} critical`, tone: 'danger' });
  if (t.health.warning) parts.push({ text: `${t.health.warning} warning`, tone: 'attention' });
  if (t.pending) parts.push({ text: `${t.pending} awaiting acceptance`, tone: 'attention' });
  if (!t.health.critical && !t.health.warning && !t.pending) parts.push({ text: 'all healthy', tone: 'ok' });
  if (t.busy) parts.push({ text: `${t.busy} busy` });
  return parts;
}

/** A dashboard row's sub line: the reasons, else what it's running and its machine. */
export function healthSub(n: OrchDashboardNode): string {
  const busy = n.busy ? busyWords(n.busy) : '';
  if (n.health.reasons.length) return [n.health.reasons.join(' · '), busy].filter(Boolean).join(' · ');
  return [busy, n.os, n.memory_used_pct != null ? `mem ${Math.round(n.memory_used_pct)}%` : '',
    n.max_disk_used_pct != null ? `disk ${Math.round(n.max_disk_used_pct)}%` : ''].filter(Boolean).join(' · ');
}

const pct = (v: number | null | undefined) => (v == null ? '–' : `${Math.round(v)}%`);

export type NodeSort = 'health' | 'cpu' | 'memory' | 'network';
export const NODE_SORTS: Array<{ value: NodeSort; label: string }> = [
  { value: 'health', label: 'Health' }, { value: 'cpu', label: 'CPU' }, { value: 'memory', label: 'Memory' }, { value: 'network', label: 'Network' },
];
const SORT_KEY = { cpu: 'cpu_pct', memory: 'mem_pct', network: 'net_bps' } as const;
export const CPU_HIGH = 85;
export const MEM_HIGH = 90;

/** Rows in the chosen order: health keeps the server's worst-first order; the others put the
 * highest current value first (nodes without history last, in health order). */
export function sortRows<T extends { id: string }>(rows: T[], sort: NodeSort, spark: Record<string, OrchNodeSpark> | undefined): T[] {
  if (sort === 'health' || !spark) return rows;
  const key = SORT_KEY[sort];
  const value = (id: string) => spark[id]?.latest?.[key] ?? null;
  return rows.map((r, i) => ({ r, i })).sort((a, b) => {
    const va = value(a.r.id); const vb = value(b.r.id);
    if (va == null && vb == null) return a.i - b.i;
    if (va == null) return 1;
    if (vb == null) return -1;
    return vb - va || a.i - b.i;
  }).map(x => x.r);
}

/** A row's numbers in words, for screen readers. */
export function sparkWords(s: OrchNodeSpark | undefined): string {
  if (!s || !s.points.length) return 'no history';
  const l = s.latest;
  return [`CPU ${formatMetric(l.cpu_pct, 'percent')}`, `memory ${formatMetric(l.mem_pct, 'percent')}`, `network ${formatMetric(l.net_bps, 'bytes_per_s')}`].join(', ');
}

/** The mini charts under a node row: CPU and memory on 0–100, network on the scope's shared scale. */
function NodeSparks({ id, spark, metrics, netMax }: { id: string; spark: OrchNodeSpark | undefined; metrics: OrchMetrics; netMax: number }) {
  if (!spark || !spark.points.length) {
    return <Text variant="caption" tone="ink3" testID={`orch-node-${id}-nohistory`}>no history</Text>;
  }
  const common = { points: spark.points as never, since: metrics.since, until: metrics.until, bucketS: Math.max(metrics.bucket_s, (metrics.until - metrics.since) / 48) };
  const high = (v: number | null, warn: number) => (v != null && v >= warn ? (v >= Math.min(100, warn + 10) ? 'danger' : 'attention') : null);
  return (
    <View style={styles.sparks} testID={`orch-node-${id}-sparks`}>
      <MiniChart {...common} label="CPU" valueKey="cpu_pct" unit="percent" max={100} tone={high(spark.latest.cpu_pct, CPU_HIGH)} testID={`orch-node-${id}-cpu`} />
      <MiniChart {...common} label="Mem" valueKey="mem_pct" unit="percent" max={100} tone={high(spark.latest.mem_pct, MEM_HIGH)} testID={`orch-node-${id}-mem`} />
      <MiniChart {...common} label="Net" valueKey="net_bps" unit="bytes_per_s" max={netMax} testID={`orch-node-${id}-net`} />
    </View>
  );
}
const tone = (v: number | null | undefined, warn: number, crit: number) => (v == null ? 'neutral' : v >= crit ? 'danger' : v >= warn ? 'attention' : 'ok');

export function NodesView({ openNode, openRun }: { openNode: (id: string) => void; openRun: (id: string) => void }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [scope, setScope] = useState<OrchScope>(null);
  const [filter, setFilter] = useState('');
  const [q, setQ] = useState('');
  const [newGroup, setNewGroup] = useState<{ members: string[] } | null>(null);
  const [runOnGroup, setRunOnGroup] = useState(false);
  const inv = useOrchData(
    () => orchestrationApi.nodes() as Promise<{ nodes: OrchNode[]; agents?: OrchAgentsSummary; endpoint?: Endpoint }>, ['node', 'run']);
  const dash = useOrchData(() => orchestrationApi.dashboard(scope), ['node', 'run'], [scope]);
  const history = useScopeHistory(scope);
  const [sort, setSort] = useState<NodeSort>('health');
  const spark = history.data?.per_node;
  const netMax = useMemo(() => Math.max(1, ...Object.values(spark || {}).map(s => s.peak?.net_bps ?? 0)) * 1.1, [spark]);
  const { run, element } = useOrchActions();
  const data = inv.data;
  const board = dash.data;
  const reloadAll = () => { void inv.reload(); void dash.reload(); };
  const move = useGroupMove({ scope, groups: board?.groups || [], onChanged: reloadAll, onNewGroup: id => setNewGroup({ members: [id] }) });

  const nodes = data?.nodes || [];
  const byId = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  const inScope = useMemo(() => new Set((board?.nodes || []).map(n => n.id)), [board]);
  const scoped = nodes.filter(n => inScope.has(n.id) || (scope === null && n.status === 'revoked'));
  const counts = useMemo(() => scoped.reduce<Record<string, number>>((acc, n) => { const k = nodeStateKey(n); acc[k] = (acc[k] || 0) + 1; return acc; }, {}), [scoped]);
  const needle = q.trim().toLowerCase();
  const matches = (n: OrchNode) => (!filter || nodeStateKey(n) === filter)
    && (!needle || [n.name, n.id, n.address, Object.entries(n.labels || {}).map(([k, v]) => `${k}=${v}`).join(' ')].join(' ').toLowerCase().includes(needle));
  const rows = sortRows((board?.nodes || []).filter(r => { const n = byId.get(r.id); return n ? matches(n) : !filter && !needle; }), sort, spark);
  const revoked = scope === null ? nodes.filter(n => n.status === 'revoked' && matches(n)) : [];

  const updateAgents = async () => {
    try {
      const res = await orchestrationApi.proposeAgentUpdate();
      const r = res.data.run as { id: string; state: string };
      toast(r.state === 'proposed' ? 'Update job proposed. Review and approve it to start.' : 'Update job started', 'ok');
      openRun(r.id);
    } catch (err) { toast(errorText(err), 'danger'); }
  };

  const group = scope && scope !== '-' ? scope : null;
  const res = board?.resources;
  const th = board?.thresholds || {};
  const error = inv.error || dash.error;
  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.pad} testID="orch-nodes" keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={inv.refreshing || dash.refreshing} onRefresh={() => { inv.refresh(); dash.refresh(); }} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {board ? <GroupPicker scope={scope} onScope={s => { setScope(s); setFilter(''); }} groups={board.groups} fleet={board.fleet} ungrouped={board.ungrouped}
          onNew={() => setNewGroup({ members: [] })} /> : null}
        {(!data || !board) && !error ? <Loading label="Loading nodes…" /> : null}
        {data && board ? (
          <>
            {scope === null ? <EndpointLine ep={data.endpoint} /> : null}
            {scope === null ? <AgentBanner agents={data.agents || null} onUpdate={() => { void updateAgents(); }} onOpenRun={openRun} /> : null}
            <View testID="orch-dashboard" style={styles.section}>
              <Text variant="title3" accessibilityRole="header">{scopeLabel(scope)}</Text>
              <StateSentence parts={dashboardState(board)} testID="orch-dashboard-state" />
              {board.totals.nodes ? (
                <View style={styles.meters} testID="orch-dashboard-meters">
                  <MeterLine label="Memory" avg={res?.memory_used_avg} max={res?.memory_used_max} warn={th.memory_warn ?? 90} crit={th.memory_crit ?? 95} />
                  <MeterLine label="Disk" avg={res?.disk_used_avg} max={res?.disk_used_max} warn={th.disk_warn ?? 85} crit={th.disk_crit ?? 95} />
                  <KV label="Load per CPU">{res?.load_per_cpu_max == null ? '–' : `avg ${res.load_per_cpu_avg} · worst ${res.load_per_cpu_max}`}</KV>
                  <KV label="Last 24 hours">{board.runs_24h.executions
                    ? [plural(board.runs_24h.executions, 'execution'), `${board.runs_24h.succeeded} succeeded`, board.runs_24h.failed ? `${board.runs_24h.failed} failed` : ''].filter(Boolean).join(' · ')
                    : 'No runs'}</KV>
                </View>
              ) : null}
            </View>
            {board.totals.nodes ? <ScopeHistory scope={scope} history={history} /> : null}
            <TextField value={q} onChangeText={setQ} placeholder="Search name, id or label" accessibilityLabel="Search nodes" autoCapitalize="none" autoCorrect={false} testID="orch-nodes-search" />
            {Object.keys(counts).length > 1 ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters} accessibilityLabel="Filter by state">
                <Button size="md" variant={!filter ? 'tonal' : 'ghost'} selected={!filter} label={`All ${scoped.length}`} onPress={() => setFilter('')} testID="orch-nodes-filter-all" />
                {NODE_STATES.filter(s => counts[s]).map(s => (
                  <Button key={s} size="md" variant={filter === s ? 'tonal' : 'ghost'} selected={filter === s}
                    label={`${cap(s === 'connecting' || s === 'offline' ? s : orchState('orch_node_status', s).label)} ${counts[s]}`}
                    onPress={() => setFilter(s)} testID={`orch-nodes-filter-${s}`} />
                ))}
              </ScrollView>
            ) : null}
            {board.totals.nodes > 1 ? (
              <Segmented<NodeSort> accessibilityLabel="Sort nodes by" value={sort} onChange={setSort} testID="orch-nodes-sort"
                options={NODE_SORTS.map(o => ({ value: o.value, label: o.label, testID: `orch-nodes-sort-${o.value}` }))} />
            ) : null}
            <Rows label="Nodes" empty={board.totals.nodes ? 'No nodes match this filter.'
              : scope === null ? 'No nodes enrolled yet. Set one up from Orchestration › Nodes on the computer.'
                : 'No nodes here yet. Long-press a node under All nodes to add it, or edit the group under Groups.'}>
              {rows.map(r => {
                const n = byId.get(r.id);
                const accept = (n?.actions || []).find(x => x.id === 'acceptNode' && x.enabled);
                const open = () => move.open({ id: r.id, name: r.name, groups: r.groups });
                return (
                  <Row key={r.id} testID={`orch-node-${r.id}`} status={orchState('orch_node_health', r.health.level, r.health.style).status}
                    title={`${r.name}${r.canary ? '  ·  canary' : ''}`}
                    sub={<View style={styles.sub}>
                      {healthSub(r) ? <Text variant="caption" tone="ink3" numberOfLines={2}>{healthSub(r)}</Text> : null}
                      {history.data && r.status !== 'pending' ? <NodeSparks id={r.id} spark={spark?.[r.id]} metrics={history.data} netMax={netMax} /> : null}
                    </View>}
                    when={r.last_seen_ago || (n?.last_seen ? ago(n.last_seen) : undefined)}
                    pinned={r.status === 'pending'} onPress={() => openNode(r.id)} onLongPress={open}
                    accessibilityLabel={`${r.name}, ${r.health.style?.label || r.health.level}${r.health.reasons.length ? `: ${r.health.reasons.join(', ')}` : ''}`
                      + (history.data && r.status !== 'pending' ? `; ${sparkWords(spark?.[r.id])}` : '')}
                    actions={<View style={styles.rowActions}>
                      {accept && n ? <Button size="md" variant="tonal" tone="ok" label="Accept" testID={`orch-node-${r.id}-accept`}
                        onPress={() => { void run(acceptWithCheck(accept, n), { args: { node_id: n.id }, done: reloadAll }); }} /> : null}
                      <IconButton icon="folder" label={`Groups for ${r.name}`} onPress={open} testID={`orch-node-${r.id}-group`} />
                    </View>} />
                );
              })}
              {revoked.map(n => <NodeRow key={n.id} node={n} onPress={() => openNode(n.id)} />)}
            </Rows>
            {board.runs_24h.recent.length ? (
              <View style={styles.section} testID="orch-dashboard-runs">
                <Text variant="label" accessibilityRole="header">Recent runs</Text>
                <Rows>
                  {board.runs_24h.recent.map(r => <RunRow key={r.id} run={{ id: r.id, title: r.title, state: r.state, style: r.style } as never} onPress={() => openRun(r.id)} />)}
                </Rows>
              </View>
            ) : null}
            {scope === null && board.ungrouped.nodes ? (
              <Text variant="caption" tone="ink3">{plural(board.ungrouped.nodes, 'node')} in no group · long-press a node to group it.</Text>
            ) : null}
          </>
        ) : null}
        {element}
      </ScrollView>
      {group ? <PrimaryPill label="Run on this group" icon="play" onPress={() => setRunOnGroup(true)} testID="orch-group-run-open" /> : null}
      {move.element}
      <GroupSheet visible={!!newGroup} group={null} initialMembers={newGroup?.members || []} onClose={() => setNewGroup(null)}
        onSaved={name => { setNewGroup(null); reloadAll(); if (name) setScope(name); }} />
      {group ? <GroupRunSheet group={group} visible={runOnGroup} onClose={() => setRunOnGroup(false)}
        onProposed={(runId, message) => { setRunOnGroup(false); toast(message, 'ok'); openRun(runId); }} /> : null}
    </View>
  );
}

/** A resource meter: the average as the bar, the worst node in words. */
function MeterLine({ label, avg, max, warn, crit }: { label: string; avg: number | null | undefined; max: number | null | undefined; warn: number; crit: number }) {
  return (
    <View style={styles.meter} accessible accessibilityLabel={`${label}: average ${pct(avg)}, worst ${pct(max)}`}>
      <Text variant="caption" tone="ink2" style={styles.meterLabel}>{label}</Text>
      <Meter value={avg ?? 0} max={100} tone={tone(max, warn, crit) as never} />
      <Text variant="caption" numeric tone="ink3">avg {pct(avg)} · worst {pct(max)}</Text>
    </View>
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
  pad: { padding: spacing.base, paddingBottom: spacing['3xl'], gap: spacing.sm },
  filters: { gap: spacing.xs },
  endpoint: { gap: spacing.xs },
  section: { gap: spacing.xs },
  meters: { gap: spacing.xs, marginTop: spacing.xs },
  meter: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  meterLabel: { width: 56 },
  rowActions: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  sub: { gap: spacing.xs },
  sparks: { flexDirection: 'row', gap: spacing.md, marginTop: 2 },
});
