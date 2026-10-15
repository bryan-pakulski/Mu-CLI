import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { WorkspaceSettingsSheet } from '../components/WorkspaceSettingsSheet';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { WORKSPACE_CATEGORIES } from '../navigation/workspace';
import { fontSizes, fontWeights, radii, spacing, tracking } from '../theme/tokens';
import { Pressable, Text } from '../ui';

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
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
      <ScrollView contentContainerStyle={[styles.content, { padding: spacing.base }]}>
        <Text variant="xs" style={[styles.kicker, { color: colors.ink3 }]}>Workspace</Text>
        <Text variant="xl" style={styles.pageTitle}>Tools</Text>
        <Text variant="sm" dim style={styles.pageSubtitle}>
          Tools are grouped by intent so the chat remains focused.
        </Text>

        <View style={[styles.sessionSection, { borderBottomColor: colors.hairline }]}>
          <View style={styles.sessionHeader}>
            <Text style={[styles.brandGlyph, { color: colors.ink }]}>μ</Text>
            <View style={styles.sessionCopy}>
              <Text variant="base" style={styles.sessionName} numberOfLines={1}>
                {activeSessionName || 'New session'}
              </Text>
              <Text variant="xs" dim numberOfLines={1}>
                {[activeProvider, activeModel].filter(Boolean).join(' · ') || 'Provider not selected'}
              </Text>
            </View>
            <View style={styles.status}>
              <View style={[styles.statusDot, { backgroundColor: isConnected ? colors.ok.ink : colors.ink3 }]} />
              <Text variant="xs" style={{ color: isConnected ? colors.ok.ink : colors.ink3 }}>
                {isConnected ? 'Online' : 'Offline'}
              </Text>
            </View>
          </View>
          <View style={styles.workspaceRow}>
            <Ionicons name="folder-outline" size={17} color={colors.ink3} />
            <Text variant="xs" dim style={styles.workspacePath} numberOfLines={1} ellipsizeMode="middle">
              {workspaces.length > 0
                ? workspaces.length === 1 ? workspaces[0] : `${workspaces[0]} +${workspaces.length - 1}`
                : 'No workspace attached'}
            </Text>
            {activeSessionName ? (
              <Pressable onPress={() => setWorkspaceEditorOpen(true)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.editButton}>
                <Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.medium }}>Edit</Text>
              </Pressable>
            ) : null}
          </View>
        </View>

        <Text variant="xs" style={[styles.sectionLabel, { color: colors.ink3 }]}>Tool groups</Text>
        <View>
          {WORKSPACE_CATEGORIES.filter(category => category.id !== 'review').map(category => (
            <Pressable
              key={category.id}
              activeOpacity={0.72}
              onPress={() => navigation.navigate('WorkspaceCategory', { categoryId: category.id, title: category.title })}
              style={styles.categoryRow}
            >
              <Ionicons name={category.icon} size={20} color={colors.ink3} />
              <View style={styles.categoryCopy}>
                <View style={styles.categoryTitleRow}>
                  <Text variant="base" style={styles.categoryTitle}>{category.title}</Text>
                  <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{category.items.length} tools</Text>
                </View>
                <Text variant="sm" dim>{category.description}</Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={colors.ink3} />
            </Pressable>
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
  content: { paddingBottom: spacing['2xl'] },
  kicker: { fontWeight: fontWeights.semibold, marginBottom: spacing.xs },
  pageTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  pageSubtitle: { marginTop: spacing.xs, marginBottom: spacing.xl, maxWidth: 320 },
  sessionSection: { paddingBottom: spacing.lg, marginBottom: spacing.lg, borderBottomWidth: 1 },
  sessionHeader: { flexDirection: 'row', alignItems: 'center' },
  workspaceRow: { marginTop: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  workspacePath: { flex: 1 },
  editButton: { minHeight: 28, paddingHorizontal: spacing.sm, alignItems: 'center', justifyContent: 'center' },
  brandGlyph: { width: 28, fontSize: fontSizes.lg, fontWeight: fontWeights.semibold },
  sessionCopy: { flex: 1, marginHorizontal: spacing.md },
  sessionName: { fontWeight: fontWeights.semibold },
  status: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  statusDot: { width: 8, height: 8, borderRadius: radii.pill },
  sectionLabel: { fontWeight: fontWeights.semibold, marginBottom: spacing.xs },
  categoryRow: { minHeight: 80, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.base },
  categoryCopy: { flex: 1, marginHorizontal: spacing.md },
  categoryTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: spacing.md },
  categoryTitle: { fontWeight: fontWeights.semibold, marginBottom: 2 },
});
