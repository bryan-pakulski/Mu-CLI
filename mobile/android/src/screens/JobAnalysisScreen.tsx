// Job performance (Work → job ⋯ → Performance, Insights → Jobs): the durable execution analysis as
// a page header with the headline numbers in its state sentence, then sections of rows.

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fonts, spacing } from '../theme/tokens';
import { Banner, BoardSection, Loading, Meter, PageHeader, Row, Text } from '../ui';

export type JobAnalysisScreenProps = NativeStackScreenProps<RootStackParamList, 'JobAnalysis'>;

type PhaseBreakdown = { status: string; seconds: number; percent: number; occurrences: number };
type IntervalEvent = { id: number; event_type: string; created_at: number; summary: string };
type PhaseInterval = {
  status: string; classification: string; duration_seconds: number; explanation: string; interpretation: string;
  event_count: number; activity_event_count: number; worker_event_count: number; agent_event_count: number;
  entry_event: IntervalEvent | null; exit_event: IntervalEvent | null;
};
type AttemptAnalysis = {
  number: number; status: string; duration_seconds: number; cost_usd: number;
  tool_calls: number; agent_messages: number; runtime_errors: number; tokens: Record<string, number>;
};
type ToolAnalysis = { name: string; count: number; share: number };
type VerificationAnalysis = {
  status: string; passed: boolean; duration_seconds: number; checks: number; checks_passed: number;
  changed_files: number; additions: number; deletions: number; dirty: boolean;
};
type RuntimeTrace = {
  available: boolean; session_name: string; trace_url: string; reason: string; run_count?: number;
  summary?: Record<string, number | string | null>;
};
type JobAnalysis = {
  job: { id: string; title: string; status: string; archived?: boolean };
  summary: {
    elapsed_seconds: number; active_seconds: number; waiting_seconds: number; verification_seconds: number;
    passive_seconds?: number; stopped_seconds?: number; review_wait_seconds?: number;
    attempts: number; retries: number; cost_usd: number; model_api_cost_usd?: number; billing_modes?: string[];
    tokens: Record<string, number>; tool_calls: number; unique_tools: number; human_gates: number; human_responses: number;
    verification_runs: number; verification_passes: number; verification_failures: number;
    first_pass_verification: boolean | null; failures: number; recoveries: number;
    changed_files: number; additions: number; deletions: number; waiting_ratio: number | null;
  };
  runtime_trace?: RuntimeTrace;
  phase_breakdown: PhaseBreakdown[];
  phase_intervals?: PhaseInterval[];
  attempts: AttemptAnalysis[];
  tools: ToolAnalysis[];
  verifications: VerificationAnalysis[];
  human_gates: Array<{ event_id: number; created_at: number; reason: string }>;
  failures: Array<{ event_id: number; created_at: number; event_type: string; summary: string }>;
};

function duration(value: number): string {
  let seconds = Math.max(0, Math.round(Number(value || 0)));
  const h = Math.floor(seconds / 3600); seconds -= h * 3600;
  const m = Math.floor(seconds / 60); seconds -= m * 60;
  if (h) return `${h}h ${m}m ${seconds}s`;
  if (m) return `${m}m ${seconds}s`;
  return `${seconds}s`;
}
function label(value: string): string {
  return String(value || '').split('_').map(part => part ? part[0].toUpperCase() + part.slice(1) : part).join(' ');
}
function tokenSummary(tokens: Record<string, number>): string {
  const values = Object.entries(tokens || {}).filter(([, value]) => Number(value));
  return values.length ? values.slice(0, 4).map(([key, value]) => `${key} ${Math.round(value).toLocaleString()}`).join(' · ') : 'No token telemetry';
}

