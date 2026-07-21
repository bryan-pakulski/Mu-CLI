import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge, Button } from '../components';
import { skillsApi, Skill } from '../api/skills';
import { spacing } from '../theme/tokens';

export function SkillsScreen() {
  const { colors } = useTheme();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await skillsApi.list();
      setSkills(res.skills);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const toggle = async (skill: Skill) => {
    try {
      await (skill.enabled ? skillsApi.disable(skill.name) : skillsApi.enable(skill.name));
      load();
    } catch (e) {
      Alert.alert('Failed', String(e));
    }
  };

  const remove = (name: string) => {
    Alert.alert('Delete skill?', `Delete "${name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        await skillsApi.delete(name);
        load();
      }},
    ]);
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

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (skills.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No skills" message="No skills installed" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={skills}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View style={{ flex: 1 }}>
                <Text variant="base" style={{ fontWeight: '500' }}>{item.name}</Text>
                <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }} numberOfLines={2}>
                  {item.description}
                </Text>
              </View>
              <Badge label={item.enabled ? 'Enabled' : 'Disabled'} variant={item.enabled ? 'success' : 'neutral'} />
            </View>
            <View style={{ flexDirection: 'row', gap: 8, marginTop: spacing.sm }}>
              <Button title="Details" variant="ghost" onPress={() => setSelected(item)} />
              <Button title={item.enabled ? 'Disable' : 'Enable'} onPress={() => toggle(item)} />
              <TouchableOpacity onPress={() => remove(item.name)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ minHeight: 44, justifyContent: 'center' }}>
                <Ionicons name="trash-outline" size={20} color={colors.error} />
              </TouchableOpacity>
            </View>
          </Card>
        )}
      />
      {selected && (
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)' }}>
          <View style={{ flex: 1, justifyContent: 'center', padding: spacing.base }}>
            <Card style={{ maxHeight: '80%' }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                <Text variant="base" style={{ fontWeight: '600' }}>{selected.name}</Text>
                <TouchableOpacity onPress={() => setSelected(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
                </TouchableOpacity>
              </View>
              <Text variant="xs" style={{ color: colors.textDim, marginBottom: 4 }}>Trigger: {selected.trigger}</Text>
              <Text variant="xs" style={{ color: colors.textDim, marginBottom: 4 }}>Source: {selected.source}</Text>
              <Text variant="sm" style={{ color: colors.text, marginTop: spacing.sm, fontFamily: 'monospace' }}>
                {selected.body.slice(0, 500)}
                {selected.body.length > 500 ? '...' : ''}
              </Text>
            </Card>
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}