// Feature mode (modes_v2.md §4.3, §5.3 Feature, §5.4 Android): the active plan — its state, the
// tasks grouped by phase with each criterion's evidence mark (✓ verified run, attested by the
// user, not met), reviews, diff proposals and events. Now holds the plan approval (user-only),
// diff decisions, blockers and "Mark feature complete?" when the engine's preconditions hold.
// Other features can be previewed read-only and loaded, unloaded or archived from ⋯.

import React, { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { PanelPage, PanelSection, Fact } from '../components/chat/PanelPage';
import {
  CheckRunRow, NowBoard, featureCriterionKey, featureNow, featureSentence, resolveBlockerAction, useCheckRunSheet, useModeActions,
  type NowItem,
} from '../components/modes';
import { featureApi, type FeaturePlan, type FeatureState, type FeatureTask } from '../api/feature';
import { modeStateApi } from '../api/modeState';
import { modeState, when as formatWhen } from '../theme/status';
import { spacing } from '../theme/tokens';
import { Banner, Button, Pre, Row, Sheet, Text, type MenuItem, type StatePart } from '../ui';

const TABS = [
  { key: 'plan', label: 'Plan' },
  { key: 'tasks', label: 'Tasks' },
  { key: 'reviews', label: 'Reviews' },
];

/** Tasks grouped by phase, in phase order; tasks without a phase last. */
export function tasksByPhase(plan: FeaturePlan): Array<{ key: string; title: string; tasks: FeatureTask[] }> {
  const phases = [...plan.phases_meta].sort((a, b) => a.order - b.order);
  const groups = phases.map(p => ({ key: String(p.id), title: p.title, tasks: plan.tasks.filter(t => t.phase_id === p.id) }));
  const loose = plan.tasks.filter(t => !phases.some(p => p.id === t.phase_id));
  if (loose.length) groups.push({ key: 'none', title: phases.length ? 'Other tasks' : 'Tasks', tasks: loose });
  return groups.filter(g => g.tasks.length);
}

/** A task row's line 2: "2 of 3 criteria met · reviewed · override". */
export function taskLine(task: FeatureTask): string {
  const met = task.criteria.filter(c => c.met).length;
  return [
    task.criteria.length ? `${met} of ${task.criteria.length} criteria met` : 'no criteria',
    task.reviewed ? 'reviewed' : '',
    task.override ? 'override' : '',
  ].filter(Boolean).join(' · ');
}

export function FeatureExplorerScreen() {
  const [state, setState] = useState<FeatureState | null>(null);
  const [preview, setPreview] = useState<FeatureState | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState('plan');
  const [openTask, setOpenTask] = useState<FeatureTask | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const next = await featureApi.getState();
      setState(next);
      if (previewId && previewId !== next.active_feature_id && next.features.some(f => f.feature_id === previewId)) {
        setPreview(await featureApi.preview(previewId));
      } else {
        setPreview(null);
        setPreviewId(null);
      }
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, [previewId]);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const actions = useModeActions(load);
  const shown = preview || state;
  const plan = shown?.plan || null;
  const sheet = useCheckRunSheet(shown?.runs);
  const now = useMemo(() => featureNow(state), [state]);
  const item = state?.features.find(f => f.feature_id === (plan?.feature_id || state?.active_feature_id)) || null;

  const openPreview = async (featureId: string) => {
    if (featureId === state?.active_feature_id) { setPreview(null); setPreviewId(null); return; }
    try { setPreview(await featureApi.preview(featureId)); setPreviewId(featureId); }
    catch (e) { setError(String(e)); }
  };

  const actionsFor = (it: NowItem) => {
    if (it.kind === 'blocker') {
      return <Button label="Answer" variant="primary" testID={`now-answer-${it.ref}`}
        onPress={() => void actions.run(resolveBlockerAction(it), args => modeStateApi.resolveBlocker(it.ref, String(args.resolution || '')))} />;
    }
    if (it.kind === 'approve_plan') {
      return <Button label="Approve plan" variant="primary" icon="circle-check" testID="feature-approve"
        onPress={() => void actions.run({ id: 'approve', label: 'Approve plan', confirm: 'Approve this plan? The agent starts implementing it, task by task.' }, () => featureApi.approve())} />;
    }
    if (it.kind === 'diff_decision') {
      return (
        <View style={styles.actions}>
          <Button label="Approve" variant="primary" testID={`diff-approve-${it.ref}`}
            onPress={() => void actions.run({ id: 'diff-approve', label: 'Approve diff' }, () => featureApi.decideDiff(it.ref, 'approved'))} />
          <Button label="Reject…" variant="secondary" testID={`diff-reject-${it.ref}`}
            onPress={() => void actions.run({ id: 'diff-reject', label: 'Reject diff', danger: true, input: { arg: 'reason', label: 'Why?', multiline: true } },
              args => featureApi.decideDiff(it.ref, 'rejected', String(args.reason || '')))} />
        </View>
      );
    }
    if (it.kind === 'complete_feature') {
      return <Button label="Mark complete" variant="primary" testID="feature-complete"
        onPress={() => void actions.run({ id: 'complete', label: 'Mark feature complete', confirm: 'Mark this feature complete? The engine checks the preconditions first.' }, () => featureApi.complete())} />;
    }
    return null;
  };

  const menu: MenuItem[] = [];
  if (item && !item.archived) {
    if (item.is_active) {
      menu.push({ key: 'unload', label: 'Unload feature', onPress: () => void actions.run({ id: 'unload', label: 'Unload', confirm: 'Unload this feature? It stays saved and can be loaded again.' }, () => featureApi.unload(item.feature_id)) });
    } else {
      menu.push({ key: 'load', label: 'Load feature', onPress: () => void actions.run({ id: 'load', label: 'Load' }, () => featureApi.load(item.feature_id)) });
      menu.push({ key: 'archive', label: 'Archive feature', onPress: () => void actions.run({ id: 'archive', label: 'Archive', confirm: 'Archive this feature? It can be restored later.' }, () => featureApi.archive(item.feature_id)) });
    }
  } else if (item?.archived) {
    menu.push({ key: 'unarchive', label: 'Restore feature', onPress: () => void actions.run({ id: 'unarchive', label: 'Restore' }, () => featureApi.unarchive(item.feature_id)) });
  }

  const sentence: StatePart[] = [...featureSentence(plan), ...(preview ? [{ text: 'preview', tone: 'quiet' as const }] : [])];
  const features = state?.features || [];
  return (
    <PanelPage testID="feature-screen" title={plan?.feature_name || 'Feature'} state={sentence}
      tabs={TABS} tab={tab} onTab={setTab} menu={menu.length ? menu : undefined}
      loading={loading} error={error && !state ? error : null} onRetry={() => void load()}
      empty={!loading && !error && !plan && !features.length ? { title: 'No feature', message: 'Describe a feature in the chat in feature mode; the agent proposes a plan for you to approve.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>{actions.element}{sheet.element}</>}>
      {error && state ? <Banner tone="danger" title="Something went wrong" testID="feature-error">{error}</Banner> : null}
      {actions.error ? <Banner tone="danger" title="That didn't go through" testID="feature-action-error">{actions.error}</Banner> : null}
      {preview ? (
        <Banner tone="info" title="Previewing a feature that isn't loaded" action={{ label: 'Back to active', onPress: () => { setPreview(null); setPreviewId(null); }, testID: 'feature-back' }}>
          Its records are read-only until you load it.
        </Banner>
      ) : <NowBoard items={now} actionsFor={actionsFor} />}

      {plan && tab === 'plan' ? (
        <>
          <PanelSection title="Plan" testID="feature-plan">
            {plan.feature_request ? <Text variant="body" tone="ink2" style={styles.para}>{plan.feature_request}</Text> : null}
            <Fact label="Status" value={modeState('feature_plan', plan.status).label} />
            {plan.revision > 1 ? <Fact label="Revision" value={String(plan.revision)} /> : null}
            {plan.approval ? <Fact label="Approved" value={[plan.approval.actor, formatWhen(plan.approval.at)].filter(Boolean).join(' · ')} /> : null}
            {plan.directory ? <Fact label="Folder" value={plan.directory} mono /> : null}
          </PanelSection>
          {(plan.completion_gaps || []).length && plan.status !== 'draft' ? (
            <PanelSection title="Before it can complete">
              {(plan.completion_gaps || []).map((gap, i) => <Row key={i} status="pending" title={gap} />)}
            </PanelSection>
          ) : null}
          <PanelSection title="Phases" empty="No phases.">
            {tasksByPhase(plan).map(g => {
              const done = g.tasks.filter(t => t.status === 'completed').length;
              return <Row key={g.key} title={g.title} when={`${done} of ${g.tasks.length}`} onPress={() => setTab('tasks')} />;
            })}
          </PanelSection>
        </>
      ) : null}
      {tab === 'plan' && features.length > 1 ? (
        <PanelSection title="Features">
          {features.map(f => (
            <Row key={f.feature_id} status={f.archived ? 'skipped' : modeState('feature_plan', f.status).status} title={f.feature_name}
              when={f.is_active ? 'loaded' : f.archived ? 'archived' : modeState('feature_plan', f.status).label}
              selected={f.feature_id === plan?.feature_id} onPress={() => void openPreview(f.feature_id)} testID={`feature-${f.feature_id}`} />
          ))}
        </PanelSection>
      ) : null}

      {plan && tab === 'tasks' ? tasksByPhase(plan).map(g => (
        <PanelSection key={g.key} title={g.title}>
          {g.tasks.map(t => {
            const mark = modeState('feature_task', t.status);
            return <Row key={t.id} status={mark.status} title={`t${t.id}  ${t.title}`} when={mark.label} sub={taskLine(t)}
              selected={t.id === plan.current_task} onPress={() => setOpenTask(t)} testID={`task-${t.id}`} />;
          })}
        </PanelSection>
      )) : null}

      {plan && tab === 'reviews' ? (
        <>
          <PanelSection title="Reviews" empty="No reviews yet.">
            {plan.review_records.map(r => (
              <Row key={r.id} title={`t${r.task_id}  ${r.summary || 'Review'}`} when={r.issues.length ? `${r.issues.length} issue${r.issues.length === 1 ? '' : 's'}` : undefined}
                sub={r.diff_stat || undefined} onPress={r.check_run ? () => sheet.open(r.check_run) : undefined} />
            ))}
          </PanelSection>
          {plan.diff_proposals.length ? (
            <PanelSection title="Diff proposals">
              {plan.diff_proposals.map(p => (
                <Row key={p.id} title={`t${p.task_id}  ${p.rationale || p.id}`}
                  when={p.status} sub={p.decision_reason || undefined} />
              ))}
            </PanelSection>
          ) : null}
          {shown?.runs.length ? (
            <PanelSection title="Runs">
              {shown.runs.map(run => <CheckRunRow key={run.id} run={run} onOpen={sheet.open} />)}
            </PanelSection>
          ) : null}
          <PanelSection title="Events" empty="No events.">
            {[...plan.event_log].reverse().slice(0, 30).map(e => (
              <Row key={e.id} title={e.kind.replace(/_/g, ' ')} when={formatWhen(e.created_at)} sub={`${e.entity} ${e.entity_id} · ${e.actor}`} />
            ))}
          </PanelSection>
        </>
      ) : null}

      <Sheet visible={openTask !== null} onClose={() => setOpenTask(null)} title={openTask ? `t${openTask.id} ${openTask.title}` : ''} testID="task-sheet">
        {openTask ? (
          <View style={styles.sheet}>
            <Row status={modeState('feature_task', openTask.status).status} title={modeState('feature_task', openTask.status).label} sub={taskLine(openTask)} />
            {openTask.objective ? <Text variant="body">{openTask.objective}</Text> : null}
            <Text variant="label" tone="ink2">Criteria</Text>
            {openTask.criteria.length ? openTask.criteria.map(c => {
              const mark = modeState('criterion', featureCriterionKey(c));
              const run = c.evidence.find(e => e.kind === 'run' && e.ref);
              const attested = c.evidence.find(e => e.kind === 'attested');
              const sub = run ? `run ${run.ref}` : attested ? `attested by ${attested.actor || 'user'}${attested.justification ? `: ${attested.justification}` : ''}` : c.check?.command || undefined;
              return <Row key={c.id} status={mark.status} title={c.text} when={mark.label} sub={sub} testID={`criterion-${c.id}`}
                onPress={run?.ref ? () => { setOpenTask(null); sheet.open(run.ref!); } : undefined} />;
            }) : <Text variant="caption" tone="ink3">No criteria.</Text>}
            {openTask.diff_stat ? (<><Text variant="label" tone="ink2">Diff</Text><Pre>{openTask.diff_stat}</Pre></>) : null}
            {openTask.override ? <Fact label="Override" value={[openTask.override.reason, openTask.override.actor].filter(Boolean).join(' · ')} /> : null}
            {openTask.notes ? <Fact label="Notes" value={openTask.notes} /> : null}
            {plan?.review_records.filter(r => r.task_id === openTask.id).map(r => <Fact key={r.id} label="Review" value={r.summary} />)}
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  para: { marginVertical: spacing.sm },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  sheet: { gap: spacing.sm },
});
