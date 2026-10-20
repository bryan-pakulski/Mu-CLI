// Job actions, shared by the Now board, the jobs list and the job page. Every button comes from
// `job.actions` (availability is decided once, on the server; disabled commands are re-checked
// there and answer 409). Confirm text and input prompts go through the kit's server-action dialog
// (src/ui/Mu.tsx useServerActions), the push dialog is its own sheet; tone and hidden actions
// follow CONTRACT §7, with defaults when an older server omits them.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { jobCoreApi, type CommandResult, type EnvironmentApproval, type JobAction, type JobLimit, type JobPart, type PushArgs } from '../api/jobCore';
import { JobPushDialog } from './JobPushDialog';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing } from '../theme/tokens';
import { ACTION_LABEL, ACTION_TONE } from '../theme/status';
import { ActionBar, Banner, Button, Field, Pre, Text, TextField, type ServerAction, type Tone, useServerActions, useToast } from '../ui';

/** Actions kept out of action bars (CONTRACT §4/§7) and the ones the approval banner owns. */
export const BAR_HIDDEN = new Set(['markFindings', 'markViewed', 'approveTool', 'denyTool',
  // Repository image/devcontainer approval lives in the environment approval banner.
  'approveEnvironment', 'rejectEnvironment',
  // Build environments live in the job's environments sheet (header pill).
  'setEnvironments', 'refreshEnvironments', 'keepEnvironments',
  // Environment repair actions (job_templates.md §7) are offered on the web job page's repair card.
  'addRepairToTemplate', 'saveRepairedTemplate']);

export function actionTone(a: JobAction): Tone {
  if (a.tone) return a.tone;
  return ACTION_TONE[a.id] || (a.danger ? 'danger' : '');
}

export function actionLabel(a: JobAction): string {
  return ACTION_LABEL[a.id] || a.label;
}

/** Default confirm wording when the server sends none. */
function confirmText(a: JobAction): string | null {
  if (a.confirm) return a.confirm;
  if (a.id === 'delete' || a.id === 'discard') return 'Running work is cancelled and the worktree is removed. The Git branch is preserved.';
  if (a.id === 'restartStage') return 'Restart the current stage? Its in-flight work is discarded.';
  return null;
}

export type InputSpec = { label: string; optional: boolean; multiline: boolean; arg?: string; value?: string; hint?: string; type?: string };
/** Input prompt for an action: the server's `input` (generic arg/value/hint/type), else the CONTRACT §7 defaults. */
export function inputSpec(a: JobAction): InputSpec | null {
  if (a.input) {
    const amount = a.input.type === 'amount';
    return {
      label: a.input.label, optional: !!a.input.optional,
      multiline: amount ? false : a.input.multiline !== false,
      arg: a.input.arg || undefined, value: a.input.value ?? undefined, hint: a.input.hint || undefined, type: a.input.type || undefined,
    };
  }
  switch (a.id) {
    case 'rejectPlan': return { label: 'What should the re-plan change?', optional: false, multiline: true };
    case 'denyTool': return { label: 'Reason for the agent (optional)', optional: true, multiline: true };
    case 'resume': return { label: 'Guidance for the resumed stage', optional: true, multiline: true };
    case 'reprompt': return { label: 'Extra instructions for the fix', optional: true, multiline: true };
    case 'message': return { label: 'Message the agent', optional: false, multiline: true };
    case 'rejectEnvironment': return { label: 'Reason (optional)', optional: true, multiline: false };
    default: return null;
  }
}

/**
 * Args carrying the prompt text. A server-sent `input.arg` wins (empty optional
 * text is omitted); otherwise the per-command fallback for older servers.
 */
export function inputArgs(id: string, value: string, spec?: InputSpec | null): Record<string, unknown> {
  if (spec?.arg) return spec.optional && !value.trim() ? {} : { [spec.arg]: value };
  switch (id) {
    case 'rejectPlan': return { feedback: value };
    case 'denyTool': return value.trim() ? { message: value } : {};
    case 'message': return { text: value };
    case 'push': return { override_reason: value };
    case 'rejectEnvironment': return value.trim() ? { reason: value } : {};
    default: return { guidance: value };
  }
}

/** A job action in the kit's server-action shape (confirm / input / danger / reason). */
export function toServerAction(a: JobAction, overrides: Partial<ServerAction> = {}): ServerAction {
  const spec = inputSpec(a);
  return {
    id: a.id, label: actionLabel(a), enabled: a.enabled, primary: a.primary, hidden: a.hidden, reason: a.reason,
    danger: actionTone(a) === 'danger', confirm: confirmText(a) || undefined, args: a.args,
    input: spec ? {
      arg: spec.arg || Object.keys(inputArgs(a.id, 'x', spec))[0] || 'guidance',
      label: spec.label, optional: spec.optional, multiline: spec.multiline, value: spec.value, hint: spec.hint,
    } : undefined,
    ...overrides,
  };
}

