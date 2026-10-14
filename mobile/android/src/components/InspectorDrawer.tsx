import React, { useState, useCallback } from 'react';
import { View, StyleSheet, TouchableOpacity, ScrollView, FlatList, RefreshControl, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text } from './Text';
import { memoryApi, type MemorySnapshot } from '../api/memory';
import { SafeAreaModal } from './SafeAreaModal';
import { elevation, fontSizes, fontWeights, radii, spacing, touchTarget } from '../theme/tokens';

export type InspectorDrawerProps = {
  visible: boolean;
  onClose: () => void;
};

type Tab = 'workspace' | 'memory' | 'stats' | 'settings';

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: 'workspace', label: 'Workspace', icon: 'folder' },
  { id: 'memory', label: 'Memory', icon: 'layers' },
  { id: 'stats', label: 'Stats', icon: 'bar-chart' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

export function InspectorDrawer({ visible, onClose }: InspectorDrawerProps) {
  const { colors, spacing, isDark } = useTheme();
  const { activeSessionName, activeProvider, activeModel, baseUrl, yolo } = useConnectionStore();
  const [tab, setTab] = useState<Tab>('workspace');
  const [memSnapshot, setMemSnapshot] = useState<MemorySnapshot | null>(null);
  const [memLoading, setMemLoading] = useState(false);

  const loadMemory = useCallback(async () => {
    setMemLoading(true);
    try {
      const snap = await memoryApi.getState();
      setMemSnapshot(snap);
    } catch {
      // best effort
    } finally {
      setMemLoading(false);
    }
  }, []);

  return (
    <SafeAreaModal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent edges={['bottom']}>
      <View style={styles.overlay}>
        <TouchableOpacity style={[styles.backdrop, { backgroundColor: colors.scrim }]} onPress={onClose} activeOpacity={1} />
        <View style={[styles.drawer, elevation(2, colors, isDark), { borderBottomWidth: 0 }]}>
          <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
            <Text style={[styles.title, { color: colors.text }]}>Inspector</Text>
            <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn}>
              <Ionicons name="close" size={22} color={colors.textDim} />
            </TouchableOpacity>
          </View>

          <View style={[styles.tabBar, { borderBottomColor: colors.hairline }]}>
            {TABS.map(t => (
              <TouchableOpacity
                key={t.id}
                onPress={() => setTab(t.id)}
                style={[styles.tab, tab === t.id && { borderBottomColor: colors.accent, borderBottomWidth: 2 }]}
              >
                <Ionicons name={t.icon as any} size={18} color={tab === t.id ? colors.accentFg : colors.textDim} />
                <Text style={[styles.tabLabel, { color: tab === t.id ? colors.accentFg : colors.textDim }, tab === t.id && { fontWeight: fontWeights.semibold }]}>
                  {t.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <ScrollView style={styles.content} contentContainerStyle={{ padding: spacing.base }}>
            {tab === 'workspace' && (
              <View>
                <Text style={[styles.sectionTitle, { color: colors.text }]}>Session</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{activeSessionName || 'None'}</Text>
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: spacing.md }]}>Provider</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{activeProvider || 'None'}</Text>
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: spacing.md }]}>Model</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{activeModel || 'None'}</Text>
              </View>
            )}

            {tab === 'memory' && (
              <View>
                <TouchableOpacity onPress={loadMemory} style={[styles.searchRow, { borderColor: colors.border }]} activeOpacity={0.7}>
                  <Text style={{ color: colors.text, fontSize: fontSizes.base }}>Load memory snapshot</Text>
                </TouchableOpacity>
                {memLoading && <ActivityIndicator color={colors.accent} style={{ marginTop: spacing.base }} />}
                {!memLoading && !memSnapshot && (
                  <Text style={[styles.emptyText, { color: colors.textDim }]}>Tap to load</Text>
                )}
                {memSnapshot && (
                  <View style={{ marginTop: spacing.md }}>
                    <Text style={[styles.sectionTitle, { color: colors.text }]}>Layers: {memSnapshot.layers?.length || 0}</Text>
                    {memSnapshot.layers?.map((layer, i) => (
                      <View key={i} style={[styles.memItem, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}>
                        <Text style={[styles.memContent, { color: colors.text }]}>{layer.name}</Text>
                        <Text style={[styles.memTags, { color: colors.textDim }]}>{layer.tokens} tokens · {layer.fill_pct}%</Text>
                      </View>
                    ))}
                  </View>
                )}
              </View>
            )}

            {tab === 'stats' && (
              <View>
                <Text style={[styles.sectionTitle, { color: colors.text }]}>Connection</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{baseUrl}</Text>
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: spacing.md }]}>YOLO</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{yolo ? 'Enabled' : 'Disabled'}</Text>
              </View>
            )}

            {tab === 'settings' && (
              <View>
                <Text style={[styles.sectionTitle, { color: colors.text }]}>Session Settings</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim, marginTop: spacing.sm }]}>
                  Configure provider, model, and variables via the Providers and Connection screens.
                </Text>
              </View>
            )}
          </ScrollView>
        </View>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  backdrop: {
    flex: 1,
  },
  drawer: {
    maxHeight: '85%',
    borderTopLeftRadius: radii.lg,
    borderTopRightRadius: radii.lg,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
  },
  title: {
    fontSize: fontSizes.md,
    fontWeight: fontWeights.semibold,
  },
  closeBtn: {
    minHeight: touchTarget,
    minWidth: touchTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tabBar: {
    flexDirection: 'row',
    borderBottomWidth: 1,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    paddingVertical: spacing.md,
    minHeight: touchTarget,
  },
  tabLabel: {
    fontSize: fontSizes.xs,
    fontWeight: fontWeights.medium,
  },
  content: {
    flex: 1,
  },
  sectionTitle: {
    fontSize: fontSizes.sm,
    fontWeight: fontWeights.semibold,
  },
  sectionValue: {
    fontSize: fontSizes.base,
    marginTop: spacing.xs,
  },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.md,
    minHeight: touchTarget,
  },
  searchInput: {
    flex: 1,
    fontSize: fontSizes.base,
    paddingVertical: spacing.md,
  },
  searchBtn: {
    minHeight: touchTarget,
    minWidth: touchTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: {
    fontSize: fontSizes.base,
    marginTop: spacing.base,
    textAlign: 'center',
  },
  memItem: {
    borderRadius: radii.sm,
    padding: spacing.md,
    marginTop: spacing.sm,
  },
  memContent: {
    fontSize: fontSizes.sm,
  },
  memTags: {
    fontSize: fontSizes.xs,
    fontVariant: ['tabular-nums'],
    marginTop: spacing.xs,
  },
});