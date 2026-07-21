import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { loopApi, LoopState as LoopStateT } from '../api/loop';
import { spacing } from '../theme/tokens';

export function LoopScreen() {
  const { colors } = useTheme();
  const [state, setState] = useState<LoopStateT | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await loopApi.getState();
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
        <EmptyState title="No loop session" message="No active loop detection session" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
      >
        <Card style={{ marginBottom: spacing.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text variant="base" style={{ fontWeight: '600' }}>Loop Detection</Text>
            <Badge label={state.loop_active ? 'Looping' : 'Idle'} variant={state.loop_active ? 'accent' : 'neutral'} />
          </View>
          <Text variant="sm" style={{ color: colors.text, marginTop: spacing.sm }}>{state.loop_goal}</Text>
        </Card>

        {state.loop_features.length > 0 && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="sm" style={{ fontWeight: '500', marginBottom: 6 }}>Features</Text>
            {state.loop_features.map((f, i) => (
              <View key={i} style={{ minHeight: 28, paddingVertical: 4 }}>
                <Text variant="xs" style={{ color: colors.text }}>{String(f)}</Text>
              </View>
            ))}
          </Card>
        )}

        {state.backlog.length > 0 && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="sm" style={{ fontWeight: '500', marginBottom: 6 }}>Backlog ({state.backlog.length})</Text>
            {state.backlog.map((b, i) => (
              <View key={i} style={{ minHeight: 28, paddingVertical: 4 }}>
                <Text variant="xs" style={{ color: colors.textDim }}>{JSON.stringify(b).slice(0, 100)}</Text>
              </View>
            ))}
          </Card>
        )}

        {state.memory.length > 0 && (
          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="sm" style={{ fontWeight: '500', marginBottom: 6 }}>Memory ({state.memory.length})</Text>
            {state.memory.map((m, i) => (
              <View key={i} style={{ minHeight: 28, paddingVertical: 4 }}>
                <Text variant="xs" style={{ color: colors.textDim }}>{JSON.stringify(m).slice(0, 100)}</Text>
              </View>
            ))}
          </Card>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}