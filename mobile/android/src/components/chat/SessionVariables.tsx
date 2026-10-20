// Session variables (the web's "All session settings" drawer): grouped runtime overrides for the
// open session, filterable; options are pickers, booleans switches, the rest text fields with Save;
// a changed value resets individually. Secrets are never shown, only replaced.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { inspectorApi, type InspectorVariable, type InspectorVariableGroup } from '../../api/inspector';
import { useTheme } from '../../theme/ThemeContext';
import { radii, spacing } from '../../theme/tokens';
import { Banner } from '../../ui/Feedback';
import { Switch, TextField } from '../../ui/Form';
import { Icon } from '../../ui/Icon';
import { IconButton } from '../../ui/Button';
import { InkDrop } from '../../ui/Progress';
import { Pressable } from '../../ui/Pressable';
import { Text } from '../../ui/Text';

type GroupMeta = { title: string; description: string; note?: string };

const GROUP_META: Record<string, GroupMeta> = {
  behavior: { title: 'Agent behaviour', description: 'Reasoning, streaming, approvals, and iteration controls.' },
  memory: { title: 'Memory & scratchpad', description: 'Retention limits and short-term working memory.' },
  'context budgets': { title: 'Context & budgets', description: 'Provider window allocation, retrieval, skills, and collation.' },
  'speed & effort': { title: 'Speed & effort', description: 'Reasoning effort, deferred turn summaries, small-window profile, and how much tool output stays inline.' },
  'provider retry': { title: 'Provider resilience', description: 'Retry counts, backoff, and total wait limits.' },
  ollama: {
    title: 'Ollama',
    description: 'Endpoint, authentication, context, and sampling options.',
    note: 'Cloud (ollama.com, or a :cloud model through the local daemon) ignores num_ctx and keep_alive. '
      + 'Local models always get one fixed context of at least 64k so the daemon never reloads them.',
  },
  openai: { title: 'OpenAI', description: 'API path for reasoning models, prompt-cache retention, and xhigh-capable models.' },
  'loop mode': { title: 'Loop automation', description: 'Loop detection and autonomous feature execution.' },
  other: { title: 'Other', description: 'Additional session variables exposed by MuCLI.' },
};

export function valueToDraft(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

/** Picker label for an option value ("" = the provider/server default). */
export function optionLabel(option: unknown): string {
  return valueToDraft(option) || 'Default';
}

function humanizeKey(key: string): string {
  return key.split('_').filter(Boolean).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function titleCase(value: string): string {
  return value.replace(/\b\w/g, c => c.toUpperCase());
}

export function SessionVariables({ sessionName, active }: { sessionName: string | null; active: boolean }) {
  const { colors } = useTheme();
  const [groups, setGroups] = useState<InspectorVariableGroup[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['behavior']));
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionName) { setGroups([]); return; }
    setLoading(true);
    setError(null);
    try {
      const response = await inspectorApi.getVariables();
      setGroups(response.groups || []);
      const next: Record<string, string> = {};
      for (const group of response.groups || []) for (const v of group.variables) next[v.key] = v.secret ? '' : valueToDraft(v.value);
      setDrafts(next);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [sessionName]);

  useEffect(() => { if (active) { setSearch(''); void load(); } }, [active, load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return groups;
    return groups.map(group => ({
      ...group,
      variables: group.variables.filter(v => v.key.toLowerCase().includes(q) || humanizeKey(v.key).toLowerCase().includes(q) || String(v.help || '').toLowerCase().includes(q)),
    })).filter(group => group.variables.length > 0);
  }, [groups, search]);

  const updateLocal = (key: string, fn: (v: InspectorVariable) => InspectorVariable) => {
    setGroups(current => current.map(g => ({ ...g, variables: g.variables.map(v => (v.key === key ? fn(v) : v)) })));
  };

  const save = async (variable: InspectorVariable, direct?: unknown) => {
    const raw = direct !== undefined ? direct : drafts[variable.key];
    setSavingKey(variable.key);
    setError(null);
    try {
      const response = await inspectorApi.setVariable(variable.key, raw);
      updateLocal(variable.key, v => ({ ...v, value: v.secret ? null : response.value, is_default: response.value === v.default, is_set: v.secret ? true : v.is_set }));
      setDrafts(current => ({ ...current, [variable.key]: variable.secret ? '' : valueToDraft(response.value) }));
    } catch (cause) {
      setError(`Could not update ${variable.key}: ${String(cause)}`);
    } finally {
      setSavingKey(null);
    }
  };

  const reset = async (variable: InspectorVariable) => {
    setSavingKey(variable.key);
    setError(null);
    try {
      const response = await inspectorApi.unsetVariable(variable.key);
      updateLocal(variable.key, v => ({ ...v, value: v.secret ? null : response.value, is_default: true, is_set: v.secret ? Boolean(response.value) : v.is_set }));
      setDrafts(current => ({ ...current, [variable.key]: variable.secret ? '' : valueToDraft(response.value) }));
    } catch (cause) {
      setError(`Could not reset ${variable.key}: ${String(cause)}`);
    } finally {
      setSavingKey(null);
    }
  };

  if (!sessionName) {
    return <Text variant="caption" tone="ink3">Load a session first: variables are stored per session.</Text>;
  }

  return (
    <View style={{ gap: spacing.md }} testID="session-variables">
      <TextField value={search} onChangeText={setSearch} placeholder="Filter variables" autoCapitalize="none" autoCorrect={false} accessibilityLabel="Filter variables" testID="variables-filter" />
      {loading ? <InkDrop label="Loading variables" /> : filtered.length === 0 ? (
        <Text variant="caption" tone="ink3">No variables match this search.</Text>
      ) : filtered.map(group => {
        const meta = GROUP_META[group.name] || { title: titleCase(group.name), description: 'Session configuration variables.' };
        const open = search.length > 0 || expanded.has(group.name);
        const changed = group.variables.filter(v => !v.is_default).length;
        return (
          <View key={group.name} style={[styles.group, { borderColor: colors.hairline }]}>
            <Pressable onPress={() => setExpanded(cur => { const next = new Set(cur); if (next.has(group.name)) next.delete(group.name); else next.add(group.name); return next; })}
              accessibilityRole="button" accessibilityState={{ expanded: open }} scale={false} style={styles.groupHeader}>
              <Icon name={open ? 'chevron-down' : 'chevron-right'} size="sm" color={colors.ink3} />
              <View style={{ flex: 1 }}>
                <Text variant="label">{meta.title}</Text>
                <Text variant="caption" tone="ink3" numberOfLines={2}>{meta.description}</Text>
              </View>
              {changed ? <Text variant="caption" tone="ink2" numeric>{changed} changed</Text> : null}
            </Pressable>
            {open ? (
              <View>
                {meta.note ? <Text variant="caption" tone="ink2" style={styles.note}>{meta.note}</Text> : null}
                {group.variables.map(v => (
                  <VariableRow key={v.key} variable={v} draft={drafts[v.key] ?? ''} saving={savingKey === v.key}
                    onDraftChange={value => setDrafts(cur => ({ ...cur, [v.key]: value }))}
                    onSave={value => { void save(v, value); }} onReset={() => { void reset(v); }} />
                ))}
              </View>
            ) : null}
          </View>
        );
      })}
      {error ? <Banner tone="danger" title="Couldn't change the setting">{error}</Banner> : null}
    </View>
  );
}

