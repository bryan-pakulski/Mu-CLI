// Pending prompts: everything the agent is waiting on you for; answer in a sheet or cancel after
// confirming. (The chat shows the same prompts inline.)

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { promptsApi, type PendingPrompt } from '../api/prompts';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Button, Row, Sheet, Text, TextField, useServerActions, useToast } from '../ui';

export function promptText(prompt: PendingPrompt | null | undefined): string {
  if (!prompt) return '';
  return String(prompt.message || prompt.question || prompt.description || prompt.tool_name || prompt.shape || 'Input required');
}

export function PromptsScreen() {
  const toast = useToast();
  const actions = useServerActions();
  const [pending, setPending] = useState<PendingPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [answering, setAnswering] = useState<PendingPrompt | null>(null);
  const [answerText, setAnswerText] = useState('');

  const load = useCallback(async () => {
    try { setError(null); setPending((await promptsApi.listPending()).pending || []); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const submitAnswer = async () => {
    if (!answering || !answerText.trim()) return;
    try {
      await promptsApi.answer(answering.id, { text: answerText.trim() });
      setAnswering(null);
      setAnswerText('');
      void load();
    } catch (e) {
      toast(`Couldn't answer: ${String(e)}`, 'danger');
    }
  };

  const cancelPrompt = async (id: string) => {
    if (!(await actions.ask({ id: 'cancel', label: 'Cancel prompt', danger: true, confirm: 'This will cancel the pending prompt.' }))) return;
    await promptsApi.cancel(id);
    void load();
  };

  return (
    <PanelPage testID="prompts-screen" title="Pending prompts"
      state={[pending.length ? { text: `${pending.length} need you`, tone: 'attention' } : { text: 'Nothing waiting', tone: 'quiet' }]}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && !pending.length ? { title: 'No pending prompts', message: 'The agent has no prompts awaiting your response.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={actions.element}>
      <PanelSection title="Waiting for you" count={pending.length}>
        {pending.map(item => (
          <Row key={item.id} testID={`pending-${item.id}`} status="waiting" title={promptText(item)} sub={item.id}
            actions={(
              <View style={styles.actions}>
                {item.requires_desktop ? <Text variant="caption" tone="ink3">Review on desktop</Text> : <Button label="Answer" variant="primary" onPress={() => { setAnswering(item); setAnswerText(''); }} testID={`pending-${item.id}-answer`} />}
                <Button label="Cancel" variant="ghost" onPress={() => void cancelPrompt(item.id)} testID={`pending-${item.id}-cancel`} />
              </View>
            )} />
        ))}
      </PanelSection>
      <Sheet visible={!!answering} onClose={() => setAnswering(null)} title="Answer prompt" testID="answer-sheet"
        footer={<>
          <Button label="Cancel" variant="secondary" onPress={() => setAnswering(null)} />
          <Button label="Send" variant="primary" onPress={() => void submitAnswer()} disabled={!answerText.trim()} testID="answer-send" />
        </>}>
        <View style={styles.sheet}>
          <Text variant="body" tone="ink2">{promptText(answering)}</Text>
          <TextField value={answerText} onChangeText={setAnswerText} placeholder="Your answer…" multiline style={styles.answer}
            accessibilityLabel="Your answer" testID="answer-text" />
        </View>
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', gap: spacing.sm },
  sheet: { gap: spacing.sm },
  answer: { minHeight: 100 },
});
