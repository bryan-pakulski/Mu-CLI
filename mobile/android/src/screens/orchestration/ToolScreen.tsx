// A tool version (web catalog.js renderTool): the server's actions — lint, test on a canary, submit
// for review, request changes, publish (typed for destructive tools), deprecate, retire, new
// version, discard, run — then Overview (behaviour, review, inputs, outputs), Results (each node's
// latest output, as one table), Manifest, Files, Diff, Lint, Tests, Versions, Notes and Usage.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, Share, StyleSheet, View } from 'react-native';
import { orchestrationApi, type OrchToolDetail } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Button, CodeBlock, DiffView, ErrorState, KV, Loading, PageHeader, Row, Rows, Text, useToast, type StatePart } from '../../ui';
import { ResultsTable } from '../../ui/ResultsTable';
import { RunSheet, SubmitSheet, TestToolSheet } from './forms';
import { OPEN_STATUSES } from './CatalogView';
import { FleetChatSheet, resultsQuestion } from './AskSheets';
import { OrchActionBar, RunRow, ago, cap, errorText, useOrchActions } from './parts';
import { lintWords, testsWords } from './StudioView';
import { useOrchData } from './useOrchData';

type Tab = 'overview' | 'results' | 'manifest' | 'files' | 'diff' | 'lint' | 'tests' | 'versions' | 'notes' | 'usage';

export function versionState(v: { style?: { label?: string; status?: string }; status: string; effect: string; effect_style?: { label: string }; ref: string }, table: 'orch_tool_status' = 'orch_tool_status'): StatePart[] {
  const st = orchState(table, v.status, v.style as never);
  return [
    { text: cap(st.label), tone: st.status === 'waiting' ? 'attention' : st.status === 'failed' ? 'danger' : '' },
    v.effect_style ? { text: v.effect_style.label, tone: v.effect === 'destructive' ? 'danger' : '' } : null,
    { text: v.ref, tone: 'quiet' },
  ].filter(Boolean) as StatePart[];
}

export function SchemaRows({ schema, empty }: { schema: Record<string, any> | undefined; empty: string }) {
  const props = Object.entries((schema || {}).properties || {}) as Array<[string, Record<string, any>]>;
  const required = new Set((schema || {}).required || []);
  return (
    <Rows empty={empty}>
      {props.map(([name, p]) => <Row key={name} title={`${name}${required.has(name) ? ' · required' : ''}`}
        sub={[Array.isArray(p.type) ? p.type.join(' | ') : (p.type || (p.enum ? 'enum' : 'any')), p.default !== undefined ? `default ${JSON.stringify(p.default)}` : '',
          p.enum ? `one of ${p.enum.map((v: unknown) => JSON.stringify(v)).join(', ')}` : '', p.description || ''].filter(Boolean).join(' · ')} />)}
    </Rows>
  );
}

export function NotesRows({ notes }: { notes: Array<{ text: string; at: number; actor?: string; action?: string; tone?: string }> }) {
  return (
    <Rows empty="No notes yet.">
      {notes.map((n, i) => <Row key={i} status={n.tone === 'danger' ? 'failed' : n.tone === 'ok' ? 'done' : n.tone === 'attention' || n.tone === 'warn' ? 'waiting' : 'pending'}
        title={n.text} sub={[n.actor, n.action].filter(Boolean).join(' · ')} when={ago(n.at)} />)}
    </Rows>
  );
}

