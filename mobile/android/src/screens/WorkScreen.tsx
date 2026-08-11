import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
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

import { jobsApi, type EngineeringJob, type JobBoardSection, type JobBoardResponse } from '../api/jobs';
import { ModernBottomSheet } from '../components/ModernBottomSheet';
import { Text } from '../components/Text';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';

export type WorkScreenProps = NativeStackScreenProps<RootStackParamList, 'Work'>;

const BOARD_ORDER: Array<{ key: JobBoardSection; label: string }> = [
  { key: 'needs_you', label: 'Needs you' },
  { key: 'running', label: 'Running' },
  { key: 'queued', label: 'Queued' },
  { key: 'ready', label: 'Ready for review' },
  { key: 'failed', label: 'Failed' },
  { key: 'done', label: 'Done' },
];

function statusLabel(value: string): string {
  return String(value || '')
    .split('_')
    .map(part => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(' ');
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

export function WorkScreen({ navigation }: WorkScreenProps) {
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const [board, setBoard] = useState<JobBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const next = await jobsApi.board();
      setBoard(next);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!quiet) setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => { void load(true); }, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const total = useMemo(
    () => Object.values(board?.counts || {}).reduce((sum, count) => sum + Number(count || 0), 0),
    [board],
  );

  const refresh = () => {
    setRefreshing(true);
    void load(true);
  };

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: 'transparent' }]}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.textDim} />}
      >
        <View style={[styles.pageHeader, { borderBottomColor: colors.hairline }]}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xl" style={styles.pageTitle}>Engineering work</Text>
            <Text variant="sm" dim style={styles.pageSubtitle}>
              Autonomous jobs, human gates, verification and review.
            </Text>
          </View>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Create engineering job"
            onPress={() => setCreateOpen(true)}
            style={[styles.newButton, { borderColor: colors.hairline }]}
          >
            <Ionicons name="add" size={18} color={colors.textSoft} />
            <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>New job</Text>
          </TouchableOpacity>
        </View>

        <View style={[styles.summaryRow, { borderBottomColor: colors.hairline }]}>
          <Text variant="xs" dim>{total} total</Text>
          <Text variant="xs" dim>
            {activeSessionName ? `New jobs inherit ${activeSessionName}` : 'No active session profile'}
          </Text>
        </View>

        {error ? (
          <View style={[styles.errorLine, { borderBottomColor: colors.hairline }]}>
            <Ionicons name="alert-circle-outline" size={17} color={colors.error} />
            <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
          </View>
        ) : null}

        {loading && !board ? (
          <ActivityIndicator style={styles.loader} color={colors.accent} />
        ) : total === 0 ? (
          <View style={styles.emptyState}>
            <Text variant="base" style={{ fontWeight: '600' }}>No engineering jobs yet</Text>
            <Text variant="sm" dim style={styles.emptyCopy}>
              Queue a real ticket here, from the TUI, or from the web Engineering Work page.
            </Text>
          </View>
        ) : (
          <View style={styles.sections}>
            {BOARD_ORDER.map(section => {
              const jobs = board?.sections?.[section.key] || [];
              if (!jobs.length) return null;
              return (
                <View key={section.key} style={styles.section}>
                  <View style={styles.sectionHeader}>
                    <Text variant="xs" style={[styles.sectionTitle, { color: colors.textDim }]}>{section.label}</Text>
                    <Text variant="xs" dim>{jobs.length}</Text>
                  </View>
                  <View style={[styles.sectionBody, { borderTopColor: colors.hairline }]}>
                    {jobs.map(job => (
                      <JobRow
                        key={job.id}
                        job={job}
                        onPress={() => navigation.navigate('JobDetail', { jobId: job.id })}
                      />
                    ))}
                  </View>
                </View>
              );
            })}
          </View>
        )}
      </ScrollView>

      <NewJobSheet
        visible={createOpen}
        sessionName={activeSessionName}
        provider={activeProvider}
        model={activeModel}
        onClose={() => setCreateOpen(false)}
        onCreated={job => {
          setCreateOpen(false);
          void load(true);
          navigation.navigate('JobDetail', { jobId: job.id });
        }}
      />
    </SafeAreaView>
  );
}

