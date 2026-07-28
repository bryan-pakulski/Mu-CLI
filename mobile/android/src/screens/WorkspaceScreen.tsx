import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { Card, Text } from '../components';
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
        <Text variant="xl" style={styles.pageTitle}>Workspace</Text>
        <Text variant="sm" dim style={styles.pageSubtitle}>
          Tools are grouped by intent so the chat remains focused.
        </Text>

        <Card style={styles.sessionCard}>
          <View style={styles.sessionHeader}>
            <View style={[styles.brandMark, { backgroundColor: colors.accentSoft }]}>
              <Text style={[styles.brandGlyph, { color: colors.accent }]}>μ</Text>
            </View>
            <View style={styles.sessionCopy}>
              <Text variant="base" style={styles.sessionName} numberOfLines={1}>
                {activeSessionName || 'New session'}
              </Text>
              <Text variant="xs" dim numberOfLines={1}>
                {[activeProvider, activeModel].filter(Boolean).join(' · ') || 'Provider not selected'}
              </Text>
            </View>
            <View style={[styles.statusPill, { backgroundColor: isConnected ? colors.accentSoft : colors.bgHover }]}>
              <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.success : colors.textDim }]} />
              <Text variant="xs" style={{ color: isConnected ? colors.accent : colors.textDim }}>
                {isConnected ? 'Online' : 'Offline'}
              </Text>
            </View>
          </View>
          <View style={[styles.workspaceRow, { borderTopColor: colors.border }]}>
            <Ionicons name="folder-outline" size={17} color={colors.textDim} />
            <Text variant="xs" dim style={styles.workspacePath} numberOfLines={1} ellipsizeMode="middle">
              {workspaces.length > 0
                ? workspaces.length === 1 ? workspaces[0] : `${workspaces[0]} +${workspaces.length - 1}`
                : 'No workspace attached'}
            </Text>
            {activeSessionName ? (
              <TouchableOpacity onPress={() => setWorkspaceEditorOpen(true)} style={[styles.editButton, { backgroundColor: colors.bgHover }]}>
                <Text variant="xs" style={{ fontWeight: '600' }}>Edit</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        </Card>

        <View style={styles.categoryList}>
          {WORKSPACE_CATEGORIES.filter(category => category.id !== 'review').map(category => (
            <TouchableOpacity
              key={category.id}
              activeOpacity={0.72}
              onPress={() => navigation.navigate('WorkspaceCategory', { categoryId: category.id, title: category.title })}
            >
              <Card style={styles.categoryCard}>
                <View style={[styles.categoryIcon, { backgroundColor: colors.bgHover }]}>
                  <Ionicons name={category.icon} size={22} color={colors.text} />
                </View>
                <View style={styles.categoryCopy}>
                  <Text variant="base" style={styles.categoryTitle}>{category.title}</Text>
                  <Text variant="sm" dim>{category.description}</Text>
                  <Text variant="xs" dim style={styles.itemCount}>{category.items.length} tools</Text>
                </View>
                <Ionicons name="chevron-forward" size={20} color={colors.textDim} />
              </Card>
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
  pageTitle: { fontWeight: '700', letterSpacing: -0.5 },
  pageSubtitle: { marginTop: 4, marginBottom: 24, maxWidth: 320 },
  sessionCard: { marginBottom: 24 },
  sessionHeader: { flexDirection: 'row', alignItems: 'center' },
  workspaceRow: { marginTop: 15, paddingTop: 13, borderTopWidth: StyleSheet.hairlineWidth, flexDirection: 'row', alignItems: 'center', gap: 8 },
  workspacePath: { flex: 1 },
  editButton: { minHeight: 34, borderRadius: 11, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center' },
  brandMark: { width: 44, height: 44, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  brandGlyph: { fontSize: 22, fontWeight: '700' },
  sessionCopy: { flex: 1, marginHorizontal: 12 },
  sessionName: { fontWeight: '600' },
  statusPill: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 6 },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  categoryList: { gap: 12 },
  categoryCard: { flexDirection: 'row', alignItems: 'center', paddingVertical: 18 },
  categoryIcon: { width: 46, height: 46, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  categoryCopy: { flex: 1, marginHorizontal: 14 },
  categoryTitle: { fontWeight: '600', marginBottom: 2 },
  itemCount: { marginTop: 8 },
});
