import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl, TouchableOpacity, TextInput, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button } from '../components';
import { memoryApi, MemorySnapshot, MemoryLayer } from '../api/memory';
import { spacing } from '../theme/tokens';
import { SafeAreaModal } from '../components/SafeAreaModal';

export function MemoryScreen() {
  const { colors } = useTheme();
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [selectedLayer, setSelectedLayer] = useState<{ layer: string; name: string; content: string } | null>(null);
  const [layerLoading, setLayerLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await memoryApi.getState();
      setSnapshot(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const openLayer = async (layer: MemoryLayer) => {
    setLayerLoading(true);
    try {
      const res = await memoryApi.getLayerContent(layer.id);
      setSelectedLayer({ layer: layer.id, name: layer.name, content: res.content || res.error || '(empty)' });
    } catch (e) {
      setSelectedLayer({ layer: layer.id, name: layer.name, content: String(e) });
    } finally {
      setLayerLoading(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          <Skeleton height={120} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={60} />
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

  if (!snapshot || !snapshot.active) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No memory data" message="No active session memory snapshot available" />
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
          <Text variant="base" style={{ fontWeight: '600' }}>Context Window</Text>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: spacing.sm, minHeight: 28 }}>
            <Text variant="sm" style={{ color: colors.textDim }}>Total tokens</Text>
            <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{snapshot.total_tokens}</Text>
          </View>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}>
            <Text variant="sm" style={{ color: colors.textDim }}>Context limit</Text>
            <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{snapshot.context_limit}</Text>
          </View>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}>
            <Text variant="sm" style={{ color: colors.textDim }}>Free tokens</Text>
            <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{snapshot.free_tokens}</Text>
          </View>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}>
            <Text variant="sm" style={{ color: colors.textDim }}>Fill %</Text>
            <Text variant="sm" style={{ color: colors.text, fontVariant: ['tabular-nums'] }}>{snapshot.fill_pct}%</Text>
          </View>
        </Card>

        <Text variant="sm" style={{ fontWeight: '500', marginBottom: spacing.sm }}>Layers</Text>
        {snapshot.layers.map(layer => (
          <TouchableOpacity key={layer.id} onPress={() => openLayer(layer)} activeOpacity={0.7}>
            <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View style={{ flex: 1 }}>
                  <Text variant="sm" style={{ fontWeight: '500' }}>{layer.name}</Text>
                  <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                    {layer.tokens} tokens · {layer.fill_pct}%
                  </Text>
                </View>
                <Badge label={String(layer.change_count)} variant="neutral" />
              </View>
            </Card>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <SafeAreaModal visible={!!selectedLayer} transparent animationType="slide" onRequestClose={() => setSelectedLayer(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center' }}>
          <View style={{ margin: spacing.base, backgroundColor: colors.bgLift, borderRadius: 12, padding: spacing.base, maxHeight: '85%' }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>{selectedLayer?.name}</Text>
              <TouchableOpacity onPress={() => setSelectedLayer(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 400 }}>
              {layerLoading ? (
                <Skeleton height={40} />
              ) : (
                <Text variant="xs" style={{ color: colors.text, fontFamily: 'monospace' }}>
                  {selectedLayer?.content}
                </Text>
              )}
            </ScrollView>
          </View>
        </View>
      </SafeAreaModal>
    </SafeAreaView>
  );
}