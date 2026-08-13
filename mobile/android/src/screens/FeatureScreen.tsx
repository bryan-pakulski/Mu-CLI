import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button, ModeWorkspaceHeader, useModeWorkspaceView } from '../components';
import { featureApi, FeatureState, FeaturePhase, FeatureTask } from '../api/feature';
import { spacing } from '../theme/tokens';

export function FeatureScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<FeatureState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState<string | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await featureApi.getState();
      setState(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          <Skeleton height={120} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={80} />
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (!state || (!state.active && state.features.length === 0)) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No features" message="No feature mode plans available" />
      </SafeAreaView>
    );
  }

  const plan = state.plan;
  const phases = workspaceView.selectedView === 'reviews'
    ? []
    : (plan?.phase_columns || []);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={phases}
        keyExtractor={item => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        ListHeaderComponent={
          <>
          <ModeWorkspaceHeader workspace={state.workspace} selectedView={workspaceView.selectedView} onSelectView={workspaceView.selectView} />
          {plan ? (
            <Card style={{ marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>{plan.feature_name}</Text>
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 2, marginBottom: spacing.sm }} numberOfLines={3}>
                {plan.feature_request}
              </Text>
              <View style={{ flexDirection: 'row', gap: 6, flexWrap: 'wrap' }}>
                <Badge label={plan.overall_status} variant={plan.overall_status === 'in_progress' ? 'accent' : 'neutral'} />
                <Badge label={plan.review_status} variant={plan.review_status === 'completed' ? 'success' : 'neutral'} />
                {plan.approved && <Badge label="Approved" variant="success" />}
              </View>
              <View style={{ flexDirection: 'row', gap: 8, marginTop: spacing.sm }}>
                {!plan.approved && (
                  <Button title="Approve" onPress={() => {
                    Alert.alert('Approve feature?', 'Approve this feature plan?', [
                      { text: 'Cancel', style: 'cancel' },
                      { text: 'Approve', onPress: async () => { await featureApi.approve(plan.feature_id); load(); } },
                    ]);
                  }} />
                )}
                <Button title="Unload" variant="ghost" onPress={async () => {
                  await featureApi.unload(plan.feature_id);
                  load();
                }} />
              </View>
            </Card>
          ) : (
            <Card style={{ marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>No active feature</Text>
              {state.features.filter(f => !f.archived).length > 0 && (
                <>
                  <Text variant="xs" style={{ color: colors.textDim, marginTop: spacing.sm, marginBottom: 4 }}>
                    Available features:
                  </Text>
                  {state.features.filter(f => !f.archived).map(f => (
                    <TouchableOpacity key={f.feature_id} onPress={async () => { await featureApi.load(f.feature_id); load(); }} style={{ minHeight: 44, paddingVertical: 8 }}>
                      <Text variant="sm" style={{ color: colors.accent }}>{f.feature_name} · {f.status}</Text>
                    </TouchableOpacity>
                  ))}
                </>
              )}
            </Card>
          )}
          </>
        }
        ListFooterComponent={workspaceView.shows('reviews') && plan ? (
          <View style={{ marginTop: spacing.sm }}>
            <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '700', letterSpacing: .8, textTransform: 'uppercase', marginBottom: spacing.sm }}>Review evidence</Text>
            {(plan.review_records || []).map(review => (
              <Card key={review.id} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}><Badge label={`task ${review.task_id}`} variant="neutral" /><Text variant="xs" style={{ color: colors.textDim }}>{review.created_at ? new Date(review.created_at * 1000).toLocaleString() : ''}</Text></View>
                <Text variant="sm" style={{ color: colors.text, marginTop: spacing.sm }}>{review.summary}</Text>
                {!!review.limitations.length && <Text variant="xs" style={{ color: colors.warning, marginTop: 6 }}>Limitations: {review.limitations.join(' · ')}</Text>}
              </Card>
            ))}
            {!(plan.review_records || []).length && <Text variant="xs" style={{ color: colors.textDim, padding: spacing.md }}>No review evidence recorded yet.</Text>}
          </View>
        ) : null}
        renderItem={({ item: phase }) => (
          <PhaseCard
            phase={phase}
            expanded={expandedPhase === String(phase.id)}
            onToggle={() => setExpandedPhase(prev => prev === String(phase.id) ? null : String(phase.id))}
            onAdvance={async (taskId) => {
              try {
                await featureApi.transitionTask(taskId, 'completed');
                load();
              } catch (e) { Alert.alert('Failed', String(e)); }
            }}
            showVerification={workspaceView.selectedView === 'verification'}
            colors={colors}
          />
        )}
      />
    </SafeAreaView>
  );
}

