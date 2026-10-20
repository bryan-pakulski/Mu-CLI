import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import {
  firstAttention, promptInitials, setupApi, stepStatus, type SetupAction, type SetupStep, type SetupVM,
} from '../api/setup';
import { DoctorPanel } from '../components/JobDoctorPanel';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { STATUS } from '../theme/status';
import { fontWeights, fonts, spacing, toneInk, touchTarget } from '../theme/tokens';
import { Banner, Button, Dialog, Field, Loading, Meter, PageHeader, Panel, Pre, Pressable, StatusIcon, Text, TextField, useInterval, useServerActions, useToast } from '../ui';

export type JobSetupScreenProps = NativeStackScreenProps<RootStackParamList, 'JobSetup'>;

type Dialog =
  | { kind: 'prompts'; step: SetupStep; action: SetupAction; values: string[] }
  | { kind: 'terminal'; step: SetupStep; action: SetupAction }
  | null;

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Wash-style guided setup: a live checklist; every step re-checks the host, so it's re-runnable. */
export function JobSetupScreen({ navigation, route }: JobSetupScreenProps) {
  const { colors } = useTheme();
  // Doctor is a tab of Setup (one entry point on every surface).
  const [tab, setTab] = useState<'checklist' | 'doctor'>(route?.params?.tab === 'doctor' ? 'doctor' : 'checklist');
  useEffect(() => { if (route?.params?.tab) setTab(route.params.tab); }, [route?.params?.tab]);
  const toast = useToast();
  const [vm, setVm] = useState<SetupVM | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState('');
  const [dialog, setDialog] = useState<Dialog>(null);
  const confirm = useServerActions();

  const load = useCallback(async (recheck = false) => {
    try {
      const next = recheck ? await setupApi.recheck() : await setupApi.get();
      setVm(next);
      setError('');
      return next;
    } catch (err) {
      setError(errorText(err));
      return null;
    }
  }, []);

  useEffect(() => {
    void load().then(next => { if (next) setOpen(o => o ?? firstAttention(next)?.id ?? null); });
  }, [load]);
  // Live log of a running setup job (image build, self-tests): poll while it runs.
  useInterval(() => { void load(); }, 1000, Boolean(vm?.job?.running));

  const run = useCallback(async (step: SetupStep, action: SetupAction, inputs: string[] = []) => {
    setBusy(`${step.id}:${action.id}`);
    try {
      const result = await setupApi.action(step.id, action.id, inputs);
      setVm(result.setup);
      if (result.nav === 'settings') navigation.navigate('JobSettings');
      else if (result.message) toast(result.message, result.ok ? 'ok' : 'warn');
    } catch (err) {
      toast(`${action.label}: ${errorText(err)}`, 'danger');
    } finally {
      setBusy('');
    }
  }, [navigation, toast]);

  const press = useCallback((step: SetupStep, action: SetupAction) => {
    const go = () => {
      if (action.kind === 'nav') { navigation.navigate('JobSettings'); return; }
      if (action.kind === 'prompts') { setDialog({ kind: 'prompts', step, action, values: promptInitials(action) }); return; }
      if (action.kind === 'terminal') { setDialog({ kind: 'terminal', step, action }); return; }
      void run(step, action);
    };
    if (action.confirm) {
      void confirm.ask({ id: action.id, label: action.label, confirm: action.confirm }).then(ok => { if (ok) go(); });
    } else {
      go();
    }
  }, [navigation, run, confirm]);

  const finish = useCallback(async () => {
    try {
      setVm(await setupApi.finish());
      toast('Setup complete', 'ok');
      navigation.goBack();
    } catch (err) {
      toast(errorText(err), 'danger');
    }
  }, [navigation, toast]);

  const header = (
    <PageHeader title="Setup" back={{ label: 'Work', onPress: () => navigation.goBack() }} testID="job-setup-header"
      state={vm ? [vm.can_finish ? { text: 'All set', tone: 'ok' } : { text: `${vm.done} of ${vm.total} done` }, vm.blocking.length ? { text: `to do: ${vm.blocking.join(', ')}`, tone: 'attention' } : null] : undefined}
      tabs={[{ key: 'checklist', label: 'Checklist' }, { key: 'doctor', label: 'Doctor' }]} tab={tab} onTab={key => setTab(key as 'checklist' | 'doctor')} />
  );
  if (tab === 'doctor') {
    return (
      <View style={styles.root}>
        {header}
        <ScrollView contentContainerStyle={styles.content} testID="job-setup-doctor-tab">
          <DoctorPanel onOpenChecklist={() => setTab('checklist')} />
        </ScrollView>
      </View>
    );
  }

  if (!vm) {
    return (
      <View style={styles.root}>
        {header}
        {error ? <View style={styles.content}><Banner tone="danger" glyph="✗" title="Could not load setup" role="alert"><Text variant="xs">{error}</Text></Banner>
          <Button label="Try again" onPress={() => void load()} /></View> : <Loading label="Checking your machine…" />}
      </View>
    );
  }

  const job = vm.job;
  return (
    <View style={styles.root}>
      {header}
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" testID="job-setup">
        <View style={styles.progress}>
          <View style={{ flex: 1 }}><Meter value={vm.done} max={vm.total || 1} tone="ok" accessibilityLabel={`${vm.done} of ${vm.total} done`} /></View>
          <Text variant="xs" tone="ink3" testID="job-setup-progress">{vm.done}/{vm.total} done</Text>
        </View>
        <Text variant="xs" tone="ink3">Each step checks the host live; re-run any time.</Text>
        {vm.can_finish
          ? <Banner tone="ok" glyph="✓" title="All set" right={<Button small primary tone="ok" label="Finish" onPress={() => void finish()} testID="job-setup-finish" />} />
          : <Text variant="sm" style={{ color: vm.blocking.length ? colors.attention.ink : colors.ink3 }} testID="job-setup-blocking">
              {vm.blocking.length ? `To do: ${vm.blocking.join(', ')}` : 'Checking…'}
            </Text>}
        <View style={styles.row}>
          <Button small label="Check all again" onPress={() => void load(true)} testID="job-setup-recheck" />
        </View>

        {job && (job.running || job.lines.length) ? (
          <Panel title={job.title} badge={job.running ? 'running' : job.ok ? 'passed' : 'failed'} badgeTone={job.running ? 'running' : job.ok ? 'ok' : 'danger'} testID="job-setup-log">
            <Pre testID="job-setup-log-lines">{job.lines.slice(-40).join('\n') || '…'}</Pre>
          </Panel>
        ) : null}

        <View style={[styles.list, { borderColor: colors.hairline }]}>
          {vm.steps.map(step => {
            const st = STATUS[stepStatus(step)];
            const expanded = open === step.id;
            return (
              <View key={step.id} style={[styles.step, { borderBottomColor: colors.hairline }]}>
                <Pressable accessibilityRole="button" accessibilityState={{ expanded }} testID={`setup-step-${step.id}`}
                  accessibilityLabel={`${step.title}, ${step.checking ? 'checking' : st.label}`}
                  onPress={() => setOpen(expanded ? null : step.id)}
                  style={({ pressed }) => [styles.stepHead, { backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
                  <StatusIcon status={stepStatus(step)} label={step.checking ? 'checking' : st.label} />
                  <View style={{ flex: 1 }}>
                    <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{step.title}{step.optional ? <Text variant="xs" tone="ink3"> (optional)</Text> : null}</Text>
                    <Text variant="xs" style={{ color: step.checking ? colors.ink3 : toneInk(colors, st.tone) }} numberOfLines={expanded ? undefined : 2}>{step.detail}</Text>
                  </View>
                  <Text variant="xs" tone="ink3">{expanded ? '▾' : '▸'}</Text>
                </Pressable>
                {expanded ? (
                  <View style={styles.stepBody} testID={`setup-step-${step.id}-body`}>
                    <Text variant="xs" tone="ink3">{step.why}</Text>
                    {step.actions.map(action => (
                      <View key={action.id} style={styles.action}>
                        <Button small label={`${action.label}${action.kind === 'terminal' ? '  (terminal)' : ''}`}
                          primary={step.state === 'todo' && action === step.actions[0]}
                          loading={busy === `${step.id}:${action.id}`}
                          disabled={Boolean(busy) || (action.kind === 'job' && Boolean(job?.running))}
                          onPress={() => press(step, action)} testID={`setup-action-${step.id}-${action.id}`} />
                        {action.hint ? <Text variant="xs" tone="ink3">{action.hint}</Text> : null}
                      </View>
                    ))}
                  </View>
                ) : null}
              </View>
            );
          })}
        </View>
      </ScrollView>

      <Dialog visible={dialog?.kind === 'prompts'} onClose={() => setDialog(null)} title={dialog?.action.label || ''} testID="setup-prompts"
        footer={<>
          <Button variant="secondary" label="Cancel" onPress={() => setDialog(null)} />
          <Button variant="primary" label="Save" testID="setup-prompt-save" onPress={() => {
            if (dialog?.kind !== 'prompts') return;
            const { step, action, values } = dialog;
            setDialog(null);
            void run(step, action, values);
          }} />
        </>}>
        {dialog?.kind === 'prompts' ? (
          <View style={styles.dialog}>
            {dialog.action.hint ? <Text variant="caption" tone="ink3">{dialog.action.hint}</Text> : null}
            {(dialog.action.prompts || []).map((prompt, i) => (
              <Field key={prompt.label} label={prompt.label}>
                <TextField value={dialog.values[i]} secureTextEntry={Boolean(prompt.secret)} autoCapitalize="none" autoCorrect={false}
                  accessibilityLabel={prompt.label} testID={`setup-prompt-${i}`}
                  onChangeText={text => setDialog(d => (d && d.kind === 'prompts' ? { ...d, values: d.values.map((v, j) => (j === i ? text : v)) } : d))} />
              </Field>
            ))}
          </View>
        ) : null}
      </Dialog>
      <Dialog visible={dialog?.kind === 'terminal'} onClose={() => setDialog(null)} title={dialog?.action.label || ''} testID="setup-terminal"
        footer={<>
          <Button variant="secondary" label="Copy" icon="copy" testID="setup-terminal-copy" onPress={() => {
            void Clipboard.setStringAsync(dialog?.action.command || '');
            toast('Command copied', 'ok');
          }} />
          <Button variant="primary" label="I ran it, check again" testID="setup-terminal-done" onPress={() => {
            if (dialog?.kind !== 'terminal') return;
            const { step, action } = dialog;
            setDialog(null);
            void run(step, action);
          }} />
        </>}>
        {dialog?.kind === 'terminal' ? (
          <View style={styles.dialog}>
            <Text variant="caption" tone="ink3">Run this in a terminal on the mucli host{dialog.action.hint ? `: ${dialog.action.hint}` : ''}.</Text>
            <Pre testID="setup-terminal-command">{dialog.action.command || ''}</Pre>
          </View>
        ) : null}
      </Dialog>
      {confirm.element}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  progress: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  list: { borderWidth: 1, borderRadius: 8, overflow: 'hidden' },
  step: { borderBottomWidth: 1 },
  stepHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: touchTarget },
  stepBody: { paddingHorizontal: spacing.md, paddingBottom: spacing.md, gap: spacing.sm },
  action: { gap: 2, alignItems: 'flex-start' },
  dialog: { gap: spacing.sm },
});
