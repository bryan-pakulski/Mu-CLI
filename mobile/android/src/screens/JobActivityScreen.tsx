import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { ApiError } from '../api/client';
import { isMissingEndpoint, jobCoreApi, type ActivityContainer, type ActivityJob, type ActivityLog, type ActivityVM } from '../api/jobCore';
import { JobsNavBar, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing } from '../theme/tokens';
import { ACTIVE_STATES, bytes, duration, money, stateLabel, stateStatus } from '../theme/status';
import { Banner, Button, Empty, Loading, Panel, Pill, Pre, Sheet, Sparkline, StatusIcon, StatusPill, Text, useToast, Icon, type IconName } from '../ui';

export type JobActivityScreenProps = NativeStackScreenProps<RootStackParamList, 'JobActivity'>;

/** Samples kept per container for the CPU/memory sparklines (last 60). */
export const SPARK_SAMPLES = 60;
/** Poll interval while the screen is shown (5 s docker poll). */
export const ACTIVITY_POLL_MS = 5000;

type Samples = Record<string, { cpu: Array<number | null>; mem: Array<number | null> }>;

/** Pure (tested): append one sample per container, keep the last N, drop vanished containers. */
export function appendSamples(prev: Samples, containers: ActivityContainer[], limit = SPARK_SAMPLES): Samples {
  const next: Samples = {};
  for (const c of containers) {
    const old = prev[c.name] || { cpu: [], mem: [] };
    next[c.name] = {
      cpu: [...old.cpu, c.cpu_percent ?? null].slice(-limit),
      mem: [...old.mem, c.mem_bytes ?? null].slice(-limit),
    };
  }
  return next;
}

const KIND_ICON: Record<string, IconName> = {
  agent: 'cpu', checks: 'hammer', provision: 'refresh-cw', push: 'cloud-upload', waiting: 'pause',
};

