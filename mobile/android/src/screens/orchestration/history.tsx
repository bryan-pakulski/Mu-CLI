// Node history on the phone (mu/orchestration/metrics.py): what CPU, memory, disk, load, network
// and disk I/O did over time, for one node (the node screen) or a dashboard scope (the fleet, a
// group, the ungrouped nodes: CPU, memory and load averaged, network and I/O summed, disk the
// fullest). Samples arrive about every 20 s per node with no change event, so the charts poll —
// every HISTORY_POLL_MS, only while the screen is focused and the app in the foreground — and
// refetch when the range changes.

import React, { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AppState, StyleSheet, View } from 'react-native';
import { NavigationContext } from '@react-navigation/native';
import { orchestrationApi, type OrchMetrics, type OrchMetricsRange, type OrchScope } from '../../api/orchestration';
import { spacing } from '../../theme/tokens';
import { Banner, LineChart, Segmented, Text, type ChartSeries, type ChartUnit } from '../../ui';
import { errorText, plural } from './parts';

export const HISTORY_POLL_MS = 30_000;
export const HISTORY_RANGES: Array<{ value: OrchMetricsRange; label: string; words: string }> = [
  { value: '1h', label: '1h', words: 'hour' }, { value: '6h', label: '6h', words: '6 hours' },
  { value: '24h', label: '24h', words: '24 hours' }, { value: '7d', label: '7d', words: '7 days' },
  { value: '30d', label: '30d', words: '30 days' },
];

type ChartDef = { id: string; title: string; unit: ChartUnit; series: ChartSeries[] };
export const HISTORY_CHARTS: ChartDef[] = [
  { id: 'cpu', title: 'CPU', unit: 'percent', series: [{ key: 'cpu_pct', label: 'CPU' }] },
  { id: 'memory', title: 'Memory', unit: 'percent', series: [{ key: 'mem_pct', label: 'Memory' }] },
  { id: 'disk', title: 'Disk (fullest)', unit: 'percent', series: [{ key: 'disk_pct', label: 'Disk' }] },
  { id: 'load', title: 'Load', unit: 'number', series: [{ key: 'load1', label: 'Load' }] },
  { id: 'network', title: 'Network', unit: 'bytes_per_s', series: [{ key: 'net_rx_bps', label: 'In' }, { key: 'net_tx_bps', label: 'Out' }] },
  { id: 'io', title: 'Disk I/O', unit: 'bytes_per_s', series: [{ key: 'disk_read_bps', label: 'Read' }, { key: 'disk_write_bps', label: 'Write' }] },
];
const COMPACT = ['cpu', 'memory', 'disk', 'network'];

/** Whether this screen is the focused one (true outside a navigator, e.g. in a sheet or a test). */
export function useScreenFocused(): boolean {
  const navigation = useContext(NavigationContext);
  const [focused, setFocused] = useState(() => (navigation ? navigation.isFocused() : true));
  useEffect(() => {
    if (!navigation) return undefined;
    setFocused(navigation.isFocused());
    const offFocus = navigation.addListener('focus', () => setFocused(true));
    const offBlur = navigation.addListener('blur', () => setFocused(false));
    return () => { offFocus(); offBlur(); };
  }, [navigation]);
  return focused;
}

function useForeground(): boolean {
  const [active, setActive] = useState(AppState.currentState !== 'background' && AppState.currentState !== 'inactive');
  useEffect(() => {
    const sub = AppState.addEventListener('change', s => setActive(s === 'active'));
    return () => sub.remove();
  }, []);
  return active;
}

/** History for a range, refetched on range change and polled while on screen. */
export function useHistory(load: (range: OrchMetricsRange) => Promise<OrchMetrics>, deps: unknown[]) {
  const [range, setRange] = useState<OrchMetricsRange>('6h');
  const [data, setData] = useState<OrchMetrics | null>(null);
  const [error, setError] = useState('');
  const loadRef = useRef(load);
  loadRef.current = load;
  const focused = useScreenFocused();
  const foreground = useForeground();
  const live = focused && foreground;
  const seq = useRef(0);

  const fetchNow = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const next = await loadRef.current(range);
      if (mine === seq.current) { setData(next); setError(''); }
    } catch (err) {
      if (mine === seq.current) setError(errorText(err));
    }
  }, [range]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setData(null); void fetchNow(); }, [range, ...deps]);
  useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(() => { void fetchNow(); }, HISTORY_POLL_MS);
    return () => clearInterval(timer);
  }, [live, fetchNow]);
  return { data, error, range, setRange };
}

