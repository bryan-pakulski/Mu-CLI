import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { Text } from '../components';
import { WorkspaceSettingsSheet } from '../components/WorkspaceSettingsSheet';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { WORKSPACE_CATEGORIES } from '../navigation/workspace';

export type WorkspaceScreenProps = NativeStackScreenProps<RootStackParamList, 'Workspace'>;

export function WorkspaceScreen({ navigation }: WorkspaceScreenProps) {
  const { colors, spacing } = useTheme();
  const { activeSessionName, activeProvider, activeModel, isConnected } = useConnectionStore();
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [workspaceEditorOpen, setWorkspaceEditorOpen] = useState(false);

  const loadWorkspaces = useCallback(async () => {
    if (!activeSessionName) {
      setWorkspaces([]);
      return;
    }
    try {
      const response = await sessionsApi.getWorkspace(activeSessionName);
      setWorkspaces(response.workspaces || []);
    } catch {
      setWorkspaces([]);
    }
  }, [activeSessionName]);

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
      <ScrollView contentContainerStyle={[styles.content, { padding: spacing.base }]}>
        <Text variant="xs" style={[styles.kicker, { color: colors.accent }]}>WORKSPACE</Text>
        <Text variant="xl" style={styles.pageTitle}>Tools</Text>
        <Text variant="sm" dim style={styles.pageSubtitle}>
          Tools are grouped by intent so the chat remains focused.
        </Text>

        <View style={[styles.sessionSection, { borderBottomColor: colors.hairline }]}>
          <View style={styles.sessionHeader}>
            <Text style={[styles.brandGlyph, { color: colors.accent }]}>μ</Text>
            <View style={styles.sessionCopy}>
              <Text variant="base" style={styles.sessionName} numberOfLines={1}>
                {activeSessionName || 'New session'}
              </Text>
              <Text variant="xs" dim numberOfLines={1}>
                {[activeProvider, activeModel].filter(Boolean).join(' · ') || 'Provider not selected'}
              </Text>
            </View>
            <View style={styles.status}>
              <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.success : colors.textDim }]} />
              <Text variant="xs" style={{ color: isConnected ? colors.success : colors.textDim }}>
                {isConnected ? 'Online' : 'Offline'}
              </Text>
            </View>
          </View>
          <View style={[styles.workspaceRow, { borderTopColor: colors.hairline }]}>
            <Ionicons name="folder-outline" size={17} color={colors.textDim} />
            <Text variant="xs" dim style={styles.workspacePath} numberOfLines={1} ellipsizeMode="middle">
              {workspaces.length > 0
                ? workspaces.length === 1 ? workspaces[0] : `${workspaces[0]} +${workspaces.length - 1}`
                : 'No workspace attached'}
            </Text>
            {activeSessionName ? (
              <TouchableOpacity onPress={() => setWorkspaceEditorOpen(true)} style={[styles.editButton, { borderBottomColor: colors.accent }]}>
                <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>Edit</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        </View>

        <Text variant="xs" style={[styles.sectionLabel, { color: colors.textDim }]}>TOOL GROUPS</Text>
        <View>
          {WORKSPACE_CATEGORIES.filter(category => category.id !== 'review').map(category => (
            <TouchableOpacity
              key={category.id}
              activeOpacity={0.72}
              onPress={() => navigation.navigate('WorkspaceCategory', { categoryId: category.id, title: category.title })}
              style={[styles.categoryRow, { borderBottomColor: colors.hairline }]}
            >
              <Ionicons name={category.icon} size={20} color={colors.textDim} />
              <View style={styles.categoryCopy}>
                <View style={styles.categoryTitleRow}>
                  <Text variant="base" style={styles.categoryTitle}>{category.title}</Text>
                  <Text variant="xs" dim>{category.items.length} tools</Text>
                </View>
                <Text variant="sm" dim>{category.description}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
      <WorkspaceSettingsSheet
        visible={workspaceEditorOpen}
        sessionName={activeSessionName}
        onClose={() => setWorkspaceEditorOpen(false)}
        onSaved={setWorkspaces}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: 40 },
  kicker: { fontWeight: '700', letterSpacing: 1.3, marginBottom: 5 },
  pageTitle: { fontWeight: '700', letterSpacing: -0.5 },
  pageSubtitle: { marginTop: 4, marginBottom: 30, maxWidth: 320 },
  sessionSection: { paddingBottom: 22, marginBottom: 28, borderBottomWidth: StyleSheet.hairlineWidth },
  sessionHeader: { flexDirection: 'row', alignItems: 'center' },
  workspaceRow: { marginTop: 15, paddingTop: 13, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 8 },
  workspacePath: { flex: 1 },
  editButton: { minHeight: 28, paddingHorizontal: 2, borderBottomWidth: 1, alignItems: 'center', justifyContent: 'center' },
  brandGlyph: { width: 28, fontSize: 22, fontWeight: '700' },
  sessionCopy: { flex: 1, marginHorizontal: 12 },
  sessionName: { fontWeight: '600' },
  status: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  sectionLabel: { fontFamily: 'monospace', fontWeight: '600', letterSpacing: 1.1, marginBottom: 4 },
  categoryRow: { minHeight: 86, flexDirection: 'row', alignItems: 'center', paddingVertical: 16, borderBottomWidth: StyleSheet.hairlineWidth },
  categoryCopy: { flex: 1, marginHorizontal: 14 },
  categoryTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 },
  categoryTitle: { fontWeight: '600', marginBottom: 2 },
});
