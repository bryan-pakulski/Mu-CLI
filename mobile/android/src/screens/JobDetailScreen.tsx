import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';

import {
  jobsApi,
  type EngineeringJob,
  type JobDiff,
  type JobEvent,
  type WorkReceipt,
} from '../api/jobs';
import { Text } from '../components/Text';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';

export type JobDetailScreenProps = NativeStackScreenProps<RootStackParamList, 'JobDetail'>;

function statusLabel(value: string): string {
  return String(value || '').split('_').map(part => part ? part[0].toUpperCase() + part.slice(1) : part).join(' ');
}

function formatDuration(value: number): string {
  let seconds = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;
  if (hours) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function eventSummary(event: JobEvent): string {
  const payload = event.payload || {};
  if (event.event_type === 'agent_message') return String(payload.text || '').slice(0, 1000);
  if (event.event_type === 'tool_call_ui') return String(payload.tool_name || '');
  if (event.event_type === 'human_response' || event.event_type === 'review_feedback') return String(payload.detail || '');
  if (event.event_type === 'status_changed') return `${event.from_status || 'new'} → ${event.to_status || ''}${event.reason ? ` · ${event.reason}` : ''}`;
  if (event.event_type === 'checkpoint_created') return `${String(payload.label || 'checkpoint')} · ${String(payload.sha || '').slice(0, 12)}`;
  if (event.reason) return event.reason;
  return String(payload.text || payload.summary || payload.error || '');
}

function latestAttentionContext(events: JobEvent[]): Record<string, unknown> {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].to_status === 'needs_human') return events[index].payload || {};
  }
  return {};
}

