import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import {
  featureApi,
  FeatureDiffProposal,
  FeatureEvent,
  FeatureListItem,
  FeaturePhase,
  FeatureReviewRecord,
  FeatureState,
  FeatureSummary,
  FeatureTask,
} from '../api/feature';
import { useTheme } from '../theme/ThemeContext';
import { Card, EmptyState, ErrorState, Skeleton, Text } from '../components';
import { spacing } from '../theme/tokens';

type FeatureFilter = 'features' | 'completed' | 'archived' | 'all';

const FILTERS: Array<{ key: FeatureFilter; label: string }> = [
  { key: 'features', label: 'Features' },
  { key: 'completed', label: 'Completed' },
  { key: 'archived', label: 'Archived' },
  { key: 'all', label: 'All' },
];

export function FeatureExplorerScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<FeatureState | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<FeatureSummary | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FeatureFilter>('features');
  const [loading, setLoading] = useState(true);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const [expandedRecords, setExpandedRecords] = useState<Set<string>>(new Set());
  const selectedIdRef = useRef<string | null>(null);

  const previewFeature = useCallback(async (featureId: string, quiet = false) => {
    if (!quiet) setPreviewLoading(true);
    setError(null);
    try {
      const response = await featureApi.preview(featureId);
      selectedIdRef.current = featureId;
      setSelectedId(featureId);
      setSelectedPlan(response.plan);
      if (response.plan?.phase_columns?.length) {
        setExpandedPhases(new Set([String(response.plan.phase_columns[0].id)]));
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      if (!quiet) setPreviewLoading(false);
    }
  }, []);

  const load = useCallback(async (preserveSelection = true) => {
    try {
      setError(null);
      const response = await featureApi.getState();
      setState(response);

      const currentSelectedId = preserveSelection ? selectedIdRef.current : null;
      if (currentSelectedId && response.features.some(item => item.feature_id === currentSelectedId)) {
        await previewFeature(currentSelectedId, true);
      } else if (response.plan) {
        selectedIdRef.current = response.plan.feature_id;
        setSelectedId(response.plan.feature_id);
        setSelectedPlan(response.plan);
        if (response.plan.phase_columns?.length) {
          setExpandedPhases(new Set([String(response.plan.phase_columns[0].id)]));
        }
      } else {
        selectedIdRef.current = null;
        setSelectedId(null);
        setSelectedPlan(null);
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [previewFeature]);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load(true);
    }, [load]),
  );

  const visibleFeatures = useMemo(() => {
    const features = state?.features || [];
    if (filter === 'all') return features;
    if (filter === 'archived') return features.filter(item => item.archived);
    if (filter === 'completed') {
      return features.filter(item => normalizeStatus(item.status) === 'completed');
    }
    return features.filter(item => !item.archived && normalizeStatus(item.status) !== 'completed');
  }, [filter, state?.features]);

  const selectedItem = state?.features.find(item => item.feature_id === selectedId) || null;

  const runAction = async (action: () => Promise<unknown>, confirmation?: { title: string; message: string }) => {
    const execute = async () => {
      try {
        setPreviewLoading(true);
        await action();
        await load(true);
      } catch (cause) {
        Alert.alert('Feature action failed', String(cause));
      } finally {
        setPreviewLoading(false);
      }
    };

    if (!confirmation) {
      await execute();
      return;
    }
    Alert.alert(confirmation.title, confirmation.message, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Continue', onPress: execute },
    ]);
  };

  const toggleSet = (
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    key: string,
  ) => {
    setter(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  if (loading) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
        <View style={styles.loadingWrap}>
          <Skeleton height={52} style={styles.loadingBlock} />
          <Skeleton height={112} style={styles.loadingBlock} />
          <Skeleton height={220} />
        </View>
      </SafeAreaView>
    );
  }

  if (error && !state) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
        <ErrorState message={error} onRetry={() => load(false)} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(true); }} />}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.pageHeader}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xl" style={styles.pageTitle}>Features</Text>
            <Text variant="sm" dim>Preview, load, and inspect every feature plan without changing the active feature.</Text>
          </View>
          {previewLoading ? <ActivityIndicator color={colors.accent} /> : null}
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
          {FILTERS.map(item => {
            const active = filter === item.key;
            const count = countForFilter(state?.features || [], item.key);
            return (
              <TouchableOpacity
                key={item.key}
                onPress={() => setFilter(item.key)}
                style={[
                  styles.filterChip,
                  { backgroundColor: active ? colors.text : colors.bgLift, borderColor: colors.border },
                ]}
              >
                <Text variant="xs" style={{ color: active ? colors.bg : colors.text }}>
                  {item.label} · {count}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        {(state?.features.length || 0) === 0 ? (
          <EmptyState title="No feature plans" message="Feature plans created in this session will appear here." />
        ) : visibleFeatures.length === 0 ? (
          <Card style={styles.emptyFilterCard}>
            <Text variant="sm" dim>No feature plans in this category.</Text>
          </Card>
        ) : (
          <View style={styles.featureList}>
            {visibleFeatures.map(item => (
              <FeatureListRow
                key={item.feature_id}
                item={item}
                selected={selectedId === item.feature_id}
                onPress={() => previewFeature(item.feature_id)}
              />
            ))}
          </View>
        )}

        {error ? (
          <View style={[styles.errorBox, { backgroundColor: colors.bgLift }]}>
            <Ionicons name="alert-circle-outline" size={18} color={colors.error} />
            <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
          </View>
        ) : null}

        {selectedPlan ? (
          <View style={styles.detailSection}>
            <FeatureOverview
              plan={selectedPlan}
              item={selectedItem}
              onApprove={() => runAction(() => featureApi.approve(selectedPlan.feature_id))}
              onLoad={() => runAction(() => featureApi.load(selectedPlan.feature_id))}
              onUnload={() => runAction(
                () => featureApi.unload(selectedPlan.feature_id),
                { title: 'Unload feature?', message: 'The feature remains saved and can be loaded again later.' },
              )}
              onArchive={() => runAction(
                () => featureApi.archive(selectedPlan.feature_id),
                { title: 'Archive feature?', message: 'Archived features remain available for preview and can be restored.' },
              )}
              onUnarchive={() => runAction(() => featureApi.unarchive(selectedPlan.feature_id))}
            />

            <SectionHeader title="Progress" detail={`${selectedPlan.task_count || 0} tasks across ${selectedPlan.phase_columns?.length || 0} phases`} />
            {(selectedPlan.phase_columns || []).length === 0 ? (
              <Card><Text variant="sm" dim>No phases or tasks recorded.</Text></Card>
            ) : (
              (selectedPlan.phase_columns || []).map(phase => (
                <PhaseDetail
                  key={String(phase.id)}
                  phase={phase}
                  activeFeature={Boolean(selectedItem?.is_active)}
                  expanded={expandedPhases.has(String(phase.id))}
                  expandedTasks={expandedTasks}
                  onToggle={() => toggleSet(setExpandedPhases, String(phase.id))}
                  onToggleTask={taskId => toggleSet(setExpandedTasks, `${phase.id}:${taskId}`)}
                  onAdvance={taskId => runAction(() => featureApi.transitionTask(taskId, 'completed'))}
                />
              ))
            )}

            <ExecutionSection execution={selectedPlan.execution || {}} nextPhase={selectedPlan.next_phase} nextTask={selectedPlan.next_task} />

            <SectionHeader title="Reviews" detail={`${selectedPlan.review_count || selectedPlan.review_records?.length || 0} review records`} />
            <ReviewSection
              records={selectedPlan.review_records || []}
              expanded={expandedRecords}
              onToggle={id => toggleSet(setExpandedRecords, `review:${id}`)}
            />

            <SectionHeader title="Diff proposals" detail={`${selectedPlan.diff_proposal_count || selectedPlan.diff_proposals?.length || 0} proposals`} />
            <DiffSection
              proposals={selectedPlan.diff_proposals || []}
              expanded={expandedRecords}
              onToggle={id => toggleSet(setExpandedRecords, `diff:${id}`)}
            />

            <SectionHeader title="Event history" detail={`${selectedPlan.event_count || selectedPlan.event_log?.length || 0} events`} />
            <EventSection
              events={selectedPlan.event_log || []}
              expanded={expandedRecords}
              onToggle={id => toggleSet(setExpandedRecords, `event:${id}`)}
            />
          </View>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function FeatureListRow({ item, selected, onPress }: { item: FeatureListItem; selected: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity
      onPress={onPress}
      activeOpacity={0.72}
      style={[
        styles.featureRow,
        { backgroundColor: selected ? colors.bgHover : colors.bgLift, borderColor: selected ? colors.borderStrong : colors.border },
      ]}
    >
      <View style={[styles.featureStatusDot, { backgroundColor: statusColor(item.status, item.archived, colors) }]} />
      <View style={styles.featureCopy}>
        <Text variant="sm" style={styles.featureName} numberOfLines={1}>{item.feature_name}</Text>
        <Text variant="xs" dim>{item.archived ? 'Archived' : formatStatus(item.status)}</Text>
      </View>
      {item.is_active ? (
        <View style={[styles.activeBadge, { backgroundColor: colors.accentSoft }]}>
          <Text variant="xs" style={{ color: colors.accent }}>Loaded</Text>
        </View>
      ) : null}
      <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
    </TouchableOpacity>
  );
}

function FeatureOverview({
  plan,
  item,
  onApprove,
  onLoad,
  onUnload,
  onArchive,
  onUnarchive,
}: {
  plan: FeatureSummary;
  item: FeatureListItem | null;
  onApprove: () => void;
  onLoad: () => void;
  onUnload: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
}) {
  const { colors } = useTheme();
  return (
    <Card style={styles.overviewCard}>
      <View style={styles.overviewTitleRow}>
        <View style={styles.overviewCopy}>
          <Text variant="lg" style={styles.overviewTitle}>{plan.feature_name}</Text>
          <Text variant="xs" dim style={styles.featureId}>{plan.feature_id}</Text>
        </View>
        <StatusPill label={item?.archived ? 'archived' : plan.overall_status} />
      </View>

      {plan.feature_request ? <Text variant="sm" style={styles.requestText}>{plan.feature_request}</Text> : null}

      <View style={styles.metadataGrid}>
        <Metadata label="Approval" value={plan.approved ? 'Approved' : 'Pending'} />
        <Metadata label="Review" value={formatStatus(plan.review_status)} />
        <Metadata label="Tasks" value={`${plan.task_count || 0}`} />
        <Metadata label="Events" value={`${plan.event_count || 0}`} />
      </View>

      {plan.directory ? (
        <View style={[styles.pathBox, { backgroundColor: colors.bgHover }]}>
          <Ionicons name="folder-outline" size={16} color={colors.textDim} />
          <Text variant="xs" style={styles.pathText} numberOfLines={2}>{plan.directory}</Text>
        </View>
      ) : null}

      <View style={styles.actionRow}>
        {!plan.approved && !item?.archived ? <ActionButton label="Approve" icon="checkmark-circle-outline" onPress={onApprove} primary /> : null}
        {item?.is_active ? (
          <ActionButton label="Unload" icon="pause-circle-outline" onPress={onUnload} />
        ) : item?.archived ? (
          <ActionButton label="Restore" icon="archive-outline" onPress={onUnarchive} primary />
        ) : (
          <ActionButton label="Load" icon="play-circle-outline" onPress={onLoad} primary />
        )}
        {!item?.is_active && !item?.archived ? <ActionButton label="Archive" icon="archive-outline" onPress={onArchive} /> : null}
      </View>
    </Card>
  );
}

function PhaseDetail({
  phase,
  activeFeature,
  expanded,
  expandedTasks,
  onToggle,
  onToggleTask,
  onAdvance,
}: {
  phase: FeaturePhase;
  activeFeature: boolean;
  expanded: boolean;
  expandedTasks: Set<string>;
  onToggle: () => void;
  onToggleTask: (taskId: number) => void;
  onAdvance: (taskId: number) => void;
}) {
  const { colors } = useTheme();
  const completed = phase.tasks.filter(task => ['completed', 'archived'].includes(normalizeStatus(task.status))).length;
  return (
    <Card style={styles.phaseCard}>
      <TouchableOpacity onPress={onToggle} style={styles.phaseHeader} activeOpacity={0.72}>
        <View style={styles.phaseCopy}>
          <Text variant="base" style={styles.phaseTitle}>{phase.title}</Text>
          <Text variant="xs" dim>{completed}/{phase.tasks.length} tasks{phase.goal ? ` · ${phase.goal}` : ''}</Text>
        </View>
        <StatusPill label={phase.status || 'pending'} />
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={colors.textDim} />
      </TouchableOpacity>

      {expanded ? (
        <View style={[styles.taskList, { borderTopColor: colors.border }]}>
          {phase.tasks.map(task => {
            const taskKey = `${phase.id}:${task.id}`;
            return (
              <TaskDetail
                key={task.id}
                task={task}
                expanded={expandedTasks.has(taskKey)}
                activeFeature={activeFeature}
                onToggle={() => onToggleTask(task.id)}
                onAdvance={() => onAdvance(task.id)}
              />
            );
          })}
        </View>
      ) : null}
    </Card>
  );
}

function TaskDetail({ task, expanded, activeFeature, onToggle, onAdvance }: {
  task: FeatureTask;
  expanded: boolean;
  activeFeature: boolean;
  onToggle: () => void;
  onAdvance: () => void;
}) {
  const { colors } = useTheme();
  return (
    <View style={[styles.task, { borderBottomColor: colors.border }]}>
      <TouchableOpacity onPress={onToggle} style={styles.taskHeader} activeOpacity={0.72}>
        <View style={styles.taskIndex}><Text variant="xs" dim>{task.id}</Text></View>
        <View style={styles.taskCopy}>
          <Text variant="sm" style={styles.taskTitle}>{task.title}</Text>
          <Text variant="xs" dim>{task.verified_exit_criteria?.length || 0}/{task.exit_criteria?.length || 0} criteria verified</Text>
        </View>
        <StatusPill label={task.status} />
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={17} color={colors.textDim} />
      </TouchableOpacity>

      {expanded ? (
        <View style={styles.taskBody}>
          <StringList title="Objectives" values={task.objectives || []} />
          <StringList title="Action points" values={task.action_points || []} />
          {(task.exit_criteria || []).length > 0 ? (
            <View style={styles.detailBlock}>
              <Text variant="xs" dim style={styles.detailLabel}>EXIT CRITERIA</Text>
              {task.exit_criteria.map((criterion, index) => {
                const verified = task.verified_exit_criteria?.includes(criterion);
                return (
                  <View key={`${criterion}-${index}`} style={styles.criteriaRow}>
                    <Ionicons name={verified ? 'checkmark-circle' : 'ellipse-outline'} size={17} color={verified ? colors.success : colors.textDim} />
                    <Text variant="xs" style={{ flex: 1, color: verified ? colors.textSoft : colors.text }}>{criterion}</Text>
                  </View>
                );
              })}
            </View>
          ) : null}
          {task.notes ? <TextBlock title="Notes" value={task.notes} /> : null}
          {task.blocked_reason ? <TextBlock title="Blocked reason" value={task.blocked_reason} error /> : null}
          {activeFeature && normalizeStatus(task.status) === 'in_progress' ? (
            <TouchableOpacity onPress={onAdvance} style={[styles.completeButton, { backgroundColor: colors.text }]}>
              <Ionicons name="checkmark" size={17} color={colors.bg} />
              <Text variant="xs" style={{ color: colors.bg, fontWeight: '700' }}>Mark completed</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function ExecutionSection({ execution, nextPhase, nextTask }: {
  execution: Record<string, unknown>;
  nextPhase?: Record<string, unknown> | null;
  nextTask?: FeatureTask | null;
}) {
  const blocked = Array.isArray(execution.blocked_tasks) ? execution.blocked_tasks : [];
  return (
    <>
      <SectionHeader title="Execution" detail="Current next actions and blockers" />
      <Card>
        <Metadata label="Next phase" value={stringValue(nextPhase?.title) || 'None'} wide />
        <Metadata label="Next task" value={nextTask?.title || stringValue((execution.next_task as Record<string, unknown> | undefined)?.title) || 'None'} wide />
        <Metadata label="Blocked tasks" value={`${blocked.length}`} wide />
        {blocked.map((item, index) => {
          const record = item as Record<string, unknown>;
          return <Text key={index} variant="xs" dim style={styles.executionLine}>• {stringValue(record.title) || `Task ${index + 1}`}</Text>;
        })}
      </Card>
    </>
  );
}

function ReviewSection({ records, expanded, onToggle }: {
  records: FeatureReviewRecord[];
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (!records.length) return <EmptyDataCard text="No review records." />;
  return (
    <View style={styles.recordList}>
      {records.map(record => (
        <ExpandableRecord
          key={record.id}
          title={`Task ${record.task_id} review`}
          subtitle={`${record.issues?.length || 0} issues · ${formatDate(record.created_at)}`}
          expanded={expanded.has(`review:${record.id}`)}
          onToggle={() => onToggle(record.id)}
        >
          <Text variant="sm">{record.summary || 'No summary.'}</Text>
          <StringList title="Limitations" values={record.limitations || []} />
          {(record.issues || []).map((issue, index) => (
            <JsonBlock key={index} title={`Issue ${index + 1}`} value={issue} />
          ))}
        </ExpandableRecord>
      ))}
    </View>
  );
}

function DiffSection({ proposals, expanded, onToggle }: {
  proposals: FeatureDiffProposal[];
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (!proposals.length) return <EmptyDataCard text="No diff proposals." />;
  return (
    <View style={styles.recordList}>
      {proposals.map(proposal => (
        <ExpandableRecord
          key={proposal.id}
          title={`Task ${proposal.task_id} · ${proposal.status}`}
          subtitle={proposal.issue_id || proposal.id}
          expanded={expanded.has(`diff:${proposal.id}`)}
          onToggle={() => onToggle(proposal.id)}
        >
          {proposal.decision_reason ? <TextBlock title="Decision" value={proposal.decision_reason} /> : null}
          <JsonBlock title="Diff" value={proposal.diff || '(empty diff)'} raw />
        </ExpandableRecord>
      ))}
    </View>
  );
}

function EventSection({ events, expanded, onToggle }: {
  events: FeatureEvent[];
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  if (!events.length) return <EmptyDataCard text="No feature events." />;
  return (
    <View style={styles.recordList}>
      {[...events].reverse().map(event => (
        <ExpandableRecord
          key={event.id}
          title={formatStatus(event.kind)}
          subtitle={`${event.entity} ${event.entity_id} · ${event.actor} · ${formatDate(event.created_at)}`}
          expanded={expanded.has(`event:${event.id}`)}
          onToggle={() => onToggle(event.id)}
        >
          <JsonBlock title="Payload" value={event.payload || {}} />
        </ExpandableRecord>
      ))}
    </View>
  );
}

function ExpandableRecord({ title, subtitle, expanded, onToggle, children }: {
  title: string;
  subtitle: string;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const { colors } = useTheme();
  return (
    <Card style={styles.recordCard}>
      <TouchableOpacity onPress={onToggle} activeOpacity={0.72} style={styles.recordHeader}>
        <View style={styles.recordCopy}>
          <Text variant="sm" style={styles.recordTitle}>{title}</Text>
          <Text variant="xs" dim numberOfLines={2}>{subtitle}</Text>
        </View>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={colors.textDim} />
      </TouchableOpacity>
      {expanded ? <View style={[styles.recordBody, { borderTopColor: colors.border }]}>{children}</View> : null}
    </Card>
  );
}

function SectionHeader({ title, detail }: { title: string; detail: string }) {
  return (
    <View style={styles.sectionHeader}>
      <Text variant="base" style={styles.sectionTitle}>{title}</Text>
      <Text variant="xs" dim>{detail}</Text>
    </View>
  );
}

function StatusPill({ label }: { label: string }) {
  const { colors } = useTheme();
  const normalized = normalizeStatus(label);
  const foreground = normalized === 'completed'
    ? colors.success
    : normalized === 'blocked'
      ? colors.error
      : normalized === 'in_progress'
        ? colors.accent
        : colors.textDim;
  return (
    <View style={[styles.statusPill, { backgroundColor: colors.bgHover }]}>
      <View style={[styles.statusMiniDot, { backgroundColor: foreground }]} />
      <Text variant="xs" style={{ color: foreground }}>{formatStatus(label)}</Text>
    </View>
  );
}

function ActionButton({ label, icon, onPress, primary = false }: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  primary?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity
      onPress={onPress}
      activeOpacity={0.72}
      style={[styles.actionButton, { backgroundColor: primary ? colors.text : colors.bgHover }]}
    >
      <Ionicons name={icon} size={17} color={primary ? colors.bg : colors.text} />
      <Text variant="xs" style={{ color: primary ? colors.bg : colors.text, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  );
}

function Metadata({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return (
    <View style={[styles.metadataItem, wide && styles.metadataWide]}>
      <Text variant="xs" dim>{label}</Text>
      <Text variant="sm" style={styles.metadataValue} numberOfLines={2}>{value}</Text>
    </View>
  );
}

function StringList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <View style={styles.detailBlock}>
      <Text variant="xs" dim style={styles.detailLabel}>{title.toUpperCase()}</Text>
      {values.map((value, index) => <Text key={`${value}-${index}`} variant="xs" style={styles.bulletLine}>• {value}</Text>)}
    </View>
  );
}

function TextBlock({ title, value, error = false }: { title: string; value: string; error?: boolean }) {
  const { colors } = useTheme();
  return (
    <View style={styles.detailBlock}>
      <Text variant="xs" dim style={styles.detailLabel}>{title.toUpperCase()}</Text>
      <Text variant="xs" style={{ color: error ? colors.error : colors.text }}>{value}</Text>
    </View>
  );
}

function JsonBlock({ title, value, raw = false }: { title: string; value: unknown; raw?: boolean }) {
  const { colors } = useTheme();
  const text = raw ? String(value) : safeJson(value);
  return (
    <View style={styles.detailBlock}>
      <Text variant="xs" dim style={styles.detailLabel}>{title.toUpperCase()}</Text>
      <View style={[styles.jsonBox, { backgroundColor: colors.bgHover }]}>
        <Text variant="xs" style={styles.jsonText} selectable>{text}</Text>
      </View>
    </View>
  );
}

function EmptyDataCard({ text }: { text: string }) {
  return <Card style={styles.emptyDataCard}><Text variant="sm" dim>{text}</Text></Card>;
}

function normalizeStatus(value: string | null | undefined): string {
  return String(value || '').trim().toLowerCase();
}

function formatStatus(value: string | null | undefined): string {
  const normalized = normalizeStatus(value) || 'unknown';
  return normalized.replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
}

function countForFilter(features: FeatureListItem[], filter: FeatureFilter): number {
  if (filter === 'all') return features.length;
  if (filter === 'archived') return features.filter(item => item.archived).length;
  if (filter === 'completed') return features.filter(item => normalizeStatus(item.status) === 'completed').length;
  return features.filter(item => !item.archived && normalizeStatus(item.status) !== 'completed').length;
}

function statusColor(status: string, archived: boolean, colors: any): string {
  if (archived) return colors.textDim;
  const normalized = normalizeStatus(status);
  if (normalized === 'completed') return colors.success;
  if (normalized === 'blocked') return colors.error;
  if (normalized === 'in_progress') return colors.warning;
  return colors.accent;
}

function formatDate(timestamp?: number | null): string {
  if (!timestamp) return 'Unknown time';
  try {
    return new Date(timestamp * 1000).toLocaleString();
  } catch {
    return 'Unknown time';
  }
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: 56 },
  loadingWrap: { padding: spacing.base },
  loadingBlock: { marginBottom: spacing.sm },
  pageHeader: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 16 },
  pageHeaderCopy: { flex: 1, paddingRight: 12 },
  pageTitle: { fontWeight: '700', letterSpacing: -0.5 },
  filterRow: { gap: 8, paddingBottom: 14 },
  filterChip: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
  featureList: { gap: 7 },
  featureRow: { minHeight: 62, borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12 },
  featureStatusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 11 },
  featureCopy: { flex: 1 },
  featureName: { fontWeight: '600' },
  activeBadge: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 4, marginRight: 8 },
  emptyFilterCard: { alignItems: 'center', paddingVertical: 28 },
  errorBox: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, borderRadius: 14, padding: 12, marginTop: 12 },
  detailSection: { marginTop: 20 },
  overviewCard: { paddingVertical: 18 },
  overviewTitleRow: { flexDirection: 'row', alignItems: 'flex-start' },
  overviewCopy: { flex: 1, paddingRight: 12 },
  overviewTitle: { fontWeight: '700', letterSpacing: -0.3 },
  featureId: { fontFamily: 'monospace', marginTop: 2 },
  requestText: { marginTop: 14, lineHeight: 21 },
  metadataGrid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 16, rowGap: 12 },
  metadataItem: { width: '50%', paddingRight: 10 },
  metadataWide: { width: '100%', marginBottom: 10 },
  metadataValue: { fontWeight: '600', marginTop: 1 },
  pathBox: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, borderRadius: 12, padding: 10, marginTop: 14 },
  pathText: { flex: 1, fontFamily: 'monospace' },
  actionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 16 },
  actionButton: { minHeight: 40, borderRadius: 13, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12 },
  sectionHeader: { marginTop: 24, marginBottom: 9 },
  sectionTitle: { fontWeight: '700' },
  phaseCard: { marginBottom: 9, paddingVertical: 6 },
  phaseHeader: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 8 },
  phaseCopy: { flex: 1 },
  phaseTitle: { fontWeight: '600' },
  taskList: { borderTopWidth: StyleSheet.hairlineWidth },
  task: { borderBottomWidth: StyleSheet.hairlineWidth },
  taskHeader: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 7 },
  taskIndex: { width: 26, height: 26, borderRadius: 9, alignItems: 'center', justifyContent: 'center' },
  taskCopy: { flex: 1 },
  taskTitle: { fontWeight: '600' },
  taskBody: { paddingLeft: 34, paddingBottom: 14 },
  detailBlock: { marginTop: 12 },
  detailLabel: { fontWeight: '700', letterSpacing: 0.6, marginBottom: 5 },
  bulletLine: { marginBottom: 3, lineHeight: 18 },
  criteriaRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 7, marginBottom: 6 },
  completeButton: { alignSelf: 'flex-start', minHeight: 38, borderRadius: 12, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, marginTop: 12 },
  statusPill: { minHeight: 28, borderRadius: 999, flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8 },
  statusMiniDot: { width: 6, height: 6, borderRadius: 3 },
  executionLine: { marginTop: 5 },
  recordList: { gap: 8 },
  recordCard: { paddingVertical: 6 },
  recordHeader: { minHeight: 54, flexDirection: 'row', alignItems: 'center' },
  recordCopy: { flex: 1, paddingRight: 10 },
  recordTitle: { fontWeight: '600' },
  recordBody: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 12, paddingBottom: 8 },
  jsonBox: { borderRadius: 12, padding: 10 },
  jsonText: { fontFamily: 'monospace', lineHeight: 17 },
  emptyDataCard: { alignItems: 'center', paddingVertical: 22 },
});
