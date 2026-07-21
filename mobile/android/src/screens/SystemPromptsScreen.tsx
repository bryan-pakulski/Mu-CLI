import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity, Modal, TextInput, Alert, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Button, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { systemPromptsApi, SystemPromptInfo } from '../api/systemPrompts';
import { spacing } from '../theme/tokens';

export function SystemPromptsScreen() {
  const { colors } = useTheme();
  const [prompts, setPrompts] = useState<SystemPromptInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [editing, setEditing] = useState<{ name: string; text: string; version: number | null } | null>(null);
  const [editText, setEditText] = useState('');

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await systemPromptsApi.list();
      setPrompts(res.items);
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

  const openEdit = async (name: string) => {
    try {
      const detail = await systemPromptsApi.get(name);
      setEditing({ name, text: detail.text, version: detail.version });
      setEditText(detail.text);
    } catch (e) {
      Alert.alert('Failed to load', String(e));
    }
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      await systemPromptsApi.put(editing.name, editText, editing.version || undefined);
      setEditing(null);
      load();
    } catch (e) {
      Alert.alert('Save failed', String(e));
    }
  };

  const resetPrompt = (name: string) => {
    Alert.alert('Reset prompt?', `Reset "${name}" to default? This removes your override.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Reset', style: 'destructive', onPress: async () => {
        await systemPromptsApi.reset(name);
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

  if (prompts.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No system prompts" message="No system prompts configured" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={prompts}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View style={{ flex: 1 }}>
                <Text variant="base" style={{ fontWeight: '500' }}>{item.name}</Text>
                <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                  {item.chars} chars · {item.source}
                </Text>
              </View>
              {item.has_override && <Badge label="Override" variant="accent" />}
            </View>
            <View style={{ flexDirection: 'row', gap: 8, marginTop: spacing.sm }}>
              <Button title="Edit" onPress={() => openEdit(item.name)} />
              {item.has_override && <Button title="Reset" variant="ghost" onPress={() => resetPrompt(item.name)} />}
            </View>
          </Card>
        )}
      />
      <Modal visible={!!editing} transparent animationType="slide" onRequestClose={() => setEditing(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center' }}>
          <View style={{ margin: spacing.base, backgroundColor: colors.bgLift, borderRadius: 12, padding: spacing.base, maxHeight: '85%' }}>
            <Text variant="lg" style={{ marginBottom: spacing.sm }}>{editing?.name}</Text>
            <ScrollView style={{ maxHeight: 400 }}>
              <TextInput
                value={editText}
                onChangeText={setEditText}
                placeholder="Prompt text…"
                placeholderTextColor={colors.textDim}
                multiline
                textAlignVertical="top"
                style={{ color: colors.text, borderWidth: 1, borderColor: colors.border, borderRadius: 6, padding: 12, minHeight: 200, fontFamily: 'monospace' }}
              />
            </ScrollView>
            <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end', marginTop: spacing.sm }}>
              <Button title="Cancel" variant="ghost" onPress={() => setEditing(null)} />
              <Button title="Save" onPress={saveEdit} disabled={!editText.trim()} />
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}