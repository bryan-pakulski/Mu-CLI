import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState } from '../components';
import { modesApi, ModeInfo } from '../api/modes';
import { spacing } from '../theme/tokens';

export function ModesScreen() {
  const { colors } = useTheme();
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await modesApi.list();
      setModes(res.modes);
      setCurrent(res.current);
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

  const selectMode = async (name: string) => {
    setSelecting(name);
    try {
      await modesApi.set(name);
      setCurrent(name);
      setModes(prev => prev.map(m => ({ ...m, is_current: m.name === name })));
    } catch (e) {
      setError(String(e));
    } finally {
      setSelecting(null);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4].map(i => (
            <Skeleton key={i} height={64} style={{ marginBottom: spacing.sm }} />
          ))}
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

  if (modes.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No modes" message="No agent modes available" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={modes}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => {
          const isActive = current === item.name;
          return (
            <TouchableOpacity
              onPress={() => !item.disabled && selectMode(item.name)}
              disabled={item.disabled || selecting !== null}
              activeOpacity={0.7}
            >
              <Card style={{ marginBottom: spacing.sm, minHeight: 44, opacity: item.disabled ? 0.5 : 1 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <View style={{ flex: 1 }}>
                    <Text variant="base" style={{ fontWeight: '500' }}>
                      {item.display_name}
                    </Text>
                    {item.description && (
                      <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }} numberOfLines={2}>
                        {item.description}
                      </Text>
                    )}
                  </View>
                  {isActive && <Ionicons name="checkmark-circle" size={20} color={colors.accent} />}
                  {selecting === item.name && !isActive && <Ionicons name="hourglass-outline" size={18} color={colors.textDim} />}
                </View>
              </Card>
            </TouchableOpacity>
          );
        }}
      />
    </SafeAreaView>
  );
}