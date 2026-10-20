// New job (Work → New job): workspace first, one "Title or ticket key" field, Run in, and models,
// environments, budgets and retries under Advanced. Jobs always run unattended.

import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import {
  jobsApi,
  costCapPayload,
  costPlaceholder,
  defaultWorkspace,
  isTicketKey,
  newJobDefaults,
  isRepositoryUrl,
  sessionForWorkspace,
  type JobExecutionProfile,
} from '../api/jobs';
import { WorkspaceField, useJobSessions, workspaceHint } from './JobSessionPicker';
import { BuildEnvironmentsField, RepositoryEnvironmentApproval } from './JobEnvironments';
import { bindingsFromDrafts, draftError, draftFromSpec, emptyDraft, type EnvironmentDraft } from '../api/environments';
import { DEFAULT_RUN_IN, runInExecution, saveLastRunIn, specKind, type RunInSpec } from '../api/runIn';
import { RunInPicker } from '../ui/RunInPicker';
import { draftsFromDefaults, stageAgentsPayload, stageModelsPayload, type StageAgentDrafts } from '../api/agents';
import { StageAgentsField, useAgentsStatus, type StageModelDefaults } from './JobAgents';
import { spacing, touchTarget } from '../theme/tokens';
import { Banner, Button, Field, Pressable, Sheet, Text, TextField } from '../ui';

function lines(value: string): string[] {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
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
 * retries sit under Advanced. Jobs always run unattended (no per-job approval toggle). */
export function NewJobSheet({ visible, sessionName, onClose, onCreated }: NewJobSheetProps) {
  const sessionsState = useJobSessions(visible);
  const touched = useRef<{ cost?: boolean; runtime?: boolean; retries?: boolean; workspace?: boolean }>({});
  const [workspace, setWorkspace] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [acceptance, setAcceptance] = useState('');
  const [validation, setValidation] = useState('');
  const [cost, setCost] = useState('');
  const [costHint, setCostHint] = useState('Settings default ($100)');
  const [runtime, setRuntime] = useState('');
  const [retries, setRetries] = useState('2');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [stageDefaults, setStageDefaults] = useState<StageModelDefaults>({});
  // null = the resolved set (repo config / session / Settings); a list = this job's override.
  const [environments, setEnvironments] = useState<EnvironmentDraft[] | null>(null);
  // "Run in" (default runtime, a template or an environment); Advanced… switches to `environments`.
  const [runIn, setRunIn] = useState<RunInSpec>(DEFAULT_RUN_IN);
  const [rememberRunIn, setRememberRunIn] = useState(false);
  // Agent and model per stage (mucli / Claude Code / Codex), agents prefilled from Settings → Agent harnesses.
  const agents = useAgentsStatus(visible);
  const [agentDrafts, setAgentDrafts] = useState<StageAgentDrafts>(() => draftsFromDefaults(null));
  useEffect(() => { if (visible) setAgentDrafts(draftsFromDefaults(agents.status?.defaults)); }, [visible, agents.status]);

  useEffect(() => {
    if (!visible) return;
    setEnvironments(null);
    setRememberRunIn(false);
    setError('');
  }, [visible]);

  // Preselect the app session's workspace (else the current / most recent one) until the user types.
  useEffect(() => {
    if (visible && !touched.current.workspace) setWorkspace(defaultWorkspace(sessionsState.sessions, sessionName));
  }, [visible, sessionsState.sessions, sessionName]);

  // Prefill limits from Settings → New job defaults unless the user changed them.
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
        // Jobs always run unattended in their sandbox: no per-job auto-approve flag is sent.
        execution: {},
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
      // Run in: a template goes by name (`execution.template`), an environment as one binding.
      else Object.assign(common.execution, runInExecution(runIn, rememberRunIn));
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
      if (!envOverride.length && path) void saveLastRunIn(path, runIn);
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
      setRememberRunIn(false);
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
      <RunInPicker active={visible} workspace={workspace} session={sessionForJob || null} sessionEnvironment={matched?.environment || null}
        value={runIn} onChange={setRunIn} remember={rememberRunIn} onRememberChange={setRememberRunIn}
        advancedSummary={environments ? `${environments.length} environment${environments.length === 1 ? '' : 's'}` : null}
        onAdvanced={() => {
          // The editor starts from the current choice (a template or environment becomes its first row).
          setEnvironments(specKind(runIn) === 'default' ? [emptyDraft()] : [draftFromSpec(runIn)]);
          setShowAdvanced(true);
        }} />
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
          <Text variant="caption" tone="ink3" style={styles.sheetHint}>
            Optional. The planner proposes checks from the repository; commands listed here are added to them.
          </Text>
        </>
      ) : null}
      <Pressable
        onPress={() => setShowAdvanced((v) => !v)}
        accessibilityRole="button"
        accessibilityState={{ expanded: showAdvanced }}
        accessibilityLabel="Advanced"
        testID="job-advanced-toggle"
        style={styles.stageToggle}
      >
        <Text variant="label" tone="accent">
          {showAdvanced ? '▴ Hide advanced' : '▾ Advanced: models, budgets, retries'}
        </Text>
      </Pressable>
      {showAdvanced ? (
        <View style={styles.advanced} testID="job-advanced">
          <StageAgentsField status={agents.status} value={agentDrafts} onChange={setAgentDrafts} stageDefaults={stageDefaults} />
          {environments ? (
            <BuildEnvironmentsField active={visible} session={sessionForJob || null} repository={remote ? undefined : workspace.trim() || undefined}
              value={environments} onChange={setEnvironments} />
          ) : null}
          <View style={styles.sheetGrid}>
            <SheetField label="Cost USD" value={cost} onChangeText={edit('cost', setCost)} placeholder={costHint} keyboardType="decimal-pad" compact />
            <SheetField label="Runtime sec" value={runtime} onChangeText={edit('runtime', setRuntime)} placeholder="no limit" keyboardType="number-pad" compact />
            <SheetField label="Retries" value={retries} onChangeText={edit('retries', setRetries)} placeholder="2" keyboardType="number-pad" compact />
          </View>
          <Text variant="caption" tone="ink3" testID="job-cost-hint" style={styles.sheetHint}>
            {costCapPayload(cost) === 0 ? 'Cost: no cap — only the per-stage budgets apply.' : 'Cost: blank = Settings default · 0 = no cap.'}
          </Text>
        </View>
      ) : null}
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      <View style={styles.sheetActions}>
        <Button variant="secondary" label="Cancel" onPress={onClose} />
        <Button variant="primary" loading={submitting} disabled={submitting} label={ticket ? 'Import ticket' : 'Queue job'} onPress={() => void submit()} testID="job-create-submit" />
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
    <View style={compact ? styles.sheetFieldCompact : undefined}>
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
  sheetFieldCompact: { flex: 1, minWidth: 80 },
  sheetTextarea: { minHeight: 72 },
  sheetGrid: { flexDirection: 'row', gap: spacing.md },
  stageToggle: { minHeight: touchTarget, justifyContent: 'center' },
  sheetHint: { marginTop: -spacing.sm, marginBottom: spacing.sm },
  advanced: { gap: spacing.md },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
