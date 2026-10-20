// Insights › Costs (web /models/costs, model_costs.js): the model pricing registry. Providers as a
// filter with counts, a search, one row per model (billing, input / output per 1M, context); a row
// opens every field in a sheet. Edits stay local until Save (PUT /api/providers/pricing, validated
// on the server); Reset returns to the packaged defaults. Rates are estimates, not invoices.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useIsFocused } from '@react-navigation/native';
import { costsApi, BILLING_MODES, type PricingCatalog, type PricingModel } from '../../api/costs';
import { spacing } from '../../theme/tokens';
import {
  Button, Chip, EmptyState, ErrorState, Field, PrimaryPill, Row, Segmented, Sheet, StateSentence, Text, TextField,
  useServerActions, useToast,
} from '../../ui';

/** Rates are per million tokens. */
export function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return n === 0 ? '$0' : `$${n < 1 ? n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') : n.toFixed(2)}`;
}

export function formatContext(tokens: number | null | undefined): string {
  const n = Number(tokens || 0);
  if (!n) return '';
  if (n >= 1_000_000) return `${+(n / 1_000_000).toFixed(1)}M context`;
  return `${Math.round(n / 1000)}K context`;
}

const BILLING_LABEL: Record<string, string> = { token: 'Per token', estimated_token: 'Estimated', local: 'Local', unknown: 'Unknown' };

/** A model row's second line: provider · billing · in / out per 1M · context. */
export function modelSub(m: PricingModel): string {
  const rates = m.billing === 'local' ? '$0 API cost' : `in ${formatRate(m.input_per_million)} · out ${formatRate(m.output_per_million)} / 1M`;
  return [m.provider, BILLING_LABEL[m.billing] || m.billing, rates, formatContext(m.context_window)].filter(Boolean).join(' · ');
}

const NUMBER_FIELDS: Array<{ key: keyof PricingModel; label: string; integer?: boolean }> = [
  { key: 'input_per_million', label: 'Input / 1M' },
  { key: 'cached_input_per_million', label: 'Cached input / 1M' },
  { key: 'output_per_million', label: 'Output / 1M' },
  { key: 'context_window', label: 'Context window (tokens)', integer: true },
  { key: 'long_context_cutoff', label: 'Long-context cutoff (tokens)', integer: true },
  { key: 'long_input_per_million', label: 'Long input / 1M' },
  { key: 'long_cached_input_per_million', label: 'Long cached input / 1M' },
  { key: 'long_output_per_million', label: 'Long output / 1M' },
];

/** Text → nullable non-negative number; returns an error message for bad input. */
export function parseNumber(text: string, integer = false): { value: number | null; error?: string } {
  const t = text.trim();
  if (!t) return { value: null };
  const n = Number(t);
  if (!Number.isFinite(n)) return { value: null, error: 'Enter a number' };
  if (n < 0) return { value: null, error: "Can't be negative" };
  if (integer && !Number.isInteger(n)) return { value: null, error: 'Enter a whole number' };
  return { value: n };
}

export function blankModel(provider = ''): PricingModel {
  return {
    provider, key: '', billing: 'token', input_per_million: null, cached_input_per_million: null, output_per_million: null,
    estimated_total_per_million: null, aliases: [], input_modalities: ['text'], output_modalities: ['text'], capabilities: [],
    context_window: null, long_context_cutoff: null, long_input_per_million: null, long_cached_input_per_million: null,
    long_output_per_million: null, role: '', notes: '', source: '',
  };
}

const identity = (m: PricingModel) => `${m.provider}/${m.key.toLowerCase()}`;

