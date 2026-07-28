import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { sessionsApi } from '../api/sessions';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { WorkspacePathField } from './WorkspacePathField';

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
  const insets = useSafeAreaInsets();
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
    <Modal visible={visible} animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <KeyboardAvoidingView
        style={[styles.root, { backgroundColor: colors.bg }]}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={[styles.header, { paddingTop: Math.max(insets.top, 16) }]}>
          <TouchableOpacity onPress={onClose} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}>
            <Ionicons name="close" size={20} color={colors.text} />
          </TouchableOpacity>
          <View style={styles.headerCopy}>
            <Text style={[styles.title, { color: colors.text }]}>Workspace folders</Text>
            <Text variant="xs" dim>{summary}</Text>
          </View>
          <View style={styles.iconSpacer} />
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={[styles.content, { paddingBottom: Math.max(insets.bottom, 20) + 96 }]}
        >
          {loading ? (
            <ActivityIndicator color={colors.accent} style={styles.loader} />
          ) : (
            <>
              <Text variant="xs" dim style={styles.sectionLabel}>ATTACHED</Text>
              {workspaces.length === 0 ? (
                <View style={[styles.empty, { backgroundColor: colors.bgLift }]}>
                  <Ionicons name="folder-open-outline" size={22} color={colors.textDim} />
                  <Text variant="sm" dim>No workspace is attached to this session.</Text>
                </View>
              ) : (
                <View style={[styles.list, { backgroundColor: colors.bgLift }]}>
                  {workspaces.map((path, index) => (
                    <View
                      key={path}
                      style={[
                        styles.workspaceRow,
                        index < workspaces.length - 1 && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
                      ]}
                    >
                      <Ionicons name="folder" size={17} color={colors.textDim} />
                      <Text variant="xs" style={styles.pathText} numberOfLines={2} ellipsizeMode="middle">
                        {path}
                      </Text>
                      <TouchableOpacity onPress={() => removeWorkspace(path)} style={styles.removeButton}>
                        <Ionicons name="close" size={17} color={colors.textDim} />
                      </TouchableOpacity>
                    </View>
                  ))}
                </View>
              )}

              <Text variant="xs" dim style={styles.sectionLabel}>ADD FOLDER</Text>
              <WorkspacePathField value={candidate} onChangeText={setCandidate} />
              <TouchableOpacity
                onPress={addWorkspace}
                disabled={!canAdd}
                style={[styles.addButton, { backgroundColor: canAdd ? colors.bgHover : colors.bgLift }]}
              >
                <Ionicons name="add" size={18} color={canAdd ? colors.text : colors.textDim} />
                <Text variant="sm" style={{ color: canAdd ? colors.text : colors.textDim, fontWeight: '600' }}>
                  Attach folder
                </Text>
              </TouchableOpacity>
            </>
          )}

          {error ? (
            <View style={[styles.errorBox, { backgroundColor: colors.bgLift }]}>
              <Ionicons name="alert-circle-outline" size={18} color={colors.error} />
              <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
            </View>
          ) : null}
        </ScrollView>

        <View style={[styles.footer, { backgroundColor: colors.bg, paddingBottom: Math.max(insets.bottom, 14) }]}>
          <TouchableOpacity
            onPress={save}
            disabled={!canSave}
            style={[styles.saveButton, { backgroundColor: canSave ? colors.text : colors.bgHover }]}
          >
            {saving ? (
              <ActivityIndicator color={colors.bg} />
            ) : (
              <Text style={{ color: canSave ? colors.bg : colors.textDim, fontWeight: '700' }}>Save workspace</Text>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingBottom: 14 },
  headerCopy: { flex: 1, alignItems: 'center' },
  title: { fontSize: 18, fontWeight: '700', letterSpacing: -0.3 },
  iconButton: { width: 40, height: 40, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  iconSpacer: { width: 40 },
  content: { paddingHorizontal: 18, paddingTop: 8 },
  loader: { marginTop: 40 },
  sectionLabel: { marginTop: 22, marginBottom: 10, letterSpacing: 0.7, fontWeight: '700' },
  empty: { minHeight: 92, borderRadius: 16, alignItems: 'center', justifyContent: 'center', gap: 8, padding: 18 },
  list: { borderRadius: 16, overflow: 'hidden' },
  workspaceRow: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 13 },
  pathText: { flex: 1, lineHeight: 17 },
  removeButton: { width: 46, minHeight: 52, alignItems: 'center', justifyContent: 'center' },
  addButton: { minHeight: 44, borderRadius: 14, marginTop: 9, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7 },
  errorBox: { flexDirection: 'row', gap: 9, borderRadius: 14, padding: 12, marginTop: 16 },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, paddingHorizontal: 18, paddingTop: 12 },
  saveButton: { minHeight: 50, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
});
