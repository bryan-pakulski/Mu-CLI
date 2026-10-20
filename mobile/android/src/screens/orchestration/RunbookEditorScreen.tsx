// A runbook draft in the Studio (web runbooks.js renderEditor): two views of one document — Steps
// (a card per step: name, tool, params, when:, on failure, node selector; plus the inputs table)
// and YAML. Switching converts through the server (validateRunbook) so the two never disagree;
// checks run after every edit with problems at their field. Invalid drafts save (even YAML that
// doesn't parse) but can't be submitted. Saving sends base_digest: a draft someone else (usually the
// AI) changed since it was loaded is never overwritten — the save gets a 409 and a banner offers
// Reload or Keep mine.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchProblem, type OrchRunbookDetail, type OrchRunbookValidation } from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Button, CheckRow, ErrorState, Field, HeaderTabs, Loading, PageHeader, Row, Rows, Segmented, Text, TextField, useToast } from '../../ui';
import { DiscoveryChatSheet } from './AskSheets';
import { CodeEditor } from './CodeEditor';
import { TestRunbookSheet } from './forms';
import { OrchActionBar, errorText, isConflict, plural, useOrchActions } from './parts';
import {
  ROW_FIELDS, errorsOf, fieldText, inputsToRows, modelToSpec, rowsToInputs, specToModel, stepOf, stepToModel, warningsOf,
  type InputRow, type RowField, type RunbookModel,
} from './runbookModel';
import { ExpressionPreview, UpgradeToolsSheet } from './runbookTools';
import { versionState } from './ToolScreen';
import { useOrchData } from './useOrchData';

export const VALIDATE_DEBOUNCE_MS = 400;
const ON_FAILURE = [{ value: 'stop', label: 'Stop' }, { value: 'continue', label: 'Continue' }, { value: 'rollback', label: 'Roll back' }];
const ROW_LABEL: Record<RowField, string> = { type: 'Type', default: 'Default', enum: 'One of', pattern: 'Pattern', maxLength: 'Max length', description: 'Description' };