function VariableRow({ variable, draft, onDraftChange, onSave, onReset, saving }: {
  variable: InspectorVariable; draft: string; onDraftChange: (v: string) => void; onSave: (v?: unknown) => void; onReset: () => void; saving: boolean;
}) {
  const { colors } = useTheme();
  return (
    <View style={styles.variable}>
      <View style={styles.variableHead}>
        <View style={{ flex: 1 }}>
          <Text variant="label">{humanizeKey(variable.key)}</Text>
          <Text variant="monoSm" tone="ink3">{variable.key}</Text>
          {variable.help ? <Text variant="caption" tone="ink2">{variable.help}</Text> : null}
        </View>
        {!variable.is_default ? <IconButton icon="refresh-cw" label={`Reset ${variable.key}`} onPress={onReset} disabled={saving} /> : null}
      </View>
      {variable.options && variable.options.length ? (
        <View style={styles.options}>
          {variable.options.map(option => {
            const on = valueToDraft(option) === valueToDraft(variable.value);
            return (
              <Pressable key={valueToDraft(option) || 'default'} onPress={() => onSave(option)} disabled={saving || on} scale={false}
                accessibilityRole="button" accessibilityState={{ selected: on }} accessibilityLabel={`${variable.key} ${optionLabel(option)}`}
                style={[styles.option, { borderColor: on ? colors.accent.ink : colors.hairline, backgroundColor: on ? colors.accent.wash : 'transparent' }]}>
                <Text variant="caption" style={{ color: on ? colors.ink : colors.ink2 }}>{optionLabel(option)}</Text>
              </Pressable>
            );
          })}
          {saving ? <InkDrop size={8} /> : null}
        </View>
      ) : variable.type === 'bool' ? (
        <View style={styles.bool}>
          <Text variant="caption" tone="ink3">{variable.value ? 'On' : 'Off'}</Text>
          {saving ? <InkDrop size={8} /> : <Switch label={variable.key} value={Boolean(variable.value)} onValueChange={value => onSave(value)} />}
        </View>
      ) : (
        <View style={styles.editor}>
          <View style={{ flex: 1 }}>
            <TextField value={draft} onChangeText={onDraftChange} secureTextEntry={Boolean(variable.secret)} autoCapitalize="none" autoCorrect={false}
              keyboardType={variable.type === 'int' || variable.type === 'float' ? 'numeric' : 'default'} accessibilityLabel={variable.key}
              placeholder={variable.secret && variable.is_set ? 'Configured — enter to replace' : valueToDraft(variable.default)} />
          </View>
          {saving ? <InkDrop size={8} /> : <IconButton icon="check" label={`Save ${variable.key}`} onPress={() => onSave()} disabled={Boolean(variable.secret) && !draft.trim()} />}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  group: { borderWidth: 1, borderRadius: radii.md, paddingHorizontal: spacing.md },
  groupHeader: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm },
  note: { paddingBottom: spacing.sm },
  variable: { paddingVertical: spacing.sm, gap: spacing.xs },
  variableHead: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  options: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.xs },
  option: { minHeight: 32, justifyContent: 'center', paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.pill },
  bool: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  editor: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
});
