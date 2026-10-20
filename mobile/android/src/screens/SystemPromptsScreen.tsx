// System prompts (web system_prompts_panel.html): every prompt with its size and source; edit an
// override in a full-screen sheet, or reset it to the default after confirming.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { systemPromptsApi, type SystemPromptInfo } from '../api/systemPrompts';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Button, Chip, Row, Sheet, TextField, useServerActions, useToast } from '../ui';

export function SystemPromptsScreen() {
  const toast = useToast();
  const actions = useServerActions();
  const [prompts, setPrompts] = useState<SystemPromptInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [editing, setEditing] = useState<{ name: string; text: string; version: number | null } | null>(null);
  const [editText, setEditText] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try { setError(null); setPrompts((await systemPromptsApi.list()).items); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const openEdit = async (name: string) => {
    try {
      const detail = await systemPromptsApi.get(name);
      setEditing({ name, text: detail.text, version: detail.version });
      setEditText(detail.text);
    } catch (e) {
      toast(`Couldn't load ${name}: ${String(e)}`, 'danger');
    }
  };

  const saveEdit = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await systemPromptsApi.put(editing.name, editText, editing.version || undefined);
      setEditing(null);
      toast(`Saved ${editing.name}`, 'ok');
      void load();
    } catch (e) {
      toast(`Couldn't save: ${String(e)}`, 'danger');
    } finally {
      setSaving(false);
    }
  };

  const resetPrompt = async (name: string) => {
    if (!(await actions.ask({ id: 'reset', label: 'Reset', danger: true, confirm: `Reset "${name}" to default? This removes your override.` }))) return;
    await systemPromptsApi.reset(name);
    void load();
  };

  const overrides = prompts.filter(p => p.has_override).length;
  return (
    <PanelPage testID="system-prompts-screen" title="System prompts"
      state={[{ text: `${prompts.length} prompts` }, overrides ? { text: `${overrides} overridden`, tone: 'attention' } : null]}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && !prompts.length ? { title: 'No system prompts', message: 'No system prompts configured.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={actions.element}>
      <PanelSection title="Prompts" count={prompts.length}>
        {prompts.map(item => (
          <Row key={item.name} testID={`prompt-${item.name}`} title={item.name} sub={`${item.chars} chars · ${item.source}`}
            onPress={() => void openEdit(item.name)}
            actions={(
              <View style={styles.actions}>
                {item.has_override ? <Chip variant="meta" label="Override" /> : null}
                <Button label="Edit" variant="secondary" onPress={() => void openEdit(item.name)} />
                {item.has_override ? <Button label="Reset" variant="ghost" onPress={() => void resetPrompt(item.name)} testID={`prompt-${item.name}-reset`} /> : null}
              </View>
            )} />
        ))}
      </PanelSection>
      <Sheet visible={!!editing} onClose={() => setEditing(null)} title={editing?.name} full testID="prompt-editor"
        footer={<>
          <Button label="Cancel" variant="secondary" onPress={() => setEditing(null)} />
          <Button label={saving ? 'Saving…' : 'Save'} variant="primary" onPress={() => void saveEdit()} disabled={!editText.trim() || saving} loading={saving} testID="prompt-save" />
        </>}>
        <TextField value={editText} onChangeText={setEditText} placeholder="Prompt text…" multiline textAlignVertical="top" mono
          style={styles.editor} accessibilityLabel={`${editing?.name || ''} prompt text`} testID="prompt-text" />
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  editor: { minHeight: 320 },
});
