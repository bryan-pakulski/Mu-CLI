// Loop mode (web loop_panel.html): the mission's execution queue, workstreams and checkpoints.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { EntryRow, PanelPage, PanelSection } from '../components/chat/PanelPage';
import { loopApi, type LoopEntry, type LoopState } from '../api/loop';
import { spacing } from '../theme/tokens';
import { Button, ProgressBar, Text } from '../ui';

/** Queue item status → the shared status vocabulary. */
export function loopItemStatus(status: string | undefined): string {
  if (status === 'completed') return 'done';
  if (status === 'blocked') return 'failed';
  if (status === 'in_progress') return 'active';
  return 'pending';
}

export function LoopScreen() {
  const [state, setState] = useState<LoopState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try { setError(null); setState(await loopApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const workspace = state?.workspace;
  const completed = state ? state.backlog.filter(item => item.status === 'completed').length : 0;
  const total = state?.backlog.length || 0;

  return (
    <PanelPage testID="loop-screen" title={workspace?.title || 'Loop'} state={workspaceState(workspace)}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      right={state?.loop_goal ? (
        <Button label={state.loop_active ? 'Pause' : 'Resume'} variant={state.loop_active ? 'secondary' : 'primary'}
          onPress={async () => { await loopApi.setActive(!state.loop_active, state.loop_goal); void load(); }} testID="loop-toggle" />
      ) : undefined}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && (!state || !state.active) ? { title: 'No loop active', message: 'Set a goal to open autonomous mission control.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}>
      {state && workspace ? (
        <>
          <ModeWorkspaceIntro workspace={workspace} />
          {workspaceView.shows('backlog') ? (
            <PanelSection title="Backlog" count={total} empty="No queue items yet.">
              {total ? (
                <View style={styles.progress}>
                  <View style={styles.progressHead}>
                    <Text variant="caption" tone="ink3">Reported queue progress</Text>
                    <Text variant="caption" tone="ink2" numeric>{completed}/{total}</Text>
                  </View>
                  <ProgressBar value={completed / total} tone="accent" accessibilityLabel={`${completed} of ${total} queue items complete`} />
                  <Text variant="caption" tone="ink3">Completion state is visible; correctness still requires verification evidence.</Text>
                </View>
              ) : null}
              {state.backlog.map((item: LoopEntry) => (
                <EntryRow key={item.id} status={loopItemStatus(item.status)} content={item.content} source={item.source}
                  testID={`loop-item-${item.id}`} />
              ))}
            </PanelSection>
          ) : null}
          {workspaceView.shows('features') ? (
            <PanelSection title="Spawned features" count={state.loop_features.length} empty="No feature workstreams spawned.">
              {state.loop_features.map((feature, index) => {
                const record = feature && typeof feature === 'object' ? feature as Record<string, unknown> : null;
                return <EntryRow key={String(record?.id || index)} content={String(record?.id || feature)} source={record?.timestamp != null ? String(record.timestamp) : null} />;
              })}
            </PanelSection>
          ) : null}
          {workspaceView.shows('memory') ? (
            <PanelSection title="Memory" count={state.memory.length} empty="No durable checkpoints recorded.">
              {state.memory.map(item => <EntryRow key={item.id} content={item.content} source={item.source} tags={item.tags} />)}
            </PanelSection>
          ) : null}
        </>
      ) : null}
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  progress: { gap: spacing.xs, paddingVertical: spacing.sm },
  progressHead: { flexDirection: 'row', justifyContent: 'space-between' },
});
