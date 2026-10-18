import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';

import {
  formatRuleValue,
  jobsApi,
  rulesPatch,
  type JobRuleColumn,
  type JobRuleField,
  type JobRuleRow,
  type JobRuleSection,
  type JobRuleSuite,
  type JobRuleValue,
  type JobRules,
  type ToolchainImage,
} from '../api/jobs';
import { useTheme } from '../theme/ThemeContext';
import { fonts, fontWeights, spacing } from '../theme/tokens';
import { Banner, Button, Field, Loading, Panel, Pill, Pre, Segmented, StatusPill, SwitchRow, Text, TextField, useToast } from '../ui';

/** Editable form state of one field: numbers/text/lists are edited as text. */
type Draft = string | boolean | JobRuleSuite[] | JobRuleRow[];

const TOOLCHAINS_PATH = 'sandbox.toolchains';
/** How often the toolchain image status is re-read while a build runs. */
export const TOOLCHAIN_POLL_MS = 3000;

function cellMatches(column: JobRuleColumn, cell: string): boolean {
  if (!column.pattern || !cell) return true;
  try {
    return new RegExp(`^(?:${column.pattern})$`).test(cell);
  } catch {
    return true; // the server validates anyway
  }
}

/** Local hint for one cell of a `rows` rule (same wording as the server's validate_rules). */
export function rowCellError(field: JobRuleField, column: JobRuleColumn, row: JobRuleRow): string | null {
  const cell = (row[column.key] || '').trim();
  const empty = (field.columns || []).every(c => !(row[c.key] || '').trim());
  if (empty) return null; // blank rows are dropped on save
  if (column.required && !cell) return `${field.label}: ${column.label} is required`;
  if (!cellMatches(column, cell)) return `${field.label}: ${column.label} '${cell}' is not valid`;
  return null;
}

function toDraft(field: JobRuleField, value: JobRuleValue | undefined): Draft {
  switch (field.kind) {
    case 'bool':
      return !!value;
    case 'list':
      return Array.isArray(value) ? (value as string[]).join('\n') : '';
    case 'suites':
      return Array.isArray(value) ? (value as JobRuleSuite[]).map(s => ({ name: s.name || '', command: s.command || '', junit: s.junit || '' })) : [];
    case 'rows':
      return Array.isArray(value)
        ? (value as JobRuleRow[]).map(row => Object.fromEntries((field.columns || []).map(c => [c.key, String(row?.[c.key] ?? '')])))
        : [];
    default:
      return value === null || value === undefined ? '' : String(value);
  }
}

/** Parses a draft into the JSON value to send; returns `{ error }` for local mistakes. */
export function parseRuleDraft(field: JobRuleField, draft: Draft): { value: JobRuleValue } | { error: string } {
  switch (field.kind) {
    case 'int':
    case 'float': {
      const text = String(draft).replace(/[,_\s]/g, '');
      if (!text) return field.optional ? { value: null } : { error: `${field.label} is required` };
      const n = Number(text);
      if (!Number.isFinite(n)) return { error: `${field.label} must be a number` };
      if (field.kind === 'int' && !Number.isInteger(n)) return { error: `${field.label} must be a whole number` };
      return { value: n };
    }
    case 'bool':
      return { value: !!draft };
    case 'list':
      return { value: String(draft).split(/[\n,]/).map(s => s.trim()).filter(Boolean) };
    case 'suites':
      return {
        value: (draft as JobRuleSuite[])
          .filter(s => s.name.trim() || s.command.trim() || (s.junit || '').trim())
          .map(s => {
            const row: JobRuleSuite = { name: s.name.trim(), command: s.command.trim() };
            if ((s.junit || '').trim()) row.junit = (s.junit || '').trim();
            return row;
          }),
      };
    case 'rows': {
      const rows: JobRuleRow[] = [];
      for (const row of draft as JobRuleRow[]) {
        for (const column of field.columns || []) {
          const error = rowCellError(field, column, row);
          if (error) return { error };
        }
        const item: JobRuleRow = {};
        (field.columns || []).forEach(c => { const cell = (row[c.key] || '').trim(); if (cell) item[c.key] = cell; });
        if (Object.keys(item).length) rows.push(item);
      }
      return { value: rows };
    }
    default:
      return { value: String(draft).trim() };
  }
}

