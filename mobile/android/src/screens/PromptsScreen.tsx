import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity, TextInput, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Button, Skeleton, ErrorState, EmptyState } from '../components';
import { promptsApi, type PendingPrompt } from '../api/prompts';
import { spacing } from '../theme/tokens';
import { SafeAreaModal } from '../components/SafeAreaModal';

function promptText(prompt: PendingPrompt | null | undefined): string {
  if (!prompt) return '';
  return String(prompt.message || prompt.question || prompt.description || prompt.tool_name || prompt.shape || 'Input required');
}

export function PromptsScreen() {
  const { colors } = useTheme();
  const [pending, setPending] = useState<PendingPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [answering, setAnswering] = useState<PendingPrompt | null>(null);
  const [answerText, setAnswerText] = useState('');

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await promptsApi.listPending();
      setPending(res.pending || []);
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
      void load();
    }, [load]),
  );

  const submitAnswer = async () => {
    if (!answering || !answerText.trim()) return;
    try {
      await promptsApi.answer(answering.id, { text: answerText.trim() });
      setAnswering(null);
      setAnswerText('');
      void load();
    } catch (e) {
      Alert.alert('Failed to answer', String(e));
    }
  };

  const cancelPrompt = (id: string) => {
    Alert.alert('Cancel prompt?', 'This will cancel the pending prompt.', [
      { text: 'No', style: 'cancel' },
      { text: 'Cancel prompt', style: 'destructive', onPress: async () => {
        await promptsApi.cancel(id);
        void load();
      }},
    ]);
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3].map(i => <Skeleton key={i} height={80} style={{ marginBottom: spacing.sm }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={() => void load()} />
      </SafeAreaView>
    );
  }

  if (pending.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No pending prompts" message="The agent has no prompts awaiting your response" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={pending}
        keyExtractor={item => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
            <Text variant="sm" style={{ color: colors.textDim, marginBottom: 4 }}>{item.id}</Text>
            <Text variant="base" style={{ marginBottom: spacing.sm }}>{promptText(item)}</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button title="Answer" onPress={() => { setAnswering(item); setAnswerText(''); }} />
              <Button title="Cancel" variant="ghost" onPress={() => cancelPrompt(item.id)} />
            </View>
          </Card>
        )}
      />
      <SafeAreaModal visible={!!answering} transparent animationType="slide" onRequestClose={() => setAnswering(null)}>
        <TouchableOpacity style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)' }} onPress={() => setAnswering(null)} activeOpacity={1}>
          <View style={{ margin: spacing.base, marginTop: 80, backgroundColor: colors.bgLift, borderRadius: 12, padding: spacing.base }}>
            <Text variant="lg" style={{ marginBottom: spacing.sm }}>Answer prompt</Text>
            <Text variant="sm" style={{ color: colors.textDim, marginBottom: spacing.sm }}>{promptText(answering)}</Text>
            <TextInput
              value={answerText}
              onChangeText={setAnswerText}
              placeholder="Your answer…"
              placeholderTextColor={colors.textDim}
              multiline
              style={{ color: colors.text, borderWidth: 1, borderColor: colors.border, borderRadius: 6, padding: 12, minHeight: 100, marginBottom: spacing.base }}
            />
            <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end' }}>
              <Button title="Cancel" variant="ghost" onPress={() => setAnswering(null)} />
              <Button title="Send" onPress={() => void submitAnswer()} disabled={!answerText.trim()} />
            </View>
          </View>
        </TouchableOpacity>
      </SafeAreaModal>
    </SafeAreaView>
  );
}
