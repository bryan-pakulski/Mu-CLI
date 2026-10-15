import React, { useCallback, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
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
import { ModeWorkspaceHeader, useModeWorkspaceView } from '../components';
import { spacing, fontWeights, fonts, radii, tracking, touchTarget, typography } from '../theme/tokens';
import { Card, EmptyState, ErrorState, Pressable, Skeleton, Text } from '../ui';

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
  const [workspace, setWorkspace] = useState<FeatureState['workspace'] | null>(null);
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
  const requestEpochRef = useRef(0);
  const workspaceView = useModeWorkspaceView(workspace);

  const previewFeature = useCallback(async (featureId: string, quiet = false) => {
    const requestId = ++requestEpochRef.current;
    if (!quiet) setPreviewLoading(true);
    setError(null);
    try {
      const response = await featureApi.preview(featureId);
      if (requestId !== requestEpochRef.current) return;
      setWorkspace(response.workspace);
      selectedIdRef.current = featureId;
      setSelectedId(featureId);
      setSelectedPlan(response.plan);
      setExpandedTasks(new Set());
      setExpandedRecords(new Set());
      if (response.plan?.phase_columns?.length) {
        setExpandedPhases(new Set([String(response.plan.phase_columns[0].id)]));
      } else {
        setExpandedPhases(new Set());
      }
    } catch (cause) {
      if (requestId !== requestEpochRef.current) return;
      setError(String(cause));
    } finally {
      if (requestId === requestEpochRef.current) {
        if (!quiet) setPreviewLoading(false);
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  const load = useCallback(async (preserveSelection = true) => {
    const requestId = ++requestEpochRef.current;
    try {
      setError(null);
      const response = await featureApi.getState();
      if (requestId !== requestEpochRef.current) return;
      setState(response);
      setWorkspace(response.workspace);

      const currentSelectedId = preserveSelection ? selectedIdRef.current : null;
      if (currentSelectedId && response.features.some(item => item.feature_id === currentSelectedId)) {
        const preview = await featureApi.preview(currentSelectedId);
        if (requestId !== requestEpochRef.current) return;
        setWorkspace(preview.workspace);
        setSelectedId(currentSelectedId);
        setSelectedPlan(preview.plan);
        setExpandedPhases(
          preview.plan?.phase_columns?.length
            ? new Set([String(preview.plan.phase_columns[0].id)])
            : new Set(),
        );
      } else if (response.plan) {
        selectedIdRef.current = response.plan.feature_id;
        setSelectedId(response.plan.feature_id);
        setSelectedPlan(response.plan);
        if (response.plan.phase_columns?.length) {
          setExpandedPhases(new Set([String(response.plan.phase_columns[0].id)]));
        } else {
          setExpandedPhases(new Set());
        }
      } else {
        selectedIdRef.current = null;
        setSelectedId(null);
        setSelectedPlan(null);
        setExpandedPhases(new Set());
      }
      setExpandedTasks(new Set());
      setExpandedRecords(new Set());
    } catch (cause) {
      if (requestId !== requestEpochRef.current) return;
      setError(String(cause));
    } finally {
      if (requestId === requestEpochRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load(true);
      return () => {
        // Ignore late preview/state responses after navigating away. Without
        // this guard a slower archived-plan response can repaint the screen
        // after a newer feature has already been selected.
        requestEpochRef.current += 1;
      };
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
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
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
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
        <ErrorState message={error} onRetry={() => load(false)} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(true); }} />}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        {workspace ? <ModeWorkspaceHeader workspace={workspace} selectedView={workspaceView.selectedView} onSelectView={workspaceView.selectView} /> : null}
        <View style={styles.pageHeader}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xl" style={styles.pageTitle}>Features</Text>
            <Text variant="sm" dim>Preview, load, and inspect every feature plan without changing the active feature.</Text>
          </View>
          {previewLoading ? <ActivityIndicator color={colors.accent.ink} /> : null}
        </View>

        {workspaceView.shows('overview') ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
          {FILTERS.map(item => {
            const active = filter === item.key;
            const count = countForFilter(state?.features || [], item.key);
            return (
              <Pressable
                key={item.key}
                onPress={() => setFilter(item.key)}
                hitSlop={{ top: spacing.xs, bottom: spacing.xs }}
                style={[
                  styles.filterChip,
                  { backgroundColor: active ? colors.accent.wash : colors.surface, borderWidth: 1, borderColor: active ? colors.accent.ink : colors.hairline },
                ]}
              >
                <Text variant="xs" style={{ color: active ? colors.accent.ink : colors.ink2, fontWeight: active ? fontWeights.semibold : fontWeights.regular, fontVariant: ['tabular-nums'] }}>
                  {item.label} · {count}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView> : null}

        {workspaceView.shows('overview') && ((state?.features.length || 0) === 0 ? (
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
        ))}

        {error ? (
          <View style={[styles.errorBox, { backgroundColor: colors.danger.wash }]}>
            <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
            <Text variant="xs" style={{ color: colors.danger.ink, flex: 1 }}>{error}</Text>
          </View>
        ) : null}

        {selectedPlan ? (
          <View style={styles.detailSection}>
            {workspaceView.shows('overview') ? <FeatureOverview
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
            /> : null}

            {workspaceView.shows('board', 'verification') ? <>
            <SectionHeader title={workspaceView.selectedView === 'verification' ? 'Acceptance criteria' : 'Progress'} detail={`${selectedPlan.task_count || 0} tasks across ${selectedPlan.phase_columns?.length || 0} phases`} />
            {(selectedPlan.phase_columns || []).length === 0 ? (
              <Card><Text variant="sm" dim>No phases or tasks recorded.</Text></Card>
            ) : (
              (selectedPlan.phase_columns || []).map(phase => (
                <PhaseDetail
                  key={`${selectedPlan.feature_id}:${phase.id}`}
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
            </> : null}

            {workspaceView.shows('reviews') ? <>
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
            </> : null}
          </View>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function FeatureListRow({ item, selected, onPress }: { item: FeatureListItem; selected: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      activeOpacity={0.72}
      style={[
        styles.featureRow,
        { backgroundColor: selected ? colors.accent.wash : 'transparent' },
      ]}
    >
      <View style={[styles.featureStatusDot, { backgroundColor: statusColor(item.status, item.archived, colors) }]} />
      <View style={styles.featureCopy}>
        <Text variant="sm" style={styles.featureName} numberOfLines={1}>{item.feature_name}</Text>
        <Text variant="xs" dim>{item.archived ? 'Archived' : formatStatus(item.status)}</Text>
      </View>
      {item.is_active ? (
        <View style={[styles.activeBadge, { backgroundColor: colors.accent.wash }]}>
          <Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.medium }}>Loaded</Text>
        </View>
      ) : null}
      <Ionicons name="chevron-forward" size={18} color={colors.ink3} />
    </Pressable>
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
        <View style={styles.pathBox}>
          <Ionicons name="folder-outline" size={16} color={colors.ink3} />
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
          <ActionButton label="Load" icon="play-circle-outline" onPress={onLoad} primary={plan.approved} />
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
      <Pressable onPress={onToggle} style={styles.phaseHeader} activeOpacity={0.72}>
        <View style={styles.phaseCopy}>
          <Text variant="base" style={styles.phaseTitle}>{phase.title}</Text>
          <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{completed}/{phase.tasks.length} tasks{phase.goal ? ` · ${phase.goal}` : ''}</Text>
        </View>
        <StatusPill label={phase.status || 'pending'} />
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={colors.ink3} />
      </Pressable>

      {expanded ? (
        <View style={styles.taskList}>
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
    <View style={styles.task}>
      <Pressable onPress={onToggle} style={styles.taskHeader} activeOpacity={0.72}>
        <View style={styles.taskIndex}><Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{task.id}</Text></View>
        <View style={styles.taskCopy}>
          <Text variant="sm" style={styles.taskTitle}>{task.title}</Text>
          <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{task.verified_exit_criteria?.length || 0}/{task.exit_criteria?.length || 0} criteria verified</Text>
        </View>
        <StatusPill label={task.status} />
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={17} color={colors.ink3} />
      </Pressable>

      {expanded ? (
        <View style={styles.taskBody}>
          <StringList title="Objectives" values={task.objectives || []} />
          <StringList title="Action points" values={task.action_points || []} />
          {(task.exit_criteria || []).length > 0 ? (
            <View style={styles.detailBlock}>
              <Text variant="xs" dim style={styles.detailLabel}>Exit criteria</Text>
              {task.exit_criteria.map((criterion, index) => {
                const verified = task.verified_exit_criteria?.includes(criterion);
                return (
                  <View key={`${criterion}-${index}`} style={styles.criteriaRow}>
                    <Ionicons name={verified ? 'checkmark-circle-outline' : 'ellipse-outline'} size={17} color={verified ? colors.ok.ink : colors.ink3} />
                    <Text variant="xs" style={{ flex: 1, color: verified ? colors.ink2 : colors.ink }}>{criterion}</Text>
                  </View>
                );
              })}
            </View>
          ) : null}
          {task.notes ? <TextBlock title="Notes" value={task.notes} /> : null}
          {task.blocked_reason ? <TextBlock title="Blocked reason" value={task.blocked_reason} error /> : null}
          {activeFeature && normalizeStatus(task.status) === 'in_progress' ? (
            <Pressable onPress={onAdvance} style={[styles.completeButton, { backgroundColor: colors.accent.ink }]}>
              <Ionicons name="checkmark" size={16} color={colors.onInk} />
              <Text variant="xs" style={{ color: colors.onInk, fontWeight: fontWeights.medium }}>Mark completed</Text>
            </Pressable>
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
      <Pressable onPress={onToggle} activeOpacity={0.72} style={styles.recordHeader}>
        <View style={styles.recordCopy}>
          <Text variant="sm" style={styles.recordTitle}>{title}</Text>
          <Text variant="xs" dim numberOfLines={2} style={{ fontVariant: ['tabular-nums'] }}>{subtitle}</Text>
        </View>
        <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={18} color={colors.ink3} />
      </Pressable>
      {expanded ? <View style={styles.recordBody}>{children}</View> : null}
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
    ? colors.ok.ink
    : normalized === 'blocked'
      ? colors.danger.ink
      : normalized === 'in_progress'
        ? colors.info.ink
        : colors.ink2;
  const background = normalized === 'completed'
    ? colors.ok.wash
    : normalized === 'blocked'
      ? colors.danger.wash
      : normalized === 'in_progress'
        ? colors.info.wash
        : colors.sunken;
  return (
    <View style={[styles.statusPill, { backgroundColor: background }]}>
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
    <Pressable
      onPress={onPress}
      activeOpacity={0.72}
      style={[styles.actionButton, primary
        ? { backgroundColor: colors.accent.ink, borderColor: colors.accent.ink }
        : { backgroundColor: 'transparent', borderColor: colors.hairline }]}
    >
      <Ionicons name={icon} size={16} color={primary ? colors.onInk : colors.ink} />
      <Text variant="xs" style={{ color: primary ? colors.onInk : colors.ink, fontWeight: fontWeights.medium }}>{label}</Text>
    </Pressable>
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
      <Text variant="xs" style={{ color: error ? colors.danger.ink : colors.ink }}>{value}</Text>
    </View>
  );
}

function JsonBlock({ title, value, raw = false }: { title: string; value: unknown; raw?: boolean }) {
  const { colors } = useTheme();
  const text = raw ? String(value) : safeJson(value);
  return (
    <View style={styles.detailBlock}>
      <Text variant="xs" dim style={styles.detailLabel}>{title.toUpperCase()}</Text>
      <View style={[styles.jsonBox, { backgroundColor: colors.canvas }]}>
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
  if (archived) return colors.ink3;
  const normalized = normalizeStatus(status);
  if (normalized === 'completed') return colors.ok.ink;
  if (normalized === 'blocked') return colors.danger.ink;
  if (normalized === 'in_progress') return colors.info.ink;
  return colors.ink3;
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
  content: { padding: spacing.base, paddingBottom: spacing['3xl'] },
  loadingWrap: { padding: spacing.base },
  loadingBlock: { marginBottom: spacing.sm },
  pageHeader: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: spacing.base },
  pageHeaderCopy: { flex: 1, paddingRight: spacing.md },
  pageTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  filterRow: { gap: spacing.sm, paddingBottom: spacing.base },
  filterChip: { minHeight: touchTarget - spacing.sm, justifyContent: 'center', borderRadius: radii.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  featureList: { gap: spacing.xs },
  featureRow: { minHeight: 56, borderRadius: radii.sm, flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.md },
  featureStatusDot: { width: 8, height: 8, borderRadius: radii.pill, marginRight: spacing.md },
  featureCopy: { flex: 1 },
  featureName: { fontWeight: fontWeights.semibold },
  activeBadge: { borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 2, marginRight: spacing.sm },
  emptyFilterCard: { alignItems: 'center', paddingVertical: spacing.xl },
  errorBox: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, borderRadius: radii.sm, padding: spacing.md, marginTop: spacing.md },
  detailSection: { marginTop: spacing.base },
  overviewCard: { paddingVertical: spacing.base },
  overviewTitleRow: { flexDirection: 'row', alignItems: 'flex-start' },
  overviewCopy: { flex: 1, paddingRight: spacing.md },
  overviewTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  featureId: { fontFamily: fonts.mono, marginTop: 2 },
  requestText: { marginTop: spacing.md, lineHeight: typography.sm.lineHeight },
  metadataGrid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: spacing.base, rowGap: spacing.md },
  metadataItem: { width: '50%', paddingRight: spacing.md },
  metadataWide: { width: '100%', marginBottom: spacing.sm },
  metadataValue: { fontWeight: fontWeights.medium, marginTop: 1, fontVariant: ['tabular-nums'] },
  pathBox: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, marginTop: spacing.md },
  pathText: { flex: 1, fontFamily: fonts.mono },
  actionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.base },
  actionButton: { minHeight: touchTarget, borderWidth: 1, borderRadius: radii.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md },
  sectionHeader: { marginTop: spacing.lg, marginBottom: spacing.sm },
  sectionTitle: { fontWeight: fontWeights.semibold },
  phaseCard: { marginBottom: spacing.sm, paddingVertical: spacing.sm },
  phaseHeader: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  phaseCopy: { flex: 1 },
  phaseTitle: { fontWeight: fontWeights.semibold },
  taskList: { paddingTop: spacing.xs },
  task: { paddingBottom: spacing.xs },
  taskHeader: { minHeight: 58, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm },
  taskIndex: { width: 26, height: 26, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  taskCopy: { flex: 1 },
  taskTitle: { fontWeight: fontWeights.semibold },
  taskBody: { paddingLeft: spacing.xl, paddingBottom: spacing.base },
  detailBlock: { marginTop: spacing.md },
  detailLabel: { fontWeight: fontWeights.semibold, marginBottom: spacing.xs },
  bulletLine: { marginBottom: spacing.xs, lineHeight: typography.xs.lineHeight },
  criteriaRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, marginBottom: spacing.xs },
  completeButton: { alignSelf: 'flex-start', minHeight: touchTarget, borderRadius: radii.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md, marginTop: spacing.md },
  statusPill: { minHeight: 24, borderRadius: radii.pill, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm },
  statusMiniDot: { width: 6, height: 6, borderRadius: radii.pill },
  executionLine: { marginTop: spacing.xs },
  recordList: { gap: spacing.sm },
  recordCard: { paddingVertical: spacing.sm },
  recordHeader: { minHeight: 54, flexDirection: 'row', alignItems: 'center' },
  recordCopy: { flex: 1, paddingRight: spacing.md },
  recordTitle: { fontWeight: fontWeights.semibold },
  recordBody: { paddingTop: spacing.xs, paddingBottom: spacing.sm },
  jsonBox: { borderRadius: radii.sm, padding: spacing.md },
  jsonText: { fontFamily: fonts.mono, lineHeight: typography.xs.lineHeight },
  emptyDataCard: { alignItems: 'center', paddingVertical: spacing.lg },
});
