import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, StyleSheet, Switch, TextInput, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import {
  inspectorApi,
  InspectorVariable,
  InspectorVariableGroup,
} from '../api/inspector';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, fonts, radii, spacing, touchTarget, typography } from '../theme/tokens';
import { Pressable, Sheet, Text } from '../ui';

export type AdvancedSettingsSheetProps = {
  visible: boolean;
  onClose: () => void;
};

type GroupMeta = {
  title: string;
  description: string;
  /** Extra guidance shown when the group is open. */
  note?: string;
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
  'speed & effort': {
    title: 'Speed & effort',
    description: 'Reasoning effort, deferred turn summaries, small-window profile, and how much tool output stays inline.',
  },
  'provider retry': {
    title: 'Provider resilience',
    description: 'Retry counts, backoff, and total wait limits.',
  },
  ollama: {
    title: 'Ollama',
    description: 'Endpoint, authentication, context, and sampling options.',
    note: 'Cloud (ollama.com, or a :cloud model through the local daemon) ignores num_ctx and keep_alive. '
      + 'Local models always get one fixed context of at least 64k so the daemon never reloads them.',
  },
  openai: {
    title: 'OpenAI',
    description: 'API path for reasoning models, prompt-cache retention, and xhigh-capable models.',
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
    <Sheet visible={visible} onClose={onClose} title="Session settings">
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

          <View style={[styles.searchShell, { borderColor: colors.hairline, backgroundColor: colors.canvas }]}>
            <Ionicons name="search-outline" size={17} color={colors.ink3} />
            <TextInput
              value={search}
              onChangeText={setSearch}
              placeholder="Filter variables"
              placeholderTextColor={colors.ink3}
              autoCapitalize="none"
              autoCorrect={false}
              style={[styles.searchInput, { color: colors.ink }]}
            />
            {search ? (
              <Pressable onPress={() => setSearch('')} style={styles.clearButton}>
                <Ionicons name="close" size={17} color={colors.ink3} />
              </Pressable>
            ) : null}
          </View>

          {loading ? (
            <ActivityIndicator color={colors.accent.ink} style={styles.loader} />
          ) : filteredGroups.length === 0 ? (
            <Text variant="sm" dim style={styles.noResults}>No variables match this search.</Text>
          ) : (
            <View style={styles.groupList}>
              {filteredGroups.map(group => {
                const meta = GROUP_META[group.name] || {
                  title: titleCase(group.name),
                  description: 'Session configuration variables.',
                };
                const isOpen = search.length > 0 || expanded.has(group.name);
                const changed = group.variables.filter(variable => !variable.is_default).length;
                return (
                  <View key={group.name} style={[styles.group, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
                    <Pressable
                      onPress={() => toggleGroup(group.name)}
                      activeOpacity={0.72}
                      style={styles.groupHeader}
                    >
                      <Ionicons
                        name={isOpen ? 'chevron-down' : 'chevron-forward'}
                        size={15}
                        color={colors.ink3}
                      />
                      <View style={styles.groupCopy}>
                        <View style={styles.groupTitleRow}>
                          <Text variant="sm" style={styles.groupTitle}>{meta.title}</Text>
                          {changed > 0 ? (
                            <Text variant="xs" style={{ color: colors.ink2, fontWeight: fontWeights.medium, fontVariant: ['tabular-nums'] }}>{changed} changed</Text>
                          ) : null}
                        </View>
                        <Text variant="xs" dim numberOfLines={2}>{meta.description}</Text>
                      </View>
                      <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{group.variables.length}</Text>
                    </Pressable>

                    {isOpen ? (
                      <View style={styles.variableList}>
                        {meta.note ? (
                          <Text variant="xs" style={[styles.groupNote, { color: colors.ink2, borderColor: colors.hairline }]}>{meta.note}</Text>
                        ) : null}
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
            <View style={styles.errorLine}>
              <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
              <Text variant="xs" style={{ color: colors.danger.ink, flex: 1 }}>{error}</Text>
            </View>
          ) : null}
        </>
      )}
    </Sheet>
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
    <View style={styles.variableRow}>
      <View style={styles.variableHeader}>
        <View style={styles.variableCopy}>
          <View style={styles.variableTitleRow}>
            <Text variant="sm" style={styles.variableTitle}>{humanizeKey(variable.key)}</Text>
            <Text variant="xs" dim>{variable.type}</Text>
          </View>
          <Text variant="xs" dim style={styles.variableKey}>{variable.key}</Text>
          {variable.help ? <Text variant="xs" style={{ color: colors.ink2, marginTop: spacing.xs }}>{variable.help}</Text> : null}
        </View>
        {!variable.is_default ? (
          <Pressable onPress={onReset} disabled={saving} style={styles.resetButton} accessibilityLabel={`Reset ${variable.key}`}>
            <Ionicons name="refresh-outline" size={15} color={colors.ink3} />
          </Pressable>
        ) : null}
      </View>

      {variable.options && variable.options.length ? (
        <View style={styles.optionPills}>
          {variable.options.map(option => {
            const active = valueToDraft(option) === valueToDraft(variable.value);
            return (
              <Pressable
                key={valueToDraft(option) || 'default'}
                onPress={() => onSave(option)}
                disabled={saving || active}
                style={[styles.optionPill, {
                  borderColor: active ? colors.accent.ink : colors.hairline,
                  backgroundColor: active ? colors.accent.wash : colors.canvas,
                }]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${variable.key} ${optionLabel(option)}`}
              >
                <Text variant="xs" style={{ color: active ? colors.ink : colors.ink2, fontWeight: active ? fontWeights.semibold : fontWeights.regular }}>
                  {optionLabel(option)}
                </Text>
              </Pressable>
            );
          })}
          {saving ? <ActivityIndicator size="small" color={colors.accent.ink} /> : null}
        </View>
      ) : isBoolean ? (
        <View style={styles.booleanControl}>
          <Text variant="xs" dim>{currentBoolean ? 'On' : 'Off'}</Text>
          {saving ? (
            <ActivityIndicator size="small" color={colors.accent.ink} />
          ) : (
            <Switch
              value={currentBoolean}
              onValueChange={(value: boolean) => onSave(value)}
              trackColor={{ false: colors.hairline, true: colors.accent.ink }}
              thumbColor={colors.raised}
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
            placeholderTextColor={colors.ink3}
            style={[styles.valueInput, { color: colors.ink, backgroundColor: colors.canvas, borderColor: colors.hairline }]}
          />
          <Pressable
            onPress={() => onSave()}
            disabled={saving || (variable.secret && !draft.trim())}
            style={styles.saveButton}
            accessibilityLabel={`Save ${variable.key}`}
          >
            {saving ? (
              <ActivityIndicator size="small" color={colors.accent.ink} />
            ) : (
              <Ionicons name="checkmark" size={18} color={colors.accent.ink} />
            )}
          </Pressable>
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

/** Picker label for an option value ("" = the provider/server default). */
export function optionLabel(option: unknown): string {
  const text = valueToDraft(option);
  return text ? text : 'Default';
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
  intro: { lineHeight: typography.sm.lineHeight, marginBottom: spacing.base },
  searchShell: {
    minHeight: touchTarget,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
    marginBottom: spacing.base,
    borderWidth: 1,
    borderRadius: radii.control,
  },
  searchInput: { flex: 1, minHeight: touchTarget, paddingHorizontal: spacing.sm, fontSize: fontSizes.base },
  clearButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  loader: { marginVertical: spacing.lg },
  noResults: { textAlign: 'center', marginVertical: spacing.lg },
  // Wash Settings: each group is a panel (surface + 1px border + radius 10).
  groupList: { gap: spacing.md },
  group: { borderWidth: 1, borderRadius: radii.panel, paddingHorizontal: spacing.md, overflow: 'hidden' },
  groupHeader: { minHeight: 62, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm, gap: spacing.sm },
  groupCopy: { flex: 1 },
  groupTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: spacing.sm },
  groupTitle: { fontWeight: fontWeights.semibold },
  variableList: { paddingLeft: 0 },
  variableRow: { paddingVertical: spacing.md },
  variableHeader: { flexDirection: 'row', alignItems: 'flex-start' },
  variableCopy: { flex: 1, paddingRight: spacing.sm },
  variableTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: spacing.sm },
  variableTitle: { fontWeight: fontWeights.semibold, flex: 1 },
  variableKey: { marginTop: 1, fontFamily: fonts.mono },
  resetButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  groupNote: { lineHeight: typography.xs.lineHeight, paddingVertical: spacing.sm, borderBottomWidth: 1 },
  optionPills: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xs, marginTop: spacing.sm },
  optionPill: { minHeight: 32, justifyContent: 'center', paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.control },
  booleanControl: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: spacing.sm },
  editorRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, marginTop: spacing.sm },
  valueInput: {
    flex: 1,
    minHeight: touchTarget,
    borderWidth: 1,
    borderRadius: radii.control,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSizes.sm,
  },
  saveButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  errorLine: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.sm,
    paddingVertical: spacing.md,
    marginVertical: spacing.sm,
  },
  emptyState: { alignItems: 'flex-start', paddingVertical: spacing.xl, paddingHorizontal: 2 },
  emptyTitle: { fontWeight: fontWeights.semibold },
  emptyBody: { marginTop: spacing.xs, lineHeight: typography.sm.lineHeight },
});