export function CostsScreen() {
  const focused = useIsFocused();
  const toast = useToast();
  const { ask, element } = useServerActions();
  const [catalog, setCatalog] = useState<PricingCatalog | null>(null);
  const [models, setModels] = useState<PricingModel[]>([]);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [provider, setProvider] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<{ index: number; draft: PricingModel } | null>(null);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const apply = (data: PricingCatalog) => {
    setCatalog(data);
    setModels(JSON.parse(JSON.stringify(data.models || [])));
    setDirty(false);
  };

  const load = useCallback(async () => {
    try { apply(await costsApi.get()); setError(''); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }, []);
  useEffect(() => { if (focused && !dirty) void load(); }, [focused, load]); // eslint-disable-line react-hooks/exhaustive-deps

  const providers = useMemo(() => {
    const counts = new Map<string, number>();
    models.forEach(m => counts.set(m.provider, (counts.get(m.provider) || 0) + 1));
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [models]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return models.map((m, index) => ({ m, index }))
      .filter(({ m }) => (provider === 'all' || m.provider === provider) && (!q || `${m.provider} ${m.key} ${(m.aliases || []).join(' ')}`.toLowerCase().includes(q)));
  }, [models, provider, query]);

  const save = useCallback(async () => {
    if (!catalog) return;
    setSaving(true);
    try { apply(await costsApi.save(catalog, models)); toast('Pricing saved', 'ok'); }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
    finally { setSaving(false); }
  }, [catalog, models, toast]);

  const reset = useCallback(async () => {
    const ok = await ask({ id: 'reset', label: 'Reset pricing', danger: true, confirm: 'Remove your pricing overrides and go back to the packaged defaults? Unsaved changes are lost too.' });
    if (!ok) return;
    try { apply(await costsApi.reset()); toast('Pricing reset to the packaged defaults', 'ok'); }
    catch (err) { toast(err instanceof Error ? err.message : String(err), 'danger'); }
  }, [ask, toast]);

  const commit = (draft: PricingModel, index: number) => {
    const clash = models.some((m, i) => i !== index && identity(m) === identity(draft));
    if (clash) return `${draft.provider}/${draft.key} is already in the registry.`;
    setModels(list => (index < 0 ? [...list, draft] : list.map((m, i) => (i === index ? draft : m))));
    setDirty(true);
    setEditing(null);
    return '';
  };
  const remove = (index: number) => { setModels(list => list.filter((_, i) => i !== index)); setDirty(true); setEditing(null); };

  if (error && !catalog) return <ErrorState message={error} onRetry={() => void load()} />;
  return (
    <View style={styles.page} testID="insights-costs">
      <FlatList
        data={shown}
        keyExtractor={({ m, index }) => `${identity(m)}-${index}`}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { if (dirty) return; setRefreshing(true); await load(); setRefreshing(false); }} />}
        ListHeaderComponent={
          <View style={styles.head}>
            <StateSentence testID="costs-state" parts={[
              dirty ? { text: 'Unsaved changes', tone: 'attention' } : null,
              catalog ? { text: `${models.length} model${models.length === 1 ? '' : 's'}` } : null,
              catalog ? { text: catalog.using_override ? 'your pricing' : 'packaged defaults', tone: 'quiet' } : null,
            ]} />
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
              <Chip variant="filter" label={`All ${models.length}`} selected={provider === 'all'} onPress={() => setProvider('all')} testID="costs-provider-all" />
              {providers.map(([name, count]) => (
                <Chip key={name} variant="filter" label={`${name} ${count}`} selected={provider === name} onPress={() => setProvider(name)} testID={`costs-provider-${name}`} />
              ))}
            </ScrollView>
            <TextField value={query} onChangeText={setQuery} placeholder="Filter provider or model" autoCapitalize="none" autoCorrect={false} accessibilityLabel="Filter models" testID="costs-filter" />
            <View style={styles.tools}>
              <Button label="Add model" icon="plus" variant="secondary" size="md" onPress={() => setEditing({ index: -1, draft: blankModel(provider === 'all' ? '' : provider) })} testID="costs-add" />
              <Button label="Reset to defaults" variant="ghost" size="md" tone="danger" onPress={() => void reset()} testID="costs-reset" />
            </View>
          </View>
        }
        ListEmptyComponent={catalog ? <EmptyState icon="bar-chart-3" pigment="lagoon" title="No model matches" message="Try another provider or filter." /> : null}
        ListFooterComponent={catalog ? <Text variant="caption" tone="ink3" style={styles.foot}>Rates are telemetry estimates, not invoices. {catalog.currency} per million tokens.</Text> : null}
        renderItem={({ item: { m, index } }) => (
          <Row testID={`costs-model-${m.provider}-${m.key}`} title={m.key} sub={modelSub(m)} accessibilityLabel={`Edit ${m.provider} ${m.key}`}
            onPress={() => setEditing({ index, draft: JSON.parse(JSON.stringify(m)) })} />
        )}
      />
      {dirty ? <PrimaryPill label={saving ? 'Saving…' : 'Save pricing'} icon="check" onPress={() => void save()} testID="costs-save" /> : null}
      <ModelSheet editing={editing} onClose={() => setEditing(null)} onCommit={commit} onRemove={remove} />
      {element}
    </View>
  );
}