export function RangePicker({ value, onChange, testID }: { value: OrchMetricsRange; onChange: (r: OrchMetricsRange) => void; testID?: string }) {
  return (
    <Segmented<OrchMetricsRange> accessibilityLabel="History range" value={value} onChange={onChange} testID={testID}
      options={HISTORY_RANGES.map(r => ({ value: r.value, label: r.label, testID: testID ? `${testID}-${r.value}` : undefined }))} />
  );
}

export function HistoryCharts({ data, compact, testID }: { data: OrchMetrics; compact?: boolean; testID?: string }) {
  const words = HISTORY_RANGES.find(r => r.value === data.range)?.words || data.range;
  const charts = compact ? HISTORY_CHARTS.filter(c => COMPACT.includes(c.id)) : HISTORY_CHARTS;
  return (
    <View style={styles.charts} testID={testID}>
      {charts.map(c => (
        <LineChart key={c.id} testID={testID ? `${testID}-${c.id}` : undefined} title={c.title} unit={c.unit} series={c.series}
          points={data.points} since={data.since} until={data.until} bucketS={data.bucket_s} rangeLabel={words} />
      ))}
    </View>
  );
}

/** The node screen's History section. */
export function NodeHistory({ nodeId, nodeName, onUpdateAgent }: { nodeId: string; nodeName: string; onUpdateAgent?: () => void }) {
  const { data, error, range, setRange } = useHistory(r => orchestrationApi.nodeMetrics(nodeId, r), [nodeId]);
  const outdated = !!data && data.needs_update.includes(nodeName);
  return (
    <View style={styles.section} testID="orch-node-history">
      <View style={styles.head}>
        <Text variant="title3" accessibilityRole="header" style={{ flex: 1 }}>History</Text>
      </View>
      <RangePicker value={range} onChange={setRange} testID="orch-node-history-range" />
      {error ? <Text variant="caption" tone="danger">✗ {error}</Text> : null}
      {outdated ? (
        <Banner tone="attention" glyph="◆" testID="orch-node-history-outdated" title="No history from this node yet"
          action={onUpdateAgent ? { label: 'Update agent', onPress: onUpdateAgent, testID: 'orch-node-history-update' } : undefined}>
          Its agent predates 1.3.0, which reports CPU, memory, disk, network and disk I/O over time.
        </Banner>
      ) : null}
      {data && !data.points.length && !outdated ? (
        <Text variant="caption" tone="ink3" testID="orch-node-history-empty">No history yet. The node sends a sample about every 20 seconds.</Text>
      ) : null}
      {data && data.points.length ? <HistoryCharts data={data} testID="orch-node-history-charts" /> : null}
    </View>
  );
}

export type HistoryState = ReturnType<typeof useHistory>;

/** The dashboard's metrics for a scope, with each node's small series (one poll serves the History
 * block and the health rows' mini charts). */
export function useScopeHistory(scope: OrchScope): HistoryState {
  return useHistory(r => orchestrationApi.dashboardMetrics(scope, r, true), [scope]);
}

/** The dashboard's History block for a scope (the fleet, a group, the ungrouped nodes). Pass
 * `history` to share a poll (NodesView); without it the block fetches its own. */
export function ScopeHistory({ scope, history }: { scope: OrchScope; history?: HistoryState }) {
  return history ? <ScopeHistoryView history={history} /> : <OwnScopeHistory scope={scope} />;
}

function OwnScopeHistory({ scope }: { scope: OrchScope }) {
  const history = useHistory(r => orchestrationApi.dashboardMetrics(scope, r), [scope]);
  return <ScopeHistoryView history={history} />;
}

function ScopeHistoryView({ history }: { history: HistoryState }) {
  const { data, error, range, setRange } = history;
  return (
    <View style={styles.section} testID="orch-dashboard-history">
      <Text variant="label" accessibilityRole="header">History</Text>
      <RangePicker value={range} onChange={setRange} testID="orch-dashboard-history-range" />
      {data && data.nodes > 1 ? (
        <Text variant="caption" tone="ink3">CPU, memory and load are averaged across {plural(data.nodes, 'node')}; network and disk I/O are summed; disk is the fullest.</Text>
      ) : null}
      {data && data.needs_update.length ? (
        <Text variant="caption" tone="attention" testID="orch-dashboard-history-outdated">
          ◆ {plural(data.needs_update.length, 'node')} without history (agent before 1.3.0): {data.needs_update.join(', ')}
        </Text>
      ) : null}
      {error ? <Text variant="caption" tone="danger">✗ {error}</Text> : null}
      {data && !data.points.length ? <Text variant="caption" tone="ink3" testID="orch-dashboard-history-empty">No history yet for these nodes.</Text> : null}
      {data && data.points.length ? <HistoryCharts data={data} compact testID="orch-dashboard-history-charts" /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: spacing.sm },
  head: { flexDirection: 'row', alignItems: 'center' },
  charts: { gap: spacing.lg },
});
