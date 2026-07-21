import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { researchApi, ResearchSource } from '../api/research';
import { spacing } from '../theme/tokens';

export function ResearchScreen() {
  const { colors } = useTheme();
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [findingCount, setFindingCount] = useState(0);
  const [active, setActive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await researchApi.getState();
      setSources(res.sources);
      setFindingCount(res.finding_count);
      setActive(res.active);
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
          {[1, 2, 3].map(i => <Skeleton key={i} height={70} style={{ marginBottom: spacing.sm }} />)}
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

  if (!active && sources.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No research session" message="No active research session. Start one from the chat." />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={sources}
        keyExtractor={item => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        ListHeaderComponent={
          <Card style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text variant="base" style={{ fontWeight: '600' }}>Research Session</Text>
              <Badge label={active ? 'Active' : 'Idle'} variant={active ? 'accent' : 'neutral'} />
            </View>
            <Text variant="xs" style={{ color: colors.textDim, marginTop: 4 }}>
              {sources.length} sources · {findingCount} findings
            </Text>
          </Card>
        }
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <Badge label={item.type} variant="neutral" />
              <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                Credibility: {Math.round(item.credibility * 100)}%
              </Text>
            </View>
            <Text variant="sm" style={{ fontWeight: '500' }} numberOfLines={2}>{item.title}</Text>
            <Text
              variant="xs"
              style={{ color: colors.accent, marginTop: 2 }}
              numberOfLines={1}
              onPress={() => Linking.openURL(item.url)}
            >
              {item.url}
            </Text>
            {item.authors.length > 0 && (
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                {item.authors.join(', ')}
              </Text>
            )}
          </Card>
        )}
      />
    </SafeAreaView>
  );
}