import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { jobCoreApi, subscribeToJobChanges, type Finding, type JobPart, type JobVM } from '../api/jobCore';
import { jobsApi, type JobAttempt, type JobDiagnosticsSnapshot } from '../api/jobs';
import { JobStageModelsSheet } from '../components/JobStageModelsSheet';
import { EnvironmentsPill, JobEnvironmentsSheet } from '../components/JobEnvironments';
import { stageAgentsLabel } from '../api/agents';
import { ApprovalBanner, EnvironmentApprovalBanner, JobActionBar, LimitBanner, useJobCommands } from '../components/JobActions';
import { budgetSummary } from '../components/jobViews';
import { ChecksTab, DiffTab, PlanTab, ReviewTab, SessionTab } from '../components/JobTabs';
import { JOB_TABS, JOB_TAB_LABELS, STEP_TABS, defaultTab, eventTone, parsePatch, type JobTab } from '../components/jobViews';
import { PrPill, jobKey } from '../components/JobsChrome';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing, toneInk, touchTarget } from '../theme/tokens';
import { STATUS, duration, money, stateLabel, stateStatus, verdictStyle, when } from '../theme/status';
import { Banner, Button, CountBadge, Empty, KV, Loading, Meter, Panel, Pill, PipelineStepper, Pre, Pressable, type TabItem, Tabs, Text, Icon } from '../ui';

export const LIVE_DIFF_REFRESH_MS = 5000;

export type JobDetailScreenProps = NativeStackScreenProps<RootStackParamList, 'JobDetail'>;
// Tab order (Task page): Session · Plan · Diff · Review · Checks · Diagnostics · Cost & audit
// (JOB_TABS / JOB_TAB_LABELS in components/jobViews.ts).

