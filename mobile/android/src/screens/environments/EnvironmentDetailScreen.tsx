// An environment's page (web /containers, containers.js envMain + aside): the state sentence, the
// actions (Terminal · Start/Stop, the rest under More), live charts (CPU, memory, network, disk),
// the monitor facts, attached sessions and the configuration. Secret-scan blocks from job build
// environments show first: they need you.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { containersApi, type ContainerStats, type ManagedContainer } from '../../api/containers';
import { useConnectionStore } from '../../store/connection';
import { spacing } from '../../theme/tokens';
import {
  ActionBar, Banner, BoardSection, Button, EmptyState, ErrorState, KV, PageHeader, Pre, Row, Sparkline, Text,
  useInterval, useServerActions, useToast, type ServerAction,
} from '../../ui';
import { appendSample, envState, formatBytes, formatRate, statusWord, type Sample } from './envModel';

export function environmentActions(ref: ManagedContainer): ServerAction[] {
  const running = ref.status === 'running';
  return [
    { id: 'shell', label: 'Terminal', primary: true },
    { id: running ? 'stop' : 'start', label: running ? 'Stop' : 'Start', primary: true },
    { id: 'configure', label: 'Configure' },
    { id: 'restart', label: 'Restart', enabled: running, reason: 'Start it first' },
    { id: 'clone', label: 'Clone' },
    { id: 'attach', label: 'Attach session', input: { arg: 'session_name', label: 'Session name' } },
    { id: 'snapshot', label: 'Create template', input: { arg: 'template_name', label: 'Template name' } },
    { id: 'remove', label: 'Remove environment', danger: true, confirm: `Remove ${ref.name} and its persistent volumes? Attached sessions lose their environment.` },
  ];
}