function sameDraft(a: Draft, b: Draft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function defaultLabel(field: JobRuleField): string {
  if (field.default === null || field.default === undefined) return 'default: not set';
  return `default: ${formatRuleValue(field, field.default)}`;
}

/** Server `detail` → the field whose label it starts with (validate_rules names fields by label). */
function fieldForError(section: JobRuleSection, detail: string): string | null {
  const hit = section.fields.find(f => detail.startsWith(f.label));
  return hit ? hit.path : null;
}

export type JobRulesFormProps = { active: boolean };

/** Generated "Run rules" form: one Wash Card per RULES_SCHEMA section. */
export function JobRulesForm({ active }: JobRulesFormProps) {
  const { colors } = useTheme();
  const toast = useToast();
  const [rules, setRules] = useState<JobRules | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    if (!active) return;
    let alive = true;
    setLoading(true);
    setLoadError('');
    jobsApi.getSettings()
      .then(settings => { if (alive) { setRules(settings.rules || null); setDrafts({}); } })
      .catch(err => alive && setLoadError(err instanceof Error ? err.message : String(err)))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [active]);

  if (loading) return <Loading label="Loading run rules…" />;
  if (loadError) return <Banner tone="danger" glyph="✗" title="Couldn't load run rules">{loadError}</Banner>;
  if (!rules || !rules.schema?.length) return null;

  const valueOf = (field: JobRuleField): Draft =>
    field.path in drafts ? drafts[field.path] : toDraft(field, rules.values[field.path]);
  const isDirty = (field: JobRuleField) =>
    field.path in drafts && !sameDraft(drafts[field.path], toDraft(field, rules.values[field.path]));
  const isSet = (field: JobRuleField) => field.path in (rules.overrides || {});

  const edit = (path: string, draft: Draft) => {
    setDrafts(prev => ({ ...prev, [path]: draft }));
    setErrors(prev => { const next = { ...prev }; delete next[path]; return next; });
  };

  const apply = async (section: JobRuleSection, entries: Record<string, unknown>, done: string) => {
    setBusy(section.id);
    setSectionErrors(prev => ({ ...prev, [section.id]: '' }));
    try {
      const result = await jobsApi.saveRules(rulesPatch(entries));
      if (result.rules) setRules(result.rules);
      setDrafts(prev => {
        const next = { ...prev };
        Object.keys(entries).forEach(p => delete next[p]);
        return next;
      });
      toast(done, 'ok');
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      const path = fieldForError(section, detail);
      if (path) setErrors(prev => ({ ...prev, [path]: detail }));
      else setSectionErrors(prev => ({ ...prev, [section.id]: detail }));
    } finally {
      setBusy('');
    }
  };

  const saveSection = (section: JobRuleSection) => {
    const entries: Record<string, unknown> = {};
    const local: Record<string, string> = {};
    section.fields.filter(isDirty).forEach(field => {
      const parsed = parseRuleDraft(field, drafts[field.path]);
      if ('error' in parsed) local[field.path] = parsed.error;
      else entries[field.path] = parsed.value;
    });
    if (Object.keys(local).length) { setErrors(prev => ({ ...prev, ...local })); return; }
    if (!Object.keys(entries).length) return;
    void apply(section, entries, `${section.title} saved`);
  };

  const reset = (section: JobRuleSection, field: JobRuleField) =>
    apply(section, { [field.path]: null }, `${field.label} reset to default`);

  const renderInput = (field: JobRuleField) => {
    const draft = valueOf(field);
    const invalid = !!errors[field.path];
    const a11y = field.label;
    switch (field.kind) {
      case 'bool':
        return null; // SwitchRow renders label + control
      case 'int':
      case 'float':
        return (
          <View style={styles.inline}>
            <TextField accessibilityLabel={a11y} keyboardType="numeric" invalid={invalid} value={String(draft)}
              placeholder={field.optional ? 'default' : undefined}
              onChangeText={t => edit(field.path, t)} style={{ flex: 1 }} />
            {field.unit ? <Text variant="xs" style={{ color: colors.ink3 }}>{field.unit}</Text> : null}
          </View>
        );
      case 'select':
        return (
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <Segmented accessibilityLabel={a11y} value={String(draft)} onChange={v => edit(field.path, v)}
              options={(field.options || []).map(([value, label]) => ({ value, label }))} />
          </ScrollView>
        );
      case 'list':
        return (
          <TextField accessibilityLabel={a11y} multiline mono invalid={invalid} value={String(draft)} autoCapitalize="none"
            placeholder="one per line" onChangeText={t => edit(field.path, t)} />
        );
      case 'suites': {
        const rows = draft as JobRuleSuite[];
        const setRow = (i: number, patch: Partial<JobRuleSuite>) =>
          edit(field.path, rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
        return (
          <View style={styles.suites}>
            {rows.map((row, i) => (
              <View key={i} style={[styles.suite, { borderColor: colors.hairline }]}>
                <TextField accessibilityLabel={`Suite ${i + 1} name`} placeholder="name" value={row.name} autoCapitalize="none"
                  onChangeText={t => setRow(i, { name: t })} invalid={invalid} />
                <TextField accessibilityLabel={`Suite ${i + 1} command`} placeholder="command" mono value={row.command} autoCapitalize="none"
                  onChangeText={t => setRow(i, { command: t })} invalid={invalid} />
                <TextField accessibilityLabel={`Suite ${i + 1} junit`} placeholder="junit report path (optional)" mono value={row.junit || ''} autoCapitalize="none"
                  onChangeText={t => setRow(i, { junit: t })} />
                <Button small ghost tone="danger" label="Remove" accessibilityLabel={`Remove suite ${i + 1}`}
                  onPress={() => edit(field.path, rows.filter((_, j) => j !== i))} />
              </View>
            ))}
            <Button small label="Add suite" onPress={() => edit(field.path, [...rows, { name: '', command: '', junit: '' }])} />
          </View>
        );
      }
      case 'rows': {
        const rows = draft as JobRuleRow[];
        const columns = field.columns || [];
        const setCell = (i: number, key: string, text: string) =>
          edit(field.path, rows.map((r, j) => (j === i ? { ...r, [key]: text } : r)));
        const blank = Object.fromEntries(columns.map(c => [c.key, '']));
        return (
          <View style={styles.suites}>
            {rows.map((row, i) => (
              <View key={i} style={[styles.suite, { borderColor: colors.hairline }]} testID={`rule-row-${field.path}-${i}`}>
                {columns.map(column => {
                  const hint = rowCellError(field, column, row);
                  return (
                    <View key={column.key} style={styles.cell}>
                      <Text variant="xs" style={{ color: colors.ink3 }}>{`${column.label}${column.required ? '' : ' (optional)'}`}</Text>
                      <TextField accessibilityLabel={`${column.label} ${i + 1}`} placeholder={column.placeholder || column.label} mono
                        value={row[column.key] || ''} autoCapitalize="none" autoCorrect={false} invalid={invalid || !!hint}
                        onChangeText={t => setCell(i, column.key, t)} />
                      {hint ? <Text variant="xs" style={{ color: colors.danger.ink }}>{hint.slice(field.label.length + 2)}</Text> : null}
                    </View>
                  );
                })}
                <Button small ghost tone="danger" label="Remove" accessibilityLabel={`Remove row ${i + 1}`}
                  onPress={() => edit(field.path, rows.filter((_, j) => j !== i))} />
              </View>
            ))}
            <Button small label="Add row" accessibilityLabel={`Add ${field.label} row`} onPress={() => edit(field.path, [...rows, blank])} />
          </View>
        );
      }
      default:
        return (
          <TextField accessibilityLabel={a11y} mono invalid={invalid} value={String(draft)} autoCapitalize="none"
            onChangeText={t => edit(field.path, t)} />
        );
    }
  };

  return (
    <View style={styles.root} testID="job-rules-form">
      <Text variant="md" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Run rules</Text>
      <Text variant="xs" style={{ color: colors.ink3 }}>Limits and policies every job run obeys. Blank or reset values use the default.</Text>
      {rules.schema.map(section => {
        const dirty = section.fields.filter(isDirty).length;
        return (
          <Panel key={section.id} testID={`rules-${section.id}`} title={section.title}
            right={dirty ? <Pill tone="warn" glyph="●" testID={`rules-${section.id}-dirty`}>{`${dirty} unsaved`}</Pill> : null}>
            {section.hint ? <Text variant="xs" style={{ color: colors.ink3 }}>{section.hint}</Text> : null}
            {section.fields.map(field => {
              const set = isSet(field);
              const meta = [
                field.hint,
                field.kind === 'int' || field.kind === 'float'
                  ? [field.min !== undefined ? `min ${formatRuleValue({ ...field, unit: undefined }, field.min)}` : '',
                     field.max !== undefined ? `max ${formatRuleValue({ ...field, unit: undefined }, field.max)}` : ''].filter(Boolean).join(' · ')
                  : '',
              ].filter(Boolean).join(' ');
              const status = (
                <View style={styles.inline}>
                  {set
                    ? <Pill tone="accent" testID={`rule-set-${field.path}`}>set</Pill>
                    : <Text variant="xs" style={{ color: colors.ink3 }}>{defaultLabel(field)}</Text>}
                  {set ? (
                    <Button small ghost label="Reset" accessibilityLabel={`Reset ${field.label}`} disabled={busy === section.id}
                      onPress={() => { void reset(section, field); }} />
                  ) : null}
                </View>
              );
              if (field.kind === 'bool') {
                return (
                  <View key={field.path} style={styles.field}>
                    <SwitchRow label={field.label} hint={field.hint} value={!!valueOf(field)} onValueChange={v => edit(field.path, v)}
                      testID={`rule-${field.path}`} />
                    {status}
                    {errors[field.path] ? <Text variant="xs" accessibilityRole="alert" style={{ color: colors.danger.ink }}>{errors[field.path]}</Text> : null}
                  </View>
                );
              }
              return (
                <Field key={field.path} label={field.label} hint={meta || undefined} error={errors[field.path]} optional={field.optional}
                  testID={`rule-${field.path}`}>
                  {renderInput(field)}
                  {status}
                  {field.path === TOOLCHAINS_PATH
                    ? <ToolchainImages saved={JSON.stringify(rules.values[field.path] ?? [])} />
                    : null}
                </Field>
              );
            })}
            {sectionErrors[section.id] ? (
              <Text variant="xs" accessibilityRole="alert" style={{ color: colors.danger.ink }}>{sectionErrors[section.id]}</Text>
            ) : null}
            <View style={styles.inline}>
              <Button primary small label={`Save ${section.title.toLowerCase()}`} accessibilityLabel={`Save ${section.title}`}
                disabled={!dirty || busy === section.id} loading={busy === section.id} onPress={() => saveSection(section)} />
              {dirty ? (
                <Button small ghost label="Discard" accessibilityLabel={`Discard ${section.title} changes`}
                  onPress={() => setDrafts(prev => {
                    const next = { ...prev };
                    section.fields.forEach(f => delete next[f.path]);
                    return next;
                  })} />
              ) : null}
            </View>
          </Panel>
        );
      })}
    </View>
  );
}

const TOOLCHAIN_STATUS: Record<string, { status: string; label: string }> = {
  built: { status: 'done', label: 'built' },
  building: { status: 'active', label: 'building' },
  failed: { status: 'failed', label: 'failed' },
  missing: { status: 'pending', label: 'not built' },
};

/** Derived image per saved Toolchains row: status, Build/Rebuild, collapsible build-log tail. Polls while building. */
export function ToolchainImages({ saved }: { saved: string }) {
  const { colors } = useTheme();
  const toast = useToast();
  const [items, setItems] = useState<ToolchainImage[] | null>(null);
  const [openLogs, setOpenLogs] = useState<Record<string, boolean>>({});
  const [starting, setStarting] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    let list: ToolchainImage[] = [];
    try {
      list = (await jobsApi.toolchains()).toolchains || [];
    } catch {
      if (alive.current) setItems([]);
      return;
    }
    if (!alive.current) return;
    setItems(list);
    if (list.some(t => t.status === 'building')) timer.current = setTimeout(() => { void load(); }, TOOLCHAIN_POLL_MS);
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load, saved]);

  const build = async (baseImage: string) => {
    setStarting(baseImage);
    try {
      await jobsApi.buildToolchain(baseImage);
      toast(`Building ${baseImage} toolchain image…`, 'running');
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally {
      if (alive.current) setStarting('');
    }
    await load();
  };

  if (items === null) return null;
  if (!items.length) {
    return <Text variant="xs" style={{ color: colors.ink3 }} testID="toolchain-images-empty">Save a row to build its image.</Text>;
  }
  return (
    <View style={styles.suites} testID="toolchain-images">
      {items.map(item => {
        const meta = TOOLCHAIN_STATUS[item.status] || TOOLCHAIN_STATUS.missing;
        const key = item.base_image;
        const logOpen = !!openLogs[key];
        return (
          <View key={`${item.repository}|${key}`} style={styles.cell} testID={`toolchain-${key}`}>
            <View style={styles.inline}>
              <StatusPill status={meta.status} label={meta.label} />
              <Text variant="xs" style={{ flexShrink: 1, fontFamily: fonts.mono }}>{`${item.repository || ''} ← ${key}`}</Text>
            </View>
            {item.error ? <Text variant="xs" style={{ color: colors.danger.ink }}>{item.error}</Text> : null}
            <View style={styles.inline}>
              {item.status === 'building' ? null : (
                <Button small label={item.status === 'built' ? 'Rebuild' : 'Build'} accessibilityLabel={`${item.status === 'built' ? 'Rebuild' : 'Build'} ${key}`}
                  loading={starting === key} disabled={!!starting} onPress={() => { void build(key); }} />
              )}
              {item.log_tail ? (
                <Button small ghost label={logOpen ? 'Hide build log' : 'Build log'} accessibilityLabel={`${logOpen ? 'Hide' : 'Show'} build log for ${key}`}
                  onPress={() => setOpenLogs(prev => ({ ...prev, [key]: !logOpen }))} />
              ) : null}
            </View>
            {logOpen && item.log_tail ? <Pre testID={`toolchain-log-${key}`} numberOfLines={40}>{item.log_tail.split('\n').slice(-40).join('\n')}</Pre> : null}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: spacing.md },
  inline: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  field: { gap: spacing.xs, marginBottom: spacing.md },
  suites: { gap: spacing.sm },
  suite: { gap: spacing.xs, borderWidth: 1, borderRadius: 8, padding: spacing.sm },
  cell: { gap: 2 },
});
