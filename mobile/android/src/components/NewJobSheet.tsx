// New job (Work → New job): the source first (a folder, a Settings → Repositories entry or a saved
// session), one "Title or ticket key" field, Run in, attachments, and models, environments, budgets
// and retries under Advanced. Jobs always run unattended. Mirrors the web sheet (work.js).

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
  configuredRepositories,
  sessionModelLabel,
  type ConfiguredRepository,
  type JobExecutionProfile,
  type JobSource,
} from '../api/jobs';
import {
  MAX_JOB_ATTACHMENTS, formatBytes, pickJobFiles, sessionFiles, type JobAttachmentDraft, type SessionFileOption,
} from '../api/jobAttachments';
import { JobSessionPicker, WorkspaceField, useJobSessions, workspaceHint } from './JobSessionPicker';
import { BuildEnvironmentsField, RepositoryEnvironmentApproval } from './JobEnvironments';
import { bindingsFromDrafts, draftError, draftFromSpec, emptyDraft, type EnvironmentDraft } from '../api/environments';
import { DEFAULT_RUN_IN, runInExecution, saveLastRunIn, specKind, type RunInSpec } from '../api/runIn';
import { RunInPicker } from '../ui/RunInPicker';
import { draftsFromDefaults, stageAgentsPayload, stageModelsPayload, type StageAgentDrafts } from '../api/agents';
import { StageAgentsField, useAgentsStatus, type StageModelDefaults } from './JobAgents';
import { spacing, touchTarget } from '../theme/tokens';
import { Banner, Button, CheckRow, Field, IconButton, Pressable, Segmented, Select, Sheet, Text, TextField } from '../ui';

export type SourceKind = 'folder' | 'repository' | 'session';
type ExtraSourceDraft = { key: string; kind: SourceKind; value: string };
const MAX_EXTRA_SOURCES = 8;

