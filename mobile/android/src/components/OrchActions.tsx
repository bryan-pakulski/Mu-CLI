// Orchestration actions: the shared ActionVM flow (mu/orchestration/actions.py)
// on the phone. Availability comes from the server (`enabled` + `reason`;
// disabled commands answer 409 there too). Input prompts, typed
// confirmation (`input.expect`: type the target id exactly) and confirm text
// follow the server's hints; then POST /api/orchestration/cmd.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { orchestrationApi, type OrchAction, type OrchActionInput, type OrchCommandResult } from '../api/orchestration';
import { useTheme } from '../theme/ThemeContext';
import { fonts, spacing, type Tone } from '../theme/tokens';
import { Button, Field, Sheet, Text, TextField, useToast } from '../ui';

export function orchActionTone(a: OrchAction): Tone {
  return (a.tone as Tone) || (a.danger ? 'danger' : '');
}

/** The global kill switch as an action: always confirmed, pause takes an optional reason. */
export function dispatchAction(paused: boolean): OrchAction {
  return paused
    ? { id: 'resumeDispatch', label: 'Resume dispatch', enabled: true, reason: '', primary: true, danger: false, tone: 'ok',
        confirm: 'Resume dispatch? Approved and running runs start sending work to nodes again.' }
    : { id: 'pauseDispatch', label: 'Pause all dispatch', enabled: true, reason: '', primary: false, danger: true, tone: 'danger',
        confirm: 'Pause all dispatch? No new work is sent to any node until you resume. Executions already running on nodes finish.',
        input: { label: 'Reason (optional)', optional: true, multiline: false, arg: 'reason' } };
}

/** Re-run a node's failed step (runs.retry_node): offered on failed node rows of running/paused runs. */
export function retryNodeAction(): OrchAction {
  return { id: 'retryNode', label: 'Retry node', enabled: true, reason: '', primary: false, danger: false, tone: 'warn',
    confirm: 'Retry the failed step on this node?' };
}

/** Args carrying the prompt text: the server's `input.arg`; empty optional text is omitted. */
export function orchInputArgs(spec: OrchActionInput, value: string): Record<string, unknown> {
  const arg = spec.arg || (spec.expect ? 'typed' : 'reason');
  if (spec.optional && !value.trim()) return {};
  return { [arg]: spec.expect ? value.trim() : value };
}

/** True when typed confirmation is required and the value matches it exactly (surrounding spaces ignored). */
export function typedMatches(spec: OrchActionInput | null | undefined, value: string): boolean {
  return !spec?.expect || value.trim() === spec.expect;
}

type Pending =
  | { kind: 'input'; action: OrchAction; spec: OrchActionInput; args: Record<string, unknown> }
  | { kind: 'confirm'; action: OrchAction; text: string; args: Record<string, unknown> };

export type OrchCommandOptions = {
  onDone?: (result: OrchCommandResult, actionId: string) => void | Promise<void>;
};

/** Runs orchestration commands with the Wash flow; returns `invoke(action, args)` and the sheets to render. */
export function useOrchCommands(opts: OrchCommandOptions = {}) {
  const toast = useToast();
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const send = useCallback(async (action: OrchAction, args: Record<string, unknown>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      const result = await orchestrationApi.command(action.id, args);
      setPending(null);
      toast(`${action.label}: done`, 'ok');
      await optsRef.current.onDone?.(result, action.id);
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), 'danger');
    } finally { busyRef.current = false; setBusy(false); }
  }, [toast]);

  const invoke = useCallback((action: OrchAction, args: Record<string, unknown>) => {
    if (!action.enabled) { toast(`${action.label}: ${action.reason || 'not available now'}`, 'warn'); return; }
    if (action.input) { setPending({ kind: 'input', action, spec: action.input, args }); return; }
    if (action.confirm) { setPending({ kind: 'confirm', action, text: action.confirm, args }); return; }
    void send(action, args);
  }, [send, toast]);

  const close = useCallback(() => { if (!busyRef.current) setPending(null); }, []);

  const sheets = (
    <>
      <OrchPromptSheet
        pending={pending?.kind === 'input' ? pending : null}
        busy={busy}
        onClose={close}
        onSubmit={value => {
          if (pending?.kind !== 'input') return;
          const next = { ...pending.args, ...orchInputArgs(pending.spec, value) };
          if (pending.action.confirm) setPending({ kind: 'confirm', action: pending.action, text: pending.action.confirm, args: next });
          else void send(pending.action, next);
        }}
      />
      <OrchConfirmSheet
        pending={pending?.kind === 'confirm' ? pending : null}
        busy={busy}
        onClose={close}
        onConfirm={() => { if (pending?.kind === 'confirm') void send(pending.action, pending.args); }}
      />
    </>
  );
  return { invoke, busy, sheets, pending };
}

