// Runbook editor tools the web keeps beside the editor (web runbooks.js):
//   ExpressionPreview  what each step's `when:` and params resolve to on one node with sample
//                      inputs (validateRunbook with `preview`); outputs of earlier steps are null
//   UpgradeToolsSheet  steps whose tool has a newer published version: the impact per step
//                      (effect, rejected params, outputs other steps use, rollback, manifest diff),
//                      then apply to the draft, or start a new version from a published runbook
// Both are read-only on the server (upgradeRunbookTools never stores anything); saving or the new
// version goes through the usual commands.

import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchEffectStyle, type OrchNode, type OrchProblem } from '../../api/orchestration';
import { spacing } from '../../theme/tokens';
import { Banner, Button, CheckRow, DiffView, Row, Rows, Select, Sheet, Text, useToast } from '../../ui';
import { ParamsForm, readParams } from './forms';
import { errorText, plural } from './parts';

/* ------------------------------------------------------------ expression preview */

export type PreviewStep = {
  name: string; runs_here: boolean; nodes_error?: string;
  when?: { value?: unknown; error?: string }; params?: unknown; params_error?: string;
};

const json = (v: unknown) => JSON.stringify(v);

/** One preview row's facts in words (the web table's columns). */
export function previewFacts(s: PreviewStep): { runs: string; when: string; params: string; failed: boolean } {
  return {
    runs: s.nodes_error ? s.nodes_error : s.runs_here ? 'runs here' : "doesn't run here (node selector)",
    when: s.when ? (s.when.error ? s.when.error : `when: ${json(s.when.value)}`) : 'always',
    params: s.params_error ? s.params_error : json(s.params ?? {}),
    failed: !!(s.nodes_error || s.when?.error || s.params_error),
  };
}

