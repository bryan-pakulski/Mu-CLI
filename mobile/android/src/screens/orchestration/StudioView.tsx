// Studio (web studio.js renderList): open tool drafts with their discovery progress, lint and test
// state; runbook drafts with their problems; new tools (describe it, or paste a manifest) and new
// runbooks. The AI can draft, lint, test on canaries and submit for review — only you publish,
// approve runs or change policy.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchToolBrief } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { vocabulary } from '../../theme/wash.generated';
import { Banner, BoardSection, Button, Field, Loading, MiniStepper, Row, Segmented, Sheet, SwitchRow, Text, TextField } from '../../ui';
import { DiscoveryChatSheet } from './AskSheets';
import { OPEN_STATUSES } from './CatalogView';
import { ago, errorText, plural } from './parts';
import { RUNBOOK_ID } from './runbookModel';
import { useOrchData } from './useOrchData';

const STAGES = Object.keys((vocabulary as Record<string, unknown>).orch_stage as Record<string, { label: string }>);
const stageLabel = (s: string) => ((vocabulary as Record<string, any>).orch_stage?.[s]?.label as string) || s;

/** The discovery stepper's steps for a draft (web studio.stepper). */
export function stageSteps(t: Pick<OrchToolBrief, 'stage' | 'status'>) {
  const cur = Math.max(0, STAGES.indexOf(t.stage));
  const published = ['published', 'deprecated', 'retired'].includes(t.status);
  return STAGES.map((s, i) => {
    let status = i < cur || published ? 'done' : i === cur ? 'active' : 'pending';
    if (!published && i === cur && t.status === 'in_review') status = 'waiting';
    return { id: s, label: stageLabel(s), status };
  });
}

export function lintWords(t: Pick<OrchToolBrief, 'lint'>): string {
  const l = t.lint;
  if (!l || l.stale || !l.current) return 'lint not run on this draft';
  return l.ok ? 'lint passed' : `lint: ${Object.entries(l.counts || {}).map(([k, n]) => `${n} ${k}`).join(', ') || 'failed'}`;
}
export function testsWords(t: Pick<OrchToolBrief, 'tests'>): string {
  const x = t.tests;
  if (!x?.count) return 'not tested';
  return `${x.last?.passed ? 'last test passed' : 'last test failed'} · ${x.passed}/${x.count}`;
}