function OrchPromptSheet({ pending, busy, onClose, onSubmit }: { pending: Extract<Pending, { kind: 'input' }> | null; busy: boolean; onClose: () => void; onSubmit: (value: string) => void }) {
  const { colors } = useTheme();
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { setValue(''); setError(''); }, [pending]);
  const spec = pending?.spec;
  const action = pending?.action;
  const typed = !!spec?.expect;
  const ready = !!spec && typedMatches(spec, value) && (spec.optional || !!value.trim());
  return (
    <Sheet visible={!!pending} onClose={onClose} title={action ? action.label : ''}>
      {spec && action ? (
        <View style={styles.sheet} testID="orch-action-prompt">
          {typed ? (
            <Text variant="sm" testID="orch-typed-hint">
              This run needs typed confirmation. Type <Text variant="sm" style={{ fontFamily: fonts.mono, color: colors.ink }} selectable>{spec.expect}</Text> to continue.
            </Text>
          ) : null}
          <Field label={spec.label} optional={!!spec.optional && !/optional/i.test(spec.label)} error={error}>
            <TextField
              accessibilityLabel={spec.label}
              value={value}
              onChangeText={t => { setValue(t); setError(''); }}
              multiline={!!spec.multiline}
              autoFocus
              invalid={!!error}
              {...(typed ? { autoCapitalize: 'none' as const, autoCorrect: false } : {})}
              testID="orch-action-prompt-input"
            />
          </Field>
          <View style={styles.actions}>
            <Button label="Cancel" onPress={onClose} />
            <Button
              primary
              tone={orchActionTone(action)}
              loading={busy}
              ariaDisabled={!ready}
              label={action.label}
              testID="orch-action-prompt-submit"
              onPress={() => {
                if (typed && !typedMatches(spec, value)) { setError(`Type ${spec.expect} exactly.`); return; }
                if (!spec.optional && !value.trim()) { setError('Required.'); return; }
                onSubmit(value);
              }}
            />
          </View>
        </View>
      ) : null}
    </Sheet>
  );
}

function OrchConfirmSheet({ pending, busy, onClose, onConfirm }: { pending: Extract<Pending, { kind: 'confirm' }> | null; busy: boolean; onClose: () => void; onConfirm: () => void }) {
  const action = pending?.action;
  return (
    <Sheet visible={!!pending} onClose={onClose} title={action ? action.label : 'Confirm'}>
      {pending && action ? (
        <View style={styles.sheet} testID="orch-action-confirm">
          <Text variant="sm">{pending.text}</Text>
          <View style={styles.actions}>
            <Button label="Cancel" onPress={onClose} />
            <Button primary tone={orchActionTone(action) || 'danger'} loading={busy} label={action.label} onPress={onConfirm} testID="orch-action-confirm-submit" />
          </View>
        </View>
      ) : null}
    </Sheet>
  );
}

/**
 * Action bar for a subject: enabled actions (primary first), then an "N unavailable" expander
 * with the server's reasons. `only` restricts it to the commands the phone offers.
 */
export function OrchActionBar({ actions, onInvoke, busy, only, testID = 'orch-action-bar' }: { actions: OrchAction[]; onInvoke: (a: OrchAction) => void; busy?: boolean; only?: string[]; testID?: string }) {
  const { colors } = useTheme();
  const [showUnavailable, setShowUnavailable] = useState(false);
  const list = actions.filter(a => !a.hidden && (!only || only.includes(a.id)));
  const enabled = [...list.filter(a => a.enabled && a.primary), ...list.filter(a => a.enabled && !a.primary)];
  const disabled = list.filter(a => !a.enabled);
  return (
    <View accessibilityRole="toolbar" accessibilityLabel="Actions" style={styles.bar} testID={testID}>
      <View style={styles.wrap}>
        {enabled.length ? enabled.map(a => (
          <Button key={a.id} small testID={`orch-action-${a.id}`} label={a.label} tone={orchActionTone(a)} primary={a.primary}
            disabled={busy} onPress={() => onInvoke(a)} />
        )) : <Text variant="xs" style={{ color: colors.ink3 }}>Nothing to do right now.</Text>}
        {disabled.length ? (
          <Button ghost small testID={`${testID}-unavailable`}
            label={showUnavailable ? '▴ fewer' : `▾ ${disabled.length} unavailable`}
            accessibilityLabel={showUnavailable ? 'Hide unavailable actions' : `${disabled.length} unavailable actions`}
            onPress={() => setShowUnavailable(v => !v)} />
        ) : null}
      </View>
      {showUnavailable && disabled.length ? (
        <View accessibilityLabel="Unavailable actions" style={styles.unavailable}>
          {disabled.map(a => (
            <View key={a.id} style={styles.unavailableRow}>
              <Button small ariaDisabled label={a.label} tone={orchActionTone(a)} onPress={() => onInvoke(a)} accessibilityHint={a.reason} />
              <Text variant="xs" style={{ color: colors.ink3, flex: 1 }}>{a.reason || 'not available now'}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: { gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  unavailable: { gap: spacing.xs },
  unavailableRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sheet: { gap: spacing.sm },
  actions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm, marginTop: spacing.sm },
});