export function JobAnalysisScreen({ route, navigation }: JobAnalysisScreenProps) {
  const { colors } = useTheme();
  const { jobId } = route.params;
  const [analysis, setAnalysis] = useState<JobAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await api.get<{ analysis: JobAnalysis }>(`/api/jobs/${encodeURIComponent(jobId)}/analysis`, { query: { timeline_limit: 1000 } });
      setAnalysis(result.analysis);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [jobId]);

  useEffect(() => { void load(); }, [load]);

  const back = navigation.canGoBack() ? { label: 'Back', onPress: () => navigation.goBack() } : undefined;
  if (!analysis) {
    return (
      <View style={styles.page} testID="job-analysis">
        <PageHeader title="Job performance" back={back} />
        <View style={styles.center}>
          {loading ? <Loading label="Loading job performance…" /> : <Text variant="body" tone="danger">{error || "Couldn't load the job analysis."}</Text>}
        </View>
      </View>
    );
  }

  const s = analysis.summary;
  const firstPass = s.first_pass_verification == null ? 'Not measured' : s.first_pass_verification ? 'Passed' : 'Needed repair';
  const maxTool = Math.max(1, ...analysis.tools.map(item => item.count));
  const costIsEstimate = (s.billing_modes || []).includes('estimated_token');
  const cost = Number(s.model_api_cost_usd ?? s.cost_usd ?? 0);
  const runtime = analysis.runtime_trace;

  return (
    <View style={styles.page} testID="job-analysis">
      <PageHeader title={analysis.job.title || 'Job performance'} back={back} state={[
        { text: `${label(analysis.job.status)}${analysis.job.archived ? ' · archived' : ''}` },
        { text: `${duration(s.elapsed_seconds)} wall · ${duration(s.active_seconds)} active` },
        { text: `${costIsEstimate ? '≈ ' : ''}$${cost.toFixed(2)}`, tone: 'quiet' },
        s.failures ? { text: `${s.failures} failure${s.failures === 1 ? '' : 's'}`, tone: 'danger' } : null,
      ]} />
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(true); }} tintColor={colors.ink3} />}>
        {error ? <Banner tone="danger" title="Couldn't refresh">{error}</Banner> : null}

        <BoardSection title="Time">
          <Row title="Wall" when={duration(s.elapsed_seconds)} />
          <Row title="Active" when={duration(s.active_seconds)} />
          <Row title="Passive" sub="Waiting, review and queues" when={duration(s.passive_seconds || 0)} />
          <Row title="Stopped" sub="Paused, failed or errored" when={duration(s.stopped_seconds || 0)} />
        </BoardSection>

        <BoardSection title="Signals">
          <Row title="First verification" when={firstPass} />
          <Row title="Verification" when={`${s.verification_passes} passed · ${s.verification_failures} failed`} />
          <Row title={costIsEstimate ? 'Cost (estimate)' : 'Cost'} when={`$${cost.toFixed(2)}`} />
          <Row title="Attempts" sub={`${s.retries} additional`} when={String(s.attempts)} />
          <Row title="Tool calls" sub={`${s.unique_tools} distinct tools`} when={String(s.tool_calls)} />
          <Row title="Human gates" sub={`${s.human_responses} responses`} when={String(s.human_gates)} />
          <Row title="Recoveries" when={String(s.recoveries)} />
          <Row title="Changed files" when={`${s.changed_files} · +${s.additions} / −${s.deletions}`} />
          <Row title="Tokens" sub={tokenSummary(s.tokens)} />
        </BoardSection>

        <BoardSection title="Harness trace">
          {runtime?.available
            ? <Row status="done" title={`${runtime.run_count || 0} run(s) recorded`} sub={[runtime.session_name, runtime.reason].filter(Boolean).join(' · ')} />
            : <Row status="skipped" title="No detailed trace" sub={runtime?.reason || 'No detailed harness trace was recorded for this job.'} />}
        </BoardSection>

        <BoardSection title="Lifecycle" empty="No lifecycle recorded.">
          {analysis.phase_breakdown.map(item => (
            <Row key={item.status} title={label(item.status)} when={`${duration(item.seconds)} · ${item.percent.toFixed(1)}%`}
              sub={<View style={styles.meter}><Meter value={Math.max(1, item.percent)} max={100} accessibilityLabel={`${label(item.status)} ${item.percent.toFixed(1)} percent`} /></View>} />
          ))}
        </BoardSection>

        <BoardSection title="State drill-down" empty="No lifecycle intervals recorded.">
          {(analysis.phase_intervals || []).map((item, index) => (
            <Row key={`${index}-${item.status}`} status={item.classification === 'stopped' ? 'failed' : undefined}
              title={label(item.status)} when={`${label(item.classification)} · ${duration(item.duration_seconds)}`}
              sub={
                <View style={styles.subStack}>
                  <Text variant="caption" tone="ink2">{item.interpretation}</Text>
                  <Text variant="caption" tone="ink3">{item.explanation}</Text>
                  <Text variant="caption" tone="ink3">Entered: {item.entry_event?.summary || 'not recorded'} · Exited: {item.exit_event?.summary || 'not recorded'}</Text>
                  <Text variant="caption" tone="ink3">{item.agent_event_count} agent/tool · {item.worker_event_count} worker · {item.event_count} events</Text>
                </View>
              } />
          ))}
        </BoardSection>

        <BoardSection title="Attempts" count={analysis.attempts.length} empty="No implementation attempts recorded.">
          {analysis.attempts.map(item => (
            <Row key={item.number} status={item.status} title={`#${item.number} · ${label(item.status)}`}
              when={`${duration(item.duration_seconds)} · $${item.cost_usd.toFixed(2)}`}
              sub={`${item.tool_calls} tools · ${item.agent_messages} messages · ${item.runtime_errors} errors · ${tokenSummary(item.tokens)}`} />
          ))}
        </BoardSection>

        <BoardSection title="Tool profile" empty="No tool-call telemetry recorded.">
          {analysis.tools.slice(0, 20).map(item => (
            <Row key={item.name} title={<Text variant="caption" style={styles.mono} numberOfLines={1}>{item.name}</Text>} when={String(item.count)}
              sub={<View style={styles.meter}><Meter value={item.count} max={maxTool} accessibilityLabel={`${item.name} ${item.count} calls`} /></View>} />
          ))}
        </BoardSection>

        <BoardSection title="Verification" count={analysis.verifications.length} empty="No deterministic verification recorded.">
          {analysis.verifications.map((item, index) => (
            <Row key={`${index}-${item.status}`} status={item.passed ? 'done' : 'failed'} title={`Run ${index + 1} · ${item.passed ? 'passed' : 'failed'}`}
              when={duration(item.duration_seconds)}
              sub={`${item.checks_passed}/${item.checks} checks · ${item.changed_files} files${item.dirty ? ' · dirty' : ''}`} />
          ))}
        </BoardSection>

        <BoardSection title="Human gates" count={analysis.human_gates.length} empty="No human gates.">
          {analysis.human_gates.slice(-6).reverse().map(item => <Row key={`g-${item.event_id}`} status="needs_you" title={item.reason || 'Human input required'} />)}
        </BoardSection>

        <BoardSection title="Failures" count={analysis.failures.length} empty="No failures.">
          {analysis.failures.slice(-8).reverse().map(item => <Row key={`f-${item.event_id}`} status="failed" title={label(item.event_type)} sub={item.summary} />)}
        </BoardSection>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.lg },
  content: { padding: spacing.base, paddingBottom: spacing['3xl'] },
  meter: { flexDirection: 'row', marginTop: spacing.xs },
  subStack: { gap: 2, marginTop: spacing.xs },
  mono: { fontFamily: fonts.mono },
});