export function JobDetailScreen({ route, navigation }: JobDetailScreenProps) {
  const { colors } = useTheme();
  const { jobId } = route.params;
  const [job, setJob] = useState<EngineeringJob | null>(null);
  const [receipt, setReceipt] = useState<WorkReceipt | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [diff, setDiff] = useState<JobDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState('');
  const [responseText, setResponseText] = useState('');

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [jobResult, receiptResult, eventsResult, diffResult] = await Promise.allSettled([
        jobsApi.get(jobId),
        jobsApi.receipt(jobId),
        jobsApi.events(jobId),
        jobsApi.diff(jobId),
      ]);
      if (jobResult.status !== 'fulfilled') throw jobResult.reason;
      const nextJob = jobResult.value.job;
      setJob(nextJob);
      navigation.setOptions({ title: nextJob.title });
      setReceipt(receiptResult.status === 'fulfilled' ? receiptResult.value.receipt : null);
      setEvents(eventsResult.status === 'fulfilled' ? eventsResult.value.events || [] : []);
      setDiff(diffResult.status === 'fulfilled' ? diffResult.value.diff : null);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!quiet) setLoading(false);
      setRefreshing(false);
    }
  }, [jobId, navigation]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => { void load(true); }, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const attentionContext = useMemo(() => latestAttentionContext(events), [events]);

  const run = async (action: 'approve' | 'deny' | 'respond' | 'changes' | 'continue' | 'discard') => {
    if (!job || acting) return;
    setActing(true);
    setError('');
    try {
      if (action === 'approve' || action === 'deny') {
        await jobsApi.respond(job.id, { decision: action, detail: responseText.trim() });
      } else if (action === 'respond') {
        if (!responseText.trim()) throw new Error('Enter a response first.');
        if (job.attention_reason === 'verification_required') {
          const commands = responseText.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
          await jobsApi.respond(job.id, { detail: responseText.trim(), value: commands });
        } else {
          await jobsApi.respond(job.id, { detail: responseText.trim(), value: responseText.trim(), selected: [responseText.trim()] });
        }
      } else if (action === 'changes') {
        if (!responseText.trim()) throw new Error('Add review feedback first.');
        await jobsApi.requestChanges(job.id, responseText.trim());
      } else if (action === 'continue') {
        await jobsApi.continue(job.id, responseText.trim());
      } else if (action === 'discard') {
        await jobsApi.discard(job.id, responseText.trim() || 'discarded from mobile review');
      }
      setResponseText('');
      await load(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActing(false);
    }
  };

  const confirmDiscard = () => {
    Alert.alert(
      job?.status === 'ready_for_review' ? 'Discard reviewed job?' : 'Cancel job?',
      'The job will stop running. Its branch, worktree and evidence remain available for inspection.',
      [
        { text: 'Keep job', style: 'cancel' },
        { text: 'Discard', style: 'destructive', onPress: () => { void run('discard'); } },
      ],
    );
  };

  if (loading && !job) {
    return (
      <SafeAreaView edges={['bottom']} style={styles.centered}>
        <ActivityIndicator color={colors.accent} />
      </SafeAreaView>
    );
  }

  if (!job) {
    return (
      <SafeAreaView edges={['bottom']} style={styles.centered}>
        <Text variant="base" style={{ color: colors.error }}>Could not load job</Text>
        <Text variant="sm" dim style={styles.centeredCopy}>{error}</Text>
      </SafeAreaView>
    );
  }

  const outcome = receipt?.outcome;
  const git = receipt?.git;
  const verification = receipt?.verification;
  const statusColor = job.status === 'ready_for_review'
    ? colors.success
    : job.status === 'needs_human' || job.status === 'conflicted'
      ? colors.warning
      : ['failed', 'environment_error', 'timed_out', 'budget_exceeded'].includes(job.status)
        ? colors.error
        : ['running', 'preparing', 'verifying', 'recovering'].includes(job.status)
          ? colors.accent
          : colors.textDim;

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: 'transparent' }]}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(true); }} tintColor={colors.textDim} />}
      >
        <View style={[styles.hero, { borderBottomColor: colors.hairline }]}>
          <Text variant="xs" style={[styles.status, { color: statusColor }]}>{statusLabel(job.status)}</Text>
          <Text variant="xl" style={styles.title}>{job.title}</Text>
          {job.description ? <Text variant="sm" dim style={styles.description}>{job.description}</Text> : null}
          {job.status === 'ready_for_review' ? (
            <View style={[styles.banner, { borderLeftColor: colors.success, backgroundColor: colors.bgHover }]}>
              <Text variant="xs" style={{ color: colors.textSoft }}>
                Ready for review. Every configured deterministic check passed on a clean isolated worktree.
              </Text>
            </View>
          ) : null}
          {job.needs_attention ? (
            <View style={[styles.banner, { borderLeftColor: colors.warning, backgroundColor: colors.bgHover }]}>
              <Text variant="xs" style={{ color: colors.warning, fontWeight: '600' }}>{statusLabel(job.attention_reason)}</Text>
              <Text variant="xs" style={{ color: colors.textSoft, marginTop: 3 }}>{job.attention_detail}</Text>
            </View>
          ) : null}
        </View>

        <View style={[styles.metrics, { borderBottomColor: colors.hairline }]}>
          <Metric label="Worked" value={formatDuration(outcome?.elapsed_seconds || 0)} />
          <Metric label="Cost" value={`$${Number(outcome?.cost_usd || job.cost_usd || 0).toFixed(2)}`} />
          <Metric label="Attempts" value={String(outcome?.attempts || 0)} />
          <Metric label="Changes" value={`+${git?.additions || 0} / -${git?.deletions || 0}`} />
        </View>

        {error ? (
          <View style={[styles.errorLine, { borderBottomColor: colors.hairline }]}>
            <Ionicons name="alert-circle-outline" size={17} color={colors.error} />
            <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
          </View>
        ) : null}

        <ReviewActions
          job={job}
          context={attentionContext}
          responseText={responseText}
          setResponseText={setResponseText}
          acting={acting}
          onAction={run}
          onDiscard={confirmDiscard}
        />

        <DetailSection title="Work receipt">
          <ReceiptRow label="Repository" value={job.repository || '—'} />
          <ReceiptRow label="Branch" value={job.branch || '—'} />
          <ReceiptRow label="Base" value={(job.base_sha || '—').slice(0, 12)} />
          <ReceiptRow label="Head" value={(git?.head_sha || '—').slice(0, 12)} />
          <ReceiptRow label="Runtime" value={`${job.execution?.provider || '—'} · ${job.execution?.model || '—'}`} />
          <ReceiptRow label="Mode" value={job.execution?.agent_mode || 'default'} />
          <ReceiptRow label="Workspace clean" value={git?.dirty === false ? 'yes' : git?.dirty === true ? 'no' : 'unknown'} />
        </DetailSection>

        <DetailSection title="Acceptance criteria">
          {job.acceptance_criteria.length ? job.acceptance_criteria.map((item, index) => (
            <View key={`${index}-${item}`} style={styles.bulletRow}>
              <Text variant="xs" style={{ color: colors.textDim }}>•</Text>
              <Text variant="sm" style={{ color: colors.textSoft, flex: 1 }}>{item}</Text>
            </View>
          )) : <Text variant="sm" dim>No explicit acceptance criteria.</Text>}
        </DetailSection>

        <DetailSection title={`Verification · ${verification ? statusLabel(verification.status) : 'Not run'}`}>
          {verification?.checks?.length ? verification.checks.map((check, index) => (
            <View key={`${index}-${check.command}`} style={[styles.checkRow, { borderBottomColor: colors.hairline }]}>
              <Ionicons name={check.passed ? 'checkmark' : 'close'} size={17} color={check.passed ? colors.success : colors.error} />
              <View style={styles.checkCopy}>
                <Text variant="xs" style={[styles.mono, { color: colors.textSoft }]} numberOfLines={2}>{check.command}</Text>
                {!check.passed && (check.error || check.stderr || check.stdout) ? (
                  <Text variant="xs" style={{ color: colors.error, marginTop: 4 }} numberOfLines={8}>
                    {String(check.error || check.stderr || check.stdout).slice(-4000)}
                  </Text>
                ) : null}
              </View>
              <Text variant="xs" dim>{Number(check.duration_seconds || 0).toFixed(2)}s</Text>
            </View>
          )) : <Text variant="sm" dim>No deterministic checks have run yet.</Text>}
        </DetailSection>

        <DetailSection title={`Git diff · ${diff?.files?.length || git?.changed_files?.length || 0} files`}>
          <Text variant="xs" dim style={[styles.mono, styles.diffStat]}>{diff?.stat || git?.diff_stat || 'No diff available yet.'}</Text>
          {diff ? (
            <ScrollView horizontal style={[styles.diffBox, { borderTopColor: colors.hairline, borderBottomColor: colors.hairline }]}>
              <Text variant="xs" style={[styles.mono, { color: colors.textSoft }]} selectable>
                {diff.patch || 'No changed lines.'}
              </Text>
            </ScrollView>
          ) : null}
        </DetailSection>

        <DetailSection title="Activity">
          {events.length ? events.slice(-35).reverse().map(event => (
            <View key={event.id} style={[styles.timelineRow, { borderBottomColor: colors.hairline }]}>
              <View style={[styles.timelineDot, { backgroundColor: colors.textDim }]} />
              <View style={styles.timelineCopy}>
                <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>{statusLabel(event.event_type)}</Text>
                {eventSummary(event) ? <Text variant="xs" dim style={styles.timelineBody}>{eventSummary(event)}</Text> : null}
              </View>
            </View>
          )) : <Text variant="sm" dim>No activity recorded yet.</Text>}
        </DetailSection>
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.metric, { borderRightColor: colors.hairline }]}>
      <Text variant="xs" style={[styles.metricLabel, { color: colors.textDim }]}>{label}</Text>
      <Text variant="base" style={styles.metricValue}>{value}</Text>
    </View>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.section, { borderBottomColor: colors.hairline }]}>
      <Text variant="sm" style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function ReceiptRow({ label, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.receiptRow, { borderTopColor: colors.hairline }]}>
      <Text variant="xs" dim style={styles.receiptLabel}>{label}</Text>
      <Text variant="xs" style={[styles.receiptValue, styles.mono, { color: colors.textSoft }]} numberOfLines={2} ellipsizeMode="middle">{value}</Text>
    </View>
  );
}

