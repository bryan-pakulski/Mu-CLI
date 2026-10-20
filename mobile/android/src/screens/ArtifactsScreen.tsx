// Session files (web artifacts_panel.html): visualizations and files the agent published, and your
// uploads, in one place. Visualizations open in a hardened in-app viewer; everything downloads.

import React, { useCallback, useEffect, useState } from 'react';
import { AppState, StyleSheet, View } from 'react-native';
import * as DocumentPicker from 'expo-document-picker';
import { WebView } from 'react-native-webview';
import { artifactsApi, type ArtifactDescriptor } from '../api/artifacts';
import { openExternalUrl } from '../api/urlSafety';
import { attachmentsApi, type AttachmentDescriptor, type PickedDocument } from '../api/attachments';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { useConnectionStore } from '../store/connection';
import { paperColors, spacing } from '../theme/tokens';
import { IconButton, PrimaryPill, Row, Sheet, Text, useServerActions, useToast } from '../ui';

export function formatBytes(value: number): string {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

export function ArtifactsScreen() {
  const toast = useToast();
  const actions = useServerActions();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  const [artifacts, setArtifacts] = useState<ArtifactDescriptor[]>([]);
  const [uploads, setUploads] = useState<AttachmentDescriptor[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [viewingArtifact, setViewingArtifact] = useState<ArtifactDescriptor | null>(null);

  // Stable exact URL the artifact WebView is allowed to load (nav gate).
  const artifactUri = viewingArtifact && sessionName ? artifactsApi.viewUrl(sessionName, viewingArtifact.artifact_id) : '';

  const load = useCallback(async () => {
    if (!sessionName) { setArtifacts([]); setUploads([]); return; }
    try {
      const [artRes, attRes] = await Promise.all([artifactsApi.list(sessionName), attachmentsApi.list(sessionName)]);
      setArtifacts(artRes.artifacts || []);
      setUploads(attRes.attachments || []);
    } catch {
      // Keep previous data on transient error.
    }
  }, [sessionName]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const appState = AppState.addEventListener('change', state => { if (state === 'active') void load(); });
    return () => appState.remove();
  }, [load]);

  const download = (url: string) => { void openExternalUrl(url); };

  const confirmDelete = async (title: string, name: string, run: () => Promise<unknown>) => {
    if (!(await actions.ask({ id: 'delete', label: 'Delete', danger: true, confirm: `${title}\n${name}` }))) return;
    await run();
    void load();
  };

  const pickFiles = async () => {
    if (!sessionName) return;
    const result = await DocumentPicker.getDocumentAsync({ type: '*/*', multiple: true, copyToCacheDirectory: true });
    if (result.canceled) return;
    setUploading(true);
    try {
      for (const asset of result.assets) await attachmentsApi.upload(sessionName, asset as PickedDocument);
      await load();
    } catch (error) {
      toast(`Upload failed: ${String(error)}`, 'danger');
    } finally {
      setUploading(false);
    }
  };

  const visualizations = artifacts.filter(a => a.kind === 'visualization');
  const modelArtifacts = artifacts.filter(a => a.kind !== 'visualization');
  const total = visualizations.length + modelArtifacts.length + uploads.length;
  const fileActions = (label: string, onDownload: () => void, onDelete: () => void) => (
    <View style={styles.actions}>
      <IconButton icon="download" label={`Download ${label}`} onPress={onDownload} />
      <IconButton icon="trash-2" label={`Delete ${label}`} onPress={onDelete} />
    </View>
  );

  return (
    <PanelPage testID="artifacts-screen" title="Session files"
      state={[{ text: `${total} files` }, uploading ? { text: 'uploading…', tone: 'quiet' } : null]}
      empty={total === 0 ? { title: 'No files yet', message: 'Agent outputs, visualizations and your uploads will appear here in one place.' } : null}
      refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }}
      overlay={<>
        {sessionName ? <PrimaryPill label={uploading ? 'Uploading…' : 'Upload'} icon="cloud-upload" onPress={() => { if (!uploading) void pickFiles(); }} testID="artifacts-upload" /> : null}
        {actions.element}
      </>}>
      {visualizations.length ? (
        <PanelSection title="Visualizations" count={visualizations.length}>
          <Text variant="caption" tone="ink3">Interactive HTML published by the agent</Text>
          {visualizations.map(a => (
            <Row key={a.artifact_id} testID={`artifact-${a.artifact_id}`} title={a.title || a.name} sub={`${formatBytes(a.size)} · visualization`}
              onPress={() => setViewingArtifact(a)}
              actions={fileActions('visualization', () => sessionName && download(artifactsApi.downloadUrl(sessionName, a.artifact_id)),
                () => sessionName && void confirmDelete('Delete artifact?', a.name, () => artifactsApi.remove(sessionName, a.artifact_id)))} />
          ))}
        </PanelSection>
      ) : null}
      {modelArtifacts.length ? (
        <PanelSection title="Model artifacts" count={modelArtifacts.length}>
          <Text variant="caption" tone="ink3">Files published by the agent</Text>
          {modelArtifacts.map(a => (
            <Row key={a.artifact_id} testID={`artifact-${a.artifact_id}`} title={a.title || a.name} sub={`${formatBytes(a.size)} · ${a.mime_type || 'file'}`}
              onPress={() => sessionName && download(artifactsApi.downloadUrl(sessionName, a.artifact_id))}
              actions={fileActions('artifact', () => sessionName && download(artifactsApi.downloadUrl(sessionName, a.artifact_id)),
                () => sessionName && void confirmDelete('Delete artifact?', a.name, () => artifactsApi.remove(sessionName, a.artifact_id)))} />
          ))}
        </PanelSection>
      ) : null}
      {uploads.length ? (
        <PanelSection title="File uploads" count={uploads.length}>
          <Text variant="caption" tone="ink3">Your uploaded source files for this session</Text>
          {uploads.map(u => (
            <Row key={u.attachment_id} testID={`upload-${u.attachment_id}`} title={u.name} sub={`${formatBytes(u.size)} · ${u.mime_type || 'file'}`}
              onPress={() => sessionName && download(attachmentsApi.downloadUrl(sessionName, u.attachment_id))}
              actions={fileActions('upload', () => sessionName && download(attachmentsApi.downloadUrl(sessionName, u.attachment_id)),
                () => sessionName && void confirmDelete('Delete upload?', u.name, () => attachmentsApi.remove(sessionName, u.attachment_id)))} />
          ))}
        </PanelSection>
      ) : null}

      <Sheet visible={viewingArtifact !== null} onClose={() => setViewingArtifact(null)} title={viewingArtifact?.title || viewingArtifact?.name || 'Visualization'}
        full scroll={false} closeLabel="Close visualization" testID="artifact-viewer"
        headerRight={sessionName && viewingArtifact ? (
          <IconButton icon="download" label="Download visualization" onPress={() => download(artifactsApi.downloadUrl(sessionName, viewingArtifact.artifact_id))} />
        ) : undefined}>
        {sessionName && viewingArtifact ? (
          <WebView
            source={{ uri: artifactUri }}
            style={[styles.web, { backgroundColor: paperColors.raised }]}
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
              void openExternalUrl(request.url);
              return false;
            }}
          />
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  web: { flex: 1 },
});
