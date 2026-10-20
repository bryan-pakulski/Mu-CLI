// Files (web files_panel.html): browse the workspace tree; folders open in place, files open
// read-only in a full-screen sheet.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { filesApi, type FileEntry } from '../api/files';
import { PanelPage } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Button, Pre, Row, Rows, Sheet, Text, useToast } from '../ui';

export function fileSize(size: number | null | undefined): string | undefined {
  if (size == null) return undefined;
  return size > 1024 ? `${Math.round(size / 1024)}KB` : `${size}B`;
}

export function parentPath(path: string | undefined): string | undefined {
  if (!path) return undefined;
  const parts = path.split('/');
  parts.pop();
  return parts.length > 1 ? parts.join('/') : undefined;
}

export function FilesScreen() {
  const toast = useToast();
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [currentPath, setCurrentPath] = useState<string | undefined>(undefined);
  const [fileContent, setFileContent] = useState<{ path: string; content: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (path?: string) => {
    try {
      setError(null);
      const res = await filesApi.getTree(path);
      setEntries(res.roots || res.entries || []);
      setCurrentPath(res.path || path);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const openEntry = async (entry: FileEntry) => {
    if (entry.is_dir) { setLoading(true); void load(entry.path); return; }
    try {
      const res = await filesApi.readFile(entry.path);
      setFileContent({ path: entry.path, content: res.content });
    } catch (e) {
      toast(`Couldn't read ${entry.name}: ${String(e)}`, 'danger');
    }
  };

  const goUp = () => {
    if (!currentPath) return;
    setLoading(true);
    void load(parentPath(currentPath));
  };

  const dirs = entries.filter(e => e.is_dir).length;
  return (
    <PanelPage testID="files-screen" title="Files"
      state={[{ text: currentPath || '/', tone: 'quiet' }, { text: `${dirs} folders · ${entries.length - dirs} files` }]}
      loading={loading} error={error} onRetry={() => void load(currentPath)}
      empty={!loading && !error && !entries.length ? { title: 'No files', message: 'No files found in workspace.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(currentPath); }}
      toolbar={currentPath ? (
        <View style={styles.toolbar}>
          <Button label="Up" icon="arrow-left" variant="ghost" onPress={goUp} testID="files-up" />
          <Text variant="monoSm" tone="ink3" numberOfLines={1} style={styles.flex}>{currentPath}</Text>
        </View>
      ) : undefined}>
      <Rows label="Files">
        {entries.map(entry => (
          <Row key={entry.path} testID={`file-${entry.name}`} title={`${entry.is_dir ? '▸ ' : ''}${entry.name}`}
            when={entry.is_dir ? undefined : fileSize(entry.size)} onPress={() => void openEntry(entry)}
            accessibilityLabel={`${entry.is_dir ? 'Folder' : 'File'} ${entry.name}`} />
        ))}
      </Rows>
      <Sheet visible={!!fileContent} onClose={() => setFileContent(null)} title={fileContent?.path.split('/').pop()} full testID="file-viewer">
        {fileContent ? (
          <View style={styles.viewer}>
            <Text variant="monoSm" tone="ink3" numberOfLines={2}>{fileContent.path}</Text>
            <Pre testID="file-content">{fileContent.content}</Pre>
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  toolbar: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  viewer: { gap: spacing.sm },
});
