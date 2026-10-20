// Forms the Studio and the catalog share (web pickers.js / studio.js / runbooks.js dialogs):
//   ParamsForm       a tool's or runbook's input schema as fields (strings, numbers, booleans, enums;
//                    anything else as JSON)
//   CanaryPicker     active canary nodes (tests run only there; runs/hub enforce it)
//   TestToolSheet    proposeTest: a declared test or ad-hoc parameters, check mode
//   TestRunbookSheet proposeRunbookTest: canary nodes, inputs, check mode
//   SubmitSheet      submitReview: headline, risks, notes
//   RunSheet         planRun → proposeRun for a published tool or runbook, after reading the plan
// Tests are decided by `run: test` access rules; approving a run still goes through its own actions.

import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchNode, type OrchRunbookDetail, type OrchToolDetail } from '../../api/orchestration';
import { spacing } from '../../theme/tokens';
import { Banner, Button, CheckRow, ChoiceRow, Field, KV, Loading, Select, Sheet, SwitchRow, Text, TextField } from '../../ui';
import { errorText, plural } from './parts';

type Schema = { type?: string; properties?: Record<string, Prop>; required?: string[] };
type Prop = { type?: string | string[]; enum?: unknown[]; default?: unknown; description?: string; maxLength?: number; minimum?: number; maximum?: number };

const typeOf = (p: Prop) => (Array.isArray(p.type) ? p.type[0] : p.type) || (p.enum ? 'enum' : 'any');
const asText = (v: unknown) => (v === undefined || v === null ? '' : typeof v === 'string' ? v : JSON.stringify(v));

/** Read a form's text values against the schema: typed values, or errors for the fields at fault. */
export function readParams(schema: Schema | null | undefined, text: Record<string, string>): { values: Record<string, unknown>; errors: string[] } {
  const values: Record<string, unknown> = {};
  const errors: string[] = [];
  const props = schema?.properties || {};
  const required = new Set(schema?.required || []);
  Object.entries(props).forEach(([name, p]) => {
    const raw = text[name] ?? asText(p.default);
    const t = typeOf(p);
    if (raw === '' || raw === undefined) { if (required.has(name)) errors.push(`${name} is required`); return; }
    if (t === 'integer' || t === 'number') {
      const n = Number(raw);
      if (Number.isNaN(n) || (t === 'integer' && !Number.isInteger(n))) { errors.push(`${name} must be ${t === 'integer' ? 'a whole number' : 'a number'}`); return; }
      values[name] = n; return;
    }
    if (t === 'boolean') { values[name] = raw === 'true'; return; }
    if (t === 'string' || t === 'enum') { values[name] = raw; return; }
    try { values[name] = JSON.parse(raw); } catch { errors.push(`${name} must be JSON`); }
  });
  return { values, errors };
}

export function ParamsForm({ schema, value, onChange, empty = 'This tool takes no inputs.', testID = 'orch-params' }: {
  schema: Schema | null | undefined; value: Record<string, string>; onChange: (next: Record<string, string>) => void; empty?: string; testID?: string;
}) {
  const props = Object.entries(schema?.properties || {});
  const required = new Set(schema?.required || []);
  if (!props.length) return <Text variant="caption" tone="ink3" testID={testID}>{empty}</Text>;
  const set = (name: string, v: string) => onChange({ ...value, [name]: v });
  return (
    <View testID={testID} style={styles.form}>
      {props.map(([name, p]) => {
        const t = typeOf(p);
        const v = value[name] ?? asText(p.default);
        if (t === 'boolean') return <SwitchRow key={name} label={name} hint={p.description} value={v === 'true'} onValueChange={on => set(name, on ? 'true' : 'false')} testID={`${testID}-${name}`} />;
        if (Array.isArray(p.enum)) {
          return <Select key={name} label={name} value={v} onChange={x => set(name, x)} testID={`${testID}-${name}`}
            options={p.enum.map(o => ({ value: asText(o), label: asText(o) }))} />;
        }
        return (
          <Field key={name} label={name} optional={!required.has(name)} hint={[t === 'any' ? 'JSON' : t, p.description].filter(Boolean).join(' · ')}>
            <TextField value={v} onChangeText={x => set(name, x)} mono={t !== 'string'} autoCapitalize="none" autoCorrect={false}
              keyboardType={t === 'integer' || t === 'number' ? 'numeric' : 'default'} maxLength={p.maxLength} testID={`${testID}-${name}`} />
          </Field>
        );
      })}
    </View>
  );
}