export function JobDetailScreen({ route, navigation }: JobDetailScreenProps) {
  const { jobId } = route.params;
  const requestedAction = route.params.action;
  const { colors } = useTheme();
  const [job, setJob] = useState<JobVM | null>(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<JobTab | null>(null);
  const [draft, setDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false);
  const [findings, setFindings] = useState<string[]>([]);
  const [models, setModels] = useState(false);
  const [environments, setEnvironments] = useState(false);
  const [history, setHistory] = useState<number | undefined>();
  const [diagnostics, setDiagnostics] = useState<JobDiagnosticsSnapshot | null>(null);
  const [attempts, setAttempts] = useState<JobAttempt[] | null>(null);
  const [localViewed, setLocalViewed] = useState<Record<string, boolean>>({});
  const [focus, setFocus] = useState<{ file: string; line: number | null; seq: number } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const contentY = useRef(0);
  const fileY = useRef<Record<string, number>>({});
  const draftRef = useRef({ text: '', dirty: false, revision: 0, savedRevision: -1, version: 0 });
  const requestBusy = useRef(false);
  const autoActionDone = useRef(false);
  const savedRevision = useRef(0);

  const load = useCallback(async (parts?: JobPart[]) => {
    try {
      const next = await jobCoreApi.job(jobId, parts);
      setJob(old => ({ ...(old || {}), ...next } as JobVM));
      setTab(old => old ?? defaultTab(next));
      navigation.setOptions({ title: next.key || next.title });
      if (next.plan && !draftRef.current.dirty) {
        const text = next.plan.draft ?? next.plan.markdown;
        draftRef.current = { text, dirty: !!next.plan.draft, revision: 0, savedRevision: -1, version: next.plan.version };
        setDraft(text); setDirty(!!next.plan.draft); setHistory(undefined);
      }
      if (next.diff?.viewed) setLocalViewed({});
      setError('');
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }, [jobId, navigation]);

  useEffect(() => {
    void load();
    const stream = subscribeToJobChanges(change => {
      if (change.scope === 'job' && change.id === jobId) void load(change.parts);
    }, () => { void load(); });
    return () => stream.close();
  }, [jobId, load]);

  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (!draftRef.current.dirty) return;
    event.preventDefault();
    Alert.alert('Unsaved plan edits', 'Leave this plan? Your last autosaved draft is kept.', [
      { text: 'Stay', style: 'cancel' },
      { text: 'Leave', onPress: () => { draftRef.current.dirty = false; navigation.dispatch(event.data.action); } },
    ]);
  }), [navigation]);

  const editable = !!job?.actions.find(a => a.id === 'savePlanDraft' && a.enabled) && history === undefined;
  useEffect(() => {
    const timer = setInterval(async () => {
      const current = { ...draftRef.current };
      if (!editable || !current.dirty || requestBusy.current || current.savedRevision === current.revision) return;
      requestBusy.current = true;
      try {
        await jobCoreApi.command(jobId, 'savePlanDraft', { plan: current.text, version: current.version, autosave: true });
        draftRef.current.savedRevision = current.revision;
      } catch (err) { setError(String(err)); }
      finally { requestBusy.current = false; }
    }, 5000);
    return () => clearInterval(timer);
  }, [jobId, editable]);

  const selectTab = useCallback((next: JobTab) => { setTab(next); }, []);

  // Live diff: while the Diff tab is open and the job is active (diff.live set), refetch the diff part.
  const liveDiff = !!job?.diff?.live;
  useEffect(() => {
    if (tab !== 'diff' || !liveDiff) return undefined;
    const timer = setInterval(() => { void load(['diff']); }, LIVE_DIFF_REFRESH_MS);
    return () => clearInterval(timer);
  }, [tab, liveDiff, load]);

  const cmd = useJobCommands({
    jobId,
    actions: job?.actions || [],
    planDirty: dirty,
    planVersion: draftRef.current.version || job?.plan?.latest_version || job?.plan?.version,
    selectedFindings: findings,
    onEditPlan: () => { setEditing(true); selectTab('plan'); },
    onNeedFindings: () => selectTab('review'),
    onEditEnvironments: () => setEnvironments(true),
    onError: message => setError(message),
    onDone: async (result, actionId) => {
      setError('');
      if (actionId === 'delete') { draftRef.current.dirty = false; navigation.goBack(); return; }
      if (actionId === 'reprompt') setFindings([]);
      if (actionId === 'savePlanDraft') {
        const current = draftRef.current;
        current.version = result.data?.version || current.version;
        current.dirty = current.revision !== savedRevision.current;
        current.savedRevision = current.revision;
        setDirty(current.dirty);
        if (!current.dirty) setEditing(false);
      }
      await load(result.parts?.length ? result.parts : undefined);
    },
  });

  // Dashboard "Needs you" primary actions open the job and start the action.
  useEffect(() => {
    if (!job || !requestedAction || autoActionDone.current) return;
    autoActionDone.current = true;
    const action = job.actions.find(a => a.id === requestedAction);
    if (action) cmd.invoke(action);
  }, [job, requestedAction, cmd]);

  const loadDiagnostics = useCallback(() => {
    void jobsApi.diagnostics(jobId).then(r => setDiagnostics(r.diagnostics)).catch(e => setError(String(e)));
  }, [jobId]);
  useEffect(() => {
    if (tab === 'diagnostics' && !diagnostics) loadDiagnostics();
    if (tab === 'costs' && !attempts) void jobsApi.attempts(jobId).then(r => setAttempts((r.attempts || []).slice().reverse())).catch(() => setAttempts([]));
  }, [tab, diagnostics, attempts, jobId, loadDiagnostics]);

  const viewed = useMemo(() => {
    const set = new Set(job?.diff?.viewed || []);
    for (const [path, on] of Object.entries(localViewed)) { if (on) set.add(path); else set.delete(path); }
    return set;
  }, [job?.diff?.viewed, localViewed]);

  const setViewed = (path: string, on: boolean) => {
    setLocalViewed(v => ({ ...v, [path]: on }));
    // CONTRACT §4: server-side marks; older servers keep the mark for this visit only.
    jobCoreApi.markViewed(jobId, path, on).catch(() => undefined);
  };

  const jumpToDiff = (f: Finding) => {
    if (!f.file) return;
    setFocus({ file: f.file, line: f.line_start, seq: Date.now() });
    selectTab('diff');
    setTimeout(() => {
      const y = fileY.current[f.file || ''];
      if (y != null) scroller.current?.scrollTo({ y: Math.max(0, contentY.current + y - spacing.base), animated: true });
    }, 120);
  };

  const fixOne = (id: string) => cmd.invoke('reprompt', { finding_ids: [id] });

  if (!job) {
    return (
      <View style={[styles.loading, { backgroundColor: colors.canvas }]}>
        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : <Loading label="Loading job…" />}
      </View>
    );
  }

  const st = stateStatus(job.state);
  const failed = job.state === 'failed';
  const [vTone, vLabel] = verdictStyle(job.verdict);
  const agentsLabel = stageAgentsLabel(job.stage_agents);
  // Stages run by Claude Code / Codex show that agent instead of an (unused) mucli model.
  const stageModels = Object.entries(job.stage_models || {}).filter(([s, m]) => m?.model && (job.stage_agents?.[s]?.agent || 'mucli') === 'mucli');
  const files = job.diff ? parsePatch(job.diff.live ? job.diff.live.patch : job.diff.patch) : [];
  const budget = budgetSummary(job);
  const openFindings = (job.review?.findings || []).filter(f => (f.disposition || 'open') === 'open');
  const lastRun = (job.checks?.runs || []).slice(-1)[0];
  const liveSession = st === 'active' && ['planning', 'implementing', 'reviewing'].includes(job.state);
  const badge = (id: JobTab): React.ReactNode => {
    switch (id) {
      case 'session': return liveSession ? <Text variant="xs" accessibilityLabel="live" style={{ color: colors.running.ink }}>●</Text> : job.session?.approval ? <Text variant="xs" accessibilityLabel="approval needed" style={{ color: colors.attention.ink }}>◆</Text> : null;
      case 'plan': return job.plan?.draft || job.state === 'plan_review' ? <Text variant="xs" accessibilityLabel="needs you" style={{ color: colors.attention.ink }}>◆</Text> : null;
      case 'diff': return files.length ? <Text variant="xs"><Text variant="xs" style={{ color: colors.ok.ink }}>+{files.reduce((n, f) => n + f.added, 0)}</Text> <Text variant="xs" style={{ color: colors.danger.ink }}>−{files.reduce((n, f) => n + f.removed, 0)}</Text></Text> : null;
      case 'review': return job.review ? <CountBadge tone={openFindings.some(f => ['blocker', 'major'].includes(f.severity)) ? 'danger' : openFindings.some(f => f.severity === 'minor') ? 'warn' : 'muted'}>{openFindings.length}</CountBadge> : null;
      case 'checks': return job.state === 'checking' ? <Text variant="xs" accessibilityLabel="running" style={{ color: colors.running.ink }}>●</Text> : lastRun ? <Text variant="xs" accessibilityLabel={lastRun.passed ? 'passed' : 'failed'} style={{ color: lastRun.passed ? colors.ok.ink : colors.danger.ink }}>{lastRun.passed ? '✓' : '✗'}</Text> : null;
      case 'diagnostics': return failed ? <CountBadge tone="danger">!</CountBadge> : null;
      default: return null;
    }
  };
  const tabItems: TabItem[] = JOB_TABS.map(id => ({ id, label: JOB_TAB_LABELS[id], badge: badge(id), testID: `job-tab-${id}` }));
  const current = tab || 'session';
  // Paused on a cost/tokens/time/iterations cap: the limit banner owns raiseLimit + Cancel job.
  const limitPaused = !!job.limit;

  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <ScrollView
        ref={scroller}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.ink3} />}
      >
        <View style={styles.header} testID="job-header">
          <View style={styles.wrap}>
            <Text variant="lg" accessibilityRole="header" style={[styles.mono, { fontWeight: fontWeights.semibold }]}>{jobKey(job)}</Text>
            <Pill tone={STATUS[st].tone} glyph={STATUS[st].icon} testID="job-state-pill">{stateLabel(job.state)}</Pill>
            {st === 'active' ? <Pill tone="running" glyph="●">{`live · ${stateLabel(job.state)}`}</Pill> : null}
            {job.paused_from ? <Pill tone="warn">{`paused in ${stateLabel(job.paused_from)}`}</Pill> : null}
            {job.fix_attempts ? <Pill tone="muted">{`fix attempts ${job.fix_attempts}`}</Pill> : null}
            {job.verdict ? <Pill tone={vTone}>{vLabel}</Pill> : null}
            <EnvironmentsPill summary={job.environments} onPress={() => setEnvironments(true)} />
            <PrPill pr={job.pr} testID="job-pr-pill" />
            <View style={{ flex: 1 }} />
            <View style={{ alignItems: 'flex-end' }} testID="job-budget">
              <Text variant="md" accessibilityLabel={budget.costLabel} style={[styles.num, { fontWeight: fontWeights.semibold, color: budget.tone === 'danger' ? colors.danger.ink : budget.tone === 'warn' ? colors.attention.ink : colors.ink }]}>{budget.cost}</Text>
              <Text variant="xs" accessibilityLabel={budget.timeLabel} style={[styles.num, { color: colors.ink3 }]}>{budget.time}</Text>
            </View>
          </View>
          <Text variant="md">{job.title}</Text>
          <View style={styles.wrap}>
            {job.branch ? <View style={styles.row} accessible accessibilityLabel={`branch ${job.branch}`}><Icon name="git-branch" size="sm" color={colors.ink3} /><Text variant="xs" style={[styles.mono, { color: colors.ink3 }]}>{job.branch}</Text></View> : null}
            {job.repository ? <Text variant="xs" style={{ color: colors.ink3 }}>repo: <Text variant="xs" style={styles.mono}>{job.repository}</Text></Text> : null}
            {failed && job.failed_from ? <Text variant="xs" style={{ color: colors.danger.ink }}>failed in {stateLabel(job.failed_from)}</Text> : null}
            {agentsLabel ? <Text variant="xs" testID="job-stage-agents" style={{ color: colors.ink3 }}>agents: {agentsLabel}</Text> : null}
            {!agentsLabel || stageModels.length ? <Text variant="xs" style={{ color: colors.ink3 }}>models: {stageModels.length ? stageModels.map(([s, m]) => `${s} ${m?.model}`).join(' · ') : 'unset'}</Text> : null}
            <Pressable accessibilityRole="button" onPress={() => setModels(true)} style={styles.link}><Text variant="xs" style={{ color: colors.accent.ink }}>Job settings</Text></Pressable>
          </View>
          {job.attention_detail && !(job.session?.approval && job.state === 'needs_input') && !limitPaused
            ? failed ? <Banner tone="danger" glyph="✗" role="alert" title={job.attention_detail} /> : <Banner tone="warn" glyph="◆" title={job.attention_detail} />
            : null}
          {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} testID="job-error" /> : null}
        </View>

        <Panel flush>
          <PipelineStepper steps={job.pipeline || job.steps || []} onStep={id => selectTab(STEP_TABS[id] || 'session')} />
        </Panel>

        <ApprovalBanner approval={job.session?.approval} actions={job.actions} onInvoke={a => cmd.invoke(a)} busy={cmd.busy} />
        <EnvironmentApprovalBanner approval={job.environment_approval} actions={job.actions} onInvoke={a => cmd.invoke(a)} busy={cmd.busy} />

        <LimitBanner limit={job.limit} actions={job.actions} onInvoke={(a, extra) => cmd.invoke(a, extra)} busy={cmd.busy}
          error={cmd.lastError?.actionId === 'raiseLimit' ? cmd.lastError.message : null} />

        <JobActionBar actions={job.actions} onInvoke={a => cmd.invoke(a)} busy={cmd.busy} planDirty={dirty} exclude={limitPaused ? ['raiseLimit', 'discard'] : []} />

        <Tabs items={tabItems} value={current} onChange={id => selectTab(id as JobTab)} accessibilityLabel="Job views" />

        <View onLayout={e => { contentY.current = e.nativeEvent.layout.y; }} accessibilityLabel={`${JOB_TAB_LABELS[current]} tab`} style={styles.tabBody}>
          {current === 'session' ? <SessionTab job={job} invoke={cmd.invoke} busy={cmd.busy} /> : null}
          {current === 'plan' ? (
            <PlanTab
              job={job}
              editing={editing && editable}
              draft={draft}
              dirty={dirty}
              historyVersion={history}
              busy={cmd.busy}
              onEdit={() => setEditing(true)}
              onChangeDraft={text => { draftRef.current.text = text; draftRef.current.dirty = true; draftRef.current.revision++; setDraft(text); setDirty(true); }}
              onSave={() => { savedRevision.current = draftRef.current.revision; cmd.invoke('savePlanDraft', { plan: draftRef.current.text, version: draftRef.current.version }); }}
              onCancel={() => setEditing(false)}
              onVersion={version => {
                if (version === undefined) { setHistory(undefined); if (!dirty) void load(['plan']); return; }
                void jobCoreApi.job(jobId, ['plan'], version).then(next => { setJob(old => ({ ...old!, plan: next.plan })); setHistory(version); }).catch(e => setError(String(e)));
              }}
            />
          ) : null}
          {current === 'diff' ? (
            <DiffTab job={job} viewed={viewed} onViewed={setViewed} onFix={fixOne} focus={focus}
              onFileLayout={(path, y) => { fileY.current[path] = y; }} />
          ) : null}
          {current === 'review' ? (
            <ReviewTab
              job={job}
              selected={findings}
              busy={cmd.busy}
              onToggle={id => setFindings(old => (old.includes(id) ? old.filter(x => x !== id) : [...old, id]))}
              onFixSelected={() => cmd.invoke('reprompt')}
              onFixOne={fixOne}
              onDisposition={(id, disposition) => cmd.invoke('markFindings', { ids: [id], disposition })}
              onJump={jumpToDiff}
            />
          ) : null}
          {current === 'checks' ? <ChecksTab job={job} invoke={cmd.invoke} busy={cmd.busy} /> : null}
          {current === 'diagnostics' ? (
            <View style={styles.col}>
              <View style={styles.row}>
                <Text variant="md" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold, flex: 1 }}>Job diagnostics</Text>
                <Button small label="Refresh" onPress={loadDiagnostics} />
                <Button small label="Job performance" onPress={() => navigation.navigate('JobAnalysis', { jobId })} />
              </View>
              {diagnostics ? (
                <>
                  <Panel title="Worker">
                    <KV label="Status">{diagnostics.status.replace(/_/g, ' ')}{diagnostics.attention_reason ? ` · ${diagnostics.attention_reason.replace(/_/g, ' ')}` : ''}</KV>
                    <KV label="Worker">{diagnostics.worker_id || '—'}</KV>
                    <KV label="Heartbeat">{diagnostics.heartbeat_at ? when(diagnostics.heartbeat_at) : '—'}</KV>
                    <KV label="Base"><Text variant="xs" style={styles.mono}>{diagnostics.base_branch} {diagnostics.base_sha?.slice(0, 12)}</Text></KV>
                    <KV label="Worktree"><Text variant="xs" style={styles.mono}>{diagnostics.worktree || '—'}</Text></KV>
                  </Panel>
                  {diagnostics.latest_failure ? (
                    <Banner tone="danger" glyph="✗" role="alert" title={`Latest failure: ${diagnostics.latest_failure.event_type}`}>
                      <Text variant="xs">{diagnostics.latest_failure.reason}</Text>
                      <Pre numberOfLines={20}>{JSON.stringify(diagnostics.latest_failure.payload, null, 2)}</Pre>
                    </Banner>
                  ) : null}
                  <Panel title="Worker log" right={<Text variant="xs" style={{ color: colors.ink3 }}>{diagnostics.worker_log_truncated ? 'tail' : ''}</Text>}>
                    <Pre testID="job-worker-log">{diagnostics?.worker_log_tail || 'No worker log.'}</Pre>
                  </Panel>
                  {diagnostics.diagnostics?.length ? (
                    <Panel title="Diagnostic events" badge={diagnostics.diagnostics.length}>
                      {diagnostics.diagnostics.slice().reverse().map(e => <AuditRow key={e.id} event={e} />)}
                    </Panel>
                  ) : null}
                </>
              ) : <Loading label="Loading diagnostics…" />}
            </View>
          ) : null}
          {current === 'costs' ? <CostsTab job={job} attempts={attempts} /> : null}
        </View>
      </ScrollView>
      {cmd.sheets}
      <JobStageModelsSheet visible={models} onClose={() => setModels(false)} onSaved={() => setModels(false)} />
      <JobEnvironmentsSheet visible={environments} onClose={() => setEnvironments(false)} environments={job.build_environments}
        actions={job.actions} invoke={cmd.invoke} busy={cmd.busy} repository={job.repository} session={job.origin_session} />
    </SafeAreaView>
  );
}

