// Insights › Jobs (web /jobs/trace): every job, newest first, as rows (status, wall time, cost);
// a row opens the job's analysis (Work's Job performance screen). Data: GET /api/jobs/history
// ?scope=all&archive=all (mu/gui/routers/jobs.py query_job_history), as the web's list pane.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, View } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import { api } from '../../api/client';
import { stateLabel, stateStatus } from '../../theme/status';
import { spacing } from '../../theme/tokens';
import { EmptyState, ErrorState, Row, StateSentence, TextField } from '../../ui';

export type HistoryJob = {
  id: string;
  title: string;
  status: string;
  archived?: boolean;
  created_at?: number;
  elapsed_seconds?: number;
  cost_usd?: number;
  execution?: { provider?: string; model?: string };
};

export function fmtDuration(seconds: number): string {
  let s = Math.max(0, Math.round(Number(seconds || 0)));
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function fmtMoney(usd: number): string {
  const n = Number(usd || 0);
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(n >= 0.01 ? 2 : 4)}`;
}

export function ago(epoch: number | undefined, now = Date.now() / 1000): string {
  if (!epoch) return '';
  const d = Math.max(0, now - Number(epoch));
  if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}m`;
  if (d < 86400) return `${Math.floor(d / 3600)}h`;
  return `${Math.floor(d / 86400)}d`;
}

/** A job row's second line: the status in words, archived, wall time, cost (zeros drop out). */
export function jobFacts(job: HistoryJob): string {
  return [stateLabel(job.status), job.archived ? 'archived' : '', Number(job.elapsed_seconds) ? fmtDuration(Number(job.elapsed_seconds)) : '', Number(job.cost_usd) ? fmtMoney(Number(job.cost_usd)) : '']
    .filter(Boolean).join(' · ');
}

export function JobsInsightsScreen() {
  const navigation = useNavigation<any>();
  const focused = useIsFocused();
  const [jobs, setJobs] = useState<HistoryJob[] | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ jobs?: HistoryJob[] }>('/api/jobs/history', { query: { scope: 'all', archive: 'all', limit: 1000, sort: 'created', order: 'desc' } });
      setJobs(data.jobs || []);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect(() => { if (focused) void load(); }, [focused, load]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (jobs || []).filter(job => !q || `${job.title} ${job.id} ${job.status}`.toLowerCase().includes(q));
  }, [jobs, query]);
  const cost = (jobs || []).reduce((sum, job) => sum + Number(job.cost_usd || 0), 0);

  if (error && !jobs) return <ErrorState message={error} onRetry={() => void load()} />;
  return (
    <FlatList
      testID="insights-jobs"
      data={shown}
      keyExtractor={job => job.id}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} />}
      ListHeaderComponent={
        <View style={styles.head}>
          <StateSentence parts={[jobs?.length ? { text: `${jobs.length} job${jobs.length === 1 ? '' : 's'}` } : null, cost ? { text: `${fmtMoney(cost)} in model cost`, tone: 'quiet' } : null]} />
          <TextField value={query} onChangeText={setQuery} placeholder="Filter by title, id or status" autoCapitalize="none" autoCorrect={false} accessibilityLabel="Filter jobs" testID="insights-jobs-filter" />
        </View>
      }
      ListEmptyComponent={jobs ? <EmptyState icon="briefcase" pigment="lagoon" title={jobs.length ? 'No job matches' : 'No jobs yet'} message={jobs.length ? 'Try another filter.' : 'Jobs you run under Work are analysed here.'} /> : null}
      renderItem={({ item }) => (
        <Row testID={`insights-job-${item.id}`} status={stateStatus(item.status)} title={item.title} sub={jobFacts(item)} when={ago(item.created_at)}
          accessibilityLabel={`Analyse ${item.title}`} onPress={() => navigation.navigate('JobAnalysis', { jobId: item.id })} />
      )}
    />
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, paddingBottom: spacing['4xl'] },
  head: { gap: spacing.sm, marginBottom: spacing.sm },
});
