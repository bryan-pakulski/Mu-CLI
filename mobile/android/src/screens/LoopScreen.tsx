import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { ModeWorkspaceHeader, useModeWorkspaceView } from '../components';
import { loopApi, LoopEntry, LoopState } from '../api/loop';
import { spacing, fontWeights, fonts, radii } from '../theme/tokens';
import { Button, Card, Chip, EmptyState, ErrorState, Skeleton, Text } from '../ui';

export function LoopScreen() {
  const { colors } = useTheme();
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
  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  if (loading) return <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas }}><View style={{ padding: spacing.base }}><Skeleton height={280} /><Skeleton height={100} style={{ marginTop: spacing.sm }} /></View></SafeAreaView>;
  if (error) return <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas }}><ErrorState message={error} onRetry={load} /></SafeAreaView>;
  if (!state || !state.active) return <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas }}><EmptyState title="No loop mission" message="Set a goal to open autonomous mission control." /></SafeAreaView>;

  const completed = state.backlog.filter(item => item.status === 'completed').length;
  const progress = state.backlog.length ? (completed / state.backlog.length) * 100 : 0;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas }}>
      <ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />} contentContainerStyle={{ padding: spacing.base, paddingBottom: spacing['2xl'] }}>
        <ModeWorkspaceHeader workspace={state.workspace} selectedView={workspaceView.selectedView} onSelectView={workspaceView.selectView} />
        {!!state.loop_goal && <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: -spacing.xs, marginBottom: spacing.md }}><Button title={state.loop_active ? 'Pause mission' : 'Resume mission'} variant={state.loop_active ? 'ghost' : 'primary'} onPress={async () => { await loopApi.setActive(!state.loop_active, state.loop_goal); load(); }} /></View>}

        {workspaceView.shows('backlog') && <ModeSection title="Execution queue" count={state.backlog.length}>
          <Card style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}><Text variant="xs" style={{ color: colors.ink3 }}>Reported queue progress</Text><Text variant="xs" style={{ color: colors.ink2, fontFamily: fonts.mono, fontVariant: ['tabular-nums'] }}>{completed}/{state.backlog.length}</Text></View>
            <View style={{ height: 4, borderRadius: radii.pill, overflow: 'hidden', marginTop: spacing.sm, backgroundColor: colors.sunken }}><View style={{ height: '100%', width: `${progress}%`, backgroundColor: colors.accent.ink }} /></View>
            <Text variant="xs" style={{ color: colors.ink3, marginTop: spacing.xs }}>Completion state is visible; correctness still requires verification evidence.</Text>
          </Card>
          {state.backlog.map(item => <QueueRow key={item.id} item={item} />)}
          {!state.backlog.length && <EmptyLine text="No queue items yet." />}
        </ModeSection>}

        {workspaceView.shows('features') && <ModeSection title="Workstreams" count={state.loop_features.length}>
          {state.loop_features.map((feature, index) => {
            const record = feature && typeof feature === 'object' ? feature as Record<string, unknown> : null;
            return <Card key={String(record?.id || index)} style={{ marginBottom: spacing.sm }}><Text variant="sm" style={{ color: colors.ink, fontWeight: fontWeights.semibold }}>{String(record?.id || feature)}</Text>{record?.timestamp != null && <Text variant="xs" style={{ color: colors.ink3, marginTop: spacing.xs, fontFamily: fonts.mono }}>{String(record.timestamp)}</Text>}</Card>;
          })}
          {!state.loop_features.length && <EmptyLine text="No feature workstreams spawned." />}
        </ModeSection>}

        {workspaceView.shows('memory') && <ModeSection title="Checkpoints" count={state.memory.length}>
          {state.memory.map(item => <Card key={item.id} style={{ marginBottom: spacing.sm }}><Text variant="sm" style={{ color: colors.ink2 }}>{item.content}</Text><View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.sm }}>{item.tags.map(tag => <Chip key={tag} label={tag} tone="neutral" />)}</View>{!!item.source && <Text variant="xs" style={{ marginTop: spacing.xs, color: colors.ink3, fontFamily: fonts.mono }}>↳ {item.source}</Text>}</Card>)}
          {!state.memory.length && <EmptyLine text="No durable checkpoints recorded." />}
        </ModeSection>}
      </ScrollView>
    </SafeAreaView>
  );
}

function ModeSection({ title, count, children }: { title: string; count: number; children: React.ReactNode }) { const { colors } = useTheme(); return <View style={{ marginBottom: spacing.md }}><View style={{ flexDirection: 'row', marginBottom: spacing.sm }}><Text variant="xs" style={{ color: colors.ink2, fontWeight: fontWeights.semibold }}>{title}</Text><Text variant="xs" style={{ marginLeft: 'auto', color: colors.ink3, fontFamily: fonts.mono, fontVariant: ['tabular-nums'] }}>{count}</Text></View>{children}</View>; }

function QueueRow({ item }: { item: LoopEntry }) { const { colors } = useTheme(); const status = item.status || 'pending'; const variant = status === 'completed' ? 'ok' : status === 'blocked' ? 'danger' : status === 'in_progress' ? 'accent' : 'neutral'; return <Card style={{ marginBottom: spacing.sm }}><View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm }}><Chip label={status.replace(/_/g, ' ')} tone={variant} /><Text variant="sm" style={{ flex: 1, color: colors.ink }}>{item.content}</Text></View>{!!item.source && <Text variant="xs" style={{ color: colors.ink3, marginTop: spacing.xs, fontFamily: fonts.mono }}>↳ {item.source}</Text>}</Card>; }
function EmptyLine({ text }: { text: string }) { const { colors } = useTheme(); return <Text variant="xs" style={{ color: colors.ink3, padding: spacing.md }}>{text}</Text>; }
