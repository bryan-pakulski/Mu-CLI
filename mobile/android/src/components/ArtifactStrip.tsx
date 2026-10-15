import React, { useCallback, useEffect, useState } from 'react';
import { AppState, ScrollView, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ArtifactDescriptor, artifactsApi } from '../api/artifacts';
import { openExternalUrl } from '../api/urlSafety';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, spacing } from '../theme/tokens';
import { Pressable, Text } from '../ui';

export function ArtifactStrip({
  sessionName,
  refreshKey = 0,
}: {
  sessionName: string | null;
  refreshKey?: number;
}) {
  const { colors } = useTheme();
  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);

  const load = useCallback(async () => {
    if (!sessionName) {
      setArtifacts([]);
      return;
    }
    try {
      const response = await artifactsApi.list(sessionName);
      const next = response.artifacts || [];
      setArtifacts(current => {
        const unchanged = current.length === next.length
          && current.every((item, index) => item.artifact_id === next[index]?.artifact_id
            && item.size === next[index]?.size);
        return unchanged ? current : next;
      });
    } catch {
      // Chat history remains useful when artifact refresh is temporarily unavailable.
    }
  }, [sessionName]);

  useEffect(() => {
    void load();
    if (!sessionName) return undefined;
    const timer = setInterval(() => { void load(); }, 5000);
    const appState = AppState.addEventListener('change', state => {
      if (state === 'active') void load();
    });
    return () => {
      clearInterval(timer);
      appState.remove();
    };
  }, [load, sessionName, refreshKey]);

  const visualizations = artifacts.filter(a => a.kind === 'visualization');
  const files = artifacts.filter(a => a.kind !== 'visualization');

  if (!sessionName || artifacts.length === 0) return null;

  return (
    <View style={styles.wrap}>
      {visualizations.length > 0 && (
        <View style={styles.section}>
          <View style={styles.heading}>
            <Text variant="xs" dim style={styles.label}>Visualizations</Text>
            <Text variant="xs" dim>{visualizations.length}</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
            {visualizations.map(artifact => {
              const isViz = artifact.kind === 'visualization';
              return (
                <Pressable
                  key={artifact.artifact_id}
                  onPress={() => openExternalUrl(artifactsApi.viewUrl(sessionName, artifact.artifact_id))}
                  style={[styles.chip, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}
                >
                  <Ionicons name="stats-chart-outline" size={16} color={colors.ink2} />
                  <View style={styles.copy}>
                    <Text variant="xs" style={styles.name} numberOfLines={1}>
                      {artifact.title || artifact.name}
                    </Text>
                    <Text variant="xs" dim>{formatBytes(artifact.size)}</Text>
                  </View>
                  <Ionicons name="open-outline" size={17} color={colors.ink3} />
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      )}

      {files.length > 0 && (
        <View style={styles.section}>
          <View style={styles.heading}>
            <Text variant="xs" dim style={styles.label}>Files</Text>
            <Text variant="xs" dim>{files.length}</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
            {files.map(artifact => (
              <Pressable
                key={artifact.artifact_id}
                onPress={() => openExternalUrl(artifactsApi.downloadUrl(sessionName, artifact.artifact_id))}
                style={[styles.chip, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}
              >
                <Ionicons name="document-attach-outline" size={16} color={colors.ink2} />
                <View style={styles.copy}>
                  <Text variant="xs" style={styles.name} numberOfLines={1}>{artifact.name}</Text>
                  <Text variant="xs" dim>{formatBytes(artifact.size)}</Text>
                </View>
                <Ionicons name="arrow-down-circle-outline" size={17} color={colors.ink3} />
              </Pressable>
            ))}
          </ScrollView>
        </View>
      )}
    </View>
  );
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

const styles = StyleSheet.create({
  wrap: { paddingTop: spacing.sm, paddingBottom: spacing.xs },
  section: { paddingBottom: spacing.xs },
  heading: { paddingHorizontal: spacing.base, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  label: { fontWeight: fontWeights.medium },
  row: { paddingHorizontal: spacing.base, gap: spacing.sm },
  chip: { width: 210, minHeight: 56, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  copy: { flex: 1 },
  name: { fontWeight: fontWeights.semibold },
});