type AuditLike = { id: number; event_type: string; reason: string; created_at: number; payload: Record<string, unknown>; to_status?: string | null };

/** Audit row: tone dot, time, type → status, reason; tap to expand the payload. */
function AuditRow({ event: e }: { event: AuditLike }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const tone = eventTone(`${e.event_type} ${e.to_status || ''}`);
  return (
    <View style={styles.audit}>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }} accessibilityLabel={`${when(e.created_at)} ${e.event_type}${e.to_status ? ` to ${e.to_status}` : ''}`}
        onPress={() => setOpen(v => !v)} style={[styles.row, { minHeight: touchTarget, alignItems: 'flex-start' }]}>
        <Text variant="xs" style={{ color: toneInk(colors, tone), marginTop: 2 }}>●</Text>
        <View style={{ flex: 1 }}>
          <Text variant="xs"><Text variant="xs" style={{ fontWeight: fontWeights.semibold }}>{e.event_type}</Text>{e.to_status ? <Text variant="xs" style={{ color: colors.ink3 }}> → {e.to_status}</Text> : null}</Text>
          {e.reason ? <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={open ? undefined : 2}>{e.reason}</Text> : null}
        </View>
        <Text variant="xs" style={[styles.mono, { color: colors.ink3 }]}>{when(e.created_at).slice(5)}</Text>
      </Pressable>
      {open ? <Pre>{JSON.stringify(e.payload)}</Pre> : null}
    </View>
  );
}