const isCanary = (n: OrchNode) => n.canary || n.labels?.canary === 'true';

/** Active canary nodes; tests can only target these. */
export function CanaryPicker({ multi, value, onChange }: { multi?: boolean; value: string[]; onChange: (ids: string[]) => void }) {
  const [nodes, setNodes] = useState<OrchNode[] | null>(null);
  useEffect(() => {
    let alive = true;
    orchestrationApi.nodes().then(res => {
      if (!alive) return;
      const list = (res.nodes || []).filter(n => n.status === 'active' && isCanary(n));
      setNodes(list);
      if (!value.length && list[0]) onChange([list[0].id]);
    }).catch(() => { if (alive) setNodes([]); });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!nodes) return <Loading label="Finding canary nodes…" />;
  if (!nodes.length) return <Text variant="caption" tone="attention" testID="orch-canary-none">◆ No active canary node. Mark a node canary (or start a test node) on the computer first.</Text>;
  return (
    <View accessibilityLabel={multi ? 'Canary nodes' : 'Canary node'} style={styles.form}>
      <Text variant="label">{multi ? 'Canary nodes' : 'Canary node'}</Text>
      {nodes.map(n => (multi
        ? <CheckRow key={n.id} label={n.name || n.id} checked={value.includes(n.id)} testID={`orch-canary-${n.id}`}
            onChange={on => onChange(on ? [...value, n.id] : value.filter(x => x !== n.id))} />
        : <ChoiceRow key={n.id} label={n.name || n.id} selected={value[0] === n.id} onPress={() => onChange([n.id])} testID={`orch-canary-${n.id}`} />))}
    </View>
  );
}

const startedToast = (state: string | undefined) => (state === 'proposed' ? 'Test proposed. Approve it to run.' : 'Test started (allowed by your access policy).');

/** proposeTest for a tool draft: a declared test case or ad-hoc parameters (web studio.testDialog). */
export function TestToolSheet({ tool, visible, onClose, onProposed }: { tool: OrchToolDetail; visible: boolean; onClose: () => void; onProposed: (runId: string, message: string) => void }) {
  const tests: Array<{ name: string }> = (tool.manifest?.tests as Array<{ name: string }>) || [];
  const [nodes, setNodes] = useState<string[]>([]);
  const [testName, setTestName] = useState(tests[0]?.name || '');
  const [params, setParams] = useState<Record<string, string>>({});
  const [check, setCheck] = useState(!!tool.check_mode || tool.effect === 'read_only');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setError('');
    if (!nodes[0]) { setError('Mark a node canary or start a test node first.'); return; }
    const args: Record<string, unknown> = { ref: tool.ref, node_id: nodes[0], test_name: testName };
    if (!testName) {
      const read = readParams(tool.manifest?.inputs, params);
      if (read.errors.length) { setError(read.errors.join('; ')); return; }
      args.params = read.values;
      args.check_mode = check;
    }
    setBusy(true);
    try {
      const res = await orchestrationApi.command('proposeTest', args);
      const r = res.data.run as { id: string; state: string };
      onProposed(r.id, startedToast(r.state));
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title={`Test ${tool.ref}`} testID="orch-test-tool"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" icon="flask-conical" label="Propose test" loading={busy} onPress={() => { void submit(); }} testID="orch-test-tool-go" /></>}>
      <View style={styles.form}>
        <Text variant="caption" tone="ink3">The test becomes a run on a canary node. It waits for your approval unless your access policy has a rule that allows such tests. Check mode reports what would change without changing anything.</Text>
        <CanaryPicker value={nodes} onChange={setNodes} />
        <Select label="Test" value={testName} onChange={setTestName} testID="orch-test-tool-case"
          options={[...tests.map(t => ({ value: t.name, label: `${t.name} (declared)` })), { value: '', label: 'Ad-hoc parameters' }]} />
        {!testName ? (
          <>
            <ParamsForm schema={tool.manifest?.inputs} value={params} onChange={setParams} />
            <SwitchRow label="Check mode (dry run)" value={check} onValueChange={setCheck} testID="orch-test-tool-check" />
          </>
        ) : null}
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-test-tool-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

/** proposeRunbookTest: the draft on canary nodes with inputs; refuses to start if the draft changes after approval. */
export function TestRunbookSheet({ runbook, visible, onClose, onProposed }: { runbook: OrchRunbookDetail; visible: boolean; onClose: () => void; onProposed: (runId: string, message: string) => void }) {
  const [nodes, setNodes] = useState<string[]>([]);
  const [params, setParams] = useState<Record<string, string>>({});
  const [check, setCheck] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setError('');
    if (!nodes.length) { setError('Choose a canary node (mark one canary or start a test node).'); return; }
    const read = readParams(runbook.inputs, params);
    if (read.errors.length) { setError(read.errors.join('; ')); return; }
    setBusy(true);
    try {
      const res = await orchestrationApi.command('proposeRunbookTest', { ref: runbook.ref, nodes, params: read.values, check_mode: check });
      const r = res.data.run as { id: string; state: string };
      onProposed(r.id, startedToast(r.state));
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title={`Test ${runbook.ref} on a canary`} testID="orch-test-runbook"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" icon="flask-conical" label="Propose test" loading={busy} onPress={() => { void submit(); }} testID="orch-test-runbook-go" /></>}>
      <View style={styles.form}>
        <Text variant="caption" tone="ink3">The test runs this draft on canary nodes. Its steps use published tools. It is approved like a tool test unless your access policy allows it; the run refuses to start if the draft changes after approval.</Text>
        <CanaryPicker multi value={nodes} onChange={setNodes} />
        <ParamsForm schema={runbook.inputs} value={params} onChange={setParams} empty="This runbook takes no inputs." />
        <SwitchRow label="Check mode (dry run: report, change nothing)" value={check} onValueChange={setCheck} />
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-test-runbook-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

/** submitReview with a headline and the risks the author calls out (web studio.submitDialog). */
export function SubmitSheet({ tool, visible, onClose, onSubmitted }: { tool: OrchToolDetail; visible: boolean; onClose: () => void; onSubmitted: () => void }) {
  const [headline, setHeadline] = useState(tool.review?.headline || '');
  const [risks, setRisks] = useState((tool.review?.risks || []).join('\n'));
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setError('');
    if (!headline.trim()) { setError('Add a one-line headline.'); return; }
    setBusy(true);
    try {
      await orchestrationApi.command('submitReview', { ref: tool.ref, headline: headline.trim(), risks: risks.split('\n').map(s => s.trim()).filter(Boolean), notes: notes.trim() });
      onSubmitted();
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title={`Submit ${tool.ref} for review`} testID="orch-submit"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label="Submit for review" loading={busy} onPress={() => { void submit(); }} testID="orch-submit-go" /></>}>
      <View style={styles.form}>
        <Text variant="caption" tone="ink3">You review it next: the manifest, the diff against the previous version, lint and test results. Nothing can run on the fleet until it is published.</Text>
        <Field label="Headline"><TextField value={headline} onChangeText={setHeadline} placeholder="Rotate nginx logs with configurable retention" testID="orch-submit-headline" /></Field>
        <Field label="Risks" hint="One per line."><TextField value={risks} onChangeText={setRisks} multiline testID="orch-submit-risks" /></Field>
        <Field label="Notes" optional><TextField value={notes} onChangeText={setNotes} multiline testID="orch-submit-notes" /></Field>
        {!tool.lint?.ok || !tool.lint?.current ? <Banner tone="caution" glyph="◆" title="Lint hasn’t passed on the current draft. Publishing needs a passing lint." /> : null}
        {!tool.tests?.count ? <Banner tone="caution" glyph="◆" title="This draft hasn’t been tested on a canary yet." /> : null}
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-submit-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

type Plan = { ok?: boolean; nodes?: string[]; errors?: string[]; warnings?: string[]; blast_radius?: string; effect?: string; batches?: string[][];
  decision?: { level?: string; summary?: string }; excluded?: Array<{ node_id: string; name?: string; reason: string }>; check_mode?: boolean };

/** Run a published tool or runbook: choose nodes and inputs, read the plan, then propose (web new-run). */
export function RunSheet({ kind, target, inputs, visible, onClose, onProposed }: {
  kind: 'tool' | 'runbook'; target: string; inputs: Schema | null | undefined; visible: boolean; onClose: () => void; onProposed: (runId: string, message: string) => void;
}) {
  const [all, setAll] = useState(false);
  const [labels, setLabels] = useState('');
  const [params, setParams] = useState<Record<string, string>>({});
  const [check, setCheck] = useState(false);
  const [title, setTitle] = useState('');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const selector = useMemo(() => (all ? { all: true } : { labels: labels.trim() }), [all, labels]);
  useEffect(() => { setPlan(null); }, [selector, params, check]);
  const read = () => {
    const r = readParams(inputs, params);
    if (r.errors.length) { setError(r.errors.join('; ')); return null; }
    if (!all && !labels.trim()) { setError('Choose nodes: a label selector like role=web, or all nodes.'); return null; }
    return r.values;
  };
  const preview = async () => {
    setError('');
    const values = read();
    if (!values) return;
    setBusy(true);
    try {
      const res = await orchestrationApi.command('planRun', { kind, target, params: values, selector, check_mode: check });
      setPlan(res.data.plan as Plan);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const propose = async () => {
    setError('');
    const values = read();
    if (!values) return;
    setBusy(true);
    try {
      const res = await orchestrationApi.command('proposeRun', { kind, target, params: values, selector, check_mode: check, title: title.trim() });
      const r = res.data.run as { id: string; state: string };
      onProposed(r.id, r.state === 'proposed' ? 'Run proposed. Approve it to start.' : 'Run started (allowed by your access policy).');
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  const blocked = !!plan && (plan.ok === false || !!plan.errors?.length);
  return (
    <Sheet visible={visible} onClose={onClose} title={`Run ${target}`} full testID="orch-run-sheet"
      footer={<>
        <Button variant="secondary" label="Cancel" onPress={onClose} />
        {plan && !blocked ? <Button variant="primary" label="Propose run" loading={busy} onPress={() => { void propose(); }} testID="orch-run-sheet-propose" />
          : <Button variant="primary" label="Preview the plan" loading={busy} onPress={() => { void preview(); }} testID="orch-run-sheet-plan" />}
      </>}>
      <View style={styles.form}>
        <SwitchRow label="All nodes" value={all} onValueChange={setAll} testID="orch-run-sheet-all" />
        {!all ? <Field label="Nodes" hint="A label selector, e.g. role=web,env!=prod"><TextField value={labels} onChangeText={setLabels} mono autoCapitalize="none" autoCorrect={false} testID="orch-run-sheet-labels" /></Field> : null}
        <ParamsForm schema={inputs} value={params} onChange={setParams} empty={kind === 'runbook' ? 'This runbook takes no inputs.' : 'This tool takes no inputs.'} />
        <SwitchRow label="Check mode (dry run)" value={check} onValueChange={setCheck} />
        <Field label="Title" optional><TextField value={title} onChangeText={setTitle} testID="orch-run-sheet-title" /></Field>
        {plan ? (
          <View style={styles.form} testID="orch-run-sheet-preview">
            {plan.errors?.length ? <Banner tone="danger" glyph="✗" title="This plan can’t run">{plan.errors.join('; ')}</Banner> : null}
            <KV label="Blast radius">{plan.blast_radius || plural((plan.nodes || []).length, 'node')}</KV>
            {plan.effect ? <KV label="Effect">{plan.effect}</KV> : null}
            <KV label="Access">{[plan.decision?.level, plan.decision?.summary].filter(Boolean).join(' · ') || '—'}</KV>
            {plan.batches?.length ? <KV label="Batches">{String(plan.batches.length)}</KV> : null}
            {(plan.warnings || []).map((w, i) => <Text key={i} variant="caption" tone="attention">◆ {w}</Text>)}
            {(plan.excluded || []).map(x => <Text key={x.node_id} variant="caption" tone="ink3">Excluded {x.name || x.node_id}: {x.reason}</Text>)}
          </View>
        ) : null}
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-run-sheet-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  form: { gap: spacing.sm },
});