/** A row of "Other sources" as the create request's `sources` entry (blank rows are dropped). */
export function extraSourcePayload(rows: ExtraSourceDraft[], repositories: ConfiguredRepository[]): JobSource[] {
  return rows.filter(r => r.value.trim()).map(r => {
    const value = r.value.trim();
    if (r.kind === 'session') return { kind: 'session', session: value };
    if (r.kind === 'repository') return repositories.some(x => x.name === value) ? { kind: 'repository', name: value } : { kind: 'repository', url: value };
    return { kind: 'folder', path: value };
  });
}
const OTHER_REPOSITORY = '\u0000other';
const SOURCE_OPTIONS: Array<{ value: SourceKind; label: string; testID: string }> = [
  { value: 'folder', label: 'Folder', testID: 'job-source-folder' },
  { value: 'repository', label: 'Repository', testID: 'job-source-repository' },
  { value: 'session', label: 'Session', testID: 'job-source-session' },
];

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
  // Source (mu/jobs/sources.py): the folder above, a configured repository (or another URL), a session.
  const [sourceKind, setSourceKind] = useState<SourceKind>('folder');
  const [repositories, setRepositories] = useState<ConfiguredRepository[]>([]);
  const [repoChoice, setRepoChoice] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [sourceSession, setSourceSession] = useState<string | null>(null);
  const [includeConversation, setIncludeConversation] = useState(false);
  // Attachments (mu/jobs/attachments.py): phone files and the session's own files.
  const [attachments, setAttachments] = useState<JobAttachmentDraft[]>([]);
  const [sessionChoices, setSessionChoices] = useState<SessionFileOption[] | null>(null);
  const [attachError, setAttachError] = useState('');
  // Other sources (mu/jobs/multi_source.py): more folders, repositories or sessions the job may change.
  const [extraSources, setExtraSources] = useState<ExtraSourceDraft[]>([]);
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
      const repos = configuredRepositories(settings.rules);
      setRepositories(repos);
      setRepoChoice(current => current || repos[0]?.name || OTHER_REPOSITORY);
      const d = newJobDefaults(settings.rules);
      const t = touched.current;
      setCostHint(costPlaceholder(settings.rules));
      if (!t.cost) setCost(d.cost);
      if (!t.runtime) setRuntime(d.runtime);
      if (!t.retries && d.retries) setRetries(d.retries);
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [visible]);

  const usableSessions = sessionsState.sessions.filter(x => x.workspace);
  const pickedSession = usableSessions.find(x => x.name === sourceSession)
    || usableSessions.find(x => x.name === sessionName) || usableSessions.find(x => x.current) || usableSessions[0];
  const chosenRepository = repositories.find(r => r.name === repoChoice);
  // The workspace this job resolves to: drives Run in, environment approval and the hints.
  const effectiveWorkspace = sourceKind === 'repository' ? (chosenRepository ? chosenRepository.url : repoUrl)
    : sourceKind === 'session' ? pickedSession?.workspace || '' : workspace;
  const remote = isRepositoryUrl(effectiveWorkspace);
  const matched = sourceKind === 'session' ? pickedSession
    : remote ? undefined : sessionForWorkspace(sessionsState.sessions, effectiveWorkspace);
  // Servers without GET /api/jobs/sessions: the app's session supplies the workspace when none is typed.
  const fallbackSession = !sessionsState.supported && sourceKind === 'folder' ? sessionName : null;
  const sessionForJob = matched?.name || fallbackSession || undefined;
  const ticket = isTicketKey(title);
  const edit = (key: 'cost' | 'runtime' | 'retries', set: (v: string) => void) => (v: string) => { touched.current = { ...touched.current, [key]: true }; set(v); };
  const pickWorkspace = (path: string) => { touched.current = { ...touched.current, workspace: true }; setWorkspace(path); };
  const room = MAX_JOB_ATTACHMENTS - attachments.length;
  const addPhoneFiles = async () => {
    setAttachError('');
    try {
      const { added, skipped } = await pickJobFiles(room);
      setAttachments(list => [...list, ...added.filter(a => !list.some(b => b.key === a.key))]);
      if (skipped.length) setAttachError(`Not added (over 25 MB or ${MAX_JOB_ATTACHMENTS} files): ${skipped.join(', ')}`);
    } catch (err) {
      setAttachError(err instanceof Error ? err.message : String(err));
    }
  };
  const toggleSessionFiles = async () => {
    if (sessionChoices) { setSessionChoices(null); return; }
    if (!matched) return;
    setAttachError('');
    try { setSessionChoices(await sessionFiles(matched.name)); }
    catch (err) { setAttachError(`Couldn't list ${matched.name}'s files: ${err instanceof Error ? err.message : String(err)}`); }
  };
  const toggleSessionFile = (f: SessionFileOption, on: boolean) => setAttachments(list => on
    ? (list.some(a => a.key === f.key) || list.length >= MAX_JOB_ATTACHMENTS ? list
      : [...list, { key: f.key, name: f.name, size: f.size, session: matched?.name, input: f.input }])
    : list.filter(a => a.key !== f.key));
  const sourceHint = sourceKind === 'repository'
    ? (chosenRepository ? `${isRepositoryUrl(chosenRepository.url) ? 'Clones' : 'Runs in'} ${chosenRepository.url}${chosenRepository.branch ? `, starting from ${chosenRepository.branch}` : ''}.`
      : repositories.length ? 'Paste a git URL (https or ssh); add #branch for a base branch.' : 'Add repositories in Settings → Repositories, or paste a git URL.')
    : sourceKind === 'session'
      ? (pickedSession ? `Runs in ${pickedSession.workspace} with the model, mode and environment of ${pickedSession.name}${sessionModelLabel(pickedSession) ? ` (${sessionModelLabel(pickedSession)})` : ''}.`
        : 'No saved session has a workspace yet.')
      : workspaceHint(sessionsState.sessions, workspace, fallbackSession);

  const submit = async () => {
    const cleanTitle = title.trim();
    const path = effectiveWorkspace.trim();
    if (!path && !fallbackSession) {
      setError(sourceKind === 'repository' ? 'Choose a repository (add them in Settings → Repositories) or paste a URL.'
        : sourceKind === 'session' ? 'Choose a session with a workspace.' : 'Choose the workspace this job works in.');
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
      // Folder: a git URL is sent as `repository` (the server clones it); a path stays the workspace.
      // Repository / session: a `source` the server resolves (mu/jobs/sources.py).
      const source: JobSource | null = sourceKind === 'repository'
        ? (chosenRepository ? { kind: 'repository', name: chosenRepository.name } : { kind: 'repository', url: path })
        : sourceKind === 'session' && pickedSession ? { kind: 'session', session: pickedSession.name, include_conversation: includeConversation }
        : null;
      if (source) common.source = source;
      else if (path) { if (isRepositoryUrl(path)) common.repository = path; else common.workspace_root = path; }
      if (attachments.length) common.attachments = attachments.map(a => a.input);
      const otherSources = extraSourcePayload(extraSources, repositories);
      if (otherSources.length) common.sources = otherSources;
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
        const res = await jobsApi.importTicket(cleanTitle, source ? undefined : sessionForJob, common);
        const created = res.created?.[0], skipped = res.skipped?.[0], failed = res.errors?.[0];
        if (failed && !created) throw new Error(`${failed.key}: ${failed.error}`);
        jobId = created?.job_id || skipped?.job_id || '';
      } else {
        const input = {
          ...common,
          title: cleanTitle,
          description: description.trim(),
          session_name: source ? undefined : sessionForJob,
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
      setAttachments([]);
      setExtraSources([]);
      setSessionChoices(null);
      setIncludeConversation(false);
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
      <Field label="Source">
        <Segmented options={SOURCE_OPTIONS} value={sourceKind} onChange={v => { setSourceKind(v); setSessionChoices(null); }}
          accessibilityLabel="Source" testID="job-source" />
      </Field>
      {sourceKind === 'folder' ? (
        <WorkspaceField sessions={sessionsState.sessions} value={workspace} onChange={pickWorkspace} hint={sourceHint} error={sessionsState.error} />
      ) : null}
      {sourceKind === 'repository' ? (
        <View style={styles.sourceBlock}>
          <Select label="Repository" testID="job-repository" value={repoChoice} onChange={setRepoChoice}
            options={[...repositories.map(r => ({ value: r.name, label: r.name, description: `${r.url}${r.branch ? ` (${r.branch})` : ''}` })),
              { value: OTHER_REPOSITORY, label: 'Another URL…' }]} />
          {repoChoice === OTHER_REPOSITORY || !chosenRepository ? (
            <TextField mono accessibilityLabel="Repository URL" testID="job-repository-url" value={repoUrl} onChangeText={setRepoUrl}
              placeholder="git@github.com:org/repo.git#main" autoCapitalize="none" autoCorrect={false} />
          ) : null}
          <Text variant="caption" tone="ink3" testID="job-source-hint">{sourceHint}</Text>
        </View>
      ) : null}
      {sourceKind === 'session' ? (
        <View style={styles.sourceBlock}>
          <JobSessionPicker sessions={usableSessions} value={pickedSession?.name || null} onChange={setSourceSession}
            loading={sessionsState.loading} error={sessionsState.error} />
          <CheckRow label="Attach the conversation" checked={includeConversation} onChange={setIncludeConversation} testID="job-session-conversation" />
          <Text variant="caption" tone="ink3" testID="job-source-hint">{sourceHint}</Text>
        </View>
      ) : null}
      <Field label="Other sources" hint="More folders or repositories the job may change, at /sources/<name> in its sandbox. Each gets its own branch and pull request; a folder's change comes back as a patch.">
        {extraSources.map((row, i) => (
          <View key={row.key} style={styles.sourceRow} testID={`job-extra-source-${i}`}>
            <Segmented options={SOURCE_OPTIONS.map(o => ({ ...o, testID: `job-extra-source-${i}-${o.value}` }))} value={row.kind}
              accessibilityLabel={`Kind of source ${i + 1}`}
              onChange={kind => setExtraSources(list => list.map(r => r.key === row.key
                ? { ...r, kind, value: kind === 'session' ? usableSessions[0]?.name || '' : '' } : r))} />
            <View style={styles.sourceValue}>
              <View style={styles.grow}>
              {row.kind === 'session' ? (
                <Select label={`Session for source ${i + 1}`} testID={`job-extra-source-${i}-session`} value={row.value}
                  options={usableSessions.map(x => ({ value: x.name, label: x.name, description: x.workspace }))}
                  onChange={value => setExtraSources(list => list.map(r => r.key === row.key ? { ...r, value } : r))} />
              ) : (
                <TextField mono accessibilityLabel={`${row.kind === 'repository' ? 'Repository' : 'Folder'} for source ${i + 1}`}
                  testID={`job-extra-source-${i}-value`} value={row.value} autoCapitalize="none" autoCorrect={false}
                  placeholder={row.kind === 'repository' ? (repositories[0]?.name ? `${repositories[0].name} — or a git URL` : 'a git URL') : '/home/me/src/other-project'}
                  onChangeText={value => setExtraSources(list => list.map(r => r.key === row.key ? { ...r, value } : r))} />
              )}
              </View>
              <IconButton icon="x" label={`Remove source ${i + 1}`} testID={`job-extra-source-${i}-remove`}
                onPress={() => setExtraSources(list => list.filter(r => r.key !== row.key))} />
            </View>
          </View>
        ))}
        <Button small variant="ghost" icon="plus" label="Add a source" disabled={extraSources.length >= MAX_EXTRA_SOURCES} testID="job-source-add"
          onPress={() => setExtraSources(list => [...list, { key: `${Date.now()}-${list.length}`, kind: 'folder', value: '' }])} />
      </Field>
      {!remote ? <RepositoryEnvironmentApproval repository={effectiveWorkspace.trim() || undefined} session={sessionForJob || null} /> : null}
      <RunInPicker active={visible} workspace={effectiveWorkspace} session={sessionForJob || null} sessionEnvironment={matched?.environment || null}
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
      <Field label="Attachments" hint="Specs, logs, screenshots or data the job needs. Every stage can read them; they are never committed.">
        <View style={styles.attachActions}>
          <Button small variant="secondary" icon="paperclip" label="Add files…" disabled={room <= 0} onPress={() => void addPhoneFiles()} testID="job-attach-files" />
          {matched ? <Button small variant="ghost" label={sessionChoices ? 'Done' : `From ${matched.name}…`} onPress={() => void toggleSessionFiles()} testID="job-attach-session" /> : null}
        </View>
        {sessionChoices ? (
          <View testID="job-session-files">
            {sessionChoices.length ? sessionChoices.map(f => (
              <CheckRow key={f.key} label={`${f.name} · ${[f.kind, formatBytes(f.size)].filter(Boolean).join(' · ')}`}
                checked={attachments.some(a => a.key === f.key)} onChange={on => toggleSessionFile(f, on)} testID={`job-session-file-${f.name}`} />
            )) : <Text variant="caption" tone="ink3">{matched?.name} has no attachments or artifacts.</Text>}
          </View>
        ) : null}
        {attachments.map(a => (
          <View key={a.key} style={styles.attachRow} testID={`job-attachment-${a.name}`}>
            <Text variant="monoSm" style={styles.grow} numberOfLines={1}>{a.name}</Text>
            <Text variant="caption" tone="ink3">{[formatBytes(a.size), a.session ? `from ${a.session}` : ''].filter(Boolean).join(' · ')}</Text>
            <IconButton icon="x" label={`Remove ${a.name}`}
              onPress={() => setAttachments(list => list.filter(b => b.key !== a.key))} testID={`job-attachment-remove-${a.name}`} />
          </View>
        ))}
        {attachError ? <Text variant="caption" tone="danger" testID="job-attach-error">{attachError}</Text> : null}
      </Field>
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
            <BuildEnvironmentsField active={visible} session={sessionForJob || null} repository={remote ? undefined : effectiveWorkspace.trim() || undefined}
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
  sourceBlock: { gap: spacing.sm },
  sourceRow: { gap: spacing.xs, marginBottom: spacing.sm },
  sourceValue: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  attachActions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  attachRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: touchTarget },
  grow: { flex: 1, minWidth: 0 },
  sheetActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.base },
});