export function EnvironmentDetailScreen() {
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const name: string = route.params?.name;
  const focused = useIsFocused();
  const isConnected = useConnectionStore(s => s.isConnected);
  const toast = useToast();
  const { ask, element } = useServerActions();
  const [ref, setRef] = useState<ManagedContainer | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState('');
  const [live, setLive] = useState<ContainerStats | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [pollMs, setPollMs] = useState(2500);
  const [busy, setBusy] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const previous = useRef<ContainerStats | null>(null);

  const load = useCallback(async () => {
    try {
      const listing = await containersApi.list();
      const found = listing.containers.find(c => c.name === name) || null;
      setRef(found);
      setMissing(!found);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [name]);

  const poll = useCallback(async () => {
    try {
      const res = await containersApi.stats();
      setPollMs(Math.max(1500, res.poll_after_ms || 2500));
      const next = res.containers?.[name] || null;
      setLive(next);
      if (next && next.status === 'running' && next.sampled_at !== previous.current?.sampled_at) {
        setSamples(s => appendSample(s, next, previous.current));
        previous.current = next;
      }
    } catch { /* keep the last sample */ }
  }, [name]);

  useEffect(() => { if (isConnected && focused) { void load(); void poll(); } }, [isConnected, focused, load, poll]);
  useInterval(() => { void poll(); }, pollMs, isConnected && focused && ref?.status === 'running');

  const run = useCallback(async (action: ServerAction) => {
    if (!ref) return;
    if (action.id === 'shell') { navigation.navigate('Shell', { container: ref.name }); return; }
    if (action.id === 'configure' || action.id === 'clone') { navigation.navigate('EnvironmentForm', { mode: action.id === 'clone' ? 'clone' : 'edit', name: ref.name }); return; }
    const args = await ask(action);
    if (!args) return;
    setBusy(action.id);
    try {
      if (action.id === 'start' || action.id === 'stop' || action.id === 'restart') await containersApi.action(ref.name, action.id);
      else if (action.id === 'attach') await containersApi.attach(ref.name, String(args.session_name || ''));
      else if (action.id === 'snapshot') { await containersApi.snapshot(ref.name, String(args.template_name || '')); toast(`Template ${String(args.template_name)} created`, 'ok'); }
      else if (action.id === 'remove') { await containersApi.remove(ref.name); toast(`${ref.name} removed`, 'ok'); navigation.goBack(); return; }
      await load();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally {
      setBusy('');
    }
  }, [ask, load, navigation, ref, toast]);

  const detach = useCallback(async (session: string) => {
    if (!ref) return;
    try { await containersApi.detach(ref.name, session); await load(); }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
  }, [load, ref, toast]);

  const back = { label: 'Environments', onPress: () => navigation.goBack() };

  if (error && !ref) return <View style={styles.page}><PageHeader title={name} back={back} /><ErrorState message={error} onRetry={() => void load()} /></View>;
  if (missing) return <View style={styles.page}><PageHeader title={name} back={back} /><EmptyState title="This environment is gone" message="It was removed, or renamed on the computer." icon="box" pigment="meadow" /></View>;

  const running = ref?.status === 'running';
  const last = samples[samples.length - 1];
  return (
    <View style={styles.page} testID="environment-detail">
      <PageHeader title={name} back={back} state={ref ? envState(ref, live).map((text, i) => ({ text, tone: i === 0 && !running ? 'quiet' : '' })) : undefined} testID="environment-header" />
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); await poll(); setRefreshing(false); }} />}>
        {ref ? <ActionBar testID="environment-actions" actions={environmentActions(ref)} onAction={a => void run(a)} /> : null}
        {busy ? <Text variant="caption" tone="ink3">{busy === 'stop' ? 'Stopping…' : busy === 'start' ? 'Starting…' : 'Working…'}</Text> : null}

        {(ref?.job_scan_blocks || []).map(block => (
          <Banner key={`${block.job_id}-${block.binding}`} tone="attention" title={`Secret scan blocked job #${String(block.job_id).slice(0, 8)}`}
            action={{ label: 'Open job', onPress: () => navigation.navigate('JobDetail', { jobId: block.job_id }) }} testID="scan-block">
            {`Build environment ${block.binding}: remove these from the environment, or allow them in Work › Settings › Build environments.\n${(block.paths || []).join('\n')}`}
          </Banner>
        ))}

        {ref ? (
          <BoardSection title="Live">
            {running ? (
              <View style={styles.charts} testID="environment-charts">
                <Chart label="CPU" value={last ? `${Math.round(last.cpu)}%` : '—'} values={samples.map(s => s.cpu)} max={100} />
                <Chart label="Memory" value={live ? `${formatBytes(live.memory_used_bytes)} of ${formatBytes(live.memory_limit_bytes)}` : '—'} values={samples.map(s => s.mem)} max={100} />
                <Chart label="Network" value={last ? `↓ ${formatRate(last.rx)}  ↑ ${formatRate(last.tx)}` : '—'} values={samples.map(s => s.rx + s.tx)} />
                <Chart label="Disk I/O" value={last ? `read ${formatRate(last.r)}  write ${formatRate(last.w)}` : '—'} values={samples.map(s => s.r + s.w)} />
              </View>
            ) : (
              <Text variant="caption" tone="ink3" testID="environment-stopped">{statusWord(ref.status)}. Start it to see live use.</Text>
            )}
            {live ? (
              <View style={styles.facts} testID="environment-facts">
                {running ? <KV label="Processes">{String(live.pids)}</KV> : null}
                <KV label="Disk">{`${formatBytes(live.storage_writable_bytes)} written`}</KV>
                {live.restart_count ? <KV label="Restarts">{String(live.restart_count)}</KV> : null}
                {live.gpu?.requested ? <KV label="GPU">{`${live.gpu.utilization_percent ?? 0}% · ${formatBytes(live.gpu.memory_used_bytes)} of ${formatBytes(live.gpu.memory_total_bytes)}`}</KV> : null}
                {live.attached_device_count ? <KV label="Devices">{String(live.attached_device_count)}</KV> : null}
                {live.error ? <KV label="Monitor">{live.error}</KV> : null}
              </View>
            ) : null}
          </BoardSection>
        ) : null}

        {ref ? (
          <BoardSection title="Sessions" count={ref.attached_sessions.length} empty="No sessions attached.">
            {ref.attached_sessions.map(session => (
              <Row key={session} title={session} testID={`session-${session}`}
                actions={<Button label="Detach" variant="ghost" size="md" onPress={() => void detach(session)} accessibilityLabel={`Detach ${session}`} testID={`detach-${session}`} />} />
            ))}
          </BoardSection>
        ) : null}

        {ref ? (
          <BoardSection title="Configuration">
            <View style={styles.facts}>
              <KV label="Template">{ref.template_name || 'custom'}</KV>
              <KV label="Image"><Pre>{ref.image}</Pre></KV>
              {ref.network_name ? <KV label="Network">{ref.network_name}</KV> : null}
              {ref.worker_port ? <KV label="Worker">{String(ref.worker_port)}</KV> : null}
              {ref.mounts?.length ? <KV label="Folders">{ref.mounts.map(m => `${m.host_path} → ${m.container_path} (${m.mode})`).join('\n')}</KV> : null}
              {ref.egress_allow?.length ? <KV label="Network allow">{ref.egress_allow.join(', ')}</KV> : null}
              {ref.jobs_using ? <KV label="Jobs">{`used by ${ref.jobs_using}`}</KV> : null}
            </View>
          </BoardSection>
        ) : null}
        {!ref ? <Text variant="caption" tone="ink3">Loading…</Text> : null}
      </ScrollView>
      {element}
    </View>
  );
}

function Chart({ label, value, values, max }: { label: string; value: string; values: number[]; max?: number }) {
  return (
    <View style={styles.chart} testID={`chart-${label}`}>
      <View style={styles.chartHead}>
        <Text variant="label" tone="ink2">{label}</Text>
        <Text variant="caption" numeric>{value}</Text>
      </View>
      <Sparkline values={values.length ? values : [null]} max={max} height={40} accessibilityLabel={`${label}: ${value}`} />
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['4xl'], gap: spacing.md },
  charts: { gap: spacing.md },
  chart: { gap: spacing.xs },
  chartHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: spacing.sm },
  facts: { marginTop: spacing.sm },
});