/** Problems grouped by where they point: "Runbook" or "Step 2 · name" (web paintChecks). */
export function groupProblems(problems: OrchProblem[], model: RunbookModel | null): Array<[string, OrchProblem[]]> {
  const groups = new Map<string, OrchProblem[]>();
  [...errorsOf(problems), ...warningsOf(problems)].forEach(p => {
    const i = stepOf(p.path);
    const key = i === null ? 'Runbook' : `Step ${i + 1}${model?.steps[i]?.name ? ` · ${model.steps[i].name}` : ''}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(p);
  });
  return [...groups.entries()];
}

const paramsText = (p: unknown) => (p && typeof p === 'object' && !Object.keys(p as object).length ? '' : JSON.stringify(p ?? {}, null, 2));

export function OrchRunbookEditorScreen({ route, navigation }: { route: { params: { runbookId: string; version: number } }; navigation: any }) {
  const { runbookId, version } = route.params;
  const { data: r, error, reload } = useOrchData(() => orchestrationApi.runbook(runbookId, version), () => false, [runbookId, version]);
  const back = { label: 'Studio', onPress: () => navigation.goBack() };
  if (!r) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title={`${runbookId} v${version}`} back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading runbook…" /></View>}
      </View>
    );
  }
  return <Editor key={`${r.ref}:${r.digest}`} r={r} navigation={navigation} back={back} reload={reload} />;
}

function Editor({ r, navigation, back, reload }: { r: OrchRunbookDetail; navigation: any; back: { label: string; onPress: () => void }; reload: () => Promise<void> }) {
  const toast = useToast();
  const editable = r.status === 'draft';
  const [tab, setTab] = useState<'form' | 'yaml'>(r.source ? 'yaml' : 'form');
  const [model, setModel] = useState<RunbookModel>(() => specToModel(r.spec));
  const [rows, setRows] = useState<InputRow[]>(() => inputsToRows(specToModel(r.spec).inputs || {}));
  const [params, setParams] = useState<string[]>(() => specToModel(r.spec).steps.map(s => paramsText(s.params)));
  const [yaml, setYaml] = useState(r.source || r.spec_yaml || '');
  const [result, setResult] = useState<OrchRunbookValidation | null>(null);
  const [checkError, setCheckError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [stale, setStale] = useState<'' | 'conflict' | 'kept'>('');
  const [saving, setSaving] = useState(false);
  const [dialog, setDialog] = useState<'test' | 'chat' | 'upgrade' | null>(null);
  const [upgradeSpec, setUpgradeSpec] = useState<unknown>(null);
  const baseDigest = useRef(r.digest);
  const edits = useRef(0);
  const seq = useRef(0);
  const { run, element } = useOrchActions();

  /** The spec as the form describes it (params parsed back from their JSON fields). */
  const formSpec = useCallback((m = model, rs = rows, ps = params) => {
    const steps = m.steps.map((s, i) => {
      let p: unknown = s.params;
      const text = (ps[i] ?? '').trim();
      if (!text) p = {};
      else { try { p = JSON.parse(text); } catch { p = s.params; } }
      return { ...s, params: p };
    });
    const inputs = rs.length || m.inputs !== undefined ? rowsToInputs(rs, m.inputs) : undefined;
    return modelToSpec({ ...m, steps, inputs });
  }, [model, rows, params]);
  const currentSpec = useCallback(() => (tab === 'yaml' ? yaml : formSpec()), [tab, yaml, formSpec]);

  const validate = useCallback(async (spec: unknown) => {
    const mine = ++seq.current;
    try {
      const res = await orchestrationApi.command('validateRunbook', { spec });
      if (mine !== seq.current) return null;
      const v = res.data as unknown as OrchRunbookValidation;
      setResult(v); setCheckError('');
      return v;
    } catch (err) {
      if (mine === seq.current) setCheckError(`Couldn’t check the draft: ${errorText(err)}`);
      return null;
    }
  }, []);

  // Debounced checks after every edit (and once on open).
  useEffect(() => {
    const timer = setTimeout(() => { void validate(currentSpec()); }, edits.current ? VALIDATE_DEBOUNCE_MS : 0);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, rows, params, yaml, tab]);

  const touched = () => { edits.current += 1; setDirty(true); };
  const setStep = (i: number, patch: Partial<RunbookModel['steps'][number]>) => { setModel(m => ({ ...m, steps: m.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) })); touched(); };
  const addStep = () => { setModel(m => ({ ...m, steps: [...m.steps, stepToModel({ name: `step${m.steps.length + 1}`, tool: '' })] })); setParams(p => [...p, '']); touched(); };
  const removeStep = (i: number) => { setModel(m => ({ ...m, steps: m.steps.filter((_, j) => j !== i) })); setParams(p => p.filter((_, j) => j !== i)); touched(); };
  const moveStep = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= model.steps.length) return;
    const swap = <T,>(a: T[]) => { const b = a.slice(); [b[i], b[j]] = [b[j], b[i]]; return b; };
    setModel(m => ({ ...m, steps: swap(m.steps) })); setParams(swap); touched();
  };
  const setRow = (i: number, patch: Partial<InputRow>) => { setRows(rs => rs.map((x, j) => (j === i ? { ...x, ...patch } : x))); touched(); };
  const setRowField = (i: number, f: RowField, v: string) => { setRows(rs => rs.map((x, j) => (j === i ? { ...x, edits: { ...x.edits, [f]: v } } : x))); touched(); };

  const switchTab = async (next: 'form' | 'yaml') => {
    if (next === tab) return;
    if (next === 'yaml') {
      const res = await validate(formSpec());
      if (res?.yaml) setYaml(res.yaml);
      setTab('yaml');
      return;
    }
    const res = await validate(yaml);
    if (!res || !res.parses || !res.spec) { toast('Fix the YAML error first: the Steps view needs YAML that parses.', 'danger'); return; }
    const m = specToModel(res.spec);
    setModel(m); setRows(inputsToRows(m.inputs || {})); setParams(m.steps.map(s => paramsText(s.params)));
    setTab('form');
  };

  const save = async (): Promise<boolean> => {
    if (stale === 'kept') { toast('Saving is refused until you reload: the draft changed elsewhere.', 'attention'); return false; }
    const sent = edits.current;
    setSaving(true);
    try {
      const res = await orchestrationApi.command('saveRunbookDraft', { spec: currentSpec(), runbook_id: r.runbook_id, base_digest: baseDigest.current });
      baseDigest.current = String(res.data.digest || baseDigest.current);
      setStale('');
      if (edits.current === sent) setDirty(false);
      const n = ((res.data.problems as unknown[]) || []).length;
      toast(n ? `Draft saved with ${plural(n, 'problem')}; it can’t be submitted until they’re fixed.` : 'Draft saved', n ? 'attention' : 'ok');
      return true;
    } catch (err) {
      if (isConflict(err)) { setStale('conflict'); return false; }
      toast(`Couldn’t save: ${errorText(err)}`, 'danger');
      return false;
    } finally { setSaving(false); }
  };
  const guard = (fn: () => void) => async () => { if (!dirty || await save()) fn(); };

  /** Upgraded tools come back as a spec: load it into the open view, unsaved (web onApply). */
  const applyUpgrade = async (spec: Record<string, any>) => {
    const m = specToModel(spec);
    setModel(m); setRows(inputsToRows(m.inputs || {})); setParams(m.steps.map(st => paramsText(st.params)));
    if (tab === 'yaml') {
      const res = await validate(spec);
      if (res?.yaml) setYaml(res.yaml);
    }
    touched();
  };

  const problems = result?.problems || [];
  const errs = errorsOf(problems);
  const warns = warningsOf(problems);
  const stepProblems = (i: number) => problems.filter(p => stepOf(p.path) === i);
  const atField = (i: number, field: string) => stepProblems(i).filter(p => p.path.slice(`steps[${i}]`.length).replace(/^\./, '').startsWith(field));

  return (
    <View style={{ flex: 1 }} testID="orch-runbook-editor">
      <PageHeader testID="orch-runbook-editor-head" title={`${r.title} v${r.version}`} back={back}
        state={[...versionState({ ...r, effect: result?.effect || r.effect, effect_style: result?.effect_style || r.effect_style }), dirty ? { text: 'unsaved changes', tone: 'attention' } : null]} />
      <ScrollView contentContainerStyle={styles.pad} keyboardShouldPersistTaps="handled">
        <View accessibilityLiveRegion="polite" testID="orch-rb-strip">
          {result ? (errs.length
            ? <Text variant="label" tone="danger">✗ {plural(errs.length, 'problem')} — this draft can’t be submitted{dirty ? ' (including unsaved edits)' : ''}</Text>
            : <Text variant="label" tone="ok">✓ {warns.length ? `Valid · ${plural(warns.length, 'warning')}` : 'Valid'}</Text>) : checkError ? null : <Loading label="Checking…" />}
          {checkError ? <Text variant="caption" tone="danger">✗ {checkError}</Text> : null}
        </View>
        {stale === 'conflict' ? (
          <Banner tone="attention" glyph="◆" testID="orch-rb-stale" title="Your save was refused: this draft changed since you opened it (the AI or another device may have saved it). Your edits are still here.">
            <View style={styles.buttons}>
              <Button size="md" variant="tonal" tone="attention" label="Reload (discard my edits)" onPress={() => { void reload(); }} testID="orch-rb-reload" />
              <Button size="md" variant="ghost" label="Keep mine" onPress={() => setStale('kept')} testID="orch-rb-keep" />
            </View>
          </Banner>
        ) : null}
        {stale === 'kept' ? <Banner tone="caution" glyph="◆" title="Keeping your edits. Saving will be refused until you reload, because the draft changed elsewhere." /> : null}
        {r.source && !dirty ? <Banner tone="caution" glyph="◆" title="This draft was saved with YAML that doesn’t parse. It reopens here with your text; the last valid version is kept until it parses." /> : null}
        {r.status === 'in_review' ? <Banner tone="attention" glyph="◆" title="In review: editing is locked. Request changes from the catalog page to reopen it." /> : null}
        {!['draft', 'in_review'].includes(r.status) ? <Banner tone="info" glyph="●" title={`This version is ${r.style?.label || r.status} and can’t be edited. Start a new version from the catalog page.`} /> : null}

        <View style={styles.buttons}>
          {editable ? <Button size="md" variant="primary" icon="check" label="Save draft" disabled={!dirty} loading={saving} onPress={() => { void save(); }} testID="orch-rb-save" /> : null}
          {editable ? <Button size="md" variant="secondary" icon="refresh-cw" label="Upgrade tools" onPress={() => { setUpgradeSpec(currentSpec()); setDialog('upgrade'); }} testID="orch-rb-upgrade" /> : null}
          <Button size="md" variant="ghost" icon="message-circle" label="Discovery chat" onPress={() => setDialog('chat')} />
          <Button size="md" variant="ghost" label="Catalog view" navigates onPress={() => navigation.navigate('OrchRunbook', { runbookId: r.runbook_id, version: r.version })} />
        </View>
        <OrchActionBar testID="orch-rb-actions" actions={r.actions} only={['testRunbook', 'submitRunbook', 'discardRunbookDraft']} run={run} ctx={{
          args: { ref: r.ref },
          done: (_res, action) => { if (action.id === 'discardRunbookDraft') navigation.goBack(); else void reload(); },
          dialogs: { test: () => { void guard(() => setDialog('test'))(); } },
          overrides: { submitRunbook: action => { void guard(() => { void run({ ...action, id: 'submitRunbook' }, { args: { ref: r.ref }, done: () => { void reload(); } }); })(); } },
        }} />

        <HeaderTabs testID="orch-rb-tabs" tabs={[{ key: 'form', label: 'Steps' }, { key: 'yaml', label: 'YAML' }]} value={tab} onChange={k => { void switchTab(k as 'form' | 'yaml'); }} />

        {tab === 'yaml' ? (
          <CodeEditor label="runbook.yaml" language="yaml" value={yaml} editable={editable} onChange={v => { setYaml(v); touched(); }} testID="orch-rb-yaml" />
        ) : (
          <View style={styles.stack} pointerEvents={editable ? 'auto' : 'none'}>
            <Field label="Title"><TextField value={model.title} editable={editable} onChangeText={v => { setModel(m => ({ ...m, title: v })); touched(); }} testID="orch-rb-title" /></Field>
            <Field label="Summary" optional><TextField value={model.summary} editable={editable} onChangeText={v => { setModel(m => ({ ...m, summary: v })); touched(); }} multiline testID="orch-rb-summary" /></Field>
            {model.steps.map((s, i) => {
              const ps = stepProblems(i);
              const info = result?.steps_info?.[i];
              return (
                <View key={i} style={styles.card} testID={`orch-rb-step-${i}`}>
                  <View style={styles.cardHead}>
                    <Text variant="label" style={{ flex: 1 }}>Step {i + 1}{info?.ok && info.effect_style ? ` · ${info.effect_style.label}` : ''}</Text>
                    {editable ? (
                      <>
                        <Button size="md" variant="ghost" label="Up" onPress={() => moveStep(i, -1)} disabled={i === 0} accessibilityLabel={`Move step ${i + 1} up`} />
                        <Button size="md" variant="ghost" label="Down" onPress={() => moveStep(i, 1)} disabled={i === model.steps.length - 1} accessibilityLabel={`Move step ${i + 1} down`} />
                        <Button size="md" variant="ghost" icon="trash-2" label="Remove" onPress={() => removeStep(i)} accessibilityLabel={`Remove step ${i + 1}`} />
                      </>
                    ) : null}
                  </View>
                  <Field label="Name" error={atField(i, 'name').map(p => p.message).join('; ') || undefined}>
                    <TextField value={s.name} onChangeText={v => setStep(i, { name: v })} mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-step-${i}-name`} />
                  </Field>
                  <Field label="Tool" hint="A pinned published version, e.g. sys.disk-usage@1" error={atField(i, 'tool').map(p => p.message).join('; ') || undefined}>
                    <TextField value={s.tool} onChangeText={v => setStep(i, { tool: v })} mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-step-${i}-tool`} />
                  </Field>
                  <Field label="Params" optional hint="JSON; values may be expressions like ${{ inputs.days }}" error={atField(i, 'params').map(p => p.message).join('; ') || undefined}>
                    <TextField value={params[i] ?? ''} onChangeText={v => { setParams(p => p.map((x, j) => (j === i ? v : x))); touched(); }} multiline mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-step-${i}-params`} />
                  </Field>
                  <Field label="When" optional hint="An expression; the step is skipped when it's false" error={atField(i, 'when').map(p => p.message).join('; ') || undefined}>
                    <TextField value={String(s.when ?? '')} onChangeText={v => setStep(i, { when: v })} mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-step-${i}-when`} />
                  </Field>
                  <Text variant="label">If this step fails</Text>
                  <Segmented<string> accessibilityLabel={`Step ${i + 1}: if it fails`} value={s.on_failure || 'stop'} onChange={v => setStep(i, { on_failure: v })} options={ON_FAILURE} />
                  <Field label="Nodes" optional hint="A label selector for this step; empty runs on every node of the run" error={atField(i, 'nodes').map(p => p.message).join('; ') || undefined}>
                    <TextField value={typeof s.nodes === 'string' ? s.nodes : JSON.stringify(s.nodes)} onChangeText={v => setStep(i, { nodes: v })} mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-step-${i}-nodes`} />
                  </Field>
                  {ps.length ? (
                    <View testID={`orch-rb-step-${i}-problems`}>
                      {ps.map((p, k) => <Text key={k} variant="caption" tone={p.severity === 'warning' ? 'caution' : 'danger'}>{p.severity === 'warning' ? '◆' : '✗'} {p.path.slice(`steps[${i}]`.length).replace(/^\./, '') || 'step'}: {p.message}</Text>)}
                    </View>
                  ) : null}
                </View>
              );
            })}
            {editable ? <Button size="md" variant="secondary" icon="plus" label="Add a step" onPress={addStep} testID="orch-rb-add-step" /> : null}

            <BoardSection title="Inputs" count={rows.length} empty="No inputs. Add one to ask for a value when the runbook runs.">
              {rows.map((row, i) => (
                <View key={i} style={styles.card} testID={`orch-rb-input-${i}`}>
                  <Field label="Name"><TextField value={row.name} onChangeText={v => setRow(i, { name: v })} mono autoCapitalize="none" autoCorrect={false} testID={`orch-rb-input-${i}-name`} /></Field>
                  <CheckRow label="Required" checked={row.required} onChange={v => setRow(i, { required: v })} />
                  {ROW_FIELDS.map(f => (
                    <Field key={f} label={ROW_LABEL[f]} optional>
                      <TextField value={fieldText(row, f)} onChangeText={v => setRowField(i, f, v)} mono={f !== 'description'} autoCapitalize="none" autoCorrect={false} testID={`orch-rb-input-${i}-${f}`} />
                    </Field>
                  ))}
                  <Button size="md" variant="ghost" icon="trash-2" label="Remove input" onPress={() => { setRows(rs => rs.filter((_, j) => j !== i)); touched(); }} />
                </View>
              ))}
            </BoardSection>
            {editable ? <Button size="md" variant="ghost" icon="plus" label="Add an input" onPress={() => { setRows(rs => [...rs, { name: '', raw: { type: 'string' }, required: false, edits: {} }]); touched(); }} testID="orch-rb-add-input" /> : null}
          </View>
        )}

        <BoardSection title="Expression preview" testID="orch-rb-preview-section">
          <ExpressionPreview spec={currentSpec} inputs={tab === 'form' ? rowsToInputs(rows, model.inputs) : model.inputs} />
        </BoardSection>

        <BoardSection title="Checks" testID="orch-rb-checks">
          <View style={styles.stack}>
            {result?.decision ? <Text variant="caption" tone="ink2">Your access decision: {result.decision.level}{result.decision.summary ? ` · ${result.decision.summary}` : ''}</Text> : null}
            {groupProblems(problems, tab === 'form' ? model : null).map(([group, ps]) => (
              <View key={group} style={styles.stack}>
                <Text variant="label" tone="ink2">{group}</Text>
                <Rows>
                  {ps.map((p, k) => <Row key={k} status={p.severity === 'warning' ? 'unverified' : 'failed'} title={p.message}
                    sub={[p.path, p.line ? `line ${p.line}` : ''].filter(Boolean).join(' · ') || undefined} />)}
                </Rows>
              </View>
            ))}
            {result && !problems.length ? <Text variant="caption" tone="ink3">No problems.</Text> : null}
          </View>
        </BoardSection>
      </ScrollView>
      {dialog === 'test' ? <TestRunbookSheet runbook={r} visible onClose={() => setDialog(null)} onProposed={(runId, msg) => { setDialog(null); toast(msg, 'ok'); navigation.navigate('OrchRun', { runId }); }} /> : null}
      <UpgradeToolsSheet visible={dialog === 'upgrade'} spec={upgradeSpec} runbookRef={r.ref} published={false} onClose={() => setDialog(null)} onApplied={spec => { void applyUpgrade(spec); }} />
      <DiscoveryChatSheet visible={dialog === 'chat'} onClose={() => setDialog(null)} draftRef={r.ref} />
      {element}
    </View>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  stack: { gap: spacing.sm },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  card: { gap: spacing.sm, paddingVertical: spacing.md },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
});
