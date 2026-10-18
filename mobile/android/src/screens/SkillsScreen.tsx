import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { skillsApi, Skill } from '../api/skills';
import { elevation, radii, spacing, fontWeights, fonts } from '../theme/tokens';
import { Button, Card, Chip, EmptyState, ErrorState, Pressable, Skeleton, Text, Icon } from '../ui';

export function SkillsScreen() {
  const { colors, isDark } = useTheme();
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
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4].map(i => <Skeleton key={i} height={60} style={{ marginBottom: spacing.sm }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (skills.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <EmptyState title="No skills" message="No skills installed" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
      <FlatList
        data={skills}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View style={{ flex: 1 }}>
                <Text variant="base" style={{ fontWeight: fontWeights.medium }}>{item.name}</Text>
                <Text variant="xs" style={{ color: colors.ink3, marginTop: 2 }} numberOfLines={2}>
                  {item.description}
                </Text>
              </View>
              <Chip label={item.enabled ? 'Enabled' : 'Disabled'} tone={item.enabled ? 'ok' : 'neutral'} />
            </View>
            <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm }}>
              <Button title="Details" variant="ghost" onPress={() => setSelected(item)} />
              <Button title={item.enabled ? 'Disable' : 'Enable'} onPress={() => toggle(item)} />
              <Pressable accessibilityLabel={`Remove skill ${item.name}`} onPress={() => remove(item.name)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ minHeight: 44, justifyContent: 'center' }}>
                <Icon name="trash-2" size="md" color={colors.danger.ink} />
              </Pressable>
            </View>
          </Card>
        )}
      />
      {selected && (
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.scrim }}>
          <View style={{ flex: 1, justifyContent: 'center', padding: spacing.base }}>
            <View style={[elevation(2, colors, isDark), { maxHeight: '80%', borderRadius: radii.lg, padding: spacing.base }]}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                <Text variant="base" style={{ fontWeight: fontWeights.semibold }}>{selected.name}</Text>
                <Pressable onPress={() => setSelected(null)} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
                  <Text variant="sm" style={{ color: colors.accent.ink }}>Close</Text>
                </Pressable>
              </View>
              <Text variant="xs" style={{ color: colors.ink3, marginBottom: spacing.xs }}>Trigger: {selected.trigger}</Text>
              <Text variant="xs" style={{ color: colors.ink3, marginBottom: spacing.xs }}>Source: {selected.source}</Text>
              <Text variant="sm" style={{ color: colors.ink, marginTop: spacing.sm, fontFamily: fonts.mono }}>
                {selected.body.slice(0, 500)}
                {selected.body.length > 500 ? '...' : ''}
              </Text>
            </View>
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}