import React, { useCallback, useEffect, useState } from 'react';
import { Linking, ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ArtifactDescriptor, artifactsApi } from '../api/artifacts';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

export function ArtifactStrip({ sessionName }: { sessionName: string | null }) {
  const { colors } = useTheme();
  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);

  const load = useCallback(async () => {
    if (!sessionName) {
      setArtifacts([]);
      return;
    }
    try {
      const response = await artifactsApi.list(sessionName);
      setArtifacts(response.artifacts || []);
    } catch {
      // Chat history remains useful when artifact refresh is temporarily unavailable.
    }
  }, [sessionName]);

  useEffect(() => {
    load();
    if (!sessionName) return undefined;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load, sessionName]);

  if (!sessionName || artifacts.length === 0) return null;

  return (
    <View style={styles.wrap}>
      <Text variant="xs" dim style={styles.label}>ARTIFACTS</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
        {artifacts.map(artifact => (
          <TouchableOpacity
            key={artifact.artifact_id}
            onPress={() => Linking.openURL(artifactsApi.downloadUrl(sessionName, artifact.artifact_id))}
            style={[styles.chip, { backgroundColor: colors.bgHover, borderColor: colors.border }]}
          >
            <Ionicons name="document-attach-outline" size={16} color={colors.accent} />
            <View style={styles.copy}>
              <Text variant="xs" style={styles.name} numberOfLines={1}>{artifact.name}</Text>
              <Text variant="xs" dim>{formatBytes(artifact.size)}</Text>
            </View>
            <Ionicons name="arrow-down-circle-outline" size={17} color={colors.textDim} />
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

const styles = StyleSheet.create({
  wrap: { paddingTop: 12, paddingBottom: 4 },
  label: { paddingHorizontal: 16, marginBottom: 7, fontWeight: '700', letterSpacing: 0.8 },
  row: { paddingHorizontal: 16, gap: 8 },
  chip: { width: 210, minHeight: 54, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', gap: 9 },
  copy: { flex: 1 },
  name: { fontWeight: '600' },
});