/** Wash Activity: jobs → agent/checks/containers with CPU/mem sparklines, log viewer, remove container. */
export function JobActivityScreen({ navigation }: JobActivityScreenProps) {
  const { colors } = useTheme();
  const toast = useToast();
  const [vm, setVm] = useState<ActivityVM | null>(null);
  const [samples, setSamples] = useState<Samples>({});
  const [error, setError] = useState('');
  const [unsupported, setUnsupported] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [logJob, setLogJob] = useState<ActivityJob | null>(null);
  const [log, setLog] = useState<ActivityLog | null>(null);
  const [logBusy, setLogBusy] = useState(false);
  const [removing, setRemoving] = useState<ActivityContainer | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const next = await jobCoreApi.activity();
      if (!alive.current) return;
      setVm(next);
      setSamples(prev => appendSamples(prev, [...next.jobs.flatMap(j => j.containers || []), ...(next.orphans || [])]));
      setError('');
    } catch (err) {
      if (!alive.current) return;
      if (isMissingEndpoint(err)) setUnsupported(true);
      else setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    let timer: ReturnType<typeof setInterval> | null = setInterval(() => { void load(); }, ACTIVITY_POLL_MS);
    // Poll only while the app is in the foreground and this screen is focused.
    const sub = AppState.addEventListener?.('change', state => {
      if (state === 'active' && !timer) { void load(); timer = setInterval(() => { void load(); }, ACTIVITY_POLL_MS); }
      if (state !== 'active' && timer) { clearInterval(timer); timer = null; }
    });
    const blur = navigation.addListener?.('blur', () => { if (timer) { clearInterval(timer); timer = null; } });
    const focus = navigation.addListener?.('focus', () => { if (!timer) { void load(); timer = setInterval(() => { void load(); }, ACTIVITY_POLL_MS); } });
    return () => {
      alive.current = false;
      if (timer) clearInterval(timer);
      sub?.remove?.();
      if (typeof blur === 'function') blur();
      if (typeof focus === 'function') focus();
    };
  }, [load, navigation]);

  const openLog = useCallback(async (job: ActivityJob) => {
    setLogJob(job); setLog(null); setLogBusy(true);
    try { setLog(await jobCoreApi.activityLogs(job.id)); }
    catch (err) { setLog({ text: isMissingEndpoint(err) ? 'This server has no activity log endpoint.' : err instanceof Error ? err.message : String(err), truncated: false, source: '' }); }
    finally { setLogBusy(false); }
  }, []);

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await jobCoreApi.removeContainer(removing.name);
      toast(`Removed ${removing.name}`, 'ok');
      setRemoving(null);
      await load();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      toast(status === 409 ? 'The job is active — stop it before removing its container.' : status === 503 ? 'Docker is unavailable.' : err instanceof Error ? err.message : String(err), 'danger');
    } finally { setBusy(false); }
  };

  const go = (page: JobsPage) => switchJobsPage(navigation, 'JobActivity', page);
  const now = Date.now() / 1000;

  const containerRow = (c: ActivityContainer, removable: boolean, reason = '') => {
    const s = samples[c.name] || { cpu: [], mem: [] };
    const running = c.status === 'running';
    const memPct = c.mem_bytes != null && c.mem_limit ? Math.round((c.mem_bytes / c.mem_limit) * 100) : null;
    return (
      <View key={c.name} testID={`container-${c.name}`} style={[styles.container, { borderColor: colors.hairline, backgroundColor: colors.canvas }]}>
        <View style={styles.row}>
          <StatusIcon status={running ? 'active' : c.status === 'exited' ? 'skipped' : 'pending'} label={c.status_text || c.status || 'unknown'} />
          <Text variant="xs" style={[styles.mono, { flex: 1 }]} numberOfLines={1}>{c.name}</Text>
          {c.role === 'engine' ? <Pill tone="accent" glyph="⬢" testID={`container-role-${c.name}`}>Docker engine</Pill>
            : c.role === 'broker' ? <Pill tone="accent" glyph="›" testID={`container-role-${c.name}`}>{`run in ${c.binding || 'environment'}`}</Pill>
              : c.stage ? <Pill tone="muted">{c.stage}</Pill> : null}
        </View>
        {c.binding && c.role !== 'broker' || c.gpus ? (
          <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>
            {[c.binding && c.role !== 'broker' ? `environment ${c.binding}` : '', c.gpus ? `GPUs ${c.gpus}` : ''].filter(Boolean).join(' · ')}
          </Text>
        ) : null}
        <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>{c.image}{c.started_at ? ` · up ${duration((now - c.started_at) * 1000)}` : ''}{c.status_text ? ` · ${c.status_text}` : ''}</Text>
        <View style={styles.metrics}>
          <View style={styles.metric}>
            <Text variant="xs" style={{ color: colors.ink3 }}>CPU <Text variant="xs" style={styles.num}>{c.cpu_percent == null ? '—' : `${c.cpu_percent.toFixed(0)}%`}</Text></Text>
            <Sparkline values={s.cpu} max={Math.max(100, ...s.cpu.map(v => v || 0))} tone="running" accessibilityLabel={`CPU ${c.cpu_percent == null ? 'no sample yet' : `${c.cpu_percent.toFixed(0)} percent`}`} testID={`cpu-spark-${c.name}`} />
          </View>
          <View style={styles.metric}>
            <Text variant="xs" style={{ color: colors.ink3 }}>Mem <Text variant="xs" style={styles.num}>{c.mem_bytes == null ? '—' : `${bytes(c.mem_bytes)}${c.mem_limit ? ` / ${bytes(c.mem_limit)}` : ''}`}</Text></Text>
            <Sparkline values={s.mem} max={c.mem_limit || undefined} tone={memPct != null && memPct > 85 ? 'warn' : 'accent'} accessibilityLabel={`Memory ${c.mem_bytes == null ? 'no sample yet' : bytes(c.mem_bytes)}${memPct != null ? `, ${memPct} percent of limit` : ''}`} testID={`mem-spark-${c.name}`} />
          </View>
        </View>
        <View style={styles.row}>
          <Button small tone="danger" label="Remove" accessibilityLabel={`Remove container ${c.name}`} disabled={!removable} onPress={() => setRemoving(c)} testID={`remove-${c.name}`} />
          {!removable && reason ? <Text variant="xs" style={{ color: colors.ink3 }}>{reason}</Text> : null}
        </View>
      </View>
    );
  };

  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <JobsNavBar current="JobActivity" onNavigate={go} />
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.ink3} />}>
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Activity</Text>
        {unsupported ? <Banner tone="muted" glyph="ℹ" title="Activity needs a newer mucli server (GET /api/jobs/vm/activity)." /> : null}
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {!vm && !unsupported && !error ? <Loading label="Loading activity…" /> : null}
        {vm ? (
          <>
            <View style={styles.wrap} accessibilityLabel="Activity summary">
              <Pill tone={vm.counts.agents ? 'running' : 'muted'} glyph="●">{`${vm.counts.agents} agents`}</Pill>
              <Pill tone={vm.counts.checks ? 'running' : 'muted'} glyph="●">{`${vm.counts.checks} checks`}</Pill>
              <Pill tone="muted" glyph="⬢">{`${vm.counts.containers} containers`}</Pill>
              <Pill tone={vm.counts.needs_you ? 'warn' : 'muted'} glyph="◆">{`${vm.counts.needs_you} needs you`}</Pill>
            </View>
            {vm.engine !== 'up' ? (
              <Banner tone="warn" glyph="◆" title={vm.engine === 'off' ? 'Docker is not installed — container stats are unavailable.' : 'Docker is not responding — container stats are unavailable.'} />
            ) : null}
            {vm.jobs.length ? vm.jobs.map(job => {
              const st = stateStatus(job.state);
              const active = ACTIVE_STATES.includes(job.state);
              return (
                <Panel
                  key={job.id}
                  testID={`activity-job-${job.id}`}
                  title={<View style={[styles.row, { flex: 1 }]}>
                    <Icon name={KIND_ICON[job.kind] || 'circle'} size="sm" color={st === 'active' ? colors.running.ink : colors.attention.ink} accessibilityLabel={job.kind} />
                    <Text variant="sm" style={[styles.mono, { color: colors.accent.ink, fontWeight: fontWeights.semibold }]}>{job.key || `#${job.id.slice(0, 8)}`}</Text>
                    <StatusPill status={st} label={stateLabel(job.state)} />
                  </View>}
                  right={<Text variant="xs" style={styles.num}>{money(job.cost_usd)}</Text>}
                >
                  <Text variant="sm" numberOfLines={1} onPress={() => navigation.navigate('JobDetail', { jobId: job.id })} accessibilityRole="link">{job.title}</Text>
                  <Text variant="xs" style={{ color: colors.ink3 }}>
                    {job.kind}{job.stage ? ` · ${job.stage}` : ''}{job.since ? ` · ${duration((now - job.since) * 1000)}` : ''}{job.worker_id ? ` · worker ${job.worker_id.slice(0, 8)}` : ''}{job.heartbeat_at ? ` · heartbeat ${duration((now - job.heartbeat_at) * 1000)} ago` : ''}
                  </Text>
                  {job.last ? <Text variant="xs" style={[styles.mono, { color: colors.ink3 }]} numberOfLines={2}>› {job.last}</Text> : null}
                  {(job.containers || []).map(c => containerRow(c, !active, active ? 'job is active' : ''))}
                  <View style={styles.wrap}>
                    <Button small label="View log" onPress={() => void openLog(job)} testID={`activity-log-${job.id}`} />
                    <Button small ghost label="Open job →" onPress={() => navigation.navigate('JobDetail', { jobId: job.id })} />
                  </View>
                </Panel>
              );
            }) : <Panel><Empty status="done">No agents, checks or containers are running.</Empty></Panel>}
            {vm.orphans?.length ? (
              <Panel title="Orphaned containers" badge={vm.orphans.length} badgeTone="warn" testID="activity-orphans">
                <Text variant="xs" style={{ color: colors.ink3 }}>mucli-job containers whose job is no longer active.</Text>
                {vm.orphans.map(c => containerRow(c, true))}
              </Panel>
            ) : null}
          </>
        ) : null}
      </ScrollView>

      <Sheet visible={!!logJob} onClose={() => setLogJob(null)} title={logJob ? `Log · ${logJob.key || logJob.id.slice(0, 8)}` : 'Log'}>
        {logBusy ? <Loading label="Loading log…" /> : (
          <View style={{ gap: spacing.sm }}>
            <View style={styles.row}>
              <Text variant="xs" style={{ color: colors.ink3, flex: 1 }}>{log?.source ? `${log.source} log` : ''}{log?.truncated ? ' · showing the tail' : ''}</Text>
              {logJob ? <Button small label="Refresh" onPress={() => void openLog(logJob)} /> : null}
            </View>
            <Pre testID="activity-log-text">{log?.text || 'No output yet.'}</Pre>
          </View>
        )}
      </Sheet>

      <Sheet visible={!!removing} onClose={() => setRemoving(null)} title="Remove container">
        <Text variant="sm">Remove <Text variant="sm" style={styles.mono}>{removing?.name}</Text>? This runs docker rm -f; the job record and its artifacts are kept.</Text>
        <View style={styles.sheetActions}>
          <Button label="Cancel" onPress={() => setRemoving(null)} />
          <Button primary tone="danger" loading={busy} label="Remove" onPress={() => void remove()} testID="remove-confirm" />
        </View>
      </Sheet>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  mono: { fontFamily: fonts.mono },
  num: { fontVariant: ['tabular-nums'] },
  container: { borderWidth: 1, borderRadius: radii.control, padding: spacing.sm, gap: spacing.xs },
  metrics: { flexDirection: 'row', gap: spacing.md },
  metric: { flex: 1, gap: 2 },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