export function ExpressionPreview({ spec, inputs, testID = 'orch-rb-preview' }: {
  spec: () => unknown; inputs: Record<string, any> | null | undefined; testID?: string;
}) {
  const [nodes, setNodes] = useState<OrchNode[]>([]);
  const [node, setNode] = useState('');
  const [sample, setSample] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<PreviewStep[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    orchestrationApi.nodes().then(res => { if (alive) setNodes((res.nodes || []).filter(n => n.status === 'active')); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const preview = async () => {
    const read = readParams(inputs, sample);
    if (read.errors.length) { setError(read.errors.join('; ')); return; }
    setBusy(true); setError('');
    try {
      const res = await orchestrationApi.command('validateRunbook', { spec: spec(), preview: { node_id: node, inputs: read.values } });
      const p = (res.data as { preview?: { steps: PreviewStep[] } | null }).preview;
      if (!p) { setSteps(null); setError('Fix the YAML first.'); return; }
      setSteps(p.steps || []);
    } catch (err) {
      setError(errorText(err));
    } finally { setBusy(false); }
  };

  return (
    <View style={styles.stack} testID={testID}>
      <Text variant="caption" tone="ink3">What each when: and parameter resolves to on one node, with sample inputs. Outputs of earlier steps aren’t known before a run, so they are null here.</Text>
      <Select label="Node" value={node} onChange={setNode} placeholder="No node (empty labels)" testID={`${testID}-node`}
        options={[{ value: '', label: 'No node (empty labels)' }, ...nodes.map(n => ({ value: n.id, label: n.name || n.id }))]} />
      <ParamsForm schema={inputs} value={sample} onChange={setSample} empty="No inputs." testID={`${testID}-inputs`} />
      <Button size="md" variant="secondary" icon="eye" label="Preview" loading={busy} disabled={busy} onPress={() => { void preview(); }} testID={`${testID}-run`} />
      <View accessibilityLiveRegion="polite">
        {error ? <Text variant="caption" tone="danger" testID={`${testID}-error`}>✗ {error}</Text> : null}
        {steps ? (
          <Rows empty="No steps." testID={`${testID}-rows`}>
            {steps.map((s, i) => {
              const f = previewFacts(s);
              return <Row key={`${i}-${s.name}`} status={f.failed ? 'failed' : s.runs_here ? 'done' : 'skipped'} title={s.name}
                sub={`${f.runs} · ${f.when} · params ${f.params}`} testID={`${testID}-step-${i}`} />;
            })}
          </Rows>
        ) : null}
      </View>
    </View>
  );
}

/* ------------------------------------------------------------ upgrade tools */

type Decision = { level: string; summary?: string };
export type UpgradeStep = {
  index: number; name: string; from: string; to: string; diff?: string;
  effect: { from: string; to: string; changed: boolean; from_style?: OrchEffectStyle; to_style?: OrchEffectStyle };
  rejected_params?: OrchProblem[]; removed_outputs?: string[]; removed_referenced?: Array<{ step: string; output: string }>;
  rollback?: { from: string; to: string };
};
export type UpgradeReport = {
  steps: UpgradeStep[]; spec: Record<string, any>;
  effect: { from: string; to: string; changed: boolean; from_style?: OrchEffectStyle; to_style?: OrchEffectStyle };
  decision: { from: Decision; to: Decision };
};

const DECISION_LABEL: Record<string, string> = { allow: 'Allowed', confirm: 'Needs approval', typed: 'Typed confirmation', deny: 'Denied by policy' };
const decisionLabel = (d?: Decision) => (d ? DECISION_LABEL[d.level] || d.level || 'Unknown' : '');
const effectLabel = (style: OrchEffectStyle | undefined, effect: string) => style?.label || effect || '';

/** What changes in one step if its tool is upgraded, as lines (the web card's notes). */
export function upgradeNotes(s: UpgradeStep): Array<{ text: string; problem: boolean }> {
  const notes: Array<{ text: string; problem: boolean }> = [];
  if (s.effect?.changed) notes.push({ text: `The effect changes: ${effectLabel(s.effect.from_style, s.effect.from)} → ${effectLabel(s.effect.to_style, s.effect.to)}`, problem: false });
  (s.rejected_params || []).forEach(p => notes.push({ text: p.path ? `${p.path}: ${p.message}` : p.message, problem: true }));
  (s.removed_referenced || []).forEach(x => notes.push({ text: `Step ${x.step} uses output ${x.output}, which ${s.to} no longer declares`, problem: true }));
  if ((s.removed_outputs || []).length && !(s.removed_referenced || []).length) notes.push({ text: `Outputs removed: ${(s.removed_outputs || []).join(', ')} (nothing here uses them)`, problem: false });
  const rb = s.rollback || { from: '', to: '' };
  if (rb.from !== rb.to) {
    notes.push({ text: rb.to ? (rb.from ? `Rollback changes to ${rb.to}` : `Rollback added: ${rb.to}`) : 'Rollback removed: changes can’t be undone automatically', problem: false });
  }
  return notes;
}

/**
 * Check for newer published tool versions and apply the chosen upgrades. A draft gets the upgraded
 * spec back (`onApplied`; nothing is saved until the user saves); a published runbook starts a new
 * draft version with it (`onNewVersion` with the new ref).
 */
export function UpgradeToolsSheet({ visible, spec, runbookRef, published, onClose, onApplied, onNewVersion }: {
  visible: boolean; spec: unknown; runbookRef: string; published: boolean; onClose: () => void;
  onApplied?: (spec: Record<string, any>) => void; onNewVersion?: (ref: string) => void;
}) {
  const toast = useToast();
  const [report, setReport] = useState<UpgradeReport | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const [diffOpen, setDiffOpen] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!visible) { setReport(null); setError(''); setDiffOpen(null); return undefined; }
    let alive = true;
    orchestrationApi.command('upgradeRunbookTools', { spec }).then(res => {
      if (!alive) return;
      const r = res.data as unknown as UpgradeReport;
      if (!(r.steps || []).length) { toast('Every step already uses the latest published version of its tool.', 'info'); onClose(); return; }
      setReport(r);
      setChosen(r.steps.map(s => s.index));
    }).catch(err => { if (alive) { toast(`Couldn’t check for upgrades: ${errorText(err)}`, 'danger'); onClose(); } });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const apply = async () => {
    if (!chosen.length) { setError('Select at least one step.'); return; }
    setBusy(true); setError('');
    try {
      const res = await orchestrationApi.command('upgradeRunbookTools', { spec, steps: chosen });
      const upgraded = (res.data as unknown as UpgradeReport).spec;
      if (published) {
        const out = await orchestrationApi.command('newRunbookVersion', { ref: runbookRef, spec: upgraded });
        const newRef = String(out.data.ref || '');
        onClose();
        toast(`${newRef} started with upgraded tools`, 'ok');
        onNewVersion?.(newRef);
      } else {
        onClose();
        onApplied?.(upgraded);
        toast(`Upgraded ${plural(chosen.length, 'step')}. Save to keep it.`, 'ok');
      }
    } catch (err) {
      setError(errorText(err));
    } finally { setBusy(false); }
  };

  const r = report;
  return (
    <Sheet visible={visible && !!r} onClose={onClose} title="Upgrade tools" full testID="orch-upgrade-sheet">
      {r ? (
        <View style={styles.stack}>
          <Text variant="caption" tone="ink3">
            Steps whose tool has a newer published version. Check what changes, then apply.{published ? ' Applying starts a new draft version with the upgraded tools.' : ' Applying edits this draft; nothing is saved until you save.'}
          </Text>
          <Text variant="caption" tone="ink2" testID="orch-upgrade-summary">
            Runbook effect: {effectLabel(r.effect.from_style, r.effect.from)}{r.effect.changed ? ` → ${effectLabel(r.effect.to_style, r.effect.to)}` : ''}
            {' · '}Your access decision: {decisionLabel(r.decision.from)}{r.decision.from?.level !== r.decision.to?.level ? ` → ${decisionLabel(r.decision.to)}` : ''}
          </Text>
          {r.steps.map(s => {
            const notes = upgradeNotes(s);
            return (
              <View key={s.index} style={styles.step} testID={`orch-upgrade-step-${s.index}`}>
                <CheckRow label={`Step ${s.name}: ${s.from} → ${s.to}`} checked={chosen.includes(s.index)} testID={`orch-upgrade-pick-${s.index}`}
                  onChange={on => setChosen(c => (on ? [...c, s.index] : c.filter(x => x !== s.index)))} />
                {notes.length
                  ? notes.map((n, i) => <Text key={i} variant="caption" tone={n.problem ? 'danger' : 'ink2'}>{n.problem ? '✗ ' : ''}{n.text}</Text>)
                  : <Text variant="caption" tone="ink3">No effect, parameter, output or rollback changes.</Text>}
                {s.diff ? (
                  <>
                    <Button size="md" variant="ghost" label={diffOpen === s.index ? 'Hide manifest diff' : 'Manifest diff'} onPress={() => setDiffOpen(o => (o === s.index ? null : s.index))} testID={`orch-upgrade-diff-${s.index}`} />
                    {diffOpen === s.index ? <DiffView patch={s.diff} /> : null}
                  </>
                ) : <Text variant="caption" tone="ink3">The manifests are identical.</Text>}
              </View>
            );
          })}
          {error ? <Banner tone="danger" title="Couldn’t upgrade" testID="orch-upgrade-error">{error}</Banner> : null}
          <View style={styles.buttons}>
            <Button size="md" variant="secondary" label="Cancel" onPress={onClose} />
            <Button size="md" variant="primary" icon="refresh-cw" label={published ? 'New version with upgraded tools' : 'Apply upgrades'} loading={busy} disabled={busy}
              onPress={() => { void apply(); }} testID="orch-upgrade-apply" />
          </View>
        </View>
      ) : null}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  stack: { gap: spacing.sm },
  step: { gap: spacing.xs, paddingVertical: spacing.sm },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, justifyContent: 'flex-end' },
});
