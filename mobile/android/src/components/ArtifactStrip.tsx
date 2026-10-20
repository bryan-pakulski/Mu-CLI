// The session's files above the composer: visualizations open in the in-app viewer, files download
// with the device token to the share sheet. It collapses into a small Files button at the right
// edge (remembered on this phone) so the conversation gets the room back.
import React, { useCallback, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AppState, ScrollView, StyleSheet, View } from 'react-native';
import { ArtifactDescriptor, artifactsApi } from '../api/artifacts';
import { shareServerFile } from '../api/artifactDownload';
import { navigateTo } from '../navigation/nav';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, spacing } from '../theme/tokens';
import { Pressable, Text, Icon, IconButton, useToast } from '../ui';

export const FILES_STRIP_COLLAPSED_KEY = '@mucli/files-strip-collapsed';

export function ArtifactStrip({
  sessionName,
  refreshKey = 0,
}: {
  sessionName: string | null;
  refreshKey?: number;
}) {
  const { colors } = useTheme();
  const toast = useToast();
  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);
  const [collapsed, setCollapsedState] = useState(false);
  useEffect(() => {
    AsyncStorage.getItem(FILES_STRIP_COLLAPSED_KEY).then(v => { if (v === '1') setCollapsedState(true); }).catch(() => undefined);
  }, []);
  const setCollapsed = (next: boolean) => {
    setCollapsedState(next);
    AsyncStorage.setItem(FILES_STRIP_COLLAPSED_KEY, next ? '1' : '0').catch(() => undefined);
  };
  // Server files need this phone's device token, which a browser doesn't have: visualizations
  // open in the in-app viewer, files download with the token and go to the share sheet.
  const share = (artifact: ArtifactDescriptor) => {
    if (!sessionName) return;
    shareServerFile(artifactsApi.downloadUrl(sessionName, artifact.artifact_id), artifact.name, artifact.mime_type)
      .catch(error => toast(`Couldn\u2019t download ${artifact.name}: ${error instanceof Error ? error.message : String(error)}`, 'danger'));
  };

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

  // In the first section's heading row, beside its count.
  const collapseButton = (
    <IconButton icon="chevron-down" label="Collapse session files" onPress={() => setCollapsed(true)} testID="files-strip-collapse" style={styles.collapse} />
  );

  if (collapsed) {
    // Takes no layout space: the button floats at the right edge just above the composer.
    return (
      <View style={styles.dock} pointerEvents="box-none" testID="files-strip-collapsed">
        <Pressable onPress={() => setCollapsed(false)} accessibilityRole="button"
          accessibilityLabel={`Show session files, ${artifacts.length}`} testID="files-strip-expand"
          style={[styles.fab, { backgroundColor: colors.raised, borderColor: colors.hairline, shadowColor: colors.shadow }]}>
          <Icon name="file" size="sm" color={colors.ink2} />
          <Text variant="caption" numeric style={{ color: colors.ink2 }}>{artifacts.length}</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.wrap} testID="files-strip">
      {visualizations.length > 0 && (
        <View style={styles.section}>
          <View style={styles.heading}>
            <Text variant="xs" dim style={styles.label}>Visualizations</Text>
            <View style={styles.headRight}>
              <Text variant="xs" dim>{visualizations.length}</Text>
              {collapseButton}
            </View>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
            {visualizations.map(artifact => {
              const isViz = artifact.kind === 'visualization';
              return (
                <Pressable
                  key={artifact.artifact_id}
                  onPress={() => navigateTo('Artifacts', { view: artifact.artifact_id })}
                  style={[styles.chip, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}
                >
                  <Icon name="bar-chart-3" size="sm" color={colors.ink2} />
                  <View style={styles.copy}>
                    <Text variant="xs" style={styles.name} numberOfLines={1}>
                      {artifact.title || artifact.name}
                    </Text>
                    <Text variant="xs" dim>{formatBytes(artifact.size)}</Text>
                  </View>
                  <Icon name="maximize-2" size="sm" color={colors.ink3} />
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
            <View style={styles.headRight}>
              <Text variant="xs" dim>{files.length}</Text>
              {visualizations.length ? null : collapseButton}
            </View>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
            {files.map(artifact => (
              <Pressable
                key={artifact.artifact_id}
                onPress={() => share(artifact)}
                style={[styles.chip, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}
              >
                <Icon name="paperclip" size="sm" color={colors.ink2} />
                <View style={styles.copy}>
                  <Text variant="xs" style={styles.name} numberOfLines={1}>{artifact.name}</Text>
                  <Text variant="xs" dim>{formatBytes(artifact.size)}</Text>
                </View>
                <Icon name="download" size="sm" color={colors.ink3} />
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
  headRight: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  // Compact in the heading row; Pressable extends the hit area to the 44pt target.
  collapse: { width: 28, height: 28 },
  dock: { height: 0, overflow: 'visible', zIndex: 2 },
  fab: { position: 'absolute', bottom: spacing.sm, right: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    minHeight: 36, paddingHorizontal: spacing.md, borderRadius: radii.pill, borderWidth: 1, elevation: 3,
    shadowOpacity: 0.12, shadowRadius: 8, shadowOffset: { width: 0, height: 2 } },
  section: { paddingBottom: spacing.xs },
  heading: { paddingHorizontal: spacing.base, marginBottom: spacing.xs, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  label: { fontWeight: fontWeights.medium },
  row: { paddingHorizontal: spacing.base, gap: spacing.sm },
  chip: { width: 210, minHeight: 56, borderRadius: radii.sm, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  copy: { flex: 1 },
  name: { fontWeight: fontWeights.semibold },
});