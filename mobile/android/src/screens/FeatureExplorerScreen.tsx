// Feature plans (web feature_panel.html): preview, load and inspect every feature plan without
// changing the active one — the list, the plan's overview and actions, phases and tasks with
// their exit criteria, execution, reviews, diff proposals and the event history.

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
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
import { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { Fact, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Banner, Button, Chip, EmptyState, InkDrop, Pre, Row, Rows, Text, useServerActions, type ServerAction } from '../ui';

type FeatureFilter = 'features' | 'completed' | 'archived' | 'all';

const FILTERS: Array<{ key: FeatureFilter; label: string }> = [
  { key: 'features', label: 'Features' },
  { key: 'completed', label: 'Completed' },
  { key: 'archived', label: 'Archived' },
  { key: 'all', label: 'All' },
];

export function FeatureExplorerScreen() {
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
  const actions = useServerActions();

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
    if (confirmation) {
      const confirm: ServerAction = { id: 'confirm', label: confirmation.title.replace(/\?$/, ''), confirm: confirmation.message };
      if (!(await actions.ask(confirm))) return;
    }
    try {
      setPreviewLoading(true);
      setError(null);
      await action();
      await load(true);
    } catch (cause) {
      setError(`Feature action failed: ${String(cause)}`);
    } finally {
      setPreviewLoading(false);
    }
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

  const features = state?.features || [];
  return (
    <PanelPage testID="feature-screen" title={workspace?.title || 'Feature plans'}
      state={[...workspaceState(workspace), previewLoading ? { text: 'loading…', tone: 'quiet' } : null]}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      right={previewLoading ? <InkDrop /> : undefined}
      loading={loading} error={error && !state ? error : null} onRetry={() => void load(false)}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(true); }}
      overlay={actions.element}>
      {workspace ? <ModeWorkspaceIntro workspace={workspace} /> : null}
      {error && state ? <Banner tone="danger" title="Something went wrong" testID="feature-error">{error}</Banner> : null}

      {workspaceView.shows('overview') ? (
        <PanelSection title="Features" count={features.length}>
          <Text variant="caption" tone="ink3">Preview, load and inspect every feature plan without changing the active feature.</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
            {FILTERS.map(item => (
              <Chip key={item.key} variant="filter" selected={filter === item.key} onPress={() => setFilter(item.key)}
                label={`${item.label} · ${countForFilter(features, item.key)}`} testID={`feature-filter-${item.key}`} />
            ))}
          </ScrollView>
          {features.length === 0 ? (
            <EmptyState title="No feature plans" message="Feature plans created in this session will appear here." />
          ) : (
            <Rows empty="No feature plans in this category.">
              {visibleFeatures.map(item => (
                <Row key={item.feature_id} testID={`feature-${item.feature_id}`} status={featureSign(item.status, item.archived)} title={item.feature_name}
                  sub={item.archived ? 'Archived' : formatStatus(item.status)} selected={selectedId === item.feature_id}
                  actions={item.is_active ? <Chip variant="meta" label="Loaded" /> : undefined}
                  onPress={() => void previewFeature(item.feature_id)} />
              ))}
            </Rows>
          )}
        </PanelSection>
      ) : null}

      {selectedPlan ? (
        <>
          {workspaceView.shows('overview') ? (
            <FeatureOverview plan={selectedPlan} item={selectedItem}
              onApprove={() => void runAction(() => featureApi.approve(selectedPlan.feature_id))}
              onLoad={() => void runAction(() => featureApi.load(selectedPlan.feature_id))}
              onUnload={() => void runAction(() => featureApi.unload(selectedPlan.feature_id),
                { title: 'Unload feature?', message: 'The feature remains saved and can be loaded again later.' })}
              onArchive={() => void runAction(() => featureApi.archive(selectedPlan.feature_id),
                { title: 'Archive feature?', message: 'Archived features remain available for preview and can be restored.' })}
              onUnarchive={() => void runAction(() => featureApi.unarchive(selectedPlan.feature_id))} />
          ) : null}

          {workspaceView.shows('board', 'verification') ? (
            <>
              <PanelSection title={workspaceView.selectedView === 'verification' ? 'Acceptance criteria' : 'Progress'}
                count={selectedPlan.phase_columns?.length || 0} empty="No phases or tasks recorded.">
                {(selectedPlan.phase_columns || []).length ? (
                  <Text variant="caption" tone="ink3">{`${selectedPlan.task_count || 0} tasks across ${selectedPlan.phase_columns?.length || 0} phases`}</Text>
                ) : null}
                {(selectedPlan.phase_columns || []).map(phase => (
                  <PhaseRow key={`${selectedPlan.feature_id}:${phase.id}`} phase={phase} activeFeature={Boolean(selectedItem?.is_active)}
                    expanded={expandedPhases.has(String(phase.id))} expandedTasks={expandedTasks}
                    onToggle={() => toggleSet(setExpandedPhases, String(phase.id))}
                    onToggleTask={taskId => toggleSet(setExpandedTasks, `${phase.id}:${taskId}`)}
                    onAdvance={taskId => void runAction(() => featureApi.transitionTask(taskId, 'completed'))} />
                ))}
              </PanelSection>
              <ExecutionSection execution={selectedPlan.execution || {}} nextPhase={selectedPlan.next_phase} nextTask={selectedPlan.next_task} />
            </>
          ) : null}

          {workspaceView.shows('reviews') ? (
            <>
              <PanelSection title="Reviews" count={selectedPlan.review_count || selectedPlan.review_records?.length || 0} empty="No review records.">
                {(selectedPlan.review_records || []).map(record => (
                  <RecordRow key={record.id} title={`Task ${record.task_id} review`} sub={`${record.issues?.length || 0} issues · ${formatDate(record.created_at)}`}
                    expanded={expandedRecords.has(`review:${record.id}`)} onToggle={() => toggleSet(setExpandedRecords, `review:${record.id}`)}>
                    <Text variant="body">{record.summary || 'No summary.'}</Text>
                    <StringList title="Limitations" values={record.limitations || []} />
                    {(record.issues || []).map((issue, index) => <JsonBlock key={index} title={`Issue ${index + 1}`} value={issue} />)}
                  </RecordRow>
                ))}
              </PanelSection>
              <PanelSection title="Diff proposals" count={selectedPlan.diff_proposal_count || selectedPlan.diff_proposals?.length || 0} empty="No diff proposals.">
                {(selectedPlan.diff_proposals || []).map((proposal: FeatureDiffProposal) => (
                  <RecordRow key={proposal.id} title={`Task ${proposal.task_id} · ${proposal.status}`} sub={proposal.issue_id || proposal.id}
                    expanded={expandedRecords.has(`diff:${proposal.id}`)} onToggle={() => toggleSet(setExpandedRecords, `diff:${proposal.id}`)}>
                    {proposal.decision_reason ? <TextBlock title="Decision" value={proposal.decision_reason} /> : null}
                    <JsonBlock title="Diff" value={proposal.diff || '(empty diff)'} raw />
                  </RecordRow>
                ))}
              </PanelSection>
              <PanelSection title="Event history" count={selectedPlan.event_count || selectedPlan.event_log?.length || 0} empty="No feature events.">
                {[...(selectedPlan.event_log || [])].reverse().map((event: FeatureEvent) => (
                  <RecordRow key={event.id} title={formatStatus(event.kind)} sub={`${event.entity} ${event.entity_id} · ${event.actor} · ${formatDate(event.created_at)}`}
                    expanded={expandedRecords.has(`event:${event.id}`)} onToggle={() => toggleSet(setExpandedRecords, `event:${event.id}`)}>
                    <JsonBlock title="Payload" value={event.payload || {}} />
                  </RecordRow>
                ))}
              </PanelSection>
            </>
          ) : null}
        </>
      ) : null}
    </PanelPage>
  );
}