/** Cost & audit: cost per stage bars, attempts and the audit timeline. */
function CostsTab({ job, attempts }: { job: JobVM; attempts: JobAttempt[] | null }) {
  const { colors } = useTheme();
  const costs = job.costs;
  const stages = Object.entries(costs?.by_stage || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(0.0001, ...stages.map(([, v]) => v));
  const events = (costs?.events || []).filter(e => !['sandbox_event', 'checks_output'].includes(e.event_type));
  const times = events.map(e => Number(e.created_at)).filter(Number.isFinite);
  const t0 = Math.min(...times), t1 = Math.max(...times);
  return (
    <View style={styles.col}>
      <Panel title="Cost per stage" right={<Text variant="sm" style={[styles.num, { fontWeight: fontWeights.bold }]}>{money(costs?.total_usd ?? job.cost_usd)}</Text>} testID="job-costs">
        {stages.length ? stages.map(([s, v]) => (
          <View key={s} style={styles.costRow}>
            <Text variant="xs" style={{ width: 84 }}>{s}</Text>
            <Meter value={v} max={max} tone="accent" accessibilityLabel={`${s} ${money(v)}`} />
            <Text variant="xs" style={[styles.num, { width: 56, textAlign: 'right' }]}>{money(v)}</Text>
          </View>
        )) : <Empty>No cost recorded.</Empty>}
      </Panel>
      <Panel title="Attempts" badge={attempts?.length ?? ''}>
        {attempts == null ? <Loading label="Loading attempts…" /> : attempts.length ? attempts.map(a => {
          const tone = /fail|error/.test(a.status) ? 'danger' : /run/.test(a.status) ? 'running' : /succ|done|complete|pass/.test(a.status) ? 'ok' : 'muted';
          return (
            <View key={a.id} style={[styles.attempt, { borderBottomColor: colors.hairline }]}>
              <View style={styles.row}>
                <Text variant="xs" style={styles.num}>#{a.number}</Text>
                <Pill tone={tone}>{a.status}</Pill>
                <View style={{ flex: 1 }} />
                <Text variant="xs" style={styles.num}>{money(a.cost_usd)}</Text>
                <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{a.finished_at && a.started_at ? duration((a.finished_at - a.started_at) * 1000) : ''}</Text>
              </View>
              <Text variant="xs" style={{ color: colors.ink3 }}>{when(a.started_at)}</Text>
              {a.error ? <Text variant="xs" style={{ color: colors.danger.ink }} numberOfLines={3}>{a.error}</Text> : null}
            </View>
          );
        }) : <Empty>None.</Empty>}
      </Panel>
      <Panel title="Audit timeline" badge={events.length} testID="job-audit">
        {times.length > 1 ? (
          <View accessible accessibilityRole="image" accessibilityLabel={`Timeline: ${events.length} events from ${when(t0)} to ${when(t1)}`}>
            <View style={styles.timeline}>
              <View style={[styles.timelineAxis, { backgroundColor: colors.hairline }]} />
              {events.map(e => (
                <View key={e.id} style={[styles.timelineDot, { left: `${((Number(e.created_at) - t0) / Math.max(1, t1 - t0)) * 96 + 2}%`, backgroundColor: toneInk(colors, eventTone(`${e.event_type} ${e.to_status || ''}`)) }]} />
              ))}
            </View>
            <View style={styles.row}>
              <Text variant="xs" style={[styles.mono, { color: colors.ink3, flex: 1 }]}>{when(t0)}</Text>
              <Text variant="xs" style={[styles.mono, { color: colors.ink3 }]}>{when(t1)}</Text>
            </View>
          </View>
        ) : null}
        {events.length ? events.slice().reverse().map(e => <AuditRow key={e.id} event={e} />) : <Empty>No audit entries.</Empty>}
      </Panel>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  loading: { flex: 1, padding: spacing.base },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['3xl'] },
  header: { gap: spacing.xs },
  col: { gap: spacing.md },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  mono: { fontFamily: fonts.mono },
  num: { fontVariant: ['tabular-nums'] },
  link: { minHeight: 32, justifyContent: 'center' },
  tabBody: { gap: spacing.md },
  costRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 28 },
  attempt: { paddingVertical: spacing.xs, borderBottomWidth: 1, gap: 2 },
  audit: { gap: spacing.xs },
  timeline: { height: 20, justifyContent: 'center', marginBottom: spacing.xs },
  timelineAxis: { height: 1, alignSelf: 'stretch' },
  timelineDot: { position: 'absolute', width: 8, height: 8, borderRadius: 4, top: 6 },
});

