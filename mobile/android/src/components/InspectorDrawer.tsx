import React, { useState, useCallback } from 'react';
import { View, StyleSheet, TouchableOpacity, ScrollView, FlatList, RefreshControl, Modal, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text } from './Text';
import { memoryApi, type MemorySnapshot } from '../api/memory';

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
  const { colors, spacing } = useTheme();
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
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />
        <View style={[styles.drawer, { backgroundColor: colors.bgLift }]}>
          <View style={[styles.header, { borderBottomColor: colors.border }]}>
            <Text style={[styles.title, { color: colors.text }]}>Inspector</Text>
            <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn}>
              <Ionicons name="close" size={22} color={colors.textDim} />
            </TouchableOpacity>
          </View>

          <View style={[styles.tabBar, { borderBottomColor: colors.border }]}>
            {TABS.map(t => (
              <TouchableOpacity
                key={t.id}
                onPress={() => setTab(t.id)}
                style={[styles.tab, tab === t.id && { borderBottomColor: colors.accent, borderBottomWidth: 2 }]}
              >
                <Ionicons name={t.icon as any} size={18} color={tab === t.id ? colors.accent : colors.textDim} />
                <Text style={[styles.tabLabel, { color: tab === t.id ? colors.accent : colors.textDim }]}>
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
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: 12 }]}>Provider</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{activeProvider || 'None'}</Text>
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: 12 }]}>Model</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{activeModel || 'None'}</Text>
              </View>
            )}

            {tab === 'memory' && (
              <View>
                <TouchableOpacity onPress={loadMemory} style={[styles.searchRow, { borderColor: colors.border }]} activeOpacity={0.7}>
                  <Text style={{ color: colors.text, fontSize: 14 }}>Load memory snapshot</Text>
                </TouchableOpacity>
                {memLoading && <ActivityIndicator color={colors.accent} style={{ marginTop: 16 }} />}
                {!memLoading && !memSnapshot && (
                  <Text style={[styles.emptyText, { color: colors.textDim }]}>Tap to load</Text>
                )}
                {memSnapshot && (
                  <View style={{ marginTop: 12 }}>
                    <Text style={[styles.sectionTitle, { color: colors.text }]}>Layers: {memSnapshot.layers?.length || 0}</Text>
                    {memSnapshot.layers?.map((layer, i) => (
                      <View key={i} style={[styles.memItem, { borderColor: colors.border }]}>
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
                <Text style={[styles.sectionTitle, { color: colors.text, marginTop: 12 }]}>YOLO</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim }]}>{yolo ? 'Enabled' : 'Disabled'}</Text>
              </View>
            )}

            {tab === 'settings' && (
              <View>
                <Text style={[styles.sectionTitle, { color: colors.text }]}>Session Settings</Text>
                <Text style={[styles.sectionValue, { color: colors.textDim, marginTop: 8 }]}>
                  Configure provider, model, and variables via the Providers and Connection screens.
                </Text>
              </View>
            )}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    justifyContent: 'flex-end',
  },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
  },
  drawer: {
    maxHeight: '85%',
    borderTopLeftRadius: 12,
    borderTopRightRadius: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
  },
  title: {
    fontSize: 16,
    fontWeight: '600',
  },
  closeBtn: {
    minHeight: 44,
    minWidth: 44,
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
    gap: 6,
    paddingVertical: 12,
    minHeight: 44,
  },
  tabLabel: {
    fontSize: 12,
    fontWeight: '500',
  },
  content: {
    flex: 1,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  sectionValue: {
    fontSize: 14,
    marginTop: 4,
  },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    minHeight: 44,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    paddingVertical: 10,
  },
  searchBtn: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyText: {
    fontSize: 14,
    marginTop: 16,
    textAlign: 'center',
  },
  memItem: {
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    marginTop: 8,
  },
  memContent: {
    fontSize: 13,
  },
  memTags: {
    fontSize: 11,
    marginTop: 4,
  },
});