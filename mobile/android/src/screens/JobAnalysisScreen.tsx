import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Text } from '../components/Text';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, fonts, radii, spacing, tracking } from '../theme/tokens';

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
      navigation.setOptions({ title: result.analysis.job.title });
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [jobId, navigation]);

  useEffect(() => { void load(); }, [load]);

  if (loading && !analysis) {
    return <SafeAreaView edges={['bottom']} style={styles.center}><ActivityIndicator color={colors.accent.ink} /></SafeAreaView>;
  }
  if (!analysis) {
    return <SafeAreaView edges={['bottom']} style={styles.center}><Text variant="sm" style={{ color: colors.danger.ink }}>{error || 'Could not load job analysis.'}</Text></SafeAreaView>;
  }

  const s = analysis.summary;
  const firstPass = s.first_pass_verification == null ? 'Not measured' : s.first_pass_verification ? 'Passed' : 'Needed repair';
  const maxTool = Math.max(1, ...analysis.tools.map(item => item.count));
  const costIsEstimate = (s.billing_modes || []).includes('estimated_token');
  const cost = Number(s.model_api_cost_usd ?? s.cost_usd ?? 0);
  const runtime = analysis.runtime_trace;

  return (
    <SafeAreaView edges={['bottom']} style={styles.safe}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(true); }} tintColor={colors.ink3} />}
      >
        <View style={styles.hero}>
          <Text variant="xs" style={{ color: colors.ink3 }}>{label(analysis.job.status)}{analysis.job.archived ? ' · Archived' : ''}</Text>
          <Text variant="xl" style={styles.title}>Job performance</Text>
          <Text variant="sm" dim style={styles.subtitle}>Durable execution analysis separates active work from waiting, review and stopped/error residence.</Text>
          {error ? <Text variant="xs" style={{ color: colors.danger.ink, marginTop: spacing.sm }}>{error}</Text> : null}
        </View>

        <View style={[styles.metrics, { backgroundColor: colors.surface }]}>
          <Metric label="Wall" value={duration(s.elapsed_seconds)} />
          <Metric label="Active" value={duration(s.active_seconds)} />
          <Metric label="Passive" value={duration(s.passive_seconds || 0)} />
          <Metric label="Stopped" value={duration(s.stopped_seconds || 0)} />
        </View>
        <View style={[styles.metrics, { backgroundColor: colors.surface }]}>
          <Metric label={costIsEstimate ? 'Cost est.' : 'Cost'} value={`$${cost.toFixed(2)}`} />
          <Metric label="Attempts" value={String(s.attempts)} />
          <Metric label="Tools" value={String(s.tool_calls)} />
          <Metric label="Failures" value={String(s.failures)} />
        </View>

        <Section title="Harness trace">
          {runtime?.available ? (
            <>
              <Row label="Detailed trace" value={`${runtime.run_count || 0} run(s) · available`} />
              <Row label="Session" value={runtime.session_name || '—'} />
              <Text variant="xs" dim style={styles.copy}>{runtime.reason}</Text>
            </>
          ) : (
            <Text variant="xs" dim style={styles.copy}>{runtime?.reason || 'No detailed harness trace was recorded for this job.'}</Text>
          )}
        </Section>

        <Section title="Signals">
          <Row label="First verification" value={firstPass} />
          <Row label="Verification" value={`${s.verification_passes} passed · ${s.verification_failures} failed`} />
          <Row label="Additional attempts" value={String(s.retries)} />
          <Row label="Human gates" value={String(s.human_gates)} />
          <Row label="Human responses" value={String(s.human_responses)} />
          <Row label="Recoveries" value={String(s.recoveries)} />
          <Row label="Changed files" value={`${s.changed_files} · +${s.additions} / -${s.deletions}`} />
          <Row label="Tokens" value={tokenSummary(s.tokens)} />
        </Section>

        <Section title="Lifecycle">
          {analysis.phase_breakdown.map(item => (
            <View key={item.status} style={styles.barRow}>
              <View style={styles.barHead}><Text variant="xs" style={{ color: colors.ink2 }}>{label(item.status)}</Text><Text variant="xs" dim style={styles.num}>{duration(item.seconds)} · {item.percent.toFixed(1)}%</Text></View>
              <View style={[styles.track, { backgroundColor: colors.sunken }]}><View style={[styles.fill, { width: `${Math.max(1, item.percent)}%`, backgroundColor: colors.accent.ink }]} /></View>
            </View>
          ))}
        </Section>

        <Section title="State drill-down">
          {(analysis.phase_intervals || []).length ? (analysis.phase_intervals || []).map((item, index) => (
            <View key={`${index}-${item.status}`} style={styles.interval}>
              <View style={styles.barHead}>
                <Text variant="sm" style={{ fontWeight: fontWeights.semibold, color: item.classification === 'stopped' ? colors.danger.ink : colors.ink }}>{label(item.status)}</Text>
                <Text variant="xs" dim style={styles.num}>{label(item.classification)} · {duration(item.duration_seconds)}</Text>
              </View>
              <Text variant="xs" style={[styles.copy, { color: colors.ink2 }]}>{item.interpretation}</Text>
              <Text variant="xs" dim style={styles.copy}>{item.explanation}</Text>
              <Row label="Entered" value={item.entry_event?.summary || 'Not recorded'} />
              <Row label="Exited" value={item.exit_event?.summary || 'Not recorded'} />
              <Row label="Activity" value={`${item.agent_event_count} agent/tool · ${item.worker_event_count} worker · ${item.event_count} total`} />
            </View>
          )) : <Text variant="sm" dim>No lifecycle intervals recorded.</Text>}
        </Section>

        <Section title={`Attempts · ${analysis.attempts.length}`}>
          {analysis.attempts.length ? analysis.attempts.map(item => (
            <View key={item.number} style={styles.attempt}>
              <View style={styles.barHead}><Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>#{item.number} · {label(item.status)}</Text><Text variant="xs" dim style={styles.num}>{duration(item.duration_seconds)} · ${item.cost_usd.toFixed(2)}</Text></View>
              <Text variant="xs" dim style={styles.attemptMeta}>{item.tool_calls} tools · {item.agent_messages} messages · {item.runtime_errors} errors</Text>
              <Text variant="xs" dim style={styles.attemptMeta}>{tokenSummary(item.tokens)}</Text>
            </View>
          )) : <Text variant="sm" dim>No implementation attempts recorded.</Text>}
        </Section>

        <Section title="Tool profile">
          {analysis.tools.length ? analysis.tools.slice(0, 20).map(item => (
            <View key={item.name} style={styles.toolRow}>
              <Text variant="xs" style={[styles.toolName, { color: colors.ink2 }]} numberOfLines={1}>{item.name}</Text>
              <View style={[styles.toolTrack, { backgroundColor: colors.sunken }]}><View style={[styles.toolFill, { width: `${item.count / maxTool * 100}%`, backgroundColor: colors.accent.ink }]} /></View>
              <Text variant="xs" dim style={styles.toolCount}>{item.count}</Text>
            </View>
          )) : <Text variant="sm" dim>No tool-call telemetry recorded.</Text>}
        </Section>

        <Section title={`Verification · ${analysis.verifications.length}`}>
          {analysis.verifications.length ? analysis.verifications.map((item, index) => (
            <View key={`${index}-${item.status}`} style={styles.verification}>
              <Text variant="sm" style={{ color: item.passed ? colors.ok.ink : colors.danger.ink, fontWeight: fontWeights.semibold }}>{item.passed ? 'PASS' : 'FAIL'} · Run {index + 1}</Text>
              <Text variant="xs" dim style={styles.attemptMeta}>{item.checks_passed}/{item.checks} checks · {duration(item.duration_seconds)} · {item.changed_files} files{item.dirty ? ' · dirty' : ''}</Text>
            </View>
          )) : <Text variant="sm" dim>No deterministic verification recorded.</Text>}
        </Section>

        <Section title="Interventions & failures">
          <Row label="Human gates" value={String(analysis.human_gates.length)} />
          {analysis.human_gates.slice(-6).reverse().map(item => <Text key={`g-${item.event_id}`} variant="xs" dim style={styles.incident}>{item.reason || 'Human input required'}</Text>)}
          <Row label="Failures" value={String(analysis.failures.length)} />
          {analysis.failures.slice(-8).reverse().map(item => <Text key={`f-${item.event_id}`} variant="xs" style={[styles.incident, { color: colors.danger.ink }]}>{label(item.event_type)} · {item.summary}</Text>)}
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({ label: metricLabel, value }: { label: string; value: string }) {
  return <View style={styles.metric}><Text variant="xs" dim>{metricLabel}</Text><Text variant="base" style={styles.metricValue}>{value}</Text></View>;
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return <View style={styles.section}><Text variant="sm" style={[styles.sectionTitle, { color: colors.ink }]}>{title}</Text>{children}</View>;
}
function Row({ label: rowLabel, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return <View style={styles.row}><Text variant="xs" dim>{rowLabel}</Text><Text variant="xs" style={{ color: colors.ink2, flex: 1, textAlign: 'right', fontVariant: ['tabular-nums'] }}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: 'transparent' },
  center: { flex: 1, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', padding: spacing.lg },
  content: { paddingBottom: spacing['3xl'] },
  hero: { paddingHorizontal: spacing.base, paddingVertical: spacing.lg },
  title: { marginTop: spacing.xs, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  subtitle: { marginTop: spacing.xs },
  metrics: { flexDirection: 'row', marginHorizontal: spacing.base, marginBottom: spacing.sm, borderRadius: radii.sm },
  metric: { flex: 1, minWidth: 0, paddingHorizontal: spacing.md, paddingVertical: spacing.md },
  metricValue: { marginTop: spacing.xs, fontWeight: fontWeights.semibold, fontSize: fontSizes.base, fontVariant: ['tabular-nums'] },
  num: { fontVariant: ['tabular-nums'] },
  section: { paddingHorizontal: spacing.base, paddingVertical: spacing.base },
  sectionTitle: { fontWeight: fontWeights.semibold, marginBottom: spacing.sm },
  row: { minHeight: 36, paddingVertical: spacing.sm, flexDirection: 'row', gap: spacing.md, alignItems: 'center' },
  barRow: { paddingVertical: spacing.sm },
  barHead: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md, alignItems: 'baseline' },
  track: { height: 4, marginTop: spacing.sm, borderRadius: radii.pill, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: radii.pill },
  interval: { paddingVertical: spacing.md },
  copy: { marginTop: spacing.xs },
  attempt: { paddingVertical: spacing.md },
  attemptMeta: { marginTop: spacing.xs, fontVariant: ['tabular-nums'] },
  toolRow: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  toolName: { width: 118, fontFamily: fonts.mono },
  toolTrack: { flex: 1, height: 4, borderRadius: radii.pill, overflow: 'hidden' },
  toolFill: { height: '100%', borderRadius: radii.pill },
  toolCount: { width: 32, textAlign: 'right', fontVariant: ['tabular-nums'] },
  verification: { paddingVertical: spacing.md },
  incident: { marginTop: spacing.sm },
});