function ModelSheet({ editing, onClose, onCommit, onRemove }: {
  editing: { index: number; draft: PricingModel } | null;
  onClose: () => void;
  onCommit: (draft: PricingModel, index: number) => string;
  onRemove: (index: number) => void;
}) {
  const [draft, setDraft] = useState<PricingModel | null>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState('');
  useEffect(() => {
    if (!editing) return;
    setDraft(editing.draft);
    setTexts(Object.fromEntries(NUMBER_FIELDS.map(f => [f.key, editing.draft[f.key] == null ? '' : String(editing.draft[f.key])])));
    setProblem('');
  }, [editing]);
  const open = !!(draft && editing);

  const errors = Object.fromEntries(NUMBER_FIELDS.map(f => [f.key, parseNumber(texts[f.key as string] || '', f.integer).error]));
  const set = (patch: Partial<PricingModel>) => setDraft(d => (d ? { ...d, ...patch } : d));
  const done = () => {
    if (!draft || !editing) return;
    if (!draft.provider.trim() || !draft.key.trim()) { setProblem('A model needs a provider and a key.'); return; }
    if (Object.values(errors).some(Boolean)) { setProblem('Fix the highlighted fields.'); return; }
    const next: PricingModel = { ...draft, provider: draft.provider.trim().toLowerCase(), key: draft.key.trim() };
    NUMBER_FIELDS.forEach(f => { (next as unknown as Record<string, unknown>)[f.key as string] = parseNumber(texts[f.key as string] || '', f.integer).value; });
    if (next.billing === 'estimated_token' && (next.input_per_million == null) !== (next.output_per_million == null)) {
      setProblem('Estimated billing needs both the input and the output rate.');
      return;
    }
    const clash = onCommit(next, editing.index);
    if (clash) setProblem(clash);
  };

  const adding = !editing || editing.index < 0;
  // The sheet stays mounted and is driven by `visible` (a conditionally mounted visible Modal can
  // stay on screen on Android).
  return (
    <Sheet visible={open} onClose={onClose} title={!draft ? '' : adding ? 'Add model' : `${draft.provider} / ${draft.key}`} full testID="costs-sheet"
      footer={open ? <>
        {!adding && editing ? <Button label="Remove" variant="danger" onPress={() => onRemove(editing.index)} testID="costs-sheet-remove" /> : null}
        <Button label="Done" variant="primary" onPress={done} testID="costs-sheet-done" />
      </> : null}>
      {draft ? <>
      <Field label="Provider">
        <TextField value={draft.provider} onChangeText={v => set({ provider: v })} autoCapitalize="none" autoCorrect={false} editable={adding} testID="costs-field-provider" />
      </Field>
      <Field label="Model key">
        <TextField value={draft.key} onChangeText={v => set({ key: v })} autoCapitalize="none" autoCorrect={false} mono editable={adding} testID="costs-field-key" />
      </Field>
      <Field label="Billing">
        <Segmented<string> accessibilityLabel="Billing" value={draft.billing} onChange={v => set({ billing: v })}
          options={BILLING_MODES.map(b => ({ value: b, label: BILLING_LABEL[b], testID: `costs-billing-${b}` }))} />
      </Field>
      {NUMBER_FIELDS.map(f => (
        <Field key={f.key as string} label={f.label} optional error={errors[f.key as string] || undefined}>
          <TextField value={texts[f.key as string] || ''} onChangeText={v => setTexts(t => ({ ...t, [f.key]: v }))} keyboardType="decimal-pad" invalid={!!errors[f.key as string]} testID={`costs-field-${f.key as string}`} />
        </Field>
      ))}
      <Field label="Aliases" hint="Comma separated." optional>
        <TextField value={(draft.aliases || []).join(', ')} onChangeText={v => set({ aliases: v.split(',').map(s => s.trim()).filter(Boolean) })} autoCapitalize="none" autoCorrect={false} />
      </Field>
      <Field label="Role / notes" optional>
        <TextField value={draft.notes || ''} onChangeText={v => set({ notes: v })} multiline />
      </Field>
      {problem ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="costs-sheet-problem">{problem}</Text> : null}
      </> : null}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['4xl'] },
  head: { gap: spacing.sm, marginBottom: spacing.sm },
  filters: { gap: spacing.xs },
  tools: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  foot: { marginTop: spacing.md },
});
