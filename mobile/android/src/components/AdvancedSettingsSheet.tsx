import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  Switch,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  inspectorApi,
  InspectorVariable,
  InspectorVariableGroup,
} from '../api/inspector';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';

export type AdvancedSettingsSheetProps = {
  visible: boolean;
  onClose: () => void;
};

type GroupMeta = {
  title: string;
  description: string;
};

const GROUP_META: Record<string, GroupMeta> = {
  behavior: {
    title: 'Agent behaviour',
    description: 'Reasoning, streaming, approvals, and iteration controls.',
  },
  memory: {
    title: 'Memory & scratchpad',
    description: 'Retention limits and short-term working memory.',
  },
  'context budgets': {
    title: 'Context & budgets',
    description: 'Provider window allocation, retrieval, skills, and collation.',
  },
  'provider retry': {
    title: 'Provider resilience',
    description: 'Retry counts, backoff, and total wait limits.',
  },
  ollama: {
    title: 'Ollama',
    description: 'Endpoint, authentication, context, and sampling options.',
  },
  'loop mode': {
    title: 'Loop automation',
    description: 'Loop detection and autonomous feature execution.',
  },
  other: {
    title: 'Other',
    description: 'Additional session variables exposed by MuCLI.',
  },
};

export function AdvancedSettingsSheet({ visible, onClose }: AdvancedSettingsSheetProps) {
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const [groups, setGroups] = useState<InspectorVariableGroup[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['behavior']));
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeSessionName) {
      setGroups([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await inspectorApi.getVariables();
      setGroups(response.groups || []);
      const nextDrafts: Record<string, string> = {};
      for (const group of response.groups || []) {
        for (const variable of group.variables) {
          nextDrafts[variable.key] = variable.secret ? '' : valueToDraft(variable.value);
        }
      }
      setDrafts(nextDrafts);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [activeSessionName]);

  useEffect(() => {
    if (!visible) return;
    setSearch('');
    void load();
  }, [load, visible]);

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return groups;
    return groups
      .map(group => ({
        ...group,
        variables: group.variables.filter(variable =>
          variable.key.toLowerCase().includes(query)
          || humanizeKey(variable.key).toLowerCase().includes(query)
          || String(variable.help || '').toLowerCase().includes(query),
        ),
      }))
      .filter(group => group.variables.length > 0);
  }, [groups, search]);

  const toggleGroup = (name: string) => {
    setExpanded(current => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const updateLocalVariable = (key: string, updater: (variable: InspectorVariable) => InspectorVariable) => {
    setGroups(current => current.map(group => ({
      ...group,
      variables: group.variables.map(variable => variable.key === key ? updater(variable) : variable),
    })));
  };

  const saveValue = async (variable: InspectorVariable, directValue?: unknown) => {
    const raw = directValue !== undefined ? directValue : drafts[variable.key];
    setSavingKey(variable.key);
    setError(null);
    try {
      const response = await inspectorApi.setVariable(variable.key, raw);
      updateLocalVariable(variable.key, current => ({
        ...current,
        value: current.secret ? null : response.value,
        is_default: response.value === current.default,
        is_set: current.secret ? true : current.is_set,
      }));
      if (!variable.secret) {
        setDrafts(current => ({ ...current, [variable.key]: valueToDraft(response.value) }));
      } else {
        setDrafts(current => ({ ...current, [variable.key]: '' }));
      }
    } catch (cause) {
      setError(`Could not update ${variable.key}: ${String(cause)}`);
    } finally {
      setSavingKey(null);
    }
  };

  const resetValue = async (variable: InspectorVariable) => {
    setSavingKey(variable.key);
    setError(null);
    try {
      const response = await inspectorApi.unsetVariable(variable.key);
      updateLocalVariable(variable.key, current => ({
        ...current,
        value: current.secret ? null : response.value,
        is_default: true,
        is_set: current.secret ? Boolean(response.value) : current.is_set,
      }));
      setDrafts(current => ({
        ...current,
        [variable.key]: variable.secret ? '' : valueToDraft(response.value),
      }));
    } catch (cause) {
      setError(`Could not reset ${variable.key}: ${String(cause)}`);
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <ModernBottomSheet visible={visible} onClose={onClose} title="Session settings">
      {!activeSessionName ? (
        <View style={styles.emptyState}>
          <Text variant="base" style={styles.emptyTitle}>Load a session first</Text>
          <Text variant="sm" dim style={styles.emptyBody}>
            Session variables are stored per session and cannot be edited without an active session.
          </Text>
        </View>
      ) : (
        <>
          <Text variant="sm" dim style={styles.intro}>
            Runtime overrides for {activeSessionName}. Changed values can be reset individually.
          </Text>

          <View style={[styles.searchShell, { borderBottomColor: colors.hairline }]}>
            <Ionicons name="search-outline" size={17} color={colors.textDim} />
            <TextInput
              value={search}
              onChangeText={setSearch}
              placeholder="Filter variables"
              placeholderTextColor={colors.textDim}
              autoCapitalize="none"
              autoCorrect={false}
              style={[styles.searchInput, { color: colors.text }]}
            />
            {search ? (
              <TouchableOpacity onPress={() => setSearch('')} style={styles.clearButton}>
                <Ionicons name="close" size={17} color={colors.textDim} />
              </TouchableOpacity>
            ) : null}
          </View>

          {loading ? (
            <ActivityIndicator color={colors.accent} style={styles.loader} />
          ) : filteredGroups.length === 0 ? (
            <Text variant="sm" dim style={styles.noResults}>No variables match this search.</Text>
          ) : (
            <View style={[styles.groupList, { borderTopColor: colors.hairline }]}>
              {filteredGroups.map(group => {
                const meta = GROUP_META[group.name] || {
                  title: titleCase(group.name),
                  description: 'Session configuration variables.',
                };
                const isOpen = search.length > 0 || expanded.has(group.name);
                const changed = group.variables.filter(variable => !variable.is_default).length;
                return (
                  <View key={group.name} style={[styles.group, { borderBottomColor: colors.hairline }]}>
                    <TouchableOpacity
                      onPress={() => toggleGroup(group.name)}
                      activeOpacity={0.72}
                      style={styles.groupHeader}
                    >
                      <Ionicons
                        name={isOpen ? 'chevron-down' : 'chevron-forward'}
                        size={15}
                        color={colors.textDim}
                      />
                      <View style={styles.groupCopy}>
                        <View style={styles.groupTitleRow}>
                          <Text variant="sm" style={styles.groupTitle}>{meta.title}</Text>
                          {changed > 0 ? (
                            <Text variant="xs" style={{ color: colors.accent }}>{changed} changed</Text>
                          ) : null}
                        </View>
                        <Text variant="xs" dim numberOfLines={2}>{meta.description}</Text>
                      </View>
                      <Text variant="xs" dim>{group.variables.length}</Text>
                    </TouchableOpacity>

                    {isOpen ? (
                      <View style={styles.variableList}>
                        {group.variables.map(variable => (
                          <VariableRow
                            key={variable.key}
                            variable={variable}
                            draft={drafts[variable.key] ?? ''}
                            onDraftChange={value => setDrafts(current => ({ ...current, [variable.key]: value }))}
                            onSave={value => saveValue(variable, value)}
                            onReset={() => resetValue(variable)}
                            saving={savingKey === variable.key}
                          />
                        ))}
                      </View>
                    ) : null}
                  </View>
                );
              })}
            </View>
          )}

          {error ? (
            <View style={[styles.errorLine, { borderTopColor: colors.hairline }]}>
              <Ionicons name="alert-circle-outline" size={17} color={colors.error} />
              <Text variant="xs" style={{ color: colors.error, flex: 1 }}>{error}</Text>
            </View>
          ) : null}
        </>
      )}
    </ModernBottomSheet>
  );
}

function VariableRow({
  variable,
  draft,
  onDraftChange,
  onSave,
  onReset,
  saving,
}: {
  variable: InspectorVariable;
  draft: string;
  onDraftChange: (value: string) => void;
  onSave: (value?: unknown) => void;
  onReset: () => void;
  saving: boolean;
}) {
  const { colors } = useTheme();
  const isBoolean = variable.type === 'bool';
  const currentBoolean = Boolean(variable.value);
  const keyboardType = variable.type === 'int' || variable.type === 'float' ? 'numeric' : 'default';

  return (
    <View style={[styles.variableRow, { borderTopColor: colors.hairline }]}>
      <View style={styles.variableHeader}>
        <View style={styles.variableCopy}>
          <View style={styles.variableTitleRow}>
            <Text variant="sm" style={styles.variableTitle}>{humanizeKey(variable.key)}</Text>
            <Text variant="xs" dim>{variable.type}</Text>
          </View>
          <Text variant="xs" dim style={styles.variableKey}>{variable.key}</Text>
          {variable.help ? <Text variant="xs" style={{ color: colors.textSoft, marginTop: 4 }}>{variable.help}</Text> : null}
        </View>
        {!variable.is_default ? (
          <TouchableOpacity onPress={onReset} disabled={saving} style={styles.resetButton} accessibilityLabel={`Reset ${variable.key}`}>
            <Ionicons name="refresh-outline" size={15} color={colors.textDim} />
          </TouchableOpacity>
        ) : null}
      </View>

      {isBoolean ? (
        <View style={styles.booleanControl}>
          <Text variant="xs" dim>{currentBoolean ? 'On' : 'Off'}</Text>
          {saving ? (
            <ActivityIndicator size="small" color={colors.accent} />
          ) : (
            <Switch
              value={currentBoolean}
              onValueChange={(value: boolean) => onSave(value)}
              trackColor={{ false: colors.borderStrong, true: colors.accent }}
              thumbColor={colors.glassStrong}
            />
          )}
        </View>
      ) : (
        <View style={styles.editorRow}>
          <TextInput
            value={draft}
            onChangeText={onDraftChange}
            secureTextEntry={Boolean(variable.secret)}
            keyboardType={keyboardType}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder={variable.secret && variable.is_set ? 'Configured — enter to replace' : valueToDraft(variable.default)}
            placeholderTextColor={colors.textDim}
            style={[styles.valueInput, { color: colors.text, borderBottomColor: colors.hairline }]}
          />
          <TouchableOpacity
            onPress={() => onSave()}
            disabled={saving || (variable.secret && !draft.trim())}
            style={styles.saveButton}
            accessibilityLabel={`Save ${variable.key}`}
          >
            {saving ? (
              <ActivityIndicator size="small" color={colors.accent} />
            ) : (
              <Ionicons name="checkmark" size={18} color={colors.accent} />
            )}
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}

function valueToDraft(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function humanizeKey(key: string): string {
  return key
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function titleCase(value: string): string {
  return value.replace(/\b\w/g, character => character.toUpperCase());
}

const styles = StyleSheet.create({
  intro: { lineHeight: 19, marginBottom: 18 },
  searchShell: {
    minHeight: 42,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 2,
    marginBottom: 20,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  searchInput: { flex: 1, minHeight: 42, paddingHorizontal: 9, fontSize: 14 },
  clearButton: { width: 34, height: 40, alignItems: 'center', justifyContent: 'center' },
  loader: { marginVertical: 28 },
  noResults: { textAlign: 'center', marginVertical: 28 },
  groupList: { borderTopWidth: StyleSheet.hairlineWidth },
  group: { borderBottomWidth: StyleSheet.hairlineWidth },
  groupHeader: { minHeight: 62, flexDirection: 'row', alignItems: 'center', paddingVertical: 9, gap: 8 },
  groupCopy: { flex: 1 },
  groupTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 },
  groupTitle: { fontWeight: '600' },
  variableList: { paddingLeft: 22 },
  variableRow: { paddingVertical: 13, borderTopWidth: StyleSheet.hairlineWidth },
  variableHeader: { flexDirection: 'row', alignItems: 'flex-start' },
  variableCopy: { flex: 1, paddingRight: 8 },
  variableTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 },
  variableTitle: { fontWeight: '600', flex: 1 },
  variableKey: { marginTop: 1, fontFamily: 'monospace' },
  resetButton: { width: 34, height: 34, alignItems: 'center', justifyContent: 'center' },
  booleanControl: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 9 },
  editorRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 9 },
  valueInput: {
    flex: 1,
    minHeight: 40,
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 2,
    paddingVertical: 7,
    fontSize: 13,
  },
  saveButton: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center' },
  errorLine: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingVertical: 12,
    marginVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  emptyState: { alignItems: 'flex-start', paddingVertical: 28, paddingHorizontal: 2 },
  emptyTitle: { fontWeight: '600' },
  emptyBody: { marginTop: 6, lineHeight: 20 },
});
