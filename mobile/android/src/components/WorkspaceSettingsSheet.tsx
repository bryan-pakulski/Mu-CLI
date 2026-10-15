import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { WorkspacePathField } from './WorkspacePathField';
import { fontSizes, fontWeights, radii, spacing, touchTarget, typography } from '../theme/tokens';
import { ModalFrame, Pressable, Text } from '../ui';

export type WorkspaceSettingsSheetProps = {
  visible: boolean;
  sessionName: string | null;
  onClose: () => void;
  onSaved: (workspaces: string[]) => void;
};

export function WorkspaceSettingsSheet({
  visible,
  sessionName,
  onClose,
  onSaved,
}: WorkspaceSettingsSheetProps) {
  const { colors } = useTheme();
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [candidate, setCandidate] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible || !sessionName) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCandidate('');
    sessionsApi.getWorkspace(sessionName)
      .then(response => {
        if (!cancelled) setWorkspaces(response.workspaces || []);
      })
      .catch(cause => {
        if (!cancelled) setError(String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sessionName, visible]);

  const normalizedCandidate = candidate.trim();
  const canAdd = Boolean(normalizedCandidate && !workspaces.includes(normalizedCandidate));
  const canSave = Boolean(sessionName && !loading && !saving);

  const addWorkspace = () => {
    if (!canAdd) return;
    setWorkspaces(current => [...current, normalizedCandidate]);
    setCandidate('');
  };

  const removeWorkspace = (path: string) => {
    setWorkspaces(current => current.filter(item => item !== path));
  };

  const save = async () => {
    if (!sessionName || !canSave) return;
    setSaving(true);
    setError(null);
    try {
      const response = await sessionsApi.updateWorkspace(sessionName, workspaces);
      setWorkspaces(response.workspaces || []);
      onSaved(response.workspaces || []);
      onClose();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setSaving(false);
    }
  };

  const summary = useMemo(() => {
    if (workspaces.length === 0) return 'No folders attached';
    if (workspaces.length === 1) return '1 folder attached';
    return `${workspaces.length} folders attached`;
  }, [workspaces.length]);

  return (
    <ModalFrame statusBarTranslucent={false} transparent={false} visible={visible} animationType="slide" onRequestClose={onClose} containerStyle={{ backgroundColor: colors.canvas }}>
      <KeyboardAvoidingView
        style={styles.root}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={[styles.header, { paddingTop: spacing.base }]}>
          <Pressable accessibilityLabel="Close" onPress={onClose} style={[styles.iconButton, { backgroundColor: colors.raised, borderWidth: 1, borderColor: colors.hairline }]}>
            <Ionicons name="close" size={20} color={colors.ink} />
          </Pressable>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.ink }]}>Workspace folders</Text>
            <Text variant="xs" dim>{summary}</Text>
          </View>
          <View style={styles.iconSpacer} />
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[styles.content, { paddingBottom: spacing['4xl'] }]}
        >
          {loading ? (
            <ActivityIndicator color={colors.accent.ink} style={styles.loader} />
          ) : (
            <>
              <Text variant="xs" accessibilityRole="header" style={[styles.sectionLabel, { color: colors.ink3 }]}>Attached</Text>
              {workspaces.length === 0 ? (
                <View style={[styles.empty, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
                  <Ionicons name="folder-open-outline" size={22} color={colors.ink3} />
                  <Text variant="sm" dim>No workspace is attached to this session.</Text>
                </View>
              ) : (
                <View style={[styles.list, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
                  {workspaces.map((path, index) => (
                    <View
                      key={path}
                      style={[
                        styles.workspaceRow,
                      ]}
                    >
                      <Ionicons name="folder-outline" size={17} color={colors.ink3} />
                      <Text variant="xs" style={styles.pathText} numberOfLines={2} ellipsizeMode="middle">
                        {path}
                      </Text>
                      <Pressable accessibilityLabel={`Remove workspace ${path}`} onPress={() => removeWorkspace(path)} style={styles.removeButton}>
                        <Ionicons name="close" size={17} color={colors.ink3} />
                      </Pressable>
                    </View>
                  ))}
                </View>
              )}

              <Text variant="xs" accessibilityRole="header" style={[styles.sectionLabel, { color: colors.ink3 }]}>Add folder</Text>
              <WorkspacePathField value={candidate} onChangeText={setCandidate} />
              <Pressable
                onPress={addWorkspace}
                disabled={!canAdd}
                style={[styles.addButton, { backgroundColor: colors.raised, borderColor: colors.hairline, opacity: canAdd ? 1 : 0.5 }]}
              >
                <Ionicons name="add" size={18} color={canAdd ? colors.ink : colors.ink3} />
                <Text variant="sm" style={{ color: canAdd ? colors.ink : colors.ink3, fontWeight: fontWeights.semibold }}>
                  Attach folder
                </Text>
              </Pressable>
            </>
          )}

          {error ? (
            <View accessibilityRole="alert" style={[styles.errorBox, { backgroundColor: colors.danger.wash, borderColor: colors.danger.ink }]}>
              <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
              <Text variant="xs" style={{ color: colors.ink, flex: 1 }}>{error}</Text>
            </View>
          ) : null}
        </ScrollView>

        <View style={[styles.footer, { backgroundColor: colors.canvas, borderTopColor: colors.hairline, paddingBottom: spacing.base }]}>
          <Pressable
            onPress={save}
            disabled={!canSave}
            style={[styles.saveButton, { backgroundColor: canSave ? colors.accent.ink : colors.raised, borderColor: canSave ? colors.accent.ink : colors.hairline }]}
          >
            {saving ? (
              <ActivityIndicator color={colors.onInk} />
            ) : (
              <Text style={{ color: canSave ? colors.onInk : colors.ink3, fontWeight: fontWeights.semibold }}>Save workspace</Text>
            )}
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </ModalFrame>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.base, paddingBottom: spacing.base },
  headerCopy: { flex: 1, alignItems: 'center' },
  title: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold },
  iconButton: { width: touchTarget, height: touchTarget, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  iconSpacer: { width: touchTarget },
  content: { paddingHorizontal: spacing.base, paddingTop: spacing.sm },
  loader: { marginTop: spacing.xl },
  sectionLabel: { marginTop: spacing.lg, marginBottom: spacing.sm, fontWeight: fontWeights.semibold },
  empty: { minHeight: 92, borderRadius: radii.panel, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, padding: spacing.base },
  list: { borderRadius: radii.panel, overflow: 'hidden' },
  workspaceRow: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingLeft: spacing.md },
  pathText: { flex: 1, lineHeight: typography.xs.lineHeight },
  removeButton: { width: touchTarget, minHeight: touchTarget, alignItems: 'center', justifyContent: 'center' },
  addButton: { minHeight: touchTarget, borderWidth: 1, borderRadius: radii.control, marginTop: spacing.sm, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  errorBox: { flexDirection: 'row', gap: spacing.sm, borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, padding: spacing.md, marginTop: spacing.base },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: spacing.base, paddingTop: spacing.md, borderTopWidth: 1 },
  saveButton: { minHeight: 48, borderWidth: 1, borderRadius: radii.control, alignItems: 'center', justifyContent: 'center' },
});
