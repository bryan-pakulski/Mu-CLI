import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { jobsApi, type JobHistoryRow, type JobReport } from '../api/jobs';
import { JobsNavBar, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing, touchTarget } from '../theme/tokens';
import { STATUS, duration, money, when, type Status } from '../theme/status';
import { Banner, Button, Empty, Loading, Panel, Pill, Pressable, Segmented, Sheet, StatTile, Text, TextField, useToast } from '../ui';

export type JobHistoryScreenProps = NativeStackScreenProps<RootStackParamList, 'JobHistory'>;

const PAGE = 50;
type ArchiveFilter = 'all' | 'active' | 'archived';
type Pending = { kind: 'archive' | 'restore' | 'delete'; row: JobHistoryRow } | null;

/** Legacy job status → status vocabulary. */
export function historyStatus(status: string): Status {
  if (['merged', 'ready_for_review'].includes(status)) return 'done';
  if (['failed', 'timed_out', 'budget_exceeded', 'environment_error', 'conflicted'].includes(status)) return 'failed';
  if (status === 'cancelled') return 'skipped';
  if (status === 'needs_human') return 'waiting';
  if (status === 'queued') return 'pending';
  return 'active';
}

/** Wash History page: search, archive filter, report tiles, archive/restore/delete. */
export function JobHistoryScreen({ navigation }: JobHistoryScreenProps) {
  const { colors } = useTheme();
  const toast = useToast();
  const [query, setQuery] = useState('');
  const [archive, setArchive] = useState<ArchiveFilter>('all');
  const [rows, setRows] = useState<JobHistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [report, setReport] = useState<JobReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (offset = 0) => {
    if (!offset) setLoading(true);
    try {
      const filters = { q: query.trim(), archive };
      const [list, rep] = await Promise.all([
        jobsApi.history({ ...filters, sort: 'updated', order: 'desc', limit: PAGE, offset }),
        offset ? Promise.resolve(null) : jobsApi.report(filters).catch(() => null),
      ]);
      setRows(prev => (offset ? [...prev, ...(list.jobs || [])] : list.jobs || []));
      setTotal(list.total || 0);
      if (rep) setReport(rep.report);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setLoading(false); }
  }, [query, archive]);

  useEffect(() => {
    const t = setTimeout(() => { void load(0); }, 250);
    return () => clearTimeout(t);
  }, [load]);

  const act = async () => {
    if (!pending) return;
    const { kind, row } = pending;
    setBusy(true);
    try {
      if (kind === 'archive') await jobsApi.archive(row.id);
      else if (kind === 'restore') await jobsApi.restore(row.id);
      else await jobsApi.delete(row.id, false);
      toast(`${kind === 'archive' ? 'Archived' : kind === 'restore' ? 'Restored' : 'Deleted'} ${row.title}`, 'ok');
      setPending(null);
      await load(0);
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally { setBusy(false); }
  };

  const go = (page: JobsPage) => switchJobsPage(navigation, 'JobHistory', page);
  const pct = (v: number) => `${Math.round((v || 0) * 100)}%`;

  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <JobsNavBar current="JobHistory" onNavigate={go} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(0); setRefreshing(false); }} tintColor={colors.ink3} />}>
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>History</Text>
        <TextField accessibilityLabel="Search job history" placeholder="Search title, repository, id…" value={query} onChangeText={setQuery} autoCapitalize="none" autoCorrect={false} />
        <Segmented<ArchiveFilter>
          accessibilityLabel="Archive filter"
          value={archive}
          onChange={setArchive}
          options={[{ value: 'all', label: 'All' }, { value: 'active', label: 'Not archived' }, { value: 'archived', label: 'Archived' }]}
        />
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {report ? (
          <View style={styles.stats} accessibilityLabel="Report" testID="history-report">
            <StatTile value={report.total_jobs} label="jobs" tone="accent" glyph="○" />
            <StatTile value={money(report.total_cost_usd)} label={`total · avg ${money(report.average_cost_usd)}`} tone="muted" glyph="$" />
            <StatTile value={pct(report.failure_rate)} label={`failed (${report.failure_count})`} tone={report.failure_count ? 'danger' : 'muted'} glyph="✗" />
            <StatTile value={pct(report.verification_pass_rate)} label={`checks passed (${report.verification_passed}/${report.verification_runs})`} tone="ok" glyph="✓" />
            <StatTile value={duration((report.average_elapsed_seconds || 0) * 1000)} label="avg run time" tone="muted" glyph="●" />
          </View>
        ) : null}
        {report && Object.keys(report.status_counts || {}).length ? (
          <View style={styles.wrap}>
            {Object.entries(report.status_counts).map(([s, n]) => <Pill key={s} tone={STATUS[historyStatus(s)].tone} glyph={STATUS[historyStatus(s)].icon}>{`${s.replace(/_/g, ' ')} ${n}`}</Pill>)}
          </View>
        ) : null}
        {loading && !rows.length ? <Loading label="Loading history…" /> : (
          <Panel title="Jobs" badge={total} flush testID="history-list">
            {rows.length ? rows.map(row => {
              const st = historyStatus(row.status);
              return (
                <View key={row.id} testID={`history-row-${row.id}`} style={[styles.row, { borderBottomColor: colors.hairline }]}>
                  <Pressable accessibilityRole="button" accessibilityLabel={`Open ${row.title}`} onPress={() => navigation.navigate('JobDetail', { jobId: row.id })} style={styles.rowMain}>
                    <View style={styles.wrap}>
                      <Pill tone={STATUS[st].tone} glyph={STATUS[st].icon}>{row.status.replace(/_/g, ' ')}</Pill>
                      {row.archived ? <Pill tone="muted">archived</Pill> : null}
                      <View style={{ flex: 1 }} />
                      <Text variant="xs" style={styles.num}>{money(row.cost_usd)}</Text>
                      <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{row.elapsed_seconds ? duration(row.elapsed_seconds * 1000) : ''}</Text>
                    </View>
                    <Text variant="sm" numberOfLines={1}>{row.title}</Text>
                    <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1}>{row.id.slice(0, 12)} · {when(row.updated_at)}{row.repository ? ` · ${row.repository}` : ''} · {row.attempt_count} attempt{row.attempt_count === 1 ? '' : 's'}</Text>
                  </Pressable>
                  <View style={styles.rowActions}>
                    {row.archived
                      ? <Button small label="Restore" onPress={() => setPending({ kind: 'restore', row })} testID={`restore-${row.id}`} />
                      : <Button small label="Archive" disabled={!row.manageable} accessibilityHint={row.manageable ? undefined : 'Stop or finish the job first'} onPress={() => setPending({ kind: 'archive', row })} testID={`history-archive-${row.id}`} />}
                    <Button small tone="danger" label="Delete" disabled={!row.manageable && !row.archived} onPress={() => setPending({ kind: 'delete', row })} testID={`history-delete-${row.id}`} />
                    {!row.manageable && !row.archived ? <Text variant="xs" style={{ color: colors.ink3 }}>active — stop it first</Text> : null}
                  </View>
                </View>
              );
            }) : <View style={{ padding: spacing.base }}><Empty>No jobs match.</Empty></View>}
            {rows.length < total ? <View style={{ padding: spacing.sm }}><Button label={`Load more (${total - rows.length})`} onPress={() => void load(rows.length)} /></View> : null}
          </Panel>
        )}
      </ScrollView>
      <Sheet visible={!!pending} onClose={() => setPending(null)} title={pending ? `${pending.kind[0].toUpperCase()}${pending.kind.slice(1)} job` : ''}>
        <Text variant="sm">
          {pending?.kind === 'delete' ? `Delete “${pending.row.title}”? Its record, telemetry and evidence are purged; the Git branch is preserved.`
            : pending?.kind === 'archive' ? `Archive “${pending.row.title}”? It leaves the board; History keeps its record and evidence.`
              : `Restore “${pending?.row.title}” to the board?`}
        </Text>
        <View style={styles.sheetActions}>
          <Button label="Cancel" onPress={() => setPending(null)} />
          <Button primary tone={pending?.kind === 'delete' ? 'danger' : ''} loading={busy} label={pending ? `${pending.kind[0].toUpperCase()}${pending.kind.slice(1)}` : 'OK'} onPress={() => void act()} testID="history-confirm" />
        </View>
      </Sheet>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  num: { fontVariant: ['tabular-nums'] },
  row: { paddingHorizontal: spacing.base, paddingVertical: spacing.sm, borderBottomWidth: 1, gap: spacing.xs },
  rowMain: { gap: 2, minHeight: touchTarget },
  rowActions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