function PhaseCard({ phase, expanded, onToggle, onAdvance, showVerification, colors }: {
  phase: FeaturePhase; expanded: boolean; onToggle: () => void; onAdvance: (taskId: number) => void; showVerification: boolean; colors: any;
}) {
  const completed = phase.tasks.filter(t => t.status === 'completed').length;
  return (
    <Card style={{ marginBottom: spacing.sm }}>
      <TouchableOpacity onPress={onToggle} activeOpacity={0.7} style={{ minHeight: 44 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1 }}>
            <Text variant="base" style={{ fontWeight: '500' }}>{phase.title}</Text>
            <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
              {completed}/{phase.tasks.length} tasks
            </Text>
          </View>
          {phase.status && <Badge label={phase.status} variant={phase.status === 'completed' ? 'success' : phase.status === 'in_progress' ? 'accent' : 'neutral'} />}
          <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={20} color={colors.textDim} />
        </View>
      </TouchableOpacity>
      {expanded && (
        <View style={{ marginTop: spacing.sm, paddingTop: spacing.sm, borderTopWidth: 0.5, borderTopColor: colors.border }}>
          {phase.tasks.map(task => <TaskRow key={task.id} task={task} onAdvance={onAdvance} showVerification={showVerification} colors={colors} />)}
        </View>
      )}
    </Card>
  );
}

function TaskRow({ task, onAdvance, showVerification, colors }: {
  task: FeatureTask; onAdvance: (id: number) => void; showVerification: boolean; colors: any;
}) {
  return (
    <View style={{ paddingVertical: 8, minHeight: 44, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text variant="sm" style={{ flex: 1, fontWeight: '500' }}>{task.title}</Text>
        <Badge label={task.status} variant={
          task.status === 'completed' ? 'success' :
          task.status === 'in_progress' ? 'accent' :
          task.status === 'blocked' ? 'error' : 'neutral'
        } />
      </View>
      {task.verified_exit_criteria.length > 0 && (
        <Text variant="xs" style={{ color: colors.textDim, marginTop: 2, fontVariant: ['tabular-nums'] }}>
          {task.verified_exit_criteria.length}/{task.exit_criteria.length} criteria verified
        </Text>
      )}
      {showVerification && task.exit_criteria.length > 0 && (
        <View style={{ marginTop: spacing.sm, gap: 5 }}>
          {task.exit_criteria.map((criterion, index) => {
            const verified = task.verified_exit_criteria.includes(criterion);
            return <View key={`${task.id}-${index}`} style={{ flexDirection: 'row', gap: 7 }}><Text variant="xs" style={{ color: verified ? colors.success : colors.textDim }}>{verified ? '✓' : '○'}</Text><Text variant="xs" style={{ flex: 1, color: verified ? colors.textSoft : colors.textDim }}>{criterion}</Text></View>;
          })}
        </View>
      )}
      {task.status === 'in_progress' && (
        <TouchableOpacity onPress={() => onAdvance(task.id)} style={{ marginTop: 4 }}>
          <Text variant="xs" style={{ color: colors.accent }}>Advance →</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}
