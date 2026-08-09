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

export type JobAnalysisScreenProps = NativeStackScreenProps<RootStackParamList, 'JobAnalysis'>;

type PhaseBreakdown = { status: string; seconds: number; percent: number; occurrences: number };
type AttemptAnalysis = {
  number: number; status: string; duration_seconds: number; cost_usd: number;
  tool_calls: number; agent_messages: number; runtime_errors: number; tokens: Record<string, number>;
};
type ToolAnalysis = { name: string; count: number; share: number };
type VerificationAnalysis = {
  status: string; passed: boolean; duration_seconds: number; checks: number; checks_passed: number;
  changed_files: number; additions: number; deletions: number; dirty: boolean;
};
type JobAnalysis = {
  job: { id: string; title: string; status: string; archived?: boolean };
  summary: {
    elapsed_seconds: number; active_seconds: number; waiting_seconds: number; verification_seconds: number;
    attempts: number; retries: number; cost_usd: number; tokens: Record<string, number>;
    tool_calls: number; unique_tools: number; human_gates: number; human_responses: number;
    verification_runs: number; verification_passes: number; verification_failures: number;
    first_pass_verification: boolean | null; failures: number; recoveries: number;
    changed_files: number; additions: number; deletions: number; waiting_ratio: number | null;
  };
  phase_breakdown: PhaseBreakdown[];
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
    return <SafeAreaView edges={['bottom']} style={styles.center}><ActivityIndicator color={colors.accent} /></SafeAreaView>;
  }
  if (!analysis) {
    return <SafeAreaView edges={['bottom']} style={styles.center}><Text variant="sm" style={{ color: colors.error }}>{error || 'Could not load job analysis.'}</Text></SafeAreaView>;
  }

  const s = analysis.summary;
  const firstPass = s.first_pass_verification == null ? 'Not measured' : s.first_pass_verification ? 'Passed' : 'Needed repair';
  const maxTool = Math.max(1, ...analysis.tools.map(item => item.count));

  return (
    <SafeAreaView edges={['bottom']} style={styles.safe}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(true); }} tintColor={colors.textDim} />}
      >
        <View style={[styles.hero, { borderBottomColor: colors.hairline }]}>
          <Text variant="xs" style={{ color: colors.textDim }}>{label(analysis.job.status)}{analysis.job.archived ? ' · Archived' : ''}</Text>
          <Text variant="xl" style={styles.title}>Job performance</Text>
          <Text variant="sm" dim style={styles.subtitle}>Retrospective analysis from durable execution, verification and human-intervention telemetry.</Text>
          {error ? <Text variant="xs" style={{ color: colors.error, marginTop: 8 }}>{error}</Text> : null}
        </View>

        <View style={[styles.metrics, { borderBottomColor: colors.hairline }]}>
          <Metric label="Wall" value={duration(s.elapsed_seconds)} />
          <Metric label="Active" value={duration(s.active_seconds)} />
          <Metric label="Waiting" value={duration(s.waiting_seconds)} />
          <Metric label="Cost" value={`$${Number(s.cost_usd || 0).toFixed(2)}`} />
        </View>
        <View style={[styles.metrics, { borderBottomColor: colors.hairline }]}>
          <Metric label="Attempts" value={String(s.attempts)} />
          <Metric label="Tools" value={String(s.tool_calls)} />
          <Metric label="Human gates" value={String(s.human_gates)} />
          <Metric label="Failures" value={String(s.failures)} />
        </View>

        <Section title="Signals">
          <Row label="First verification" value={firstPass} />
          <Row label="Verification" value={`${s.verification_passes} passed · ${s.verification_failures} failed`} />
          <Row label="Additional attempts" value={String(s.retries)} />
          <Row label="Human responses" value={String(s.human_responses)} />
          <Row label="Recoveries" value={String(s.recoveries)} />
          <Row label="Changed files" value={`${s.changed_files} · +${s.additions} / -${s.deletions}`} />
          <Row label="Tokens" value={tokenSummary(s.tokens)} />
        </Section>

        <Section title="Lifecycle">
          {analysis.phase_breakdown.map(item => (
            <View key={item.status} style={[styles.barRow, { borderTopColor: colors.hairline }]}>
              <View style={styles.barHead}><Text variant="xs" style={{ color: colors.textSoft }}>{label(item.status)}</Text><Text variant="xs" dim>{duration(item.seconds)} · {item.percent.toFixed(1)}%</Text></View>
              <View style={[styles.track, { backgroundColor: colors.hairline }]}><View style={[styles.fill, { width: `${Math.max(1, item.percent)}%`, backgroundColor: colors.accent }]} /></View>
            </View>
          ))}
        </Section>

        <Section title={`Attempts · ${analysis.attempts.length}`}>
          {analysis.attempts.length ? analysis.attempts.map(item => (
            <View key={item.number} style={[styles.attempt, { borderTopColor: colors.hairline }]}>
              <View style={styles.barHead}><Text variant="sm" style={{ fontWeight: '600' }}>#{item.number} · {label(item.status)}</Text><Text variant="xs" dim>{duration(item.duration_seconds)} · ${item.cost_usd.toFixed(2)}</Text></View>
              <Text variant="xs" dim style={styles.attemptMeta}>{item.tool_calls} tools · {item.agent_messages} messages · {item.runtime_errors} errors</Text>
              <Text variant="xs" dim style={styles.attemptMeta}>{tokenSummary(item.tokens)}</Text>
            </View>
          )) : <Text variant="sm" dim>No implementation attempts recorded.</Text>}
        </Section>

        <Section title="Tool profile">
          {analysis.tools.length ? analysis.tools.slice(0, 20).map(item => (
            <View key={item.name} style={styles.toolRow}>
              <Text variant="xs" style={[styles.toolName, { color: colors.textSoft }]} numberOfLines={1}>{item.name}</Text>
              <View style={[styles.toolTrack, { backgroundColor: colors.hairline }]}><View style={[styles.toolFill, { width: `${item.count / maxTool * 100}%`, backgroundColor: colors.accent }]} /></View>
              <Text variant="xs" dim style={styles.toolCount}>{item.count}</Text>
            </View>
          )) : <Text variant="sm" dim>No tool-call telemetry recorded.</Text>}
        </Section>

        <Section title={`Verification · ${analysis.verifications.length}`}>
          {analysis.verifications.length ? analysis.verifications.map((item, index) => (
            <View key={`${index}-${item.status}`} style={[styles.verification, { borderTopColor: colors.hairline }]}>
              <Text variant="sm" style={{ color: item.passed ? colors.success : colors.error, fontWeight: '600' }}>{item.passed ? 'PASS' : 'FAIL'} · Run {index + 1}</Text>
              <Text variant="xs" dim style={styles.attemptMeta}>{item.checks_passed}/{item.checks} checks · {duration(item.duration_seconds)} · {item.changed_files} files{item.dirty ? ' · dirty' : ''}</Text>
            </View>
          )) : <Text variant="sm" dim>No deterministic verification recorded.</Text>}
        </Section>

        <Section title="Interventions & failures">
          <Row label="Human gates" value={String(analysis.human_gates.length)} />
          {analysis.human_gates.slice(-6).reverse().map(item => <Text key={`g-${item.event_id}`} variant="xs" dim style={styles.incident}>{item.reason || 'Human input required'}</Text>)}
          <Row label="Failures" value={String(analysis.failures.length)} />
          {analysis.failures.slice(-8).reverse().map(item => <Text key={`f-${item.event_id}`} variant="xs" style={[styles.incident, { color: colors.error }]}>{label(item.event_type)} · {item.summary}</Text>)}
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({ label: metricLabel, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return <View style={[styles.metric, { borderRightColor: colors.hairline }]}><Text variant="xs" dim>{metricLabel}</Text><Text variant="base" style={styles.metricValue}>{value}</Text></View>;
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return <View style={[styles.section, { borderBottomColor: colors.hairline }]}><Text variant="sm" style={styles.sectionTitle}>{title}</Text>{children}</View>;
}
function Row({ label: rowLabel, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return <View style={[styles.row, { borderTopColor: colors.hairline }]}><Text variant="xs" dim>{rowLabel}</Text><Text variant="xs" style={{ color: colors.textSoft, flex: 1, textAlign: 'right' }}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: 'transparent' },
  center: { flex: 1, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', padding: 24 },
  content: { paddingBottom: 60 },
  hero: { paddingHorizontal: 18, paddingVertical: 20, borderBottomWidth: StyleSheet.hairlineWidth },
  title: { marginTop: 4, fontWeight: '600', letterSpacing: -0.45 },
  subtitle: { marginTop: 6, lineHeight: 19 },
  metrics: { flexDirection: 'row', borderBottomWidth: StyleSheet.hairlineWidth },
  metric: { flex: 1, minWidth: 0, paddingHorizontal: 10, paddingVertical: 13, borderRightWidth: StyleSheet.hairlineWidth },
  metricValue: { marginTop: 4, fontWeight: '600', fontSize: 14 },
  section: { paddingHorizontal: 18, paddingVertical: 18, borderBottomWidth: StyleSheet.hairlineWidth },
  sectionTitle: { fontWeight: '600', marginBottom: 9 },
  row: { minHeight: 38, paddingVertical: 9, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: 'row', gap: 12, alignItems: 'center' },
  barRow: { paddingVertical: 10, borderTopWidth: StyleSheet.hairlineWidth },
  barHead: { flexDirection: 'row', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' },
  track: { height: 5, marginTop: 7, borderRadius: 3, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 3, opacity: 0.72 },
  attempt: { paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth },
  attemptMeta: { marginTop: 4, lineHeight: 16 },
  toolRow: { minHeight: 34, flexDirection: 'row', alignItems: 'center', gap: 8 },
  toolName: { width: 118, fontFamily: 'monospace' },
  toolTrack: { flex: 1, height: 5, borderRadius: 3, overflow: 'hidden' },
  toolFill: { height: '100%', borderRadius: 3, opacity: 0.68 },
  toolCount: { width: 32, textAlign: 'right' },
  verification: { paddingVertical: 11, borderTopWidth: StyleSheet.hairlineWidth },
  incident: { marginTop: 7, lineHeight: 16 },
});