export function OrchToolScreen({ route, navigation }: { route: { params: { toolId: string; version: number; tab?: Tab } }; navigation: any }) {
  const { colors } = useTheme();
  const toast = useToast();
  const { toolId, version } = route.params;
  const [tab, setTab] = useState<Tab>(route.params.tab || 'overview');
  const [dialog, setDialog] = useState<'test' | 'run' | 'submit' | null>(null);
  const [ask, setAsk] = useState<string | null>(null);
  const { data: t, error, refreshing, refresh, reload } = useOrchData(() => orchestrationApi.tool(toolId, version),
    c => c.scope === 'tool' && (!c.id || c.id === toolId), [toolId, version]);
  const results = useOrchData(() => (tab === 'results' ? orchestrationApi.toolResults(toolId) : Promise.resolve(null)), c => c.scope === 'run', [toolId, tab]);
  const { run, element } = useOrchActions();
  const back = { label: 'Catalog', onPress: () => navigation.goBack() };

  if (!t) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title={`${toolId} v${version}`} back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading tool…" /></View>}
      </View>
    );
  }
  const findings = (t.lint_result?.findings || []).length;
  const tabs = [
    { key: 'overview', label: 'Overview' }, { key: 'results', label: 'Results' }, { key: 'manifest', label: 'Manifest' },
    { key: 'files', label: 'Files', count: Object.keys(t.files || {}).length }, { key: 'diff', label: 'Diff' },
    { key: 'lint', label: 'Lint', count: findings || null }, { key: 'tests', label: 'Tests', count: t.tests?.count || null },
    { key: 'versions', label: 'Versions', count: (t.versions || []).length }, { key: 'notes', label: 'Notes' }, { key: 'usage', label: 'Usage', count: (t.usage || []).length || null },
  ];
  const editable = OPEN_STATUSES.includes(t.status);
  const done = (result: { data: Record<string, unknown> }, action: { id: string }) => {
    const ref = String(result?.data?.ref || '');
    if (action.id === 'newVersion' && ref) { const [id, v] = ref.split('@'); navigation.replace('OrchToolEditor', { toolId: id, version: Number(v) }); return; }
    if (action.id === 'discardDraft') { navigation.goBack(); return; }
    void reload();
  };
  const opened = (runId: string, message: string) => { setDialog(null); toast(message, 'ok'); navigation.navigate('OrchRun', { runId }); };
  const shareCsv = async (_step: number, view: string) => {
    try { await Share.share({ title: `${t.tool_id} results`, message: String(await orchestrationApi.toolResultsCsv(t.tool_id, view)) }); }
    catch (err) { toast(`Couldn’t share the CSV: ${errorText(err)}`, 'danger'); }
  };

  return (
    <View style={{ flex: 1 }} testID="orch-tool">
      <PageHeader testID="orch-tool-head" title={`${t.title} v${t.version}`} back={back} state={versionState(t)} tabs={tabs} tab={tab} onTab={k => setTab(k as Tab)} />
      <ScrollView contentContainerStyle={styles.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
        <OrchActionBar testID="orch-tool-actions" actions={t.actions} run={run} ctx={{
          args: { ref: t.ref }, done,
          dialogs: { test: () => setDialog('test'), run: () => setDialog('run') },
          overrides: { submitReview: () => setDialog('submit') },
        }} />
        {editable ? <Button size="md" variant="ghost" icon="pencil" label="Open in the Studio" onPress={() => navigation.navigate('OrchToolEditor', { toolId: t.tool_id, version: t.version })} testID="orch-tool-edit" /> : null}
        <Text variant="caption" tone="ink3">{lintWords(t)} · {testsWords(t)}</Text>

        {tab === 'overview' ? (
          <View style={styles.stack}>
            {t.status === 'in_review' && (t.review?.headline || t.review?.risks?.length) ? (
              <Banner tone="attention" glyph="◆" title={t.review?.headline || 'Submitted for review'} testID="orch-tool-review">
                {[(t.review?.risks || []).length ? `Risks: ${(t.review?.risks || []).join('; ')}` : '', t.review?.notes || '',
                  `Check the Diff, Lint and Tests tabs before publishing.${t.effect === 'destructive' ? ' Destructive tools are published by typing the tool id.' : ''}`].filter(Boolean).join('\n')}
              </Banner>
            ) : null}
            {t.summary ? <Text variant="bodyLg">{t.summary}</Text> : null}
            <View style={styles.kv}>
              <KV label="Effect">{t.effect_style?.label || t.effect}</KV>
              <KV label="Runs as">{t.run_as || 'default'}</KV>
              <KV label="Idempotent">{t.idempotent ? 'yes' : 'no'}</KV>
              <KV label="Check mode">{t.check_mode ? 'supported (dry run reports, changes nothing)' : 'not supported'}</KV>
              <KV label="Rollback">{t.rollback || 'none: changes can’t be undone automatically'}</KV>
              <KV label="Secrets">{(t.secrets || []).length ? t.secrets.map(s => (typeof s === 'string' ? s : (s as any).name)).join(', ') : 'none'}</KV>
              <KV label="Category">{t.category}</KV>
              <KV label="Created by">{t.created_by}</KV>
              {t.intent ? <KV label="Intent">{t.intent}</KV> : null}
            </View>
            <BoardSection title="Inputs"><SchemaRows schema={t.manifest?.inputs} empty="This tool takes no inputs." /></BoardSection>
            {t.manifest?.outputs?.properties ? <BoardSection title="Outputs"><SchemaRows schema={t.manifest.outputs} empty="No declared outputs." /></BoardSection> : null}
          </View>
        ) : null}
        {tab === 'results' ? (results.data
          ? <ResultsTable testID="orch-tool-results" data={results.data} onAsk={v => setAsk(resultsQuestion({ toolId: t.tool_id }, v))} empty="No node has returned results for this tool yet. Run it to see them here."
              onShareCsv={(s, v) => { void shareCsv(s, v); }} onRow={row => row.run_id && navigation.navigate('OrchRun', { runId: row.run_id })} />
          : results.error ? <Banner tone="danger" glyph="✗" title={results.error} /> : <Loading label="Loading results…" />) : null}
        {tab === 'manifest' ? <CodeBlock code={t.manifest_yaml || ''} language="yaml" colors={colors} /> : null}
        {tab === 'files' ? (Object.keys(t.files || {}).length ? Object.entries(t.files).map(([name, text]) => (
          <View key={name} style={styles.stack}>
            <Text variant="label">{name}</Text>
            <CodeBlock code={text} language={langFor(name)} colors={colors} />
            {t.blobs?.[name] ? <Text variant="monoSm" tone="ink3">sha256 {t.blobs[name]}</Text> : null}
          </View>
        )) : <Text variant="caption" tone="ink3">This version has no files.</Text>) : null}
        {tab === 'diff' ? (!t.previous ? <Text variant="caption" tone="ink3">This is the first published version: there is nothing to compare with.</Text>
          : !t.diff ? <Text variant="caption" tone="ink3">No changes against {t.previous}.</Text>
          : <View style={styles.stack}><Text variant="caption" tone="ink3">Changes against {t.previous} (manifest and files).</Text><DiffView patch={t.diff} /></View>) : null}
        {tab === 'lint' ? (
          <Rows testID="orch-tool-lint" empty={t.lint_result && !t.lint_result.stale && t.lint_result.at ? 'No findings. Lint passed.' : 'Lint hasn’t run on the current content yet.'}>
            {(t.lint_result?.findings || []).map((f, i) => <Row key={i} status={f.severity === 'error' ? 'failed' : f.severity === 'warning' ? 'unverified' : 'pending'}
              title={f.message} sub={[f.severity, f.rule, f.file ? `${f.file}${f.line ? `:${f.line}` : ''}` : ''].filter(Boolean).join(' · ')} />)}
          </Rows>
        ) : null}
        {tab === 'tests' ? (
          <View style={styles.stack}>
            <BoardSection title="Declared tests" empty="No tests declared in the manifest.">
              {((t.manifest?.tests as Array<Record<string, any>>) || []).map((c, i) => <Row key={i} title={String(c.name)} sub={c.expect ? `expects ${JSON.stringify(c.expect)}` : ''} />)}
            </BoardSection>
            <Text variant="caption" tone="ink3">{testsWords(t)}</Text>
          </View>
        ) : null}
        {tab === 'versions' ? (
          <Rows>
            {(t.versions || []).slice().reverse().map(v => <Row key={v.version} status={orchState('orch_tool_status', v.status, v.style).status} title={v.ref}
              sub={v.version === t.version ? 'this version' : v.style?.label} selected={v.version === t.version}
              onPress={v.version === t.version ? undefined : () => navigation.push('OrchTool', { toolId: t.tool_id, version: v.version })} />)}
          </Rows>
        ) : null}
        {tab === 'notes' ? <NotesRows notes={t.notes || []} /> : null}
        {tab === 'usage' ? <Rows empty="This version hasn’t run yet.">{(t.usage || []).map(r => <RunRow key={r.id} run={r} onPress={() => navigation.navigate('OrchRun', { runId: r.id })} />)}</Rows> : null}
      </ScrollView>
      {dialog === 'test' ? <TestToolSheet tool={t} visible onClose={() => setDialog(null)} onProposed={opened} /> : null}
      {dialog === 'run' ? <RunSheet kind="tool" target={t.ref} inputs={t.manifest?.inputs} visible onClose={() => setDialog(null)} onProposed={opened} /> : null}
      {dialog === 'submit' ? <SubmitSheet tool={t} visible onClose={() => setDialog(null)} onSubmitted={() => { setDialog(null); toast(`${t.ref} submitted for review`, 'ok'); void reload(); }} /> : null}
      <FleetChatSheet visible={ask !== null} prefill={ask || undefined} onClose={() => setAsk(null)} />
      {element}
    </View>
  );
}

const LANG: Record<string, string> = { py: 'python', sh: 'bash', bash: 'bash', yaml: 'yaml', yml: 'yaml', json: 'json', js: 'javascript', ts: 'typescript', ps1: 'powershell' };
export const langFor = (name: string) => LANG[String(name || '').split('.').pop()?.toLowerCase() || ''] || '';

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  stack: { gap: spacing.sm },
  kv: { gap: spacing.xs },
});