export function StudioView({ openToolEditor, openRunbookEditor, openTool, openRunbook }: {
  openToolEditor: (id: string, v: number) => void; openRunbookEditor: (id: string, v: number) => void;
  openTool: (id: string, v: number) => void; openRunbook: (id: string, v: number) => void;
}) {
  const { colors } = useTheme();
  const { data, error, refreshing, refresh } = useOrchData(() => orchestrationApi.tools(), ['tool']);
  const [newTool, setNewTool] = useState(false);
  const [newRunbook, setNewRunbook] = useState(false);
  const [chat, setChat] = useState<string | null>(null);
  const drafts = (data?.tools || []).filter(v => OPEN_STATUSES.includes(v.status)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  const rbDrafts = (data?.runbooks || []).filter(r => ['draft', 'in_review'].includes(r.status)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={styles.pad} testID="orch-studio"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
        <View style={styles.buttons}>
          <Button size="md" variant="primary" icon="plus" label="New tool" onPress={() => setNewTool(true)} testID="orch-studio-new-tool" />
          <Button size="md" variant="secondary" icon="plus" label="New runbook" onPress={() => setNewRunbook(true)} testID="orch-studio-new-runbook" />
          <Button size="md" variant="ghost" icon="message-circle" label="Discovery chat" onPress={() => setChat('')} testID="orch-studio-chat" />
        </View>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
        {!data && !error ? <Loading label="Loading drafts…" /> : null}
        {data ? (
          <>
            <BoardSection title="Tool drafts" count={drafts.length} testID="orch-studio-drafts" empty="No drafts in progress. Start a tool and the AI walks you through intent, spec, discovery, implementation and tests.">
              {drafts.map(t => (
                <Row key={t.ref} testID={`orch-draft-${t.ref}`} status={orchState('orch_tool_status', t.status, t.style).status} title={t.title} pinned={t.status === 'in_review'}
                  sub={<View style={styles.sub}>
                    <MiniStepper steps={stageSteps(t)} />
                    <Text variant="caption" tone="ink3" numberOfLines={2}>{[t.ref, t.effect_style?.label, lintWords(t), testsWords(t), `by ${t.created_by}`, t.updated_at ? `updated ${ago(t.updated_at)}` : ''].filter(Boolean).join(' · ')}</Text>
                    {t.intent ? <Text variant="caption" tone="ink2" numberOfLines={2}>{t.intent}</Text> : null}
                  </View>}
                  onPress={() => (t.status === 'in_review' ? openTool(t.tool_id, t.version) : openToolEditor(t.tool_id, t.version))}
                  actions={<Button size="md" variant="ghost" icon="message-circle" label={t.session_name ? 'Continue chat' : 'Discovery chat'} onPress={() => setChat(t.ref)} />} />
              ))}
            </BoardSection>
            <BoardSection title="Runbook drafts" count={rbDrafts.length} testID="orch-studio-runbooks" empty="No runbook drafts. Compose published tools into ordered steps with when: conditions.">
              {rbDrafts.map(r => {
                const n = (r.problems || []).length;
                return <Row key={r.ref} testID={`orch-rbdraft-${r.ref}`} status={orchState('orch_tool_status', r.status, r.style).status} title={r.title} pinned={r.status === 'in_review'}
                  sub={[r.ref, plural((r.steps || []).length, 'step'), n ? plural(n, 'problem') : 'valid', r.parses === false ? 'YAML doesn’t parse' : '', r.updated_at ? `updated ${ago(r.updated_at)}` : ''].filter(Boolean).join(' · ')}
                  onPress={() => (r.status === 'in_review' ? openRunbook(r.runbook_id, r.version) : openRunbookEditor(r.runbook_id, r.version))} />;
              })}
            </BoardSection>
            <Text variant="caption" tone="ink3">The AI can draft, lint, test on canaries and submit for review. Only you publish, approve runs or change policy.</Text>
          </>
        ) : null}
      </ScrollView>
      <NewToolSheet visible={newTool} onClose={() => setNewTool(false)} onCreated={(ref, openChat) => {
        setNewTool(false);
        const [id, v] = ref.split('@');
        openToolEditor(id, Number(v));
        if (openChat) setChat(ref);
      }} />
      <NewRunbookSheet visible={newRunbook} onClose={() => setNewRunbook(false)} onCreated={ref => {
        setNewRunbook(false);
        const [id, v] = ref.split('@');
        openRunbookEditor(id, Number(v));
      }} />
      <DiscoveryChatSheet visible={chat != null} onClose={() => setChat(null)} draftRef={chat || ''} />
    </View>
  );
}

/** New tool: describe it (id + intent) or paste a manifest (createDraft). */
export function NewToolSheet({ visible, onClose, onCreated }: { visible: boolean; onClose: () => void; onCreated: (ref: string, openChat: boolean) => void }) {
  const [mode, setMode] = useState<'intent' | 'yaml'>('intent');
  const [toolId, setToolId] = useState('');
  const [intent, setIntent] = useState('');
  const [yaml, setYaml] = useState('');
  const [chat, setChat] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setError('');
    const args = mode === 'yaml' ? { manifest: yaml } : { tool_id: toolId.trim(), intent: intent.trim() };
    if (mode === 'intent' && !toolId.trim()) { setError('Give the tool an id.'); return; }
    if (mode === 'yaml' && !yaml.trim()) { setError('Paste a manifest.'); return; }
    setBusy(true);
    try {
      const res = await orchestrationApi.command('createDraft', args);
      onCreated(String(res.data.ref), chat);
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title="New tool" testID="orch-new-tool"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label="Create draft" loading={busy} onPress={() => { void submit(); }} testID="orch-new-tool-create" /></>}>
      <View style={styles.form}>
        <Segmented<'intent' | 'yaml'> accessibilityLabel="Start from" value={mode} onChange={setMode}
          options={[{ value: 'intent', label: 'Describe it', testID: 'orch-new-tool-intent' }, { value: 'yaml', label: 'Paste a manifest', testID: 'orch-new-tool-yaml' }]} />
        {mode === 'intent' ? (
          <>
            <Field label="Tool id" hint="A dotted lowercase name. Versions are numbered for you.">
              <TextField value={toolId} onChangeText={setToolId} mono autoCapitalize="none" autoCorrect={false} placeholder="nginx.rotate-logs" testID="orch-new-tool-id" />
            </Field>
            <Field label="What should it do?" hint="Goal, targets, what done looks like and what must never happen. The AI asks follow-up questions in the chat.">
              <TextField value={intent} onChangeText={setIntent} multiline testID="orch-new-tool-what" />
            </Field>
          </>
        ) : (
          <Field label="Manifest YAML">
            <TextField value={yaml} onChangeText={setYaml} multiline mono autoCapitalize="none" autoCorrect={false} testID="orch-new-tool-manifest" />
          </Field>
        )}
        <SwitchRow label="Open the discovery chat afterwards" value={chat} onValueChange={setChat} />
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-new-tool-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

/** New runbook: an id and a title; the draft starts with one empty step (saveRunbookDraft). */
export function NewRunbookSheet({ visible, onClose, onCreated }: { visible: boolean; onClose: () => void; onCreated: (ref: string) => void }) {
  const [id, setId] = useState('');
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setError('');
    if (!RUNBOOK_ID.test(id.trim())) { setError('Use a dotted lowercase id like web.safe-deploy.'); return; }
    if (!title.trim()) { setError('Give it a title.'); return; }
    setBusy(true);
    try {
      const res = await orchestrationApi.command('saveRunbookDraft', { spec: { id: id.trim(), title: title.trim(), steps: [{ name: 'step1', tool: '' }] } });
      onCreated(String(res.data.ref));
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Sheet visible={visible} onClose={onClose} title="New runbook" testID="orch-new-runbook"
      footer={<><Button variant="secondary" label="Cancel" onPress={onClose} /><Button variant="primary" label="Create draft" loading={busy} onPress={() => { void submit(); }} testID="orch-new-runbook-create" /></>}>
      <View style={styles.form}>
        <Field label="Runbook id" hint="A dotted lowercase name. It can’t change after the runbook is created.">
          <TextField value={id} onChangeText={setId} mono autoCapitalize="none" autoCorrect={false} placeholder="web.safe-deploy" testID="orch-new-runbook-id" />
        </Field>
        <Field label="Title"><TextField value={title} onChangeText={setTitle} placeholder="Safe web deploy" testID="orch-new-runbook-title" /></Field>
        {error ? <Text variant="caption" tone="danger" accessibilityRole="alert" testID="orch-new-runbook-error">✗ {error}</Text> : null}
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.sm },
  buttons: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm },
  sub: { gap: spacing.xs, marginTop: spacing.xs },
  form: { gap: spacing.md },
});
