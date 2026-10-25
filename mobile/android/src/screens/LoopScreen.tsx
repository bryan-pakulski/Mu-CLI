// Loop mode (modes_v2.md §4.4, §5.3 Loop, §5.4 Android): the mission — goal, done-when criteria
// with evidence marks, the tightest budget's meter — the timeline of increments, and the backlog
// of work items. Pause / Resume is the page's one primary action (the watchdog obeys it); Stop
// mission sits in ⋯ and asks first. Now holds blockers, a reached budget and a finished mission.

import React, { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { PanelPage, PanelSection, Fact } from '../components/chat/PanelPage';
import {
  CheckRunRow, NowBoard, loopCriterionKey, loopNow, loopSentence, resolveBlockerAction, useCheckRunSheet, useModeActions,
  type NowItem,
} from '../components/modes';
import { loopApi, type LoopBudgetMeter, type LoopIncrement, type LoopState } from '../api/loop';
import { modeStateApi } from '../api/modeState';
import { duration, modeState, money } from '../theme/status';
import { spacing } from '../theme/tokens';
import { Banner, Button, Meter, Row, Sheet, Text, type ServerAction } from '../ui';

const TABS = [
  { key: 'mission', label: 'Mission' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'backlog', label: 'Backlog' },
];

const STOP: ServerAction = { id: 'stop', label: 'Stop mission', danger: true, confirm: 'Stop the mission? The agent stops working on it; the record stays.' };

/** "cost $3.40 of $10" / "increments 12 of 20" / "runtime 1h 5m of 2h". */
export function meterText(meter: LoopBudgetMeter): string {
  if (meter.kind === 'cost_usd') return `cost ${money(meter.used)} of ${money(meter.cap)}`;
  if (meter.kind === 'runtime_s') return `runtime ${duration(meter.used * 1000)} of ${duration(meter.cap * 1000)}`;
  return `${meter.kind} ${meter.used} of ${meter.cap}`;
}

export function LoopScreen() {
  const [state, setState] = useState<LoopState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState('mission');
  const [openInc, setOpenInc] = useState<LoopIncrement | null>(null);

  const load = useCallback(async () => {
    try { setError(null); setState(await loopApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const actions = useModeActions(load);
  const sheet = useCheckRunSheet(state?.runs);
  const now = useMemo(() => loopNow(state), [state]);
  const mission = state?.mission || null;
  const live = mission && (mission.status === 'running' || mission.status === 'paused' || mission.status === 'blocked');

  const control = (action: 'pause' | 'resume') => actions.run({ id: action, label: action === 'pause' ? 'Pause' : 'Resume' }, () => loopApi.control(action));
  const stop = () => actions.run(STOP, () => loopApi.control('stop'));

  const actionsFor = (item: NowItem) => {
    if (item.kind === 'blocker') {
      return <Button label="Answer" variant="primary" testID={`now-answer-${item.ref}`}
        onPress={() => void actions.run(resolveBlockerAction(item), args => modeStateApi.resolveBlocker(item.ref, String(args.resolution || '')))} />;
    }
    if (item.kind === 'mission_paused') return <Button label="Resume" variant="primary" onPress={() => void control('resume')} testID="now-resume" />;
    if (item.kind === 'budget_exhausted') return <Button label="Stop mission" variant="danger" onPress={() => void stop()} testID="now-stop" />;
    return null;
  };

  const right = live ? (
    mission!.status === 'running'
      ? <Button label="Pause" variant="secondary" icon="pause" onPress={() => void control('pause')} loading={actions.busy === 'pause'} testID="loop-pause" />
      : <Button label="Resume" variant="primary" icon="play" onPress={() => void control('resume')} loading={actions.busy === 'resume'} testID="loop-resume" />
  ) : undefined;

  return (
    <PanelPage testID="loop-screen" title="Loop" state={loopSentence(mission)}
      tabs={TABS} tab={tab} onTab={setTab} right={right}
      menu={live ? [{ key: 'stop', label: 'Stop mission', icon: 'square', tone: 'danger', onPress: () => void stop() }] : undefined}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && !mission ? { title: 'No mission', message: 'Start a mission in the chat in loop mode: a goal, what done looks like, and a budget.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>{actions.element}{sheet.element}</>}>
      {actions.error ? <Banner tone="danger" title="That didn't go through" testID="loop-error">{actions.error}</Banner> : null}
      <NowBoard items={now} actionsFor={actionsFor} />

      {mission && tab === 'mission' ? (
        <>
          <PanelSection title="Goal" testID="loop-goal">
            <Text variant="body" style={styles.para}>{mission.goal}</Text>
            {state?.budget_meter ? (
              <View style={styles.meter}>
                <Meter value={state.budget_meter.ratio} accessibilityLabel={meterText(state.budget_meter)} />
                <Text variant="caption" tone="ink3" numeric>{meterText(state.budget_meter)}</Text>
              </View>
            ) : null}
          </PanelSection>
          <PanelSection title="Done when" empty="No done-when criteria.">
            {mission.done_when.map(c => {
              const mark = modeState('criterion', loopCriterionKey(c));
              return <Row key={c.id} status={mark.status} title={c.text} when={mark.label}
                sub={c.evidence.length ? `evidence ${c.evidence.join(', ')}` : c.attested_by ? `attested by ${c.attested_by}${c.note ? `: ${c.note}` : ''}` : c.check?.command}
                onPress={c.evidence.length ? () => sheet.open(c.evidence[c.evidence.length - 1]) : undefined} testID={`criterion-${c.id}`} />;
            })}
          </PanelSection>
          {state?.runs.length ? (
            <PanelSection title="Evidence runs">
              {state.runs.map(run => <CheckRunRow key={run.id} run={run} onOpen={sheet.open} />)}
            </PanelSection>
          ) : null}
        </>
      ) : null}

      {mission && tab === 'timeline' ? (
        <PanelSection title="Increments" empty="No increments recorded yet.">
          {[...mission.increments].reverse().map(inc => {
            const mark = modeState('loop_increment', inc.result);
            return <Row key={inc.n} status={mark.status} title={`${inc.n}  ${inc.objective}`} when={mark.label}
              sub={inc.next ? `next: ${inc.next}` : inc.decision || undefined} onPress={() => setOpenInc(inc)} testID={`increment-${inc.n}`} />;
          })}
        </PanelSection>
      ) : null}

      {mission && tab === 'backlog' ? (
        <PanelSection title="Backlog" empty="No backlog items.">
          {(state?.backlog || []).map(item => {
            const mark = modeState('work_item', item.status);
            return <Row key={item.id} status={mark.status} title={item.title} when={mark.label} sub={item.note || undefined}
              selected={item.id === state?.current_item} testID={`work-${item.id}`} />;
          })}
        </PanelSection>
      ) : null}

      <Sheet visible={openInc !== null} onClose={() => setOpenInc(null)} title={openInc ? `Increment ${openInc.n}` : ''} testID="increment-sheet">
        {openInc ? (
          <View style={styles.sheet}>
            <Row status={modeState('loop_increment', openInc.result).status} title={openInc.objective} when={modeState('loop_increment', openInc.result).label} />
            {openInc.decision ? <Fact label="Decision" value={openInc.decision} /> : null}
            {openInc.next ? <Fact label="Next" value={openInc.next} /> : null}
            {openInc.actions.length ? <Fact label="Actions" value={openInc.actions.join('\n')} /> : null}
            {openInc.evidence.map(id => {
              const run = state?.runs.find(r => r.id === id);
              return run
                ? <CheckRunRow key={id} run={run} onOpen={r => { setOpenInc(null); sheet.open(r); }} />
                : <Row key={id} title={`Run ${id}`} onPress={() => { setOpenInc(null); sheet.open(id); }} />;
            })}
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  para: { marginVertical: spacing.sm },
  meter: { gap: spacing.xs, paddingVertical: spacing.sm },
  sheet: { gap: spacing.sm },
});
