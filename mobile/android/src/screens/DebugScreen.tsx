import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { ModeWorkspaceHeader, useModeWorkspaceView } from '../components';
import { debugApi, DebugEntry, DebugHypothesis, DebugState } from '../api/debug';
import { spacing, fontWeights, fonts, radii } from '../theme/tokens';
import { Card, Chip, EmptyState, ErrorState, Pressable, Skeleton, Text } from '../ui';

export function DebugScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<DebugState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try { setError(null); setState(await debugApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  if (loading) return <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}><View style={{ padding: spacing.base }}><Skeleton height={280} /><Skeleton height={110} style={{ marginTop: spacing.sm }} /></View></SafeAreaView>;
  if (error) return <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}><ErrorState message={error} onRetry={load} /></SafeAreaView>;
  if (!state || !state.active) return <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}><EmptyState title="No debug case" message="Start debugging to build a hypothesis-and-evidence board." /></SafeAreaView>;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
      <ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />} contentContainerStyle={{ padding: spacing.base, paddingBottom: spacing['2xl'] }}>
        <ModeWorkspaceHeader workspace={state.workspace} selectedView={workspaceView.selectedView} onSelectView={workspaceView.selectView} />

        {workspaceView.shows('hypotheses') && <DebugSection title="Competing hypotheses" count={state.hypotheses.length}>
          {state.hypotheses.map(item => <HypothesisCard key={item.id} item={item} expanded={expanded === item.id} onToggle={() => setExpanded(expanded === item.id ? null : item.id)} />)}
          {!state.hypotheses.length && <EmptyLine text="No hypotheses captured yet." />}
        </DebugSection>}

        {workspaceView.shows('evidence') && <>
          <DebugSection title="Suspect surface" count={state.suspects.length}>
            {state.suspects.map(item => <EvidenceRow key={item.id} item={item} tone="warning" />)}
            {!state.suspects.length && <EmptyLine text="No suspect files or symbols recorded." />}
          </DebugSection>
          <DebugSection title="Observations & tests" count={state.notes.length}>
            {state.notes.map(item => <EvidenceRow key={item.id} item={item} tone="neutral" />)}
            {!state.notes.length && <EmptyLine text="No reproduction or test observations recorded." />}
          </DebugSection>
        </>}

        {workspaceView.shows('findings') && <DebugSection title="Durable root causes" count={state.findings.length}>
          {state.findings.map(item => <EvidenceRow key={item.id} item={item} tone="success" />)}
          {!state.findings.length && <EmptyLine text="The investigation has not produced a durable root cause." />}
        </DebugSection>}
      </ScrollView>
    </SafeAreaView>
  );
}

function DebugSection({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  const { colors } = useTheme();
  return <View style={{ marginBottom: spacing.md }}><View style={{ flexDirection: 'row', marginBottom: spacing.sm }}><Text variant="xs" style={{ color: colors.ink2, fontWeight: fontWeights.semibold }}>{title}</Text><Text variant="xs" style={{ marginLeft: 'auto', color: colors.ink3, fontFamily: fonts.mono, fontVariant: ['tabular-nums'] }}>{count}</Text></View>{children}</View>;
}

function statusVariant(status: string) {
  if (status === 'confirmed' || status === 'supported') return 'ok' as const;
  if (status === 'disproved') return 'danger' as const;
  return 'attention' as const;
}

function HypothesisCard({ item, expanded, onToggle }: { item: DebugHypothesis; expanded: boolean; onToggle: () => void }) {
  const { colors } = useTheme();
  return <Pressable onPress={onToggle} style={({ pressed }) => ({ opacity: pressed ? 0.72 : 1 })}><Card style={{ marginBottom: spacing.sm }}>
    <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm }}><Chip label={item.status} tone={statusVariant(item.status)} /><Text variant="sm" style={{ flex: 1, color: colors.ink, fontWeight: fontWeights.medium }}>{item.content}</Text></View>
    {expanded && <View style={{ marginTop: spacing.sm, paddingTop: spacing.xs }}>
      <Text variant="xs" style={{ color: colors.ink3 }}>{item.status === 'untested' ? 'Needs a discriminating test.' : `Investigation state: ${item.status}. This is not an independent accuracy score.`}</Text>
      {!!item.source && <Text variant="xs" style={{ color: colors.ink2, marginTop: spacing.xs, fontFamily: fonts.mono }}>↳ {item.source}</Text>}
      <View style={{ flexDirection: 'row', gap: spacing.xs, flexWrap: 'wrap', marginTop: spacing.sm }}>{item.tags.map(tag => <Chip key={tag} label={tag} tone="neutral" />)}</View>
    </View>}
  </Card></Pressable>;
}

function EvidenceRow({ item, tone }: { item: DebugEntry; tone: 'neutral' | 'warning' | 'success' }) {
  const { colors } = useTheme();
  return <Card style={{ marginBottom: spacing.sm }}><View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm }}><View style={{ width: 6, height: 6, marginTop: spacing.sm, borderRadius: radii.pill, backgroundColor: tone === 'success' ? colors.ok.ink : tone === 'warning' ? colors.attention.ink : colors.ink3 }} /><Text variant="sm" style={{ flex: 1, color: colors.ink2 }}>{item.content}</Text></View>{!!item.source && <Text variant="xs" style={{ color: colors.ink3, marginTop: spacing.xs, fontFamily: fonts.mono }}>↳ {item.source}</Text>}</Card>;
}

function EmptyLine({ text }: { text: string }) { const { colors } = useTheme(); return <Text variant="xs" style={{ color: colors.ink3, padding: spacing.md }}>{text}</Text>; }