type ReviewActionsProps = {
  job: EngineeringJob;
  context: Record<string, unknown>;
  responseText: string;
  setResponseText: (value: string) => void;
  acting: boolean;
  onAction: (action: 'approve' | 'deny' | 'respond' | 'changes' | 'continue' | 'discard') => Promise<void>;
  onDiscard: () => void;
};

function ReviewActions({ job, context, responseText, setResponseText, acting, onAction, onDiscard }: ReviewActionsProps) {
  const { colors } = useTheme();
  if (['merged', 'cancelled'].includes(job.status)) return null;

  let title = statusLabel(job.status);
  let description = 'MuCLI is working in the background. Closing mobile does not affect execution.';
  let placeholder = 'Optional guidance';
  let controls: React.ReactNode = (
    <ActionButton label="Cancel job" destructive disabled={acting} onPress={onDiscard} />
  );

  if (job.status === 'needs_human' && job.attention_reason === 'approval_required') {
    title = 'Approval required';
    description = job.attention_detail || `Approve ${String(context.tool_name || 'tool request')}?`;
    placeholder = 'Optional explanation';
    controls = (
      <>
        {context.can_approve !== false ? <ActionButton label="Approve" primary disabled={acting} onPress={() => void onAction('approve')} /> : null}
        <ActionButton label="Deny" destructive disabled={acting} onPress={() => void onAction('deny')} />
      </>
    );
  } else if (job.status === 'needs_human') {
    title = job.attention_reason === 'verification_required' ? 'Validation required' : 'Input required';
    description = job.attention_detail || 'The agent needs your input to continue.';
    placeholder = job.attention_reason === 'verification_required' ? 'One validation command per line' : 'Your response';
    controls = <ActionButton label="Respond & continue" primary disabled={acting} onPress={() => void onAction('respond')} />;
  } else if (job.status === 'ready_for_review') {
    title = 'Review decision';
    description = 'Verification passed. Review the evidence and diff before sending it back for more work.';
    placeholder = 'Feedback for another implementation pass';
    controls = (
      <>
        <ActionButton label="Request changes" primary disabled={acting} onPress={() => void onAction('changes')} />
        <ActionButton label="Continue work" disabled={acting} onPress={() => void onAction('continue')} />
        <ActionButton label="Discard" destructive disabled={acting} onPress={onDiscard} />
      </>
    );
  } else if (['failed', 'timed_out', 'budget_exceeded', 'environment_error'].includes(job.status)) {
    title = 'Recovery';
    description = 'Add guidance and retry the same durable branch and job session.';
    placeholder = 'Optional guidance for the retry';
    controls = (
      <>
        <ActionButton label="Retry / continue" primary disabled={acting} onPress={() => void onAction('continue')} />
        <ActionButton label="Discard" destructive disabled={acting} onPress={onDiscard} />
      </>
    );
  }

  return (
    <View style={[styles.actionPanel, { borderBottomColor: colors.hairline }]}>
      <Text variant="sm" style={styles.actionTitle}>{title}</Text>
      <Text variant="xs" dim style={styles.actionDescription}>{description}</Text>
      {job.status === 'needs_human' && job.attention_reason === 'approval_required' && context.tool_name ? (
        <Text variant="xs" style={[styles.mono, { color: colors.textDim, marginBottom: 8 }]}>{String(context.tool_name)}</Text>
      ) : null}
      {!['queued', 'preparing', 'running', 'verifying', 'recovering'].includes(job.status) ? (
        <TextInput
          value={responseText}
          onChangeText={setResponseText}
          placeholder={placeholder}
          placeholderTextColor={colors.textDim}
          multiline
          style={[styles.actionInput, { color: colors.text, borderBottomColor: colors.hairline }]}
        />
      ) : null}
      <View style={styles.actionButtons}>{controls}</View>
      {acting ? <ActivityIndicator size="small" color={colors.accent} style={styles.actionSpinner} /> : null}
    </View>
  );
}

