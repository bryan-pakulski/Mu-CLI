import React from 'react';
import { StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

export type SessionStartPromptProps = {
  onLoadSession: () => void;
  onCreateSession: () => void;
};

export function SessionStartPrompt({ onLoadSession, onCreateSession }: SessionStartPromptProps) {
  const { colors } = useTheme();

  return (
    <View style={[styles.root, { backgroundColor: colors.bg }]}>
      <View style={styles.content}>
        <View style={[styles.mark, { backgroundColor: colors.bgHover }]}>
          <Text style={[styles.glyph, { color: colors.text }]}>μ</Text>
        </View>
        <Text style={[styles.title, { color: colors.text }]}>Load or create a session to get started</Text>
        <Text variant="sm" dim style={styles.subtitle}>
          The MuCLI server is connected. Choose an existing session or create a new workspace-backed session.
        </Text>

        <View style={styles.actions}>
          <TouchableOpacity
            onPress={onLoadSession}
            activeOpacity={0.72}
            style={[styles.primaryButton, { backgroundColor: colors.text }]}
          >
            <Ionicons name="folder-open-outline" size={19} color={colors.bg} />
            <Text style={{ color: colors.bg, fontWeight: '700' }}>Load session</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={onCreateSession}
            activeOpacity={0.72}
            style={[styles.secondaryButton, { backgroundColor: colors.bgLift }]}
          >
            <Ionicons name="add" size={19} color={colors.text} />
            <Text style={{ color: colors.text, fontWeight: '700' }}>Create session</Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'center', paddingHorizontal: 26 },
  content: { width: '100%', maxWidth: 430, alignSelf: 'center', alignItems: 'center' },
  mark: { width: 54, height: 54, borderRadius: 19, alignItems: 'center', justifyContent: 'center', marginBottom: 20 },
  glyph: { fontSize: 27, fontWeight: '700' },
  title: { maxWidth: 360, textAlign: 'center', fontSize: 24, lineHeight: 31, fontWeight: '700', letterSpacing: -0.7 },
  subtitle: { maxWidth: 360, textAlign: 'center', lineHeight: 21, marginTop: 10 },
  actions: { width: '100%', gap: 9, marginTop: 28 },
  primaryButton: { minHeight: 50, borderRadius: 16, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  secondaryButton: { minHeight: 50, borderRadius: 16, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
});
