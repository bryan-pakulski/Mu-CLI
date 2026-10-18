import React, { useState, useCallback } from 'react';
import { View, ScrollView, RefreshControl, StyleSheet, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { filesApi, FileEntry } from '../api/files';
import { spacing, fonts } from '../theme/tokens';
import { Card, Chip, EmptyState, ErrorState, Pressable, Skeleton, Text, Icon } from '../ui';

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
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4, 5].map(i => <Skeleton key={i} height={44} style={{ marginBottom: spacing.xs }} />)}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <ErrorState message={error} onRetry={() => load(currentPath)} />
      </SafeAreaView>
    );
  }

  if (entries.length === 0 && !fileContent) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <EmptyState title="No files" message="No files found in workspace" />
      </SafeAreaView>
    );
  }

  if (fileContent) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.base, borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
          <Pressable onPress={() => setFileContent(null)} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }} accessibilityRole="button" accessibilityLabel="Back">
            <Icon name="arrow-left" size="lg" color={colors.ink} />
          </Pressable>
          <Text variant="sm" style={{ color: colors.ink, marginLeft: spacing.sm, flex: 1, fontFamily: fonts.mono }} numberOfLines={1}>
            {fileContent.path}
          </Text>
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.base }}>
          <Text variant="xs" style={{ color: colors.ink, fontFamily: fonts.mono }}>
            {fileContent.content}
          </Text>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.base, borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
        {currentPath && (
          <Pressable onPress={goUp} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }} accessibilityRole="button" accessibilityLabel="Back">
            <Icon name="arrow-left" size="lg" color={colors.ink} />
          </Pressable>
        )}
        <Text variant="sm" style={{ color: colors.ink3, marginLeft: spacing.sm, flex: 1 }} numberOfLines={1}>
          {currentPath || '/'}
        </Text>
      </View>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(currentPath); }} />}
        contentContainerStyle={{ padding: spacing.base }}
      >
        {entries.map(entry => (
          <Pressable key={entry.path} onPress={() => openEntry(entry)} activeOpacity={0.7}>
            <Card style={{ marginBottom: spacing.xs, minHeight: 44 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
                <Icon
                  name={entry.is_dir ? 'folder' : 'file'}
                  size="md"
                  color={entry.is_dir ? colors.ink2 : colors.ink3}
                />
                <Text variant="sm" style={{ flex: 1 }} numberOfLines={1}>{entry.name}</Text>
                {!entry.is_dir && entry.size !== null && (
                  <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>
                    {entry.size > 1024 ? `${Math.round(entry.size / 1024)}KB` : `${entry.size}B`}
                  </Text>
                )}
              </View>
            </Card>
          </Pressable>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}