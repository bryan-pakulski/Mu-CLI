import React, { useState, useCallback } from 'react';
import { View, TextInput, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { audioApi } from '../api/audio';
import { spacing, fontWeights, radii } from '../theme/tokens';
import { Button, Card, EmptyState, ErrorState, Text } from '../ui';

export function AudioScreen() {
  const { colors } = useTheme();
  const { activeSessionName } = useConnectionStore();
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tts = useCallback(async () => {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const blob = await audioApi.tts(text.trim(), undefined, activeSessionName || undefined);
      Alert.alert('TTS', `Generated ${blob.size} bytes of audio`);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [text, activeSessionName]);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
      <View style={{ padding: spacing.base }}>
        <Text variant="lg" style={{ fontWeight: fontWeights.semibold, marginBottom: spacing.sm }}>Audio</Text>

        <Card style={{ marginBottom: spacing.sm }}>
          <Text variant="base" style={{ fontWeight: fontWeights.medium, marginBottom: spacing.sm }}>Text-to-Speech</Text>
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder="Enter text to synthesize…"
            placeholderTextColor={colors.ink3}
            multiline
            style={{ color: colors.ink, borderWidth: 1, borderColor: colors.hairline, borderRadius: radii.sm, padding: spacing.md, minHeight: 100, marginBottom: spacing.sm }}
          />
          <Button title={loading ? 'Generating…' : 'Generate TTS'} onPress={tts} disabled={loading || !text.trim()} />
        </Card>

        <Card style={{ marginBottom: spacing.sm }}>
          <Text variant="base" style={{ fontWeight: fontWeights.medium, marginBottom: spacing.sm }}>Speech-to-Text</Text>
          <Text variant="xs" style={{ color: colors.ink3 }}>
            STT requires recording audio. Connect a microphone and use the record button below.
          </Text>
          <Button
            title="Record (not available on this device)"
            variant="ghost"
            disabled
            onPress={() => {}}
          />
        </Card>

        {error && <ErrorState message={error} onRetry={() => setError(null)} />}
      </View>
    </SafeAreaView>
  );
}