/** Feature / phase / task status → the shared status vocabulary. */
export function featureSign(status: string | null | undefined, archived = false): string {
  if (archived) return 'skipped';
  const normalized = normalizeStatus(status);
  if (normalized === 'completed') return 'done';
  if (normalized === 'blocked') return 'failed';
  if (normalized === 'in_progress') return 'active';
  return 'pending';
}

function FeatureOverview({ plan, item, onApprove, onLoad, onUnload, onArchive, onUnarchive }: {
  plan: FeatureSummary;
  item: FeatureListItem | null;
  onApprove: () => void;
  onLoad: () => void;
  onUnload: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
}) {
  return (
    <PanelSection title={plan.feature_name} testID="feature-overview"
      right={<Chip variant="meta" label={item?.archived ? 'archived' : formatStatus(plan.overall_status)} />}>
      <Text variant="monoSm" tone="ink3">{plan.feature_id}</Text>
      {plan.feature_request ? <Text variant="body" style={styles.request}>{plan.feature_request}</Text> : null}
      <Fact label="Approval" value={plan.approved ? 'Approved' : 'Pending'} />
      <Fact label="Review" value={formatStatus(plan.review_status)} />
      <Fact label="Tasks" value={`${plan.task_count || 0}`} />
      <Fact label="Events" value={`${plan.event_count || 0}`} />
      {plan.directory ? <Fact label="Folder" value={plan.directory} mono /> : null}
      <View style={styles.actions}>
        {!plan.approved && !item?.archived ? <Button label="Approve" icon="circle-check" variant="primary" onPress={onApprove} testID="feature-approve" /> : null}
        {item?.is_active ? (
          <Button label="Unload" icon="pause" variant="secondary" onPress={onUnload} testID="feature-unload" />
        ) : item?.archived ? (
          <Button label="Restore" icon="archive" variant="primary" onPress={onUnarchive} testID="feature-restore" />
        ) : (
          <Button label="Load" icon="play" variant={plan.approved ? 'primary' : 'secondary'} onPress={onLoad} testID="feature-load" />
        )}
        {!item?.is_active && !item?.archived ? <Button label="Archive" icon="archive" variant="secondary" onPress={onArchive} testID="feature-archive" /> : null}
      </View>
    </PanelSection>
  );
}

