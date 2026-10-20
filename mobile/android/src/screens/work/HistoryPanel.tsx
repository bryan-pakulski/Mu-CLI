// Work → History: search, the archive filter, the report in one sentence, and archive / restore /
// delete. History and its report are REST (no view model); delete is the job command.

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';

import { jobsApi, type JobHistoryRow, type JobReport } from '../../api/jobs';
import { jobCoreApi } from '../../api/jobCore';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { duration, money, when, type Status } from '../../theme/status';
import { Banner, Button, Dialog, Loading, Row, Rows, Segmented, Text, TextField, useToast } from '../../ui';

const PAGE = 50;
type ArchiveFilter = 'all' | 'active' | 'archived';
type Pending = { kind: 'archive' | 'restore' | 'delete'; row: JobHistoryRow } | null;

/** History rows carry the stored job status → the status vocabulary. */
export function historyStatus(status: string): Status {
  if (['merged', 'ready_for_review'].includes(status)) return 'done';
  if (['failed', 'timed_out', 'budget_exceeded', 'environment_error', 'conflicted'].includes(status)) return 'failed';
  if (status === 'cancelled') return 'skipped';
  if (status === 'needs_human') return 'waiting';
  if (status === 'queued') return 'pending';
  return 'active';
}

/** The report as one sentence (no stat tiles): jobs, cost, failures, checks, run time. */
export function reportSentence(r: JobReport): string {
  const pct = (v: number) => `${Math.round((v || 0) * 100)}%`;
  return [
    `${r.total_jobs} job${r.total_jobs === 1 ? '' : 's'}`,
    `${money(r.total_cost_usd)} total, ${money(r.average_cost_usd)} each`,
    r.failure_count ? `${pct(r.failure_rate)} failed (${r.failure_count})` : '',
    r.verification_runs ? `checks passed ${pct(r.verification_pass_rate)} (${r.verification_passed}/${r.verification_runs})` : '',
    r.average_elapsed_seconds ? `${duration(r.average_elapsed_seconds * 1000)} average run` : '',
  ].filter(Boolean).join(' · ');
}

const VERB = { archive: 'Archive', restore: 'Restore', delete: 'Delete' } as const;

export function HistoryPanel({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
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
      else await jobCoreApi.command(row.id, 'delete');
      toast(`${kind === 'archive' ? 'Archived' : kind === 'restore' ? 'Restored' : 'Deleted'} ${row.title}`, 'ok');
      setPending(null);
      await load(0);
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally { setBusy(false); }
  };

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" testID="work-history"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(0); setRefreshing(false); }} tintColor={colors.ink3} />}>
      <TextField accessibilityLabel="Search job history" placeholder="Search title, repository, id…" value={query} onChangeText={setQuery} autoCapitalize="none" autoCorrect={false} />
      <Segmented<ArchiveFilter> accessibilityLabel="Archive filter" value={archive} onChange={setArchive}
        options={[{ value: 'all', label: 'All' }, { value: 'active', label: 'Not archived' }, { value: 'archived', label: 'Archived' }]} />
      {error ? <Banner tone="danger" role="alert" title={error} /> : null}
      {report ? <Text variant="caption" tone="ink2" testID="history-report">{reportSentence(report)}</Text> : null}
      {loading && !rows.length ? <Loading label="Loading history…" /> : (
        <Rows testID="history-list" label="Job history" empty="No jobs match.">
          {rows.map(row => (
            <Row key={row.id} testID={`history-row-${row.id}`} status={historyStatus(row.status)} onPress={() => onOpenJob(row.id)}
              accessibilityLabel={`Open ${row.title}`}
              title={row.title}
              when={when(row.updated_at)}
              sub={[row.status.replace(/_/g, ' '), row.archived ? 'archived' : '', money(row.cost_usd),
                row.elapsed_seconds ? duration(row.elapsed_seconds * 1000) : '', `${row.attempt_count} attempt${row.attempt_count === 1 ? '' : 's'}`, row.repository || ''].filter(Boolean).join(' · ')}
              actions={(
                <View style={styles.rowActions}>
                  {row.archived
                    ? <Button small variant="ghost" label="Restore" onPress={() => setPending({ kind: 'restore', row })} testID={`restore-${row.id}`} />
                    : <Button small variant="ghost" label="Archive" disabled={!row.manageable} accessibilityHint={row.manageable ? undefined : 'Stop or finish the job first'}
                        onPress={() => setPending({ kind: 'archive', row })} testID={`history-archive-${row.id}`} />}
                  <Button small variant="ghost" tone="danger" label="Delete" disabled={!row.manageable && !row.archived} onPress={() => setPending({ kind: 'delete', row })} testID={`history-delete-${row.id}`} />
                  {!row.manageable && !row.archived ? <Text variant="caption" tone="ink3">Active: stop it first</Text> : null}
                </View>
              )}
            />
          ))}
        </Rows>
      )}
      {rows.length < total ? <Button variant="secondary" label={`Load more (${total - rows.length})`} onPress={() => void load(rows.length)} /> : null}
      <Dialog visible={!!pending} onClose={() => setPending(null)} title={pending ? `${VERB[pending.kind]} job` : ''}
        footer={<>
          <Button variant="secondary" label="Cancel" onPress={() => setPending(null)} />
          <Button variant={pending?.kind === 'delete' ? 'danger' : 'primary'} loading={busy} label={pending ? VERB[pending.kind] : 'OK'} onPress={() => void act()} testID="history-confirm" />
        </>}>
        <Text variant="body">
          {pending?.kind === 'delete' ? `Delete “${pending.row.title}”? Running work is cancelled; its record, telemetry and evidence are purged. The Git branch is preserved.`
            : pending?.kind === 'archive' ? `Archive “${pending.row.title}”? It leaves the board; History keeps its record and evidence.`
              : `Restore “${pending?.row.title}” to the board?`}
        </Text>
      </Dialog>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['3xl'] },
  rowActions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xs },
});