export type JobCommandOptions = {
  jobId: string;
  actions: JobAction[];
  /** Plan editor has unsaved edits (blocks approvePlan). */
  planDirty?: boolean;
  /** Version to approve (latest plan version). */
  planVersion?: number;
  /** Findings selected on the Review/Diff tabs (reprompt). */
  selectedFindings?: string[];
  /** Called for savePlanDraft ("Edit plan"): open the plan editor instead of sending. */
  onEditPlan?: () => void;
  /** Called when reprompt has no selected findings. */
  onNeedFindings?: () => void;
  /** Called for setEnvironments without bindings (dialog "environments"): open the editor. */
  onEditEnvironments?: () => void;
  onDone?: (result: CommandResult, actionId: string) => void | Promise<void>;
  onError?: (message: string) => void;
};

/**
 * Runs job commands: disabled → toast with the reason; confirm / input → the kit's server-action
 * dialog (one dialog for both); push → the push dialog; then POST /api/jobs/cmd. Returns `invoke`
 * plus the dialogs to render.
 */
export function useJobCommands(opts: JobCommandOptions) {
  const toast = useToast();
  const dialog = useServerActions();
  const [pushing, setPushing] = useState<JobAction | null>(null);
  const [busy, setBusy] = useState(false);
  /** Last failed command (e.g. a 400 for an unparseable limit amount) so banners can show it inline. */
  const [lastError, setLastError] = useState<{ actionId: string; message: string } | null>(null);
  const busyRef = useRef(false);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const send = useCallback(async (action: JobAction, args: Record<string, unknown>) => {
    if (busyRef.current) return;
    const o = optsRef.current;
    busyRef.current = true; setBusy(true); setLastError(null);
    try {
      const result = await jobCoreApi.command(o.jobId, action.id, { ...(action.args || {}), ...args });
      setPushing(null);
      toast(`${actionLabel(action)}: done`, 'ok');
      await o.onDone?.(result, action.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setLastError({ actionId: action.id, message });
      toast(message, 'danger');
      o.onError?.(message);
    } finally { busyRef.current = false; setBusy(false); }
  }, [toast]);

  /** Start an action by id (or object), with optional pre-filled args. */
  const invoke = useCallback((idOrAction: string | JobAction, extra: Record<string, unknown> = {}) => {
    const o = optsRef.current;
    const action = typeof idOrAction === 'string' ? o.actions.find(a => a.id === idOrAction) : idOrAction;
    if (!action) return;
    if (!action.enabled) {
      const msg = `${actionLabel(action)}: ${action.reason || 'not available now'}`;
      toast(msg, 'warn'); o.onError?.(msg);
      return;
    }
    let args: Record<string, unknown> = { ...extra };
    if (action.id === 'savePlanDraft' && o.onEditPlan && !('plan' in extra)) { o.onEditPlan(); return; }
    if (action.id === 'approvePlan') {
      if (o.planDirty) { const msg = 'Save a plan version before approving.'; toast(msg, 'warn'); o.onError?.(msg); return; }
      if (o.planVersion != null) args.version = o.planVersion;
    }
    if (action.id === 'reprompt' && !('finding_ids' in extra)) {
      if (!o.selectedFindings?.length) { const msg = 'Select findings for the implementer.'; toast(msg, 'warn'); o.onError?.(msg); o.onNeedFindings?.(); return; }
      args = { ...args, finding_ids: o.selectedFindings };
    }
    if (action.dialog === 'push' || action.id === 'push') { setPushing(action); return; }
    // Never send setEnvironments without bindings: an empty list would clear the job's override.
    if ((action.dialog === 'environments' || action.id === 'setEnvironments') && !('environments' in extra)) { o.onEditEnvironments?.(); return; }
    const server = toServerAction(action);
    // Args the caller already supplied (e.g. the limit banner's amount) skip the prompt.
    const prefilled = server.input && server.input.arg in extra;
    const ask = prefilled ? { ...server, input: undefined } : server;
    if (!ask.confirm && !ask.input) { void send(action, args); return; }
    void dialog.ask(ask).then(result => {
      if (!result) return;
      // The dialog echoes the action's args; keep only what was typed (which may override one).
      const typed: Record<string, unknown> = { ...result };
      for (const key of Object.keys(action.args || {})) if (key !== ask.input?.arg) delete typed[key];
      void send(action, { ...args, ...typed });
    });
  }, [send, toast, dialog]);

  const jobId = opts.jobId;
  const sheets = (
    <>
      {dialog.element}
      <JobPushDialog
        visible={!!pushing}
        jobId={jobId}
        action={pushing}
        busy={busy}
        onClose={() => { if (!busyRef.current) setPushing(null); }}
        onSubmit={(args: PushArgs) => { if (pushing) void send(pushing, args as Record<string, unknown>); }}
      />
    </>
  );
  return { invoke, busy, sheets, pushing, lastError };
}

/**
 * The job's action bar (kit ActionBar): enabled primary actions as buttons, everything else under
 * More, disabled ones with their reason. Banner-owned and hidden actions stay out.
 */
export function JobActionBar({ actions, onInvoke, exclude = [], planDirty, testID = 'job-action' }: { actions: JobAction[]; onInvoke: (a: JobAction) => void; busy?: boolean; exclude?: string[]; planDirty?: boolean; testID?: string }) {
  const list = actions.filter(a => !a.hidden && !BAR_HIDDEN.has(a.id) && !exclude.includes(a.id));
  const server = list.map(a => toServerAction(a, a.id === 'approvePlan' && planDirty ? { enabled: false, reason: 'Save or cancel your plan edits first' } : {}));
  if (!list.length) return null;
  return (
    <View accessibilityLabel="Job actions" testID="job-action-bar">
      <ActionBar testID={testID} actions={server} onAction={s => { const a = list.find(x => x.id === s.id); if (a) onInvoke(a); }} />
    </View>
  );
}

/** Tool approval banner (◆ warn): tool name, args, Approve + Deny (deny asks for an optional reason). */
export function ApprovalBanner({ approval, actions, onInvoke, busy }: { approval: { tool_name: string; tool_args: unknown; reason?: string; detail?: string } | null | undefined; actions: JobAction[]; onInvoke: (a: JobAction) => void; busy?: boolean }) {
  const { colors } = useTheme();
  const approve = actions.find(a => a.id === 'approveTool');
  const deny = actions.find(a => a.id === 'denyTool');
  if (!approval || (!approve?.enabled && !deny?.enabled)) return null;
  const args = typeof approval.tool_args === 'string' ? approval.tool_args : JSON.stringify(approval.tool_args || {}, null, 2);
  return (
    <Banner tone="warn" glyph="◆" role="alert" testID="job-approval-banner" title={<Text variant="sm" style={{ flex: 1, fontWeight: fontWeights.semibold }}>The agent asks to use <Text variant="sm" style={{ fontFamily: fonts.mono, color: colors.ink }}>{approval.tool_name || 'a tool'}</Text></Text>}>
      <Pre numberOfLines={12}>{args}</Pre>
      {approval.reason || approval.detail ? <Text variant="xs" tone="ink3">{approval.reason || approval.detail}</Text> : null}
      <View style={styles.wrap}>
        {approve ? <Button primary={approve.enabled} tone="warn" disabled={busy || !approve.enabled} label={actionLabel(approve)} onPress={() => onInvoke(approve)} testID="job-approve-tool" /> : null}
        {deny ? <Button tone="danger" disabled={busy || !deny.enabled} label={actionLabel(deny)} onPress={() => onInvoke(deny)} testID="job-deny-tool" /> : null}
        {approve && !approve.enabled && approve.reason ? <Text variant="xs" tone="ink3">{approve.reason}</Text> : null}
      </View>
    </Banner>
  );
}

/** What a repository-declared environment would build/use, in plain words. */
export function environmentApprovalLines(approval: EnvironmentApproval): Array<[string, string]> {
  const rows: Array<[string, string]> = [
    ['What', approval.label || approval.ref],
    ['Kind', approval.kind === 'build' ? 'Dockerfile build' : approval.kind === 'image' ? 'Container image' : approval.kind || '—'],
    ['Ref', approval.ref || '—'],
    ['Fingerprint', approval.fingerprint],
  ];
  if (approval.ignored_features?.length) rows.push(['Ignored features', approval.ignored_features.join(', ')]);
  return rows;
}

/** Environment approval banner (◆ warn): repository image/Dockerfile + Approve / Reject (explicit human action). */
export function EnvironmentApprovalBanner({ approval, actions, onInvoke, busy }: { approval: EnvironmentApproval | null | undefined; actions: JobAction[]; onInvoke: (a: JobAction) => void; busy?: boolean }) {
  const { colors } = useTheme();
  const approve = actions.find(a => a.id === 'approveEnvironment');
  const reject = actions.find(a => a.id === 'rejectEnvironment');
  if (!approval || (!approve?.enabled && !reject?.enabled)) return null;
  return (
    <Banner tone="warn" glyph="◆" role="alert" testID="job-environment-approval" title={<Text variant="sm" style={{ flex: 1, fontWeight: fontWeights.semibold }}>{approval.kind === 'egress' ? 'The repository asks for network egress (stages run without it until approved)' : `The repository asks to ${approval.kind === 'build' ? 'build and use' : 'use'} its own build environment`}</Text>}>
      {environmentApprovalLines(approval).map(([k, v]) => (
        <Text key={k} variant="xs"><Text variant="xs" tone="ink3">{k}: </Text><Text variant="xs" style={{ fontFamily: fonts.mono }} selectable>{v}</Text></Text>
      ))}
      {approval.kind === 'build' ? <Text variant="xs" tone="attention">Building runs the Dockerfile's RUN steps on your container engine with network access; review it first.</Text> : null}
      {approval.message ? <Text variant="xs" tone="ink3">{approval.message}</Text> : null}
      <View style={styles.wrap}>
        {approve ? <Button primary={approve.enabled} tone="warn" disabled={busy || !approve.enabled} label={actionLabel(approve)} onPress={() => onInvoke(approve)} testID="job-approve-environment" /> : null}
        {reject ? <Button tone="danger" disabled={busy || !reject.enabled} label={actionLabel(reject)} onPress={() => onInvoke(reject)} testID="job-reject-environment" /> : null}
      </View>
    </Banner>
  );
}

/**
 * Limit banner (◆ warn): the job paused on a cost/tokens/time/iterations cap.
 * Amount field prefilled from the raiseLimit action's input; "Approve more &
 * continue" sends raiseLimit {kind, scope, stage, amount}; "Cancel job" is the
 * discard action (with its confirm). Server 400 details show inline.
 */
export function LimitBanner({ limit, actions, onInvoke, busy, error }: { limit: JobLimit | null | undefined; actions: JobAction[]; onInvoke: (a: JobAction, extra?: Record<string, unknown>) => void; busy?: boolean; error?: string | null }) {
  const { colors } = useTheme();
  const raise = actions.find(a => a.id === 'raiseLimit');
  const cancel = actions.find(a => a.id === 'discard');
  const spec = raise ? inputSpec(raise) : null;
  const prefill = spec?.value ?? limit?.suggested_text ?? '';
  const [amount, setAmount] = useState(prefill);
  const [invalid, setInvalid] = useState('');
  const key = `${limit?.kind}:${limit?.scope}:${limit?.stage}:${limit?.limit}:${prefill}`;
  useEffect(() => { setAmount(prefill); setInvalid(''); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!limit) return null;
  const label = spec?.label || 'Additional amount';
  const arg = spec?.arg || 'amount';
  const shown = invalid || error || '';
  return (
    <Banner tone="warn" glyph="◆" role="alert" testID="job-limit-banner" title={<Text variant="sm" style={{ flex: 1, fontWeight: fontWeights.semibold }}>{limit.message || limit.summary}</Text>}>
      {limit.summary && limit.message ? <Text variant="xs" tone="ink3">{limit.summary}</Text> : null}
      {raise ? (
        <Field label={label} hint={spec?.hint} error={shown}>
          <TextField accessibilityLabel={label} value={amount} onChangeText={t => { setAmount(t); setInvalid(''); }} autoCapitalize="none" autoCorrect={false}
            keyboardType="default" invalid={!!shown} editable={raise.enabled} testID="job-limit-amount" />
        </Field>
      ) : null}
      <View style={styles.wrap}>
        {raise ? (
          <Button primary={raise.enabled} tone="ok" loading={busy} disabled={busy || !raise.enabled} label={actionLabel(raise)} testID="job-limit-raise"
            onPress={() => {
              if (!amount.trim()) { setInvalid('Enter an amount, e.g. 25, 30m or 5M.'); return; }
              onInvoke(raise, { ...(raise.args || {}), [arg]: amount.trim() });
            }} />
        ) : null}
        {cancel ? <Button tone="danger" disabled={busy || !cancel.enabled} label={actionLabel(cancel)} onPress={() => onInvoke(cancel)} testID="job-limit-cancel" /> : null}
        {raise && !raise.enabled && raise.reason ? <Text variant="xs" tone="ink3">{raise.reason}</Text> : null}
      </View>
    </Banner>
  );
}

export type { JobPart };

const styles = StyleSheet.create({
  bar: { gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
});
