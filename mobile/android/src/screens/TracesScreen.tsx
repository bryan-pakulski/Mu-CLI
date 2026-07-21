import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { tracesApi, TraceRun, TraceSummary } from '../api/traces';
import { spacing } from '../theme/tokens';

export function TracesScreen() {
  const { colors } = useTheme();
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [selectedSummary, setSelectedSummary] = useState<TraceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await tracesApi.list();
      setRuns(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const openRun = async (run: TraceRun) => {
    setSummaryLoading(true);
    try {
      const summary = await tracesApi.getSummary(run.run_id);
      setSelectedSummary(summary);
    } catch (e) {
      setError(String(e));
    } finally {
      setSummaryLoading(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4].map(i => <Skeleton key={i} height={60} style={{ marginBottom: spacing.sm }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error && runs.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (runs.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No traces" message="No agent run traces available" />
      </SafeAreaView>
    );
  }

  if (selectedSummary) {
    const s = selectedSummary;
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.base, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
          <TouchableOpacity onPress={() => setSelectedSummary(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="arrow-back" size={24} color={colors.accent} />
          </TouchableOpacity>
          <Text variant="sm" style={{ color: colors.text, marginLeft: spacing.sm, flex: 1 }} numberOfLines={1}>
            {s.run_id}
          </Text>
        </View>
        <FlatList
          data={[
            { k: 'Session', v: s.session },
            { k: 'Model', v: `${s.provider} · ${s.model}` },
            { k: 'Mode', v: s.mode },
            { k: 'Status', v: s.status },
            { k: 'Iterations', v: String(s.iters) },
            { k: 'Max iters', v: String(s.max_iterations) },
            { k: 'Tokens in', v: String(s.total_in) },
            { k: 'Tokens out', v: String(s.total_out) },
            { k: 'Cost ($)', v: s.total_cost.toFixed(6) },
            { k: 'Peak context', v: String(s.peak_context) },
            { k: 'Peak drift', v: String(s.peak_drift_abs) },
            { k: 'Mean drift', v: s.mean_drift.toFixed(2) },
            { k: 'Compactions', v: String(s.compaction_count) },
            { k: 'Nudges', v: String(s.nudge_count) },
            { k: 'Nudges broken', v: String(s.nudges_broken) },
            { k: 'Tool calls', v: String(s.tool_calls) },
            { k: 'Redundant reads', v: String(s.redundant_reads) },
            { k: 'Subagent iters', v: String(s.subagent_iters) },
            { k: 'Wall time (s)', v: (s.total_wall_ms / 1000).toFixed(2) },
            { k: 'Peak wall (s)', v: (s.peak_wall_ms / 1000).toFixed(2) },
          ]}
          keyExtractor={item => item.k}
          contentContainerStyle={{ padding: spacing.base }}
          renderItem={({ item }) => (
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 36, paddingVertical: 6 }}>
              <Text variant="sm" style={{ color: colors.textDim }}>{item.k}</Text>
              <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{item.v}</Text>
            </View>
          )}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={runs}
        keyExtractor={item => item.run_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <TouchableOpacity onPress={() => openRun(item)} activeOpacity={0.7}>
            <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ flex: 1 }}>
                  <Text variant="sm" style={{ fontWeight: '500' }} numberOfLines={1}>{item.run_id}</Text>
                  <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                    {item.provider} · {item.model} · {item.mode}
                  </Text>
                </View>
                <Badge label={`${item.iters}/${item.max_iterations}`} variant="neutral" />
              </View>
              <View style={{ flexDirection: 'row', gap: 12, marginTop: 4 }}>
                <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                  {item.session}
                </Text>
                <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                  {(item.bytes / 1024).toFixed(0)}KB
                </Text>
              </View>
            </Card>
          </TouchableOpacity>
        )}
      />
    </SafeAreaView>
  );
}