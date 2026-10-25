// Debug mode (modes_v2.md §4.2, §5.3 Debug, §5.4 Android): the case climbing its ladder
// (open → reproduced → root caused → fixed → verified), hypotheses with their evidence, the case's
// check runs and past root causes. Now holds blockers and "Close case?" once the fix is verified.
// Records are read-only here; the agent moves the case.

import React, { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { PanelPage, PanelSection, Fact } from '../components/chat/PanelPage';
import {
  CheckRunRow, NowBoard, blockersFor, debugNow, debugSentence, debugSteps, resolveBlockerAction, useCheckRunSheet, useModeActions,
  type NowItem,
} from '../components/modes';
import { debugApi, type DebugHypothesis, type DebugState } from '../api/debug';
import { modeStateApi, type Blocker } from '../api/modeState';
import { modeState } from '../theme/status';
import { spacing } from '../theme/tokens';
import { Banner, Button, PipelineStepper, Row, Sheet, Text } from '../ui';

const TABS = [
  { key: 'case', label: 'Case' },
  { key: 'hypotheses', label: 'Hypotheses' },
  { key: 'runs', label: 'Runs' },
  { key: 'causes', label: 'Root causes' },
];

export function DebugScreen() {
  const [state, setState] = useState<DebugState | null>(null);
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState('case');
  const [openHyp, setOpenHyp] = useState<DebugHypothesis | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [next, open] = await Promise.all([
        debugApi.getState(),
        modeStateApi.listBlockers().then(r => r.open).catch(() => [] as Blocker[]),
      ]);
      setState(next);
      setBlockers(blockersFor('debug', open));
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const actions = useModeActions(load);
  const sheet = useCheckRunSheet(state?.runs);
  const now = useMemo(() => debugNow(state, blockers), [state, blockers]);
  const c = state?.case || null;

  const actionsFor = (item: NowItem) => {
    if (item.kind === 'blocker') {
      return <Button label="Answer" variant="primary" testID={`now-answer-${item.ref}`}
        onPress={() => void actions.run(resolveBlockerAction(item), args => modeStateApi.resolveBlocker(item.ref, String(args.resolution || '')))} />;
    }
    if (item.kind === 'close_case') {
      return <Button label="Close case" variant="primary" testID="debug-close"
        onPress={() => void actions.run({ id: 'close', label: 'Close case', input: { arg: 'cause', label: 'Root cause', multiline: true, hint: 'The actual defect, not the symptom.' } },
          args => debugApi.close({ cause: String(args.cause || '') }))} />;
    }
    return null;
  };

  const empty = !loading && !error && !c && !(state?.cases.length) && !now.length;
  return (
    <PanelPage testID="debug-screen" title="Debug" state={debugSentence(state)}
      tabs={TABS} tab={tab} onTab={setTab}
      loading={loading} error={error} onRetry={() => void load()}
      empty={empty ? { title: 'No debug case', message: 'Describe the bug in the chat in debug mode to open a case; the agent reproduces it first.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>{actions.element}{sheet.element}</>}>
      {actions.error ? <Banner tone="danger" title="That didn't go through" testID="debug-error">{actions.error}</Banner> : null}
      <NowBoard items={now} actionsFor={actionsFor} />

      {tab === 'case' ? (
        <>
          {c ? (
            <PanelSection title={c.title} testID="debug-case">
              {c.status !== 'unreproducible' ? <PipelineStepper steps={debugSteps(c.status)} /> : null}
              <Text variant="body" tone="ink2" style={styles.para}>{c.symptom}</Text>
              {c.repro?.command ? <Fact label="Reproducer" value={c.repro.command} mono /> : null}
              {c.fix?.summary ? <Fact label="Fix" value={c.fix.summary} /> : null}
              {c.fix?.diff_stat ? <Fact label="Diff" value={c.fix.diff_stat} mono /> : null}
              {c.root_cause?.cause ? <Fact label="Root cause" value={c.root_cause.cause} /> : null}
              {c.unreproducible_reason ? <Fact label="Unreproducible" value={c.unreproducible_reason} /> : null}
            </PanelSection>
          ) : null}
          {c?.suspects.length ? (
            <PanelSection title="Suspects">
              {c.suspects.map((s, i) => (
                <Row key={`${s.path}:${s.line ?? i}`} title={<Text variant="monoSm">{s.line != null ? `${s.path}:${s.line}` : s.path}{s.symbol ? ` · ${s.symbol}` : ''}</Text>} sub={s.reason} />
              ))}
            </PanelSection>
          ) : null}
          {(state?.cases.length || 0) > 1 ? (
            <PanelSection title="Cases">
              {state!.cases.map(item => {
                const mark = modeState('debug_case', item.status);
                return <Row key={item.id} status={mark.status} title={item.title} when={mark.label} selected={item.id === c?.id} testID={`debug-case-${item.id}`} />;
              })}
            </PanelSection>
          ) : null}
        </>
      ) : null}

      {tab === 'hypotheses' ? (
        <PanelSection title="Hypotheses" empty="No hypotheses yet.">
          {(c?.hypotheses || []).map(h => {
            const mark = modeState('hypothesis', h.status);
            const reason = h.evidence.length ? h.evidence[h.evidence.length - 1].note || `${h.evidence.length} evidence` : '';
            return <Row key={h.id} status={mark.status} title={h.statement} when={mark.label} sub={reason || undefined}
              onPress={() => setOpenHyp(h)} testID={`hypothesis-${h.id}`} />;
          })}
        </PanelSection>
      ) : null}

      {tab === 'runs' ? (
        <PanelSection title="Runs" empty="No runs recorded for this case.">
          {(state?.runs || []).map(run => <CheckRunRow key={run.id} run={run} onOpen={sheet.open} />)}
        </PanelSection>
      ) : null}

      {tab === 'causes' ? (
        <PanelSection title="Past root causes" empty="No recorded root causes for these files.">
          {(state?.past_root_causes || []).map(p => (
            <Row key={p.id} title={p.root_cause?.cause || p.statement} sub={[p.root_cause?.signature, p.case].filter(Boolean).join(' · ') || undefined} />
          ))}
        </PanelSection>
      ) : null}

      <Sheet visible={openHyp !== null} onClose={() => setOpenHyp(null)} title="Hypothesis" testID="hypothesis-sheet">
        {openHyp ? (
          <View style={styles.sheet}>
            <Row status={modeState('hypothesis', openHyp.status).status} title={openHyp.statement} when={modeState('hypothesis', openHyp.status).label} />
            <Text variant="label" tone="ink2">Evidence</Text>
            {openHyp.evidence.length ? openHyp.evidence.map((e, i) => (
              <Row key={i} title={`${e.stance} · ${e.kind}${e.ref ? ` ${e.ref}` : ''}`} sub={e.note || undefined}
                onPress={e.kind === 'run' && e.ref ? () => { setOpenHyp(null); sheet.open(e.ref); } : undefined} />
            )) : <Text variant="caption" tone="ink3">No evidence recorded yet.</Text>}
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  para: { marginVertical: spacing.sm },
  sheet: { gap: spacing.sm },
});
