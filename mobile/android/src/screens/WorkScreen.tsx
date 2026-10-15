import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import {
  jobsApi,
  costCapPayload,
  costPlaceholder,
  defaultWorkspace,
  isTicketKey,
  newJobDefaults,
  isRepositoryUrl,
  sessionForWorkspace,
  unattendedDefault,
  type JobExecutionProfile,
  type JobStageName,
} from '../api/jobs';
import { jobCoreApi, subscribeToJobChanges, type HomeVM, type JobSummary } from '../api/jobCore';
import { JobStageModelsSheet } from '../components/JobStageModelsSheet';
import { JiraSheet } from '../components/JiraSheet';
import { WorkspaceField, useJobSessions, workspaceHint } from '../components/JobSessionPicker';
import { BuildEnvironmentsField, RepositoryEnvironmentApproval } from '../components/JobEnvironments';
import { bindingsFromDrafts, draftError, type EnvironmentDraft } from '../api/environments';
import { draftsFromDefaults, stageAgentsPayload, stageModelsPayload, type StageAgentDrafts } from '../api/agents';
import { StageAgentsField, useAgentsStatus, type StageModelDefaults } from '../components/JobAgents';
import { setupApi } from '../api/setup';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, touchTarget, tracking } from '../theme/tokens';
import { STATUS, duration, money, stateLabel, stateStatus } from '../theme/status';
import { BAR_HIDDEN, actionLabel, actionTone } from '../components/JobActions';
import { JobRow, JobsNavBar, ResultRow, focusStep, jobKey, stepsOf, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import { Banner, Button, Empty, Field, Loading, Panel, Pressable, Sheet, StatStrip, StatusIcon, WashBackground, SwitchRow, Text, TextField, useInterval, Icon } from '../ui';

export type WorkScreenProps = NativeStackScreenProps<RootStackParamList, 'Work'>;

/** Dashboard panel order (homeVM): Needs you, Running now, then the jobs table. */
const BOARD_ORDER: Array<{ key: 'needs_you' | 'running' | 'jobs'; label: string }> = [
  { key: 'needs_you', label: 'Needs you' },
  { key: 'running', label: 'Running now' },
  { key: 'jobs', label: 'Jobs' },
];

function lines(value: string): string[] {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
}

export function WorkScreen({ navigation }: WorkScreenProps) {
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const [board, setBoard] = useState<HomeVM | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [jiraOpen, setJiraOpen] = useState(false);
  const [missingStages, setMissingStages] = useState<JobStageName[]>([]);
  const [setupPending, setSetupPending] = useState(false);
  useEffect(() => {
    // Subtle nudge until the guided setup has been finished once (never blocks the dashboard).
    let live = true;
    setupApi.get().then(vm => { if (live) setSetupPending(!vm?.completed_at); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useInterval(() => setNow(Date.now() / 1000), 30_000);

  const loadSettings = useCallback(async () => {
    try {
      const settings = await jobsApi.getSettings();
      setMissingStages(Array.isArray(settings.missing) ? settings.missing : []);
    } catch {
      // Older servers have no settings endpoint; leave the banner hidden.
    }
  }, []);

  useEffect(() => { void loadSettings(); }, [loadSettings]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const next = await jobCoreApi.home();
      setBoard(next);
      setError('');
      setNow(Date.now() / 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!quiet) setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const stream = subscribeToJobChanges(change => { if (change.scope === 'home') void load(true); }, () => { void load(true); });
    return () => stream.close();
  }, [load]);

  const total = board?.counts.total || 0;
  const counts = useMemo(() => {
    const jobs = board?.jobs || [];
    return {
      needs: board?.needs_you.length || 0,
      running: board?.running.length || 0,
      checking: jobs.filter(j => j.state === 'checking').length,
      ready: jobs.filter(j => j.state === 'review_done').length,
      jobs: jobs.length,
    };
  }, [board]);
  const recent = useMemo(() => (board?.jobs || [])
    .filter(j => ['done', 'pushed', 'failed', 'cancelled'].includes(j.state))
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, 6), [board]);

  const refresh = () => {
    setRefreshing(true);
    void load(true);
  };
  const open = (jobId: string, action?: string) => navigation.navigate('JobDetail', action ? { jobId, action } : { jobId });
  const go = (page: JobsPage) => switchJobsPage(navigation, 'Work', page);

  const sections: Record<'needs_you' | 'running' | 'jobs', React.ReactNode> = {
    needs_you: (
      <Panel key="needs_you" title="Needs you" glyph="◆" glyphTone="warn" badge={counts.needs} badgeTone="warn" testID="dashboard-needs-you">
        {board?.needs_you.length ? board.needs_you.map(job => <NeedsYouCard key={job.id} job={job} onOpen={() => open(job.id)} onAction={id => open(job.id, id)} />)
          : <Empty status="done">Nothing needs you right now.</Empty>}
      </Panel>
    ),
    running: (
      <Panel key="running" title="Running now" glyph="●" glyphTone="running" badge={counts.running} badgeTone="running" testID="dashboard-running">
        {board?.running.length ? board.running.map(job => <RunningRow key={job.id} job={job} onPress={() => open(job.id)} />)
          : <Empty>Nothing is running.</Empty>}
      </Panel>
    ),
    jobs: (
      <Panel key="jobs" title="Jobs" badge={counts.jobs} flush right={<Button small ghost label="All jobs →" onPress={() => go('JobsList')} />} testID="dashboard-jobs">
        {(board?.jobs || []).slice(0, 12).map(job => <JobRow key={job.id} job={job} now={now} onPress={() => open(job.id)} />)}
      </Panel>
    ),
  };

  return (
    <WashBackground art="lagoon" height={220} testID="work-wash">
    <SafeAreaView edges={[]} style={[styles.safeArea, { backgroundColor: 'transparent' }]}>
      <JobsNavBar current="Work" onNavigate={go} home={board} />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}
      >
        <View style={styles.pageHeader}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xl" accessibilityRole="header" style={styles.pageTitle}>Dashboard</Text>
            <Text variant="xs" style={{ color: colors.ink3 }}>
              All sessions · <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{total} total</Text>
            </Text>
          </View>
          <Button
            label="Jira"
            accessibilityLabel="Jira tickets and link settings"
            icon={<Icon name="ticket" size="md" color={colors.ink3} />}
            onPress={() => setJiraOpen(true)}
          />
          <Button
            iconOnly
            label="Job settings"
            accessibilityLabel="Job settings: stage models"
            icon={<Icon name="sliders-horizontal" size="md" color={colors.ink3} />}
            onPress={() => setSettingsOpen(true)}
          />
          <Button
            primary
            label="New job"
            accessibilityLabel="Create engineering job"
            icon={<Icon name="plus" size="md" color={colors.onInk} />}
            onPress={() => setCreateOpen(true)}
          />
        </View>

        {setupPending || missingStages.length ? (
          // One setup call-to-action: unfinished setup first, else stages without a default model.
          <Pressable accessibilityRole="button" testID="work-setup-cta"
            accessibilityLabel={setupPending ? 'Jobs setup not finished. Open setup.' : 'Stage models not set. Open job settings.'}
            onPress={() => (setupPending ? navigation.navigate('JobSetup') : setSettingsOpen(true))}>
            <Banner tone={setupPending ? 'accent' : 'warn'} glyph={setupPending ? STATUS.pending.icon : STATUS.waiting.icon}
              title={setupPending
                ? `Finish jobs setup: check and test everything jobs need.${missingStages.length ? ` No default model for ${missingStages.join(', ')} (jobs use their session’s model).` : ''}`
                : `No default model for ${missingStages.join(', ')}: jobs use their session’s model for those stages.`}
              right={<Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>{setupPending ? 'Open setup' : 'Set models'}</Text>} />
          </Pressable>
        ) : null}

        {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}

        {loading && !board ? (
          <Loading label="Loading jobs…" />
        ) : (
          <>
            {/* The Lagoon wash band sits above; the strip starts where it has faded to paper. */}
            <View style={{ height: 96 }} />
            {/* Wash stat strip (G§9.3): one row of big tabular numbers with
                captions and hairline dividers, not a card per metric. */}
            <View style={styles.stats} accessibilityLabel="Summary">
              <StatStrip items={[
                { testID: 'stat-needs', value: counts.needs, label: 'needs you', tone: counts.needs ? 'attention' : 'neutral', glyph: STATUS.waiting.icon },
                { testID: 'stat-running', value: counts.running, label: counts.running === 1 ? 'job running' : 'jobs running', tone: counts.running ? 'running' : 'neutral', glyph: STATUS.active.icon, onPress: () => go('JobActivity') },
                { testID: 'stat-checking', value: counts.checking, label: counts.checking === 1 ? 'check running' : 'checks running', tone: counts.checking ? 'running' : 'neutral', glyph: STATUS.active.icon, onPress: () => go('JobActivity') },
                { testID: 'stat-ready', value: counts.ready, label: 'ready to push', tone: counts.ready ? 'ok' : 'neutral', glyph: STATUS.done.icon },
                { testID: 'stat-jobs', value: counts.jobs, label: 'jobs', tone: 'neutral', glyph: STATUS.pending.icon, onPress: () => go('JobsList') },
              ]} />
            </View>
            {total === 0 ? (
              <Panel title="No engineering jobs yet">
                <Text variant="sm" style={{ color: colors.ink3 }}>
                  Queue a real ticket here, from the TUI, or from the web Engineering Work page. Import tickets from a Jira board on Tickets.
                </Text>
              </Panel>
            ) : BOARD_ORDER.map(section => sections[section.key])}
            {total ? (
              <Panel title="Recent results" testID="dashboard-recent">
                {recent.length ? recent.map(job => <ResultRow key={job.id} job={job} now={now} onPress={() => open(job.id)} />)
                  : <Empty>No jobs finished recently.</Empty>}
              </Panel>
            ) : null}
          </>
        )}
      </ScrollView>

      <JobStageModelsSheet
        visible={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={missing => {
          setMissingStages(missing);
          setSettingsOpen(false);
        }}
      />

      <JiraSheet
        visible={jiraOpen}
        sessionName={activeSessionName}
        onClose={() => setJiraOpen(false)}
        onImported={() => { void load(true); }}
        onOpenJob={jobId => {
          setJiraOpen(false);
          navigation.navigate('JobDetail', { jobId });
        }}
      />

      <NewJobSheet
        visible={createOpen}
        sessionName={activeSessionName}
        provider={activeProvider}
        model={activeModel}
        onClose={() => setCreateOpen(false)}
        onCreated={job => {
          setCreateOpen(false);
          void load(true);
          navigation.navigate('JobDetail', { jobId: job.id });
        }}
      />
    </SafeAreaView>
    </WashBackground>
  );
}

/** Needs-you card: state, title, attention detail and the job's enabled primary actions. */
function NeedsYouCard({ job, onOpen, onAction }: { job: JobSummary; onOpen: () => void; onAction: (id: string) => void }) {
  const { colors } = useTheme();
  const failed = job.state === 'failed';
  const primary = job.actions.filter(a => a.primary && a.enabled && !BAR_HIDDEN.has(a.id) && !a.hidden);
  return (
    <View testID={`needs-you-${job.id}`} style={[styles.card, { borderColor: failed ? colors.danger.pigment : colors.hairline, backgroundColor: colors.raised, borderLeftColor: failed ? colors.danger.ink : colors.attention.ink }]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Open ${jobKey(job)} ${job.title}`} onPress={onOpen} style={styles.cardHead}>
        <StatusIcon status={stateStatus(job.state)} label={stateLabel(job.state)} />
        <Text variant="sm" style={[styles.key, { color: colors.accent.ink }]}>{jobKey(job)}</Text>
        <Text variant="xs" style={{ color: failed ? colors.danger.ink : colors.attention.ink }}>{stateLabel(job.state)}</Text>
        <View style={{ flex: 1 }} />
        <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{money(job.cost_usd)}</Text>
      </Pressable>
      <Text variant="sm" numberOfLines={2}>{job.title}</Text>
      {job.origin_session ? <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1}>session · {job.origin_session}</Text> : null}
      {job.limit ? (
        <Text variant="xs" testID={`needs-you-${job.id}-limit`} style={{ color: colors.attention.ink, fontWeight: fontWeights.semibold }} numberOfLines={1}>{job.limit.summary || job.attention_summary || job.limit.message}</Text>
      ) : null}
      {job.attention_detail ? <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={2}>{job.attention_detail}</Text> : null}
      {primary.length ? (
        <View style={styles.cardActions}>
          {primary.map(a => <Button key={a.id} small primary tone={actionTone(a)} label={actionLabel(a)} onPress={() => onAction(a.id)} testID={`needs-you-${job.id}-${a.id}`} />)}
        </View>
      ) : null}
    </View>
  );
}

/** Running-now row: stage, elapsed in the focus step, cost and the step detail line. */
function RunningRow({ job, onPress }: { job: JobSummary; onPress: () => void }) {
  const { colors } = useTheme();
  const step = focusStep(stepsOf(job));
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${jobKey(job)} ${stateLabel(job.state)}`} onPress={onPress}
      style={({ pressed }) => [styles.running, { backgroundColor: pressed ? colors.sunken : 'transparent', borderBottomColor: colors.hairline }]}>
      <View style={styles.cardHead}>
        <Icon name={job.state === 'checking' ? 'hammer' : job.state === 'pushing' ? 'cloud-upload' : 'cpu'} size="sm" color={colors.running.ink} accessibilityLabel={job.state === 'checking' ? 'checks' : 'agent'} />
        <Text variant="sm" style={[styles.key, { color: colors.accent.ink }]}>{jobKey(job)}</Text>
        <Text variant="xs">{stateLabel(job.state)}</Text>
        <View style={{ flex: 1 }} />
        <Text variant="xs" style={[styles.num, { color: colors.ink3 }]}>{(step?.elapsed || 0) >= 1 ? duration((step?.elapsed || 0) * 1000) : ''}</Text>
        <Text variant="xs" style={styles.num}>{money(job.cost_usd)}</Text>
      </View>
      <Text variant="xs" numberOfLines={1}>{job.title}{job.origin_session ? <Text variant="xs" style={{ color: colors.ink3 }}>{`  · ${job.origin_session}`}</Text> : null}</Text>
      {step?.detail ? <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }} numberOfLines={1}>› {step.detail}</Text> : null}
    </Pressable>
  );
}

type NewJobSheetProps = {
  visible: boolean;
  sessionName: string | null;
  provider: string | null;
  model: string | null;
  onClose: () => void;
  onCreated: (job: { id: string }) => void;
};

/** New job form, workspace first: recent workspaces come from saved sessions (the app's session
 * preselected) and any path can be typed, so there is no dead end without a saved session. One
 * "Title or ticket key" field: a Jira key imports that ticket. Models, environments, budgets and
 * retries sit under Advanced; "Run unattended in sandbox" is an explicit toggle (Settings default). */
export function NewJobSheet({ visible, sessionName, onClose, onCreated }: NewJobSheetProps) {
  const { colors } = useTheme();
  const sessionsState = useJobSessions(visible);
  const touched = useRef<{ cost?: boolean; runtime?: boolean; retries?: boolean; unattended?: boolean; workspace?: boolean }>({});
  const [workspace, setWorkspace] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [acceptance, setAcceptance] = useState('');
  const [validation, setValidation] = useState('');
  const [cost, setCost] = useState('');
  const [costHint, setCostHint] = useState('Settings default ($100)');
  const [runtime, setRuntime] = useState('');
  const [retries, setRetries] = useState('2');
  const [unattended, setUnattended] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [stageDefaults, setStageDefaults] = useState<StageModelDefaults>({});
  // null = the resolved set (repo config / session / Settings); a list = this job's override.
  const [environments, setEnvironments] = useState<EnvironmentDraft[] | null>(null);
  // Agent and model per stage (mucli / Claude Code / Codex), agents prefilled from Settings → Agent harnesses.
  const agents = useAgentsStatus(visible);
  const [agentDrafts, setAgentDrafts] = useState<StageAgentDrafts>(() => draftsFromDefaults(null));
  useEffect(() => { if (visible) setAgentDrafts(draftsFromDefaults(agents.status?.defaults)); }, [visible, agents.status]);

  useEffect(() => {
    if (!visible) return;
    setEnvironments(null);
    setError('');
  }, [visible]);

  // Preselect the app session's workspace (else the current / most recent one) until the user types.
  useEffect(() => {
    if (visible && !touched.current.workspace) setWorkspace(defaultWorkspace(sessionsState.sessions, sessionName));
  }, [visible, sessionsState.sessions, sessionName]);

  // Prefill limits and the unattended toggle from Settings → New job defaults unless the user changed them.
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    jobsApi.getSettings().then(settings => {
      if (!alive || !settings) return;
      setStageDefaults((settings.stage_models || {}) as StageModelDefaults);
      if (!settings.rules) return;
      const d = newJobDefaults(settings.rules);
      const t = touched.current;
      setCostHint(costPlaceholder(settings.rules));
      if (!t.cost) setCost(d.cost);
      if (!t.runtime) setRuntime(d.runtime);
      if (!t.retries && d.retries) setRetries(d.retries);
      if (!t.unattended) setUnattended(unattendedDefault(settings.rules));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [visible]);

  const remote = isRepositoryUrl(workspace);
  const matched = remote ? undefined : sessionForWorkspace(sessionsState.sessions, workspace);
  // Servers without GET /api/jobs/sessions: the app's session supplies the workspace when none is typed.
  const fallbackSession = !sessionsState.supported ? sessionName : null;
  const sessionForJob = matched?.name || fallbackSession || undefined;
  const ticket = isTicketKey(title);
  const edit = (key: 'cost' | 'runtime' | 'retries', set: (v: string) => void) => (v: string) => { touched.current = { ...touched.current, [key]: true }; set(v); };
  const pickWorkspace = (path: string) => { touched.current = { ...touched.current, workspace: true }; setWorkspace(path); };

  const submit = async () => {
    const cleanTitle = title.trim();
    const path = workspace.trim();
    if (!path && !fallbackSession) {
      setError('Choose the workspace this job works in.');
      return;
    }
    if (!cleanTitle) {
      setError('Enter a title or a ticket key.');
      return;
    }
    const envError = (environments || []).map(draftError).find(Boolean);
    if (envError) {
      setError(envError);
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const common: Record<string, unknown> & { execution: Partial<JobExecutionProfile> } = {
        max_retries: Math.max(0, Number(retries || 2)),
        // Unattended sandbox writes are always explicit: the job never inherits session YOLO.
        execution: { auto_approve_writes: unattended },
      };
      // A git URL is sent as `repository` (the server clones it); a path stays the workspace.
      if (path) { if (isRepositoryUrl(path)) common.repository = path; else common.workspace_root = path; }
      // Blank = Settings default (omitted); 0 is sent explicitly and means no cap.
      const costValue = costCapPayload(cost);
      const runtimeValue = Number(runtime || 0);
      if (costValue !== undefined) common.max_cost_usd = costValue;
      if (runtimeValue > 0) common.max_runtime_seconds = runtimeValue;
      const stages = stageModelsPayload(agentDrafts);
      if (Object.keys(stages).length) common.execution.stages = stages;
      const envOverride = environments ? bindingsFromDrafts(environments) : [];
      if (envOverride.length) common.execution.environments = envOverride;
      const stageAgents = stageAgentsPayload(agentDrafts, agents.status?.defaults);
      if (stageAgents) common.execution.stage_agents = stageAgents;
      let jobId = '';
      if (ticket) {
        const res = await jobsApi.importTicket(cleanTitle, sessionForJob, common);
        const created = res.created?.[0], skipped = res.skipped?.[0], failed = res.errors?.[0];
        if (failed && !created) throw new Error(`${failed.key}: ${failed.error}`);
        jobId = created?.job_id || skipped?.job_id || '';
      } else {
        const input = {
          ...common,
          title: cleanTitle,
          description: description.trim(),
          session_name: sessionForJob,
          acceptance_criteria: lines(acceptance),
          validation_commands: lines(validation),
        } as Parameters<typeof jobsApi.create>[0];
        const response = await jobsApi.create(input);
        jobId = response.job.id;
      }
      setTitle('');
      setDescription('');
      setAcceptance('');
      setValidation('');
      setCost('');
      setRuntime('');
      setRetries('2');
      touched.current = { workspace: touched.current.workspace };
      setShowAdvanced(false);
      setEnvironments(null);
      if (jobId) onCreated({ id: jobId });
      else onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Sheet visible={visible} onClose={onClose} title="New engineering job">
      <WorkspaceField sessions={sessionsState.sessions} value={workspace} onChange={pickWorkspace}
        hint={workspaceHint(sessionsState.sessions, workspace, fallbackSession)} error={sessionsState.error} />
      {!remote ? <RepositoryEnvironmentApproval repository={workspace.trim() || undefined} session={sessionForJob || null} /> : null}
      <View>
        <Field label="Title or ticket key" hint={ticket ? `Imports Jira ticket ${title.trim()}: its summary, description and acceptance criteria become the job.` : 'A Jira key (PROJ-123) imports that ticket; anything else becomes the job title.'}>
          <TextField accessibilityLabel="Title or ticket key" testID="job-title" value={title} onChangeText={setTitle}
            placeholder="Fix OAuth callback validation — or PROJ-123" autoCapitalize="sentences" />
        </Field>
      </View>
      {!ticket ? (
        <>
          <SheetField label="Description" value={description} onChangeText={setDescription} placeholder="What should change?" multiline />
          <SheetField label="Acceptance criteria" value={acceptance} onChangeText={setAcceptance} placeholder="One per line (optional: the planner proposes them)" multiline />
          <SheetField label="Validation commands" value={validation} onChangeText={setValidation} placeholder={'pytest tests/auth\nnpm run typecheck'} multiline />
          <Text variant="xs" style={[styles.sheetHint, { color: colors.ink3 }]}>
            Optional. The planner proposes checks from the repository; commands listed here are added to them.
          </Text>
        </>
      ) : null}
      <SwitchRow label="Run unattended in sandbox" testID="job-unattended" value={unattended}
        hint="On: the implement stage auto-approves its writes inside the job’s disposable container. Off: each write waits for your approval. Default from Settings; never taken from the chat session."
        onValueChange={v => { touched.current = { ...touched.current, unattended: true }; setUnattended(v); }} />
      <Pressable
        onPress={() => setShowAdvanced((v) => !v)}
        accessibilityRole="button"
        accessibilityState={{ expanded: showAdvanced }}
        accessibilityLabel="Advanced"
        testID="job-advanced-toggle"
        style={styles.stageToggle}
      >
        <Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>
          {showAdvanced ? '▴ Hide advanced' : '▾ Advanced: models, environments, budgets, retries'}
        </Text>
      </Pressable>
      {showAdvanced ? (
        <View style={styles.advanced} testID="job-advanced">
          <StageAgentsField status={agents.status} value={agentDrafts} onChange={setAgentDrafts} stageDefaults={stageDefaults} />
          <BuildEnvironmentsField active={visible} session={sessionForJob || null} repository={remote ? undefined : workspace.trim() || undefined}
            value={environments} onChange={setEnvironments} />
          <View style={styles.sheetGrid}>
            <SheetField label="Cost USD" value={cost} onChangeText={edit('cost', setCost)} placeholder={costHint} keyboardType="decimal-pad" compact />
            <SheetField label="Runtime sec" value={runtime} onChangeText={edit('runtime', setRuntime)} placeholder="no limit" keyboardType="number-pad" compact />
            <SheetField label="Retries" value={retries} onChangeText={edit('retries', setRetries)} placeholder="2" keyboardType="number-pad" compact />
          </View>
          <Text variant="xs" testID="job-cost-hint" style={[styles.sheetHint, { color: colors.ink3 }]}>
            {costCapPayload(cost) === 0 ? 'Cost: no cap — only the per-stage budgets apply.' : 'Cost: blank = Settings default · 0 = no cap.'}
          </Text>
        </View>
      ) : null}
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      <View style={styles.sheetActions}>
        <Button label="Cancel" onPress={onClose} />
        <Button primary loading={submitting} disabled={submitting} label={ticket ? 'Import ticket' : 'Queue job'} onPress={() => void submit()} testID="job-create-submit" />
      </View>
    </Sheet>
  );
}

type SheetFieldProps = {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  placeholder: string;
  multiline?: boolean;
  keyboardType?: 'default' | 'decimal-pad' | 'number-pad';
  compact?: boolean;
};

function SheetField({ label, value, onChangeText, placeholder, multiline = false, keyboardType = 'default', compact = false }: SheetFieldProps) {
  return (
    <View style={[styles.sheetField, compact && styles.sheetFieldCompact]}>
      <Field label={label}>
        <TextField
          accessibilityLabel={label}
          value={value}
          onChangeText={onChangeText}
          placeholder={placeholder}
          multiline={multiline}
          keyboardType={keyboardType}
          autoCapitalize="none"
          style={multiline ? styles.sheetTextarea : undefined}
        />
      </Field>
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  pageHeader: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: spacing.sm,
  },
  pageHeaderCopy: { flex: 1, minWidth: 160 },
  pageTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  num: { fontVariant: ['tabular-nums'] },
  key: { fontFamily: fonts.mono, fontWeight: fontWeights.semibold },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  card: { borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, padding: spacing.md, gap: spacing.xs },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 28 },
  cardActions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  running: { paddingVertical: spacing.sm, gap: 2, borderBottomWidth: 1, minHeight: touchTarget },
  sheetIntro: { marginBottom: spacing.base },
  sheetField: {},
  sheetFieldCompact: { flex: 1, minWidth: 80 },
  sheetTextarea: { minHeight: 72 },
  sheetGrid: { flexDirection: 'row', gap: spacing.md },
  stageToggle: { minHeight: touchTarget, justifyContent: 'center' },
  sheetHint: { marginTop: -spacing.sm, marginBottom: spacing.sm },
  advanced: { gap: spacing.md },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
