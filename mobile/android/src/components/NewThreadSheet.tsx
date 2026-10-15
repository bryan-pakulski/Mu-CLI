import React, { useEffect, useState } from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { threadsApi, type CreateThreadResponse } from '../api/threads';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, radii, spacing, typography } from '../theme/tokens';
import { Button, Sheet, Text } from '../ui';

export type NewThreadSheetProps = {
  visible: boolean;
  parentSessionName: string | null;
  onClose: () => void;
  onCreated: (thread: CreateThreadResponse) => void;
};

export function NewThreadSheet({
  visible,
  parentSessionName,
  onClose,
  onCreated,
}: NewThreadSheetProps) {
  const { colors } = useTheme();
  const [title, setTitle] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setTitle('');
    setCreating(false);
    setError(null);
  }, [visible]);

  const create = async () => {
    const cleanTitle = title.trim();
    if (!parentSessionName || !cleanTitle || creating) return;
    setCreating(true);
    setError(null);
    try {
      const response = await threadsApi.create({
        parentSessionName,
        title: cleanTitle,
        activate: true,
      }, { timeoutMs: 30_000 });
      onCreated(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCreating(false);
    }
  };

  return (
    <Sheet visible={visible} onClose={onClose} title="New thread">
      <View style={styles.content}>
        <Text variant="sm" dim style={styles.intro}>
          Start an independent agent conversation that shares the environment from{' '}
          <Text variant="sm" style={styles.parentName}>{parentSessionName || 'the current session'}</Text>.
          Chat history and working memory start clean.
        </Text>
        <Text variant="xs" style={[styles.label, { color: colors.ink3 }]}>Thread title</Text>
        <TextInput
          testID="thread-title-input"
          value={title}
          onChangeText={setTitle}
          placeholder="Investigate the parser"
          placeholderTextColor={colors.ink3}
          maxLength={120}
          autoCapitalize="sentences"
          autoCorrect
          returnKeyType="done"
          onSubmitEditing={() => void create()}
          style={[
            styles.input,
            {
              color: colors.ink,
              backgroundColor: colors.surface,
              borderColor: colors.hairline,
            },
          ]}
        />
        <Text variant="xs" dim style={styles.help}>
          The new thread inherits provider, mode, workspace, and container access.
        </Text>
        {error ? <Text variant="xs" style={[styles.error, { color: colors.danger.ink }]}>{error}</Text> : null}
        <View style={styles.actions}>
          <Button title="Cancel" variant="ghost" onPress={onClose} disabled={creating} style={styles.action} />
          <Button
            title="Create thread"
            onPress={() => void create()}
            disabled={!parentSessionName || !title.trim()}
            loading={creating}
            style={styles.action}
          />
        </View>
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  content: { paddingTop: spacing.md, paddingBottom: spacing.sm },
  intro: { lineHeight: typography.base.lineHeight, marginBottom: spacing.lg },
  parentName: { fontWeight: fontWeights.semibold },
  label: { fontWeight: fontWeights.semibold, marginBottom: spacing.sm },
  input: {
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderWidth: 1,
    borderRadius: radii.sm,
    fontSize: fontSizes.md,
  },
  help: { lineHeight: typography.xs.lineHeight, marginTop: spacing.sm },
  error: { lineHeight: typography.xs.lineHeight, marginTop: spacing.md },
  actions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.lg },
  action: { flex: 1 },
});