function JobRow({ job, onPress }: { job: EngineeringJob; onPress: () => void }) {
  const { colors } = useTheme();
  const statusColor = job.status === 'ready_for_review'
    ? colors.success
    : job.status === 'needs_human' || job.status === 'conflicted'
      ? colors.warning
      : ['failed', 'environment_error', 'timed_out', 'budget_exceeded'].includes(job.status)
        ? colors.error
        : ['running', 'preparing', 'verifying', 'recovering'].includes(job.status)
          ? colors.accent
          : colors.textDim;
  const detail = job.needs_attention
    ? (job.attention_detail || job.attention_reason)
    : (job.branch || job.repository || 'Waiting for workspace');

  return (
    <TouchableOpacity
      activeOpacity={0.68}
      onPress={onPress}
      style={[styles.jobRow, { borderBottomColor: colors.hairline }]}
    >
      <View style={styles.jobCopy}>
        <View style={styles.jobTitleRow}>
          <Text variant="sm" style={styles.jobTitle} numberOfLines={1}>{job.title}</Text>
          <Text variant="xs" style={[styles.jobStatus, { color: statusColor }]}>{statusLabel(job.status)}</Text>
        </View>
        <Text variant="xs" dim numberOfLines={1}>{detail}</Text>
      </View>
      <View style={styles.jobTrailing}>
        <Text variant="xs" dim>${Number(job.cost_usd || 0).toFixed(2)}</Text>
        <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
      </View>
    </TouchableOpacity>
  );
}

type NewJobSheetProps = {
  visible: boolean;
  sessionName: string | null;
  provider: string | null;
  model: string | null;
  onClose: () => void;
  onCreated: (job: EngineeringJob) => void;
};

function NewJobSheet({ visible, sessionName, provider, model, onClose, onCreated }: NewJobSheetProps) {
  const { colors } = useTheme();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [repository, setRepository] = useState('');
  const [acceptance, setAcceptance] = useState('');
  const [validation, setValidation] = useState('');
  const [cost, setCost] = useState('');
  const [runtime, setRuntime] = useState('');
  const [retries, setRetries] = useState('2');
  const [providerValue, setProviderValue] = useState(provider || '');
  const [modelValue, setModelValue] = useState(model || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    setProviderValue(provider || '');
    setModelValue(model || '');
    setError('');
  }, [model, provider, visible]);

  const submit = async () => {
    const cleanTitle = title.trim();
    if (!cleanTitle) {
      setError('Title is required.');
      return;
    }
    if (!sessionName && (!providerValue.trim() || !modelValue.trim())) {
      setError('Provider and model are required without an active session.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const input: Parameters<typeof jobsApi.create>[0] = {
        title: cleanTitle,
        description: description.trim(),
        repository: repository.trim() || undefined,
        session_name: sessionName || undefined,
        acceptance_criteria: lines(acceptance),
        validation_commands: lines(validation),
        max_retries: Math.max(0, Number(retries || 2)),
      };
      const costValue = Number(cost || 0);
      const runtimeValue = Number(runtime || 0);
      if (costValue > 0) input.max_cost_usd = costValue;
      if (runtimeValue > 0) input.max_runtime_seconds = runtimeValue;
      if (!sessionName || providerValue.trim() || modelValue.trim()) {
        input.execution = {};
        if (providerValue.trim()) input.execution.provider = providerValue.trim();
        if (modelValue.trim()) input.execution.model = modelValue.trim();
      }
      const response = await jobsApi.create(input);
      setTitle('');
      setDescription('');
      setRepository('');
      setAcceptance('');
      setValidation('');
      setCost('');
      setRuntime('');
      setRetries('2');
      onCreated(response.job);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="New engineering job">
      <Text variant="xs" dim style={styles.sheetIntro}>
        {sessionName
          ? `Provider, model, mode and workspace inherit from ${sessionName} unless overridden.`
          : 'No active session is available, so enter a repository, provider and model.'}
      </Text>
      <SheetField label="Title" value={title} onChangeText={setTitle} placeholder="Fix OAuth callback validation" />
      <SheetField label="Description" value={description} onChangeText={setDescription} placeholder="What should change?" multiline />
      <SheetField label="Repository" value={repository} onChangeText={setRepository} placeholder={sessionName ? 'Inherit current workspace' : '/path/to/repository'} />
      <SheetField label="Acceptance criteria" value={acceptance} onChangeText={setAcceptance} placeholder="One criterion per line" multiline />
      <SheetField label="Validation commands" value={validation} onChangeText={setValidation} placeholder={'pytest tests/auth\nnpm run typecheck'} multiline />
      <View style={styles.sheetGrid}>
        <SheetField label="Cost USD" value={cost} onChangeText={setCost} placeholder="5.00" keyboardType="decimal-pad" compact />
        <SheetField label="Runtime sec" value={runtime} onChangeText={setRuntime} placeholder="7200" keyboardType="number-pad" compact />
        <SheetField label="Retries" value={retries} onChangeText={setRetries} placeholder="2" keyboardType="number-pad" compact />
      </View>
      {!sessionName ? (
        <>
          <SheetField label="Provider" value={providerValue} onChangeText={setProviderValue} placeholder="openai" />
          <SheetField label="Model" value={modelValue} onChangeText={setModelValue} placeholder="model name" />
        </>
      ) : null}
      {error ? <Text variant="xs" style={[styles.sheetError, { color: colors.error }]}>{error}</Text> : null}
      <View style={styles.sheetActions}>
        <TouchableOpacity onPress={onClose} style={[styles.sheetButton, { borderColor: colors.hairline }]}>
          <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>Cancel</Text>
        </TouchableOpacity>
        <TouchableOpacity disabled={submitting} onPress={() => void submit()} style={[styles.sheetButton, { borderColor: colors.accent, backgroundColor: colors.accentSoft }]}>
          {submitting ? <ActivityIndicator size="small" color={colors.accent} /> : <Text variant="xs" style={{ color: colors.text, fontWeight: '600' }}>Queue job</Text>}
        </TouchableOpacity>
      </View>
    </ModernBottomSheet>
  );
}

