import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button } from '../components';
import { inspectorApi, InspectorStats, InspectorMemoryResponse } from '../api/inspector';
import { spacing } from '../theme/tokens';

export function InspectorScreen() {
  const { colors } = useTheme();
  const [stats, setStats] = useState<InspectorStats | null>(null);
  const [memory, setMemory] = useState<InspectorMemoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [s, m] = await Promise.all([inspectorApi.getStats(), inspectorApi.getMemory()]);
      setStats(s);
      setMemory(m);
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
          <Skeleton height={80} style={{ marginBottom: spacing.sm }} />
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

  if (!stats && !memory) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No data" message="No inspector data available" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
      >
        {stats && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="base" style={{ fontWeight: '600', marginBottom: spacing.sm }}>Session Stats</Text>
            {stats.name && <StatRow label="Session" value={stats.name} colors={colors} />}
            {stats.provider && <StatRow label="Provider" value={`${stats.provider} · ${stats.model || ''}`} colors={colors} />}
            {stats.agent_mode && <StatRow label="Mode" value={stats.agent_mode} colors={colors} />}
            {stats.history_length !== undefined && <StatRow label="History" value={String(stats.history_length)} colors={colors} />}
            {stats.estimated_cost_usd !== undefined && (
              <StatRow label="Est. cost" value={`$${stats.estimated_cost_usd.toFixed(4)}`} colors={colors} />
            )}
            {stats.tokens && (
              <View style={{ marginTop: spacing.xs }}>
                <Text variant="xs" style={{ color: colors.textDim, marginBottom: 4 }}>Tokens</Text>
                {Object.entries(stats.tokens).map(([k, v]) => (
                  <View key={k} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                    <Text variant="xs" style={{ color: colors.textDim }}>{k}</Text>
                    <Text variant="xs" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{v}</Text>
                  </View>
                ))}
              </View>
            )}
          </Card>
        )}

        {stats?.memory_status_counts && Object.keys(stats.memory_status_counts).length > 0 && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="base" style={{ fontWeight: '600', marginBottom: spacing.sm }}>Memory Status</Text>
            {Object.entries(stats.memory_status_counts).map(([k, v]) => (
              <View key={k} style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}>
                <Text variant="sm" style={{ color: colors.textDim }}>{k}</Text>
                <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{v}</Text>
              </View>
            ))}
          </Card>
        )}

        {memory && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="base" style={{ fontWeight: '600', marginBottom: spacing.sm }}>
              Task Memory ({memory.task_memory.length})
            </Text>
            <View style={{ maxHeight: 300, overflow: 'hidden' }}>
              <ScrollView nestedScrollEnabled style={{ maxHeight: 300 }}>
                {memory.task_memory.length === 0 ? (
                  <Text variant="xs" style={{ color: colors.textDim }}>No task memory entries</Text>
                ) : (
                  memory.task_memory.map(e => <MemoryEntry key={e.id} entry={e} colors={colors} />)
                )}
              </ScrollView>
            </View>
          </Card>
        )}

        {memory && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="base" style={{ fontWeight: '600', marginBottom: spacing.sm }}>
              Scratchpad ({memory.scratchpad.length})
            </Text>
            <View style={{ maxHeight: 250, overflow: 'hidden' }}>
              <ScrollView nestedScrollEnabled style={{ maxHeight: 250 }}>
                {memory.scratchpad.length === 0 ? (
                  <Text variant="xs" style={{ color: colors.textDim }}>No scratchpad entries</Text>
                ) : (
                  memory.scratchpad.map(e => <MemoryEntry key={e.id} entry={e} colors={colors} />)
                )}
              </ScrollView>
            </View>
          </Card>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function StatRow({ label, value, colors }: { label: string; value: string; colors: any }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}>
      <Text variant="sm" style={{ color: colors.textDim }}>{label}</Text>
      <Text variant="sm" style={{ color: colors.text }}>{value}</Text>
    </View>
  );
}

function MemoryEntry({ entry, colors }: { entry: { id: number; content: string; kind: string; status: string; tags: string[] }; colors: any }) {
  return (
    <View style={{ paddingVertical: 6, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 2 }}>
        <Badge label={entry.kind} variant="neutral" />
        <Badge label={entry.status} variant={entry.status === 'active' ? 'success' : 'neutral'} />
      </View>
      <Text variant="xs" style={{ color: colors.text }} numberOfLines={3}>{entry.content}</Text>
      {entry.tags.length > 0 && (
        <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>#{entry.tags.join(' #')}</Text>
      )}
    </View>
  );
}