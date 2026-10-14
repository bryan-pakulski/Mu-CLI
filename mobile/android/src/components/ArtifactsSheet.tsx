import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, AppState, StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ArtifactDescriptor, artifactsApi } from '../api/artifacts';
import { openExternalUrl } from '../api/urlSafety';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';
import { fontWeights, radii, spacing, touchTarget, typography } from '../theme/tokens';

export function ArtifactsSheet({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const { colors } = useTheme();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  const [items, setItems] = useState<ArtifactDescriptor[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!sessionName) return setItems([]);
    setLoading(true);
    try {
      const response = await artifactsApi.list(sessionName);
      setItems(response.artifacts || []);
    } catch (error) {
      Alert.alert('Could not load artifacts', String(error));
    } finally {
      setLoading(false);
    }
  }, [sessionName]);

  useEffect(() => {
    if (!visible) return undefined;
    void load();
    const appState = AppState.addEventListener('change', state => {
      if (state === 'active') void load();
    });
    return () => appState.remove();
  }, [load, visible]);

  const remove = (artifact: ArtifactDescriptor) => {
    if (!sessionName) return;
    Alert.alert('Delete artifact?', artifact.name, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await artifactsApi.remove(sessionName, artifact.artifact_id);
          load();
        },
      },
    ]);
  };

  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Artifacts">
      {loading ? <ActivityIndicator color={colors.accent} style={styles.loader} /> : null}
      {!loading && items.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="document-attach-outline" size={24} color={colors.textDim} />
          <Text variant="base" style={styles.emptyTitle}>No artifacts yet</Text>
          <Text variant="sm" dim style={styles.emptyBody}>The agent can publish reports, archives, images, and other deliverables with upload_artifact.</Text>
        </View>
      ) : items.map(item => (
        <View key={item.artifact_id} style={styles.row}>
          <TouchableOpacity
            style={styles.open}
            onPress={() => sessionName && openExternalUrl(artifactsApi.downloadUrl(sessionName, item.artifact_id))}
          >
            <View style={[styles.icon, { backgroundColor: colors.bgHover }]}>
              <Ionicons name="document-outline" size={20} color={colors.textSoft} />
            </View>
            <View style={styles.copy}>
              <Text variant="sm" style={styles.name} numberOfLines={1}>{item.name}</Text>
              <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{formatBytes(item.size)} · {item.mime_type}</Text>
            </View>
            <Ionicons name="download-outline" size={19} color={colors.textDim} />
          </TouchableOpacity>
          <TouchableOpacity onPress={() => remove(item)} style={styles.delete}>
            <Ionicons name="trash-outline" size={18} color={colors.error} />
          </TouchableOpacity>
        </View>
      ))}
    </ModernBottomSheet>
  );
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

const styles = StyleSheet.create({
  loader: { marginVertical: spacing.lg },
  empty: { alignItems: 'center', paddingHorizontal: spacing.lg, paddingVertical: spacing.xl },
  emptyTitle: { fontWeight: fontWeights.semibold, marginTop: spacing.md },
  emptyBody: { textAlign: 'center', marginTop: spacing.xs, lineHeight: typography.sm.lineHeight },
  row: { minHeight: 64, flexDirection: 'row', alignItems: 'center' },
  open: { flex: 1, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm },
  icon: { width: 40, height: 40, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  copy: { flex: 1, marginHorizontal: spacing.md },
  name: { fontWeight: fontWeights.semibold },
  delete: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
});
