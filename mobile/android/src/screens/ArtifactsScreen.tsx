import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, AppState, FlatList, Modal, RefreshControl, StyleSheet, useWindowDimensions, View } from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import * as DocumentPicker from 'expo-document-picker';
import { WebView } from 'react-native-webview';
import { ArtifactDescriptor, artifactsApi } from '../api/artifacts';
import { openExternalUrl } from '../api/urlSafety';
import { attachmentsApi, type AttachmentDescriptor, type PickedDocument } from '../api/attachments';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, paperColors, radii, spacing, touchTarget } from '../theme/tokens';
import { EmptyState, ModalFrame, Pressable, Text, Icon } from '../ui';

type UnifiedItem =
  | { kind: 'visualization'; artifact: ArtifactDescriptor }
  | { kind: 'model'; artifact: ArtifactDescriptor }
  | { kind: 'upload'; attachment: AttachmentDescriptor };

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

export function ArtifactsScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const sessionName = useConnectionStore(state => state.activeSessionName);

  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);
  const [uploads, setUploads] = useState<AttachmentDescriptor[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [viewingArtifact, setViewingArtifact] = useState<ArtifactDescriptor | null>(null);

  // Stable exact URL the artifact WebView is allowed to load (nav gate).
  const artifactUri = viewingArtifact
    ? artifactsApi.viewUrl(sessionName as string, viewingArtifact.artifact_id)
    : '';

  const load = useCallback(async () => {
    if (!sessionName) {
      setArtifacts([]);
      setUploads([]);
      return;
    }
    try {
      const [artRes, attRes] = await Promise.all([
        artifactsApi.list(sessionName),
        attachmentsApi.list(sessionName),
      ]);
      setArtifacts(artRes.artifacts || []);
      setUploads(attRes.attachments || []);
    } catch {
      // Keep previous data on transient error.
    }
  }, [sessionName]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const appState = AppState.addEventListener('change', state => {
      if (state === 'active') void load();
    });
    return () => appState.remove();
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const removeArtifact = (artifact: ArtifactDescriptor) => {
    if (!sessionName) return;
    Alert.alert('Delete artifact?', artifact.name, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await artifactsApi.remove(sessionName, artifact.artifact_id);
          void load();
        },
      },
    ]);
  };

  const removeUpload = (item: AttachmentDescriptor) => {
    if (!sessionName) return;
    Alert.alert('Delete upload?', item.name, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await attachmentsApi.remove(sessionName, item.attachment_id);
          void load();
        },
      },
    ]);
  };

  const pickFiles = async () => {
    if (!sessionName) return;
    const result = await DocumentPicker.getDocumentAsync({
      type: '*/*',
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (result.canceled) return;
    setUploading(true);
    try {
      for (const asset of result.assets) {
        await attachmentsApi.upload(sessionName, asset as PickedDocument);
      }
      await load();
    } catch (error) {
      Alert.alert('Upload failed', String(error));
    } finally {
      setUploading(false);
    }
  };

  // Build unified list: visualizations first, then model artifacts, then uploads
  const visualizations = artifacts.filter(a => a.kind === 'visualization');
  const modelArtifacts = artifacts.filter(a => a.kind !== 'visualization');
  const totalCount = visualizations.length + modelArtifacts.length + uploads.length;

  const sections: { title: string; subtitle: string; count: number }[] = [
    { title: 'Visualizations', subtitle: 'Interactive HTML published by the agent', count: visualizations.length },
    { title: 'Model artifacts', subtitle: 'Files published by the agent', count: modelArtifacts.length },
    { title: 'File uploads', subtitle: 'Your uploaded source files for this session', count: uploads.length },
  ];

  const items: (UnifiedItem | { kind: 'header'; title: string; subtitle: string; count: number })[] = [
    ...visualizations.map(a => ({ kind: 'visualization' as const, artifact: a })),
    ...modelArtifacts.map(a => ({ kind: 'model' as const, artifact: a })),
    ...uploads.map(a => ({ kind: 'upload' as const, attachment: a })),
  ];

  const renderItem = ({ item }: { item: UnifiedItem | { kind: 'header'; title: string; subtitle: string; count: number } }) => {
    if (item.kind === 'header') {
      return (
        <View style={styles.sectionHeader}>
          <View style={{ flex: 1 }}>
            <Text variant="xs" style={{ fontWeight: fontWeights.semibold, color: colors.ink3 }}>
              {item.title}
            </Text>
            <Text variant="xs" dim style={{ marginTop: 2 }}>
              {item.subtitle}
            </Text>
          </View>
          <View style={[styles.countBadge, { backgroundColor: colors.sunken }]}>
            <Text variant="xs" style={{ fontWeight: fontWeights.medium, color: colors.ink2, fontVariant: ['tabular-nums'] }}>{item.count}</Text>
          </View>
        </View>
      );
    }

    if (item.kind === 'visualization') {
      const a = item.artifact;
      return (
        <Pressable
          onPress={() => setViewingArtifact(a)}
          style={styles.row}
          activeOpacity={0.7}
        >
          <View style={[styles.rowIcon, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
            <Icon name="bar-chart-3" size="md" color={colors.ink2} />
          </View>
          <View style={styles.rowCopy}>
            <Text variant="sm" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>
              {a.title || a.name}
            </Text>
            <Text variant="xs" dim numberOfLines={1}>
              {formatBytes(a.size)} · visualization
            </Text>
          </View>
          <Pressable
            onPress={() => sessionName && openExternalUrl(artifactsApi.downloadUrl(sessionName, a.artifact_id))}
            style={styles.rowAction}
            hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
            accessibilityRole="button"
            accessibilityLabel="Download visualization"
          >
            <Icon name="download" size="md" color={colors.ink3} />
          </Pressable>
          <Pressable accessibilityLabel="Delete artifact"
            onPress={() => removeArtifact(a)}
            style={styles.rowAction}
            hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
          >
            <Icon name="trash-2" size="md" color={colors.danger.ink} />
          </Pressable>
        </Pressable>
      );
    }

    if (item.kind === 'model') {
      const a = item.artifact;
      return (
        <Pressable
          onPress={() => sessionName && openExternalUrl(artifactsApi.downloadUrl(sessionName, a.artifact_id))}
          style={styles.row}
          activeOpacity={0.7}
        >
          <View style={[styles.rowIcon, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
            <Icon name="paperclip" size="md" color={colors.ink2} />
          </View>
          <View style={styles.rowCopy}>
            <Text variant="sm" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>
              {a.title || a.name}
            </Text>
            <Text variant="xs" dim numberOfLines={1}>
              {formatBytes(a.size)} · {a.mime_type || 'file'}
            </Text>
          </View>
          <Pressable accessibilityLabel="Download artifact"
            onPress={() => sessionName && openExternalUrl(artifactsApi.downloadUrl(sessionName, a.artifact_id))}
            style={styles.rowAction}
            hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
          >
            <Icon name="download" size="md" color={colors.ink3} />
          </Pressable>
          <Pressable accessibilityLabel="Delete artifact"
            onPress={() => removeArtifact(a)}
            style={styles.rowAction}
            hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
          >
            <Icon name="trash-2" size="md" color={colors.danger.ink} />
          </Pressable>
        </Pressable>
      );
    }

    // upload
    const u = item.attachment;
    return (
      <Pressable
        onPress={() => sessionName && openExternalUrl(attachmentsApi.downloadUrl(sessionName, u.attachment_id))}
        style={styles.row}
        activeOpacity={0.7}
      >
        <View style={[styles.rowIcon, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
          <Icon name="cloud-upload" size="md" color={colors.ink2} />
        </View>
        <View style={styles.rowCopy}>
          <Text variant="sm" style={{ fontWeight: fontWeights.semibold }} numberOfLines={1}>
            {u.name}
          </Text>
          <Text variant="xs" dim numberOfLines={1}>
            {formatBytes(u.size)} · {u.mime_type || 'file'}
          </Text>
        </View>
        <Pressable accessibilityLabel="Download upload"
          onPress={() => sessionName && openExternalUrl(attachmentsApi.downloadUrl(sessionName, u.attachment_id))}
          style={styles.rowAction}
          hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
        >
          <Icon name="download" size="md" color={colors.ink3} />
        </Pressable>
        <Pressable accessibilityLabel="Delete upload"
          onPress={() => removeUpload(u)}
          style={styles.rowAction}
          hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
        >
          <Icon name="trash-2" size="md" color={colors.danger.ink} />
        </Pressable>
      </Pressable>
    );
  };

  // Build flat list with section headers interleaved
  type FlatItem = UnifiedItem | { kind: 'header'; title: string; subtitle: string; count: number };
  const flatData: FlatItem[] = [];
  let sectionIdx = 0;
  if (visualizations.length > 0) {
    flatData.push({ kind: 'header', ...sections[0] });
    visualizations.forEach(a => flatData.push({ kind: 'visualization', artifact: a }));
    sectionIdx++;
  }
  if (modelArtifacts.length > 0) {
    flatData.push({ kind: 'header', ...sections[1] });
    modelArtifacts.forEach(a => flatData.push({ kind: 'model', artifact: a }));
    sectionIdx++;
  }
  if (uploads.length > 0) {
    flatData.push({ kind: 'header', ...sections[2] });
    uploads.forEach(a => flatData.push({ kind: 'upload', attachment: a }));
    sectionIdx++;
  }

  return (
    <SafeAreaView edges={['top']} style={{ flex: 1, backgroundColor: 'transparent' }}>
      {/* Header */}
      <View style={[styles.header, { borderBottomColor: colors.hairline, paddingTop: insets.top + spacing.xs }]}>
        <Text variant="lg" style={{ fontWeight: fontWeights.semibold }}>Session files</Text>
        <View style={styles.headerActions}>
          <Pressable
            onPress={pickFiles}
            disabled={uploading || !sessionName}
            style={[styles.uploadBtn, { backgroundColor: colors.accent.ink, opacity: uploading || !sessionName ? 0.5 : 1 }]}
          >
            {uploading ? (
              <ActivityIndicator color={colors.onInk} size="small" />
            ) : (
              <Icon name="cloud-upload" size="md" color={colors.onInk} />
            )}
            <Text variant="xs" style={{ color: colors.onInk, fontWeight: fontWeights.semibold, marginLeft: spacing.xs }}>
              {uploading ? 'Uploading' : 'Upload'}
            </Text>
          </Pressable>
        </View>
      </View>

      {/* List */}
      <FlatList
        data={flatData}
        keyExtractor={(item, index) => {
          if (item.kind === 'header') return `header-${item.title}`;
          if (item.kind === 'visualization' || item.kind === 'model') return `art-${item.artifact.artifact_id}`;
          return `att-${item.attachment.attachment_id}`;
        }}
        renderItem={renderItem}
        contentContainerStyle={{ paddingBottom: spacing['2xl'] }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent.ink} />}
        ListEmptyComponent={
          totalCount === 0 ? (
            <EmptyState
              icon="folder-open"
              title="No files yet"
              message="Agent outputs, visualizations, and your uploads will appear here in one place."
            />
          ) : null
        }
      />

      {/* In-app visualization viewer */}
      <ModalFrame transparent={false}
        visible={viewingArtifact !== null}
        animationType="fade"
        presentationStyle="fullScreen"
        statusBarTranslucent
        onRequestClose={() => setViewingArtifact(null)}
      >
        <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
          <View style={[styles.visHeader, { borderBottomColor: colors.hairline }]}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text variant="xs" dim style={{ fontWeight: fontWeights.semibold }}>
                VISUALIZATION
              </Text>
              <Text numberOfLines={1} style={{ fontSize: fontSizes.base, fontWeight: fontWeights.semibold, color: colors.ink }}>
                {viewingArtifact?.title || viewingArtifact?.name || ''}
              </Text>
            </View>
            {sessionName && viewingArtifact && (
              <Pressable
                onPress={() => openExternalUrl(artifactsApi.downloadUrl(sessionName, viewingArtifact.artifact_id))}
                style={[styles.iconButton, { backgroundColor: colors.sunken }]}
                accessibilityRole="button"
                accessibilityLabel="Download visualization"
              >
                <Icon name="download" size="md" color={colors.ink3} />
              </Pressable>
            )}
            <Pressable
              onPress={() => setViewingArtifact(null)}
              style={[styles.closeButton, { backgroundColor: colors.sunken }]}
              accessibilityRole="button"
              accessibilityLabel="Close visualization"
            >
              <Icon name="x" size="lg" color={colors.ink} />
            </Pressable>
          </View>
          {sessionName && viewingArtifact && (
            <WebView
              source={{ uri: artifactUri }}
              style={{ flex: 1, backgroundColor: paperColors.raised }}
              originWhitelist={['http://*', 'https://*']}
              javaScriptEnabled
              domStorageEnabled
              cacheEnabled={false}
              // Same hardening as VisualizationCard's InteractiveWebView: an
              // artifact is untrusted active content — confine it to the exact
              // backend URL, no cookies/file access, no mixed content.
              incognito
              sharedCookiesEnabled={false}
              thirdPartyCookiesEnabled={false}
              allowFileAccess={false}
              allowUniversalAccessFromFileURLs={false}
              javaScriptCanOpenWindowsAutomatically={false}
              setSupportMultipleWindows={false}
              mixedContentMode="never"
              scrollEnabled
              nestedScrollEnabled
              showsVerticalScrollIndicator
              showsHorizontalScrollIndicator
              onShouldStartLoadWithRequest={(request) => {
                if (request.url === artifactUri || request.url === 'about:blank') return true;
                // Any other navigation attempt goes to the OS browser instead
                // (http/https only — see openExternalUrl).
                openExternalUrl(request.url);
                return false;
              }}
            />
          )}
        </SafeAreaView>
      </ModalFrame>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingBottom: spacing.md,
    borderBottomWidth: 1,
  },
  headerActions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  uploadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    minHeight: touchTarget,
    borderRadius: radii.sm,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    paddingTop: spacing.lg,
    paddingBottom: spacing.sm,
  },
  countBadge: {
    minWidth: 24,
    height: 24,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 64,
    paddingHorizontal: spacing.base,
  },
  rowIcon: {
    width: 40,
    height: 40,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowCopy: {
    flex: 1,
    marginLeft: spacing.md,
  },
  rowAction: {
    width: touchTarget,
    height: touchTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  visHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
  },
  iconButton: {
    width: touchTarget,
    height: touchTarget,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeButton: {
    width: touchTarget,
    height: touchTarget,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
});