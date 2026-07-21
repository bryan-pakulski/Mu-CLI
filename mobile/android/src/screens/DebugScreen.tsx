import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { debugApi, DebugState as DebugStateT } from '../api/debug';
import { spacing } from '../theme/tokens';

export function DebugScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<DebugStateT | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await debugApi.getState();
      setState(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

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

  if (!state || !state.active) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No debug session" message="No active debug investigation" />
      </SafeAreaView>
    );
  }

  const renderList = (title: string, items: Array<Record<string, unknown>>) => {
    if (items.length === 0) return null;
    return (
      <Card style={{ marginBottom: spacing.sm }}>
        <Text variant="sm" style={{ fontWeight: '500', marginBottom: 6 }}>{title} ({items.length})</Text>
        {items.map((item, i) => (
          <View key={i} style={{ minHeight: 28, paddingVertical: 4 }}>
            <Text variant="xs" style={{ color: colors.textDim }}>{JSON.stringify(item).slice(0, 120)}</Text>
          </View>
        ))}
      </Card>
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
      >
        <Card style={{ marginBottom: spacing.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text variant="base" style={{ fontWeight: '600' }}>Debug Target</Text>
            <Badge label="Active" variant="accent" />
          </View>
          <Text variant="sm" style={{ color: colors.text, marginTop: spacing.sm }}>{state.debug_target}</Text>
          <Text variant="xs" style={{ color: colors.textDim, marginTop: 4, fontVariant: ['tabular-nums'] }}>
            Scratchpad: {state.scratchpad_count} entries
          </Text>
        </Card>

        {renderList('Hypotheses', state.hypotheses)}
        {renderList('Suspects', state.suspects)}
        {renderList('Notes', state.notes)}
        {renderList('Findings', state.findings)}
      </ScrollView>
    </SafeAreaView>
  );
}