function PhaseRow({ phase, activeFeature, expanded, expandedTasks, onToggle, onToggleTask, onAdvance }: {
  phase: FeaturePhase;
  activeFeature: boolean;
  expanded: boolean;
  expandedTasks: Set<string>;
  onToggle: () => void;
  onToggleTask: (taskId: number) => void;
  onAdvance: (taskId: number) => void;
}) {
  const completed = phase.tasks.filter(task => ['completed', 'archived'].includes(normalizeStatus(task.status))).length;
  return (
    <Row status={featureSign(phase.status)} title={phase.title} when={`${completed}/${phase.tasks.length}`} onPress={onToggle}
      sub={[formatStatus(phase.status || 'pending'), phase.goal].filter(Boolean).join(' · ')} testID={`phase-${phase.id}`}
      accessibilityLabel={`${phase.title}, ${completed} of ${phase.tasks.length} tasks${expanded ? ', expanded' : ''}`}
      actions={expanded ? (
        <View style={styles.nested}>
          <Rows>
            {phase.tasks.map(task => (
              <TaskRow key={task.id} task={task} expanded={expandedTasks.has(`${phase.id}:${task.id}`)} activeFeature={activeFeature}
                onToggle={() => onToggleTask(task.id)} onAdvance={() => onAdvance(task.id)} />
            ))}
          </Rows>
        </View>
      ) : undefined} />
  );
}