function ActionButton({ label, primary = false, destructive = false, disabled = false, onPress }: { label: string; primary?: boolean; destructive?: boolean; disabled?: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  const color = destructive ? colors.error : primary ? colors.text : colors.textSoft;
  const borderColor = destructive ? colors.error : primary ? colors.accent : colors.hairline;
  return (
    <TouchableOpacity
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.actionButton,
        { borderColor, backgroundColor: primary ? colors.accentSoft : 'transparent', opacity: disabled ? 0.5 : 1 },
      ]}
    >
      <Text variant="xs" style={{ color, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  centered: { flex: 1, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', padding: 24 },
  centeredCopy: { marginTop: 6, textAlign: 'center' },
  content: { paddingBottom: 60 },
  hero: { paddingHorizontal: 18, paddingTop: 18, paddingBottom: 20, borderBottomWidth: StyleSheet.hairlineWidth },
  status: { fontSize: 9.5, textTransform: 'uppercase', letterSpacing: 0.6, fontWeight: '650' },
  title: { marginTop: 5, fontWeight: '650', letterSpacing: -0.5 },
  description: { marginTop: 7, lineHeight: 20 },
  banner: { marginTop: 15, paddingHorizontal: 11, paddingVertical: 10, borderLeftWidth: 2 },
  metrics: { flexDirection: 'row', borderBottomWidth: StyleSheet.hairlineWidth },
  metric: { flex: 1, paddingHorizontal: 12, paddingVertical: 14, borderRightWidth: StyleSheet.hairlineWidth },
  metricLabel: { textTransform: 'uppercase', letterSpacing: 0.5, fontWeight: '600' },
  metricValue: { marginTop: 4, fontWeight: '600', fontSize: 15 },
  errorLine: { minHeight: 46, paddingHorizontal: 18, flexDirection: 'row', alignItems: 'center', gap: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  actionPanel: { paddingHorizontal: 18, paddingVertical: 18, borderBottomWidth: StyleSheet.hairlineWidth },
  actionTitle: { fontWeight: '600' },
  actionDescription: { marginTop: 4, marginBottom: 8, lineHeight: 18 },
  actionInput: { minHeight: 68, paddingHorizontal: 1, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, textAlignVertical: 'top', fontSize: 13, marginBottom: 12 },
  actionButtons: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  actionButton: { minHeight: 36, paddingHorizontal: 11, borderWidth: StyleSheet.hairlineWidth, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  actionSpinner: { marginTop: 12, alignSelf: 'flex-start' },
  section: { paddingHorizontal: 18, paddingVertical: 19, borderBottomWidth: StyleSheet.hairlineWidth },
  sectionTitle: { fontWeight: '600', marginBottom: 11 },
  receiptRow: { minHeight: 39, paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 12 },
  receiptLabel: { width: 102 },
  receiptValue: { flex: 1, textAlign: 'right' },
  bulletRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  checkRow: { minHeight: 46, paddingVertical: 9, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'flex-start', gap: 9 },
  checkCopy: { flex: 1 },
  mono: { fontFamily: 'monospace' },
  diffStat: { lineHeight: 17, marginBottom: 10 },
  diffBox: { maxHeight: 420, paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth, borderBottomWidth: StyleSheet.hairlineWidth },
  timelineRow: { minHeight: 48, paddingVertical: 9, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: 'row', gap: 10 },
  timelineDot: { width: 4, height: 4, borderRadius: 2, marginTop: 6 },
  timelineCopy: { flex: 1 },
  timelineBody: { marginTop: 3, lineHeight: 17 },
});
