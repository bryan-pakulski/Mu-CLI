import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl, TouchableOpacity, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { filesApi, FileEntry } from '../api/files';
import { spacing } from '../theme/tokens';

export function FilesScreen() {
  const { colors } = useTheme();
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

  useFocusEffect(useCallback(() => { setLoading(true); load(); }, [load]));

  const openEntry = async (entry: FileEntry) => {
    if (entry.is_dir) {
      setLoading(true);
      load(entry.path);
    } else {
      try {
        const res = await filesApi.readFile(entry.path);
        setFileContent({ path: entry.path, content: res.content });
      } catch (e) {
        Alert.alert('Read failed', String(e));
      }
    }
  };

  const goUp = () => {
    if (!currentPath) return;
    const parts = currentPath.split('/');
    parts.pop();
    const parent = parts.length > 1 ? parts.join('/') : undefined;
    setLoading(true);
    load(parent);
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4, 5].map(i => <Skeleton key={i} height={44} style={{ marginBottom: 4 }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={() => load(currentPath)} />
      </SafeAreaView>
    );
  }

  if (entries.length === 0 && !fileContent) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No files" message="No files found in workspace" />
      </SafeAreaView>
    );
  }

  if (fileContent) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.base, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
          <TouchableOpacity onPress={() => setFileContent(null)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="arrow-back" size={24} color={colors.accent} />
          </TouchableOpacity>
          <Text variant="sm" style={{ color: colors.text, marginLeft: spacing.sm, flex: 1 }} numberOfLines={1}>
            {fileContent.path}
          </Text>
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.base }}>
          <Text variant="xs" style={{ color: colors.text, fontFamily: 'monospace' }}>
            {fileContent.content}
          </Text>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.base, borderBottomWidth: 0.5, borderBottomColor: colors.border }}>
        {currentPath && (
          <TouchableOpacity onPress={goUp} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="arrow-back" size={24} color={colors.accent} />
          </TouchableOpacity>
        )}
        <Text variant="sm" style={{ color: colors.textDim, marginLeft: spacing.sm, flex: 1 }} numberOfLines={1}>
          {currentPath || '/'}
        </Text>
      </View>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(currentPath); }} />}
        contentContainerStyle={{ padding: spacing.base }}
      >
        {entries.map(entry => (
          <TouchableOpacity key={entry.path} onPress={() => openEntry(entry)} activeOpacity={0.7}>
            <Card style={{ marginBottom: 4, minHeight: 44 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Ionicons
                  name={entry.is_dir ? 'folder-outline' : 'document-outline'}
                  size={20}
                  color={entry.is_dir ? colors.accent : colors.textDim}
                />
                <Text variant="sm" style={{ flex: 1 }} numberOfLines={1}>{entry.name}</Text>
                {!entry.is_dir && entry.size !== null && (
                  <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                    {entry.size > 1024 ? `${Math.round(entry.size / 1024)}KB` : `${entry.size}B`}
                  </Text>
                )}
              </View>
            </Card>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}