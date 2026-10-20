// A runbook version (web catalog.js renderRunbook): its problems (with field paths), the server's
// actions — edit, test on a canary, submit, request changes, publish, deprecate, retire, new
// version, discard, run — then Overview (steps and inputs), Spec (the YAML), Diff, Tests, Versions,
// Notes and Usage.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi } from '../../api/orchestration';
import { orchState } from '../../theme/status';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, BoardSection, Button, CodeBlock, DiffView, ErrorState, Loading, PageHeader, Row, Rows, Text, useToast } from '../../ui';
import { RunSheet, TestRunbookSheet } from './forms';
import { OrchActionBar, RunRow, plural, useOrchActions } from './parts';
import { errorsOf, warningsOf } from './runbookModel';
import { NotesRows, SchemaRows, versionState } from './ToolScreen';
import { UpgradeToolsSheet } from './runbookTools';
import { useOrchData } from './useOrchData';

type Tab = 'overview' | 'spec' | 'diff' | 'tests' | 'versions' | 'notes' | 'usage';

export function OrchRunbookScreen({ route, navigation }: { route: { params: { runbookId: string; version: number } }; navigation: any }) {
  const { colors } = useTheme();
  const toast = useToast();
  const { runbookId, version } = route.params;
  const [tab, setTab] = useState<Tab>('overview');
  const [dialog, setDialog] = useState<'test' | 'run' | 'upgrade' | null>(null);
  const { data: r, error, refreshing, refresh, reload } = useOrchData(() => orchestrationApi.runbook(runbookId, version), ['tool'], [runbookId, version]);
  const { run, element } = useOrchActions();
  const back = { label: 'Catalog', onPress: () => navigation.goBack() };

  if (!r) {
    return (
      <View style={{ flex: 1 }}>
        <PageHeader title={`${runbookId} v${version}`} back={back} />
        {error ? <ErrorState message={error} onRetry={() => { void reload(); }} /> : <View style={styles.pad}><Loading label="Loading runbook…" /></View>}
      </View>
    );
  }
  const errs = errorsOf(r.problem_details);
  const warns = warningsOf(r.problem_details);
  const tabs = [
    { key: 'overview', label: 'Overview', count: errs.length || null }, { key: 'spec', label: 'Spec' }, { key: 'diff', label: 'Diff' },
    { key: 'tests', label: 'Tests', count: r.tests?.count || null }, { key: 'versions', label: 'Versions', count: (r.versions || []).length },
    { key: 'notes', label: 'Notes' }, { key: 'usage', label: 'Usage', count: (r.usage || []).length || null },
  ];
  const editor = () => navigation.navigate('OrchRunbookEditor', { runbookId: r.runbook_id, version: r.version });
  const opened = (runId: string, message: string) => { setDialog(null); toast(message, 'ok'); navigation.navigate('OrchRun', { runId }); };

  return (
    <View style={{ flex: 1 }} testID="orch-runbook">
      <PageHeader testID="orch-runbook-head" title={`${r.title} v${r.version}`} back={back} state={versionState(r)} tabs={tabs} tab={tab} onTab={k => setTab(k as Tab)} />
      <ScrollView contentContainerStyle={styles.pad} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
        <OrchActionBar testID="orch-runbook-actions" actions={r.actions} run={run} ctx={{
          args: { ref: r.ref },
          done: (result, action) => {
            const ref = String(result?.data?.ref || '');
            if (action.id === 'newRunbookVersion' && ref) { const [id, v] = ref.split('@'); navigation.replace('OrchRunbookEditor', { runbookId: id, version: Number(v) }); return; }
            if (action.id === 'discardRunbookDraft') { navigation.goBack(); return; }
            void reload();
          },
          dialogs: { edit: editor, test: () => setDialog('test'), run: () => setDialog('run') },
        }} />
        {['published', 'deprecated'].includes(r.status) ? (
          <Button size="md" variant="ghost" icon="refresh-cw" label="New version with upgraded tools" onPress={() => setDialog('upgrade')} testID="orch-runbook-upgrade" />
        ) : null}
        {!r.parses ? <Banner tone="caution" glyph="◆" title="The draft’s YAML doesn’t parse." action={{ label: 'Open editor', onPress: editor }}>The last valid version is shown here.</Banner> : null}

        {tab === 'overview' ? (
          <View style={styles.stack}>
            {r.summary ? <Text variant="bodyLg">{r.summary}</Text> : null}
            {errs.length ? (
              <Banner tone="danger" glyph="✗" testID="orch-runbook-problems" title={`${plural(errs.length, 'problem')}: this runbook can’t be submitted, published or run until ${errs.length === 1 ? 'it is' : 'they are'} fixed.`}>
                {errs.map(p => `${p.path ? `${p.path}: ` : ''}${p.message}${p.line ? ` (line ${p.line})` : ''}`).join('\n')}
              </Banner>
            ) : null}
            {warns.length ? <Text variant="caption" tone="attention">◆ {warns.map(p => `${p.path ? `${p.path}: ` : ''}${p.message}`).join('; ')}</Text> : null}
            <BoardSection title="Steps" empty="No steps.">
              {(r.steps || []).map((s, i) => <Row key={i} title={`${i + 1}. ${s.name || ''}`}
                sub={[s.tool || '—', Object.keys(s.params || {}).length ? JSON.stringify(s.params) : '', s.when ? `when ${s.when}` : '', s.nodes ? `nodes ${s.nodes}` : 'all nodes', `on failure ${s.on_failure || 'stop'}`].filter(Boolean).join(' · ')} />)}
            </BoardSection>
            <BoardSection title="Inputs"><SchemaRows schema={r.inputs} empty="This runbook takes no inputs." /></BoardSection>
          </View>
        ) : null}
        {tab === 'spec' ? <View style={styles.stack}><Text variant="monoSm" tone="ink3">digest {r.digest}</Text><CodeBlock code={r.source || r.spec_yaml || ''} language="yaml" colors={colors} /></View> : null}
        {tab === 'diff' ? (!r.previous ? <Text variant="caption" tone="ink3">There is no earlier published version to compare with.</Text>
          : !r.diff ? <Text variant="caption" tone="ink3">No changes against {r.previous}.</Text> : <DiffView patch={r.diff} />) : null}
        {tab === 'tests' ? (
          <Rows empty="No tests yet. Test the draft on a canary.">
            {(r.tests?.runs || []).map(x => <RunRow key={x.id} run={x} onPress={() => navigation.navigate('OrchRun', { runId: x.id })} />)}
          </Rows>
        ) : null}
        {tab === 'versions' ? (
          <Rows>
            {(r.versions || []).slice().reverse().map(v => <Row key={v.version} status={orchState('orch_tool_status', v.status, v.style).status} title={v.ref}
              sub={v.version === r.version ? 'this version' : v.style?.label} selected={v.version === r.version}
              onPress={v.version === r.version ? undefined : () => navigation.push('OrchRunbook', { runbookId: r.runbook_id, version: v.version })} />)}
          </Rows>
        ) : null}
        {tab === 'notes' ? <NotesRows notes={r.notes || []} /> : null}
        {tab === 'usage' ? <Rows empty="This runbook hasn’t run yet.">{(r.usage || []).map(x => <RunRow key={x.id} run={x} onPress={() => navigation.navigate('OrchRun', { runId: x.id })} />)}</Rows> : null}
      </ScrollView>
      {dialog === 'test' ? <TestRunbookSheet runbook={r} visible onClose={() => setDialog(null)} onProposed={opened} /> : null}
      {dialog === 'run' ? <RunSheet kind="runbook" target={r.ref} inputs={r.inputs} visible onClose={() => setDialog(null)} onProposed={opened} /> : null}
      <UpgradeToolsSheet visible={dialog === 'upgrade'} spec={r.spec} runbookRef={r.ref} published onClose={() => setDialog(null)}
        onNewVersion={ref => { const [id, v] = ref.split('@'); if (id && v) navigation.replace('OrchRunbookEditor', { runbookId: id, version: Number(v) }); }} />
      {element}
    </View>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  stack: { gap: spacing.sm },
});
