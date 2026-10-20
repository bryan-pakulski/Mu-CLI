// Work → Jobs: every job as a row, with a filter, the origin-session filter and "only jobs that
// need me"; archive (REST: no command) and delete (the job's own `delete` action, confirmed).

import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';

import { jobsApi } from '../../api/jobs';
import type { JobSummary } from '../../api/jobCore';
import { useJobCommands } from '../../components/JobActions';
import { JobRow, jobKey, useJobsHome } from '../../components/JobsChrome';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { stateLabel, stateStatus } from '../../theme/status';
import { Banner, Button, CheckRow, Chip, Dialog, Loading, Rows, Text, TextField, useToast } from '../../ui';

/** Session filter value meaning "every session" (Work is one plane across sessions). */
export const ALL_SESSIONS = '*';
/** Session filter value for jobs with no recorded origin session. */
export const NO_SESSION = '';

/** Text query over key/title/state/repo/session/id, "only jobs that need me" and the origin session. */
export function filterJobs(rows: JobSummary[], query: string, needsOnly: boolean, session: string = ALL_SESSIONS): JobSummary[] {
  const q = query.trim().toLowerCase();
  return rows.filter(j => (!needsOnly || ['waiting', 'failed'].includes(stateStatus(j.state))) &&
    (session === ALL_SESSIONS || (j.origin_session || '') === session) &&
    (!q || `${jobKey(j)} ${j.title} ${stateLabel(j.state)} ${j.repository || ''} ${j.origin_session || ''} ${j.id}`.toLowerCase().includes(q)));
}

/** Distinct origin sessions with counts, most jobs first; '' (no session) last. */
export function jobSessions(rows: JobSummary[]): Array<{ name: string; count: number }> {
  const counts = new Map<string, number>();
  rows.forEach(j => counts.set(j.origin_session || '', (counts.get(j.origin_session || '') || 0) + 1));
  return [...counts.entries()].map(([name, count]) => ({ name, count }))
    .sort((a, b) => (a.name === '' ? 1 : 0) - (b.name === '' ? 1 : 0) || b.count - a.count || a.name.localeCompare(b.name));
}

export function JobsPanel({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
  const { colors } = useTheme();
  const toast = useToast();
  const { home, loading, error, reload } = useJobsHome();
  const [query, setQuery] = useState('');
  const [needsOnly, setNeedsOnly] = useState(false);
  const [session, setSession] = useState<string>(ALL_SESSIONS);
  const [refreshing, setRefreshing] = useState(false);
  const [archiveRow, setArchiveRow] = useState<JobSummary | null>(null);
  const [deleteRow, setDeleteRow] = useState<JobSummary | null>(null);
  const sessions = useMemo(() => jobSessions(home?.jobs || []), [home]);
  const rows = useMemo(() => filterJobs(home?.jobs || [], query, needsOnly, session), [home, query, needsOnly, session]);
  const now = Date.now() / 1000;
  const del = useJobCommands({
    jobId: deleteRow?.id || '',
    actions: deleteRow?.actions || [],
    onDone: async () => { setDeleteRow(null); await reload(true); },
    onError: () => setDeleteRow(null),
  });

  const archive = async () => {
    const row = archiveRow;
    if (!row) return;
    try {
      await jobsApi.archive(row.id);
      toast(`Archived ${jobKey(row)}`, 'ok');
      setArchiveRow(null);
      await reload(true);
    } catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
  };

  const total = home?.jobs.length || 0;
  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" testID="work-jobs"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await reload(true); setRefreshing(false); }} tintColor={colors.ink3} />}>
      <TextField accessibilityLabel="Filter jobs" placeholder="Filter…" value={query} onChangeText={setQuery} autoCapitalize="none" autoCorrect={false} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips} accessibilityLabel="Filter by session" testID="jobs-session-filter">
        {[{ name: ALL_SESSIONS, label: 'All sessions', count: total }, ...sessions.map(s => ({ name: s.name, label: s.name || 'no session', count: s.count }))].map(o => (
          <Chip key={o.name || '__none'} variant="filter" selected={o.name === session} onPress={() => setSession(o.name)}
            accessibilityLabel={`${o.label}, ${o.count} job${o.count === 1 ? '' : 's'}`}
            testID={`jobs-session-${o.name === ALL_SESSIONS ? 'all' : o.name || 'none'}`}>{`${o.label} · ${o.count}`}</Chip>
        ))}
      </ScrollView>
      <CheckRow testID="jobs-needs-only" label="only jobs that need me" checked={needsOnly} onChange={setNeedsOnly} />
      {error ? <Banner tone="danger" role="alert" title={error} /> : null}
      {loading && !home ? <Loading /> : (
        <Rows testID="jobs-list" label="Jobs"
          empty={total ? (session !== ALL_SESSIONS ? `No jobs from ${session || 'no session'} match the filter.` : 'No jobs match the filter.') : 'No jobs yet. Create one with New job, or import tickets on Tickets.'}>
          {rows.map(row => {
            const deleteAction = row.actions.find(a => a.id === 'delete');
            return (
              <JobRow key={row.id} job={row} now={now} onPress={() => onOpenJob(row.id)}
                actions={(
                  <View style={styles.rowActions}>
                    <Button small variant="ghost" label="Archive" accessibilityLabel={`Archive ${jobKey(row)}`} testID={`archive-${row.id}`} onPress={() => setArchiveRow(row)} />
                    <Button small variant="ghost" tone="danger" label="Delete" accessibilityLabel={`Delete ${jobKey(row)}`} testID={`delete-${row.id}`}
                      disabled={!deleteAction?.enabled} onPress={() => { setDeleteRow(row); if (deleteAction) setTimeout(() => del.invoke(deleteAction), 0); }} />
                  </View>
                )}
              />
            );
          })}
        </Rows>
      )}
      <Dialog visible={!!archiveRow} onClose={() => setArchiveRow(null)} title={archiveRow ? `Archive ${jobKey(archiveRow)}` : 'Archive'}
        footer={<><Button variant="secondary" label="Cancel" onPress={() => setArchiveRow(null)} /><Button variant="primary" label="Archive" onPress={() => void archive()} testID="archive-confirm" /></>}>
        <Text variant="body">It leaves the board; History keeps its record and evidence.</Text>
      </Dialog>
      {del.sheets}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['3xl'] },
  chips: { gap: spacing.xs, alignItems: 'center' },
  rowActions: { flexDirection: 'row', gap: spacing.xs },
});
