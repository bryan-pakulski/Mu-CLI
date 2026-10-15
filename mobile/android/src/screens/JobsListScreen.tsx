import React, { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';

import { jobsApi } from '../api/jobs';
import type { JobSummary } from '../api/jobCore';
import { useJobCommands } from '../components/JobActions';
import { JobRow, JobsNavBar, jobKey, useJobsHome, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, spacing, touchTarget } from '../theme/tokens';
import { stateLabel, stateStatus } from '../theme/status';
import { Banner, Button, CheckRow, Empty, Loading, Panel, Pressable, Sheet, Text, TextField, useToast } from '../ui';

export type JobsListScreenProps = NativeStackScreenProps<RootStackParamList, 'JobsList'>;

/** Session filter value meaning "every session" (jobs mode is one plane across sessions). */
export const ALL_SESSIONS = '*';
/** Session filter value for jobs with no recorded origin session. */
export const NO_SESSION = '';

/** Pure filter (tested): text query over key/title/state/repo/session/id, the "only jobs that need me"
 * switch and the origin-session filter (ALL_SESSIONS = no filter). */
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

/** Wash Jobs page: filter, only-needs-me, rows with mini steppers, archive and delete. */
export function JobsListScreen({ navigation }: JobsListScreenProps) {
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
  const go = (page: JobsPage) => switchJobsPage(navigation, 'JobsList', page);
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

  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <JobsNavBar current="JobsList" onNavigate={go} home={home} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await reload(true); setRefreshing(false); }} tintColor={colors.ink3} />}>
        <View style={styles.head}>
          <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Jobs <Text variant="xl" style={{ color: colors.ink3 }}>({home?.jobs.length || 0})</Text></Text>
          <Text variant="xs" style={{ color: colors.ink3, marginLeft: spacing.sm }}>{session === ALL_SESSIONS ? 'all sessions' : `session · ${session || 'none'}`}</Text>
        </View>
        <TextField accessibilityLabel="Filter jobs" placeholder="Filter…" value={query} onChangeText={setQuery} autoCapitalize="none" autoCorrect={false} />
        <SessionFilter sessions={sessions} value={session} onChange={setSession} total={home?.jobs.length || 0} />
        <CheckRow testID="jobs-needs-only" label="only jobs that need me" checked={needsOnly} onChange={setNeedsOnly} />
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {loading && !home ? <Loading /> : (
          <Panel flush testID="jobs-list">
            {rows.length ? rows.map(row => {
              const deleteAction = row.actions.find(a => a.id === 'delete');
              return (
                <JobRow key={row.id} job={row} now={now} onPress={() => navigation.navigate('JobDetail', { jobId: row.id })}
                  right={(
                    <View style={styles.rowActions}>
                      <Button small ghost iconOnly label={`Archive ${jobKey(row)}`} testID={`archive-${row.id}`}
                        icon={<Ionicons name="archive-outline" size={18} color={colors.ink3} />} onPress={() => setArchiveRow(row)} />
                      <Button small ghost iconOnly label={`Delete ${jobKey(row)}`} testID={`delete-${row.id}`} disabled={!deleteAction?.enabled}
                        icon={<Ionicons name="trash-outline" size={18} color={colors.danger.ink} />} onPress={() => { setDeleteRow(row); if (deleteAction) setTimeout(() => del.invoke(deleteAction), 0); }} />
                    </View>
                  )}
                />
              );
            }) : (
              <View style={{ padding: spacing.base }}>
                <Empty>{home?.jobs.length ? (session !== ALL_SESSIONS ? `No jobs from ${session || 'no session'} match the filter.` : 'No jobs match the filter.') : 'No jobs yet. Create one with “New job”, or import tickets from a Jira board.'}</Empty>
              </View>
            )}
          </Panel>
        )}
      </ScrollView>
      <Sheet visible={!!archiveRow} onClose={() => setArchiveRow(null)} title={archiveRow ? `Archive ${jobKey(archiveRow)}` : 'Archive'}>
        <Text variant="sm">Archive {archiveRow ? jobKey(archiveRow) : ''}? It leaves the board; History keeps its record and evidence.</Text>
        <View style={styles.sheetActions}>
          <Button label="Cancel" onPress={() => setArchiveRow(null)} />
          <Button primary label="Archive" onPress={() => void archive()} testID="archive-confirm" />
        </View>
      </Sheet>
      {del.sheets}
    </SafeAreaView>
  );
}

/** Session filter chips: All sessions + one per origin session seen on the board. */
function SessionFilter({ sessions, value, onChange, total }: { sessions: Array<{ name: string; count: number }>; value: string; onChange: (v: string) => void; total: number }) {
  const { colors } = useTheme();
  const options = [{ name: ALL_SESSIONS, label: 'All sessions', count: total }, ...sessions.map(s => ({ name: s.name, label: s.name || 'no session', count: s.count }))];
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sessionRow}
      accessibilityRole="radiogroup" accessibilityLabel="Filter by session" testID="jobs-session-filter">
      {options.map(o => {
        const on = o.name === value;
        return (
          <Pressable key={o.name || '__none'} testID={`jobs-session-${o.name === ALL_SESSIONS ? 'all' : o.name || 'none'}`} accessibilityRole="radio"
            accessibilityLabel={`${o.label}, ${o.count} job${o.count === 1 ? '' : 's'}`} accessibilityState={{ selected: on, checked: on }}
            onPress={() => onChange(o.name)}
            style={[styles.sessionChip, { borderColor: on ? colors.accent.ink : colors.hairline, backgroundColor: on ? colors.accent.wash : colors.raised }]}>
            <Text variant="xs" style={{ color: on ? colors.ink : colors.ink3, fontWeight: on ? fontWeights.semibold : fontWeights.medium }} numberOfLines={1}>{o.label} · {o.count}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  sessionRow: { gap: spacing.xs },
  sessionChip: { borderWidth: 1, borderRadius: radii.control, paddingHorizontal: spacing.md, minHeight: touchTarget, justifyContent: 'center', maxWidth: 220 },
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['2xl'] },
  head: { flexDirection: 'row', alignItems: 'center' },
  rowActions: { flexDirection: 'row', gap: spacing.xs },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