function TaskRow({ task, expanded, activeFeature, onToggle, onAdvance }: {
  task: FeatureTask;
  expanded: boolean;
  activeFeature: boolean;
  onToggle: () => void;
  onAdvance: () => void;
}) {
  return (
    <Row status={featureSign(task.status)} title={`${task.id}. ${task.title}`} onPress={onToggle} testID={`task-${task.id}`}
      sub={`${formatStatus(task.status)} · ${task.verified_exit_criteria?.length || 0}/${task.exit_criteria?.length || 0} criteria verified`}
      actions={expanded ? (
        <View style={styles.nested}>
          <StringList title="Objectives" values={task.objectives || []} />
          <StringList title="Action points" values={task.action_points || []} />
          {(task.exit_criteria || []).length ? (
            <View style={styles.block}>
              <Text variant="label" tone="ink2">Exit criteria</Text>
              {task.exit_criteria.map((criterion, index) => {
                const verified = task.verified_exit_criteria?.includes(criterion);
                return (
                  <Text key={`${criterion}-${index}`} variant="caption" tone={verified ? 'ink2' : 'ink'}
                    accessibilityLabel={`${criterion}, ${verified ? 'verified' : 'not verified'}`}>
                    <Text variant="caption" tone={verified ? 'ok' : 'ink3'}>{verified ? '✓ ' : '○ '}</Text>{criterion}
                  </Text>
                );
              })}
            </View>
          ) : null}
          {task.notes ? <TextBlock title="Notes" value={task.notes} /> : null}
          {task.blocked_reason ? <TextBlock title="Blocked reason" value={task.blocked_reason} error /> : null}
          {activeFeature && normalizeStatus(task.status) === 'in_progress' ? (
            <View style={styles.actions}><Button label="Mark completed" icon="check" variant="primary" onPress={onAdvance} testID={`task-${task.id}-complete`} /></View>
          ) : null}
        </View>
      ) : undefined} />
  );
}

function ExecutionSection({ execution, nextPhase, nextTask }: {
  execution: Record<string, unknown>;
  nextPhase?: Record<string, unknown> | null;
  nextTask?: FeatureTask | null;
}) {
  const blocked = Array.isArray(execution.blocked_tasks) ? execution.blocked_tasks : [];
  return (
    <PanelSection title="Execution">
      <Text variant="caption" tone="ink3">Current next actions and blockers</Text>
      <Fact label="Next phase" value={stringValue(nextPhase?.title) || 'None'} />
      <Fact label="Next task" value={nextTask?.title || stringValue((execution.next_task as Record<string, unknown> | undefined)?.title) || 'None'} />
      <Fact label="Blocked tasks" value={`${blocked.length}`} />
      {blocked.map((item, index) => {
        const record = item as Record<string, unknown>;
        return <Text key={index} variant="caption" tone="ink3">• {stringValue(record.title) || `Task ${index + 1}`}</Text>;
      })}
    </PanelSection>
  );
}

function RecordRow({ title, sub, expanded, onToggle, children }: { title: string; sub: string; expanded: boolean; onToggle: () => void; children: React.ReactNode }) {
  return <Row title={title} sub={sub} onPress={onToggle} actions={expanded ? <View style={styles.nested}>{children}</View> : undefined} />;
}

function StringList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <View style={styles.block}>
      <Text variant="label" tone="ink2">{title}</Text>
      {values.map((value, index) => <Text key={`${value}-${index}`} variant="caption">• {value}</Text>)}
    </View>
  );
}

function TextBlock({ title, value, error = false }: { title: string; value: string; error?: boolean }) {
  return (
    <View style={styles.block}>
      <Text variant="label" tone="ink2">{title}</Text>
      <Text variant="caption" tone={error ? 'danger' : 'ink'}>{value}</Text>
    </View>
  );
}

function JsonBlock({ title, value, raw = false }: { title: string; value: unknown; raw?: boolean }) {
  return (
    <View style={styles.block}>
      <Text variant="label" tone="ink2">{title}</Text>
      <Pre>{raw ? String(value) : safeJson(value)}</Pre>
    </View>
  );
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
  filters: { gap: spacing.sm, paddingVertical: spacing.sm },
  request: { marginVertical: spacing.sm },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.sm },
  nested: { flex: 1, gap: spacing.xs },
  block: { gap: spacing.xs, marginTop: spacing.sm },
});