type SheetFieldProps = {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  placeholder: string;
  multiline?: boolean;
  keyboardType?: 'default' | 'decimal-pad' | 'number-pad';
  compact?: boolean;
};

function SheetField({ label, value, onChangeText, placeholder, multiline = false, keyboardType = 'default', compact = false }: SheetFieldProps) {
  const { colors } = useTheme();
  return (
    <View style={[styles.sheetField, compact && styles.sheetFieldCompact]}>
      <Text variant="xs" style={{ color: colors.textDim, fontWeight: '600' }}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textDim}
        multiline={multiline}
        keyboardType={keyboardType}
        autoCapitalize="none"
        style={[
          styles.sheetInput,
          multiline && styles.sheetTextarea,
          { color: colors.text, borderBottomColor: colors.hairline },
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: 48 },
  pageHeader: {
    paddingHorizontal: 18,
    paddingTop: 18,
    paddingBottom: 18,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 14,
  },
  pageHeaderCopy: { flex: 1 },
  pageTitle: { fontWeight: '600', letterSpacing: -0.45 },
  pageSubtitle: { marginTop: 4, lineHeight: 19 },
  newButton: {
    minHeight: 36,
    paddingHorizontal: 10,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  summaryRow: {
    minHeight: 42,
    paddingHorizontal: 18,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  errorLine: {
    minHeight: 46,
    paddingHorizontal: 18,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  loader: { marginTop: 50 },
  emptyState: { paddingHorizontal: 24, paddingTop: 54, alignItems: 'center' },
  emptyCopy: { textAlign: 'center', marginTop: 6, lineHeight: 20, maxWidth: 320 },
  sections: { paddingTop: 10 },
  section: { marginBottom: 10 },
  sectionHeader: {
    height: 30,
    paddingHorizontal: 18,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  sectionTitle: { textTransform: 'uppercase', letterSpacing: 0.7, fontWeight: '600' },
  sectionBody: { borderTopWidth: StyleSheet.hairlineWidth },
  jobRow: {
    minHeight: 72,
    paddingHorizontal: 18,
    paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  jobCopy: { flex: 1, minWidth: 0 },
  jobTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 5 },
  jobTitle: { flex: 1, fontWeight: '600' },
  jobStatus: { fontSize: 9.5, textTransform: 'uppercase', letterSpacing: 0.45, fontWeight: '600' },
  jobTrailing: { alignItems: 'flex-end', gap: 5 },
  sheetIntro: { lineHeight: 18, marginBottom: 16 },
  sheetField: { marginBottom: 16 },
  sheetFieldCompact: { flex: 1, minWidth: 80 },
  sheetInput: { minHeight: 38, paddingHorizontal: 1, paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth, fontSize: 14 },
  sheetTextarea: { minHeight: 74, textAlignVertical: 'top' },
  sheetGrid: { flexDirection: 'row', gap: 12 },
  sheetError: { marginTop: 2, lineHeight: 18 },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 18 },
  sheetButton: { minWidth: 88, minHeight: 38, borderWidth: StyleSheet.hairlineWidth, borderRadius: 8, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12 },
});
