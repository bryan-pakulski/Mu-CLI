import React, { useCallback, useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as Clipboard from 'expo-clipboard';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import {
  firstAttention, promptInitials, setupApi, stepStatus, type SetupAction, type SetupStep, type SetupVM,
} from '../api/setup';
import { DoctorPanel } from './JobDoctorScreen';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { STATUS } from '../theme/status';
import { fontWeights, fonts, spacing, toneInk, touchTarget } from '../theme/tokens';
import { Banner, Button, Field, Loading, Meter, ModalFrame, Panel, Pre, Pressable, StatusIcon, Tabs, Text, TextField, useInterval, useToast } from '../ui';

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
      Alert.alert(action.label, action.confirm, [{ text: 'Cancel', style: 'cancel' }, { text: 'Continue', onPress: go }]);
    } else {
      go();
    }
  }, [navigation, run]);

  const finish = useCallback(async () => {
    try {
      setVm(await setupApi.finish());
      toast('Setup complete', 'ok');
      navigation.goBack();
    } catch (err) {
      toast(errorText(err), 'danger');
    }
  }, [navigation, toast]);

  const tabs = (
    <Tabs accessibilityLabel="Setup" value={tab} onChange={id => setTab(id as 'checklist' | 'doctor')}
      items={[{ id: 'checklist', label: 'Checklist', testID: 'job-setup-tab-checklist' }, { id: 'doctor', label: 'Doctor', testID: 'job-setup-tab-doctor' }]} />
  );
  if (tab === 'doctor') {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
        <ScrollView contentContainerStyle={styles.content} testID="job-setup-doctor-tab">
          <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Setup</Text>
          {tabs}
          <DoctorPanel onOpenChecklist={() => setTab('checklist')} />
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (!vm) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
        {error ? <View style={styles.content}><Banner tone="danger" glyph="✗" title="Could not load setup" role="alert"><Text variant="xs">{error}</Text></Banner>
          <Button label="Try again" onPress={() => void load()} /></View> : <Loading label="Checking your machine…" />}
      </SafeAreaView>
    );
  }

  const job = vm.job;
  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" testID="job-setup">
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Setup</Text>
        {tabs}
        <View style={styles.progress}>
          <View style={{ flex: 1 }}><Meter value={vm.done} max={vm.total || 1} tone="ok" accessibilityLabel={`${vm.done} of ${vm.total} done`} /></View>
          <Text variant="xs" style={{ color: colors.ink3 }} testID="job-setup-progress">{vm.done}/{vm.total} done</Text>
        </View>
        <Text variant="xs" style={{ color: colors.ink3 }}>Each step checks the host live; re-run any time.</Text>
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
                    <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>{step.title}{step.optional ? <Text variant="xs" style={{ color: colors.ink3 }}> (optional)</Text> : null}</Text>
                    <Text variant="xs" style={{ color: step.checking ? colors.ink3 : toneInk(colors, st.tone) }} numberOfLines={expanded ? undefined : 2}>{step.detail}</Text>
                  </View>
                  <Text variant="xs" style={{ color: colors.ink3 }}>{expanded ? '▾' : '▸'}</Text>
                </Pressable>
                {expanded ? (
                  <View style={styles.stepBody} testID={`setup-step-${step.id}-body`}>
                    <Text variant="xs" style={{ color: colors.ink3 }}>{step.why}</Text>
                    {step.actions.map(action => (
                      <View key={action.id} style={styles.action}>
                        <Button small label={`${action.label}${action.kind === 'terminal' ? '  (terminal)' : ''}`}
                          primary={step.state === 'todo' && action === step.actions[0]}
                          loading={busy === `${step.id}:${action.id}`}
                          disabled={Boolean(busy) || (action.kind === 'job' && Boolean(job?.running))}
                          onPress={() => press(step, action)} testID={`setup-action-${step.id}-${action.id}`} />
                        {action.hint ? <Text variant="xs" style={{ color: colors.ink3 }}>{action.hint}</Text> : null}
                      </View>
                    ))}
                  </View>
                ) : null}
              </View>
            );
          })}
        </View>
      </ScrollView>

      <ModalFrame statusBarTranslucent={false} visible={dialog !== null} transparent animationType="fade" onRequestClose={() => setDialog(null)}>
        <View style={[styles.backdrop, { backgroundColor: colors.scrim }]}>
          <View style={[styles.sheet, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
            {dialog?.kind === 'prompts' ? (
              <>
                <Text variant="md" style={{ fontWeight: fontWeights.semibold }}>{dialog.action.label}</Text>
                {dialog.action.hint ? <Text variant="xs" style={{ color: colors.ink3 }}>{dialog.action.hint}</Text> : null}
                {(dialog.action.prompts || []).map((prompt, i) => (
                  <Field key={prompt.label} label={prompt.label}>
                    <TextField value={dialog.values[i]} secureTextEntry={Boolean(prompt.secret)} autoCapitalize="none" autoCorrect={false}
                      accessibilityLabel={prompt.label} testID={`setup-prompt-${i}`}
                      onChangeText={text => setDialog(d => (d && d.kind === 'prompts' ? { ...d, values: d.values.map((v, j) => (j === i ? text : v)) } : d))} />
                  </Field>
                ))}
                <View style={styles.row}>
                  <Button label="Cancel" onPress={() => setDialog(null)} />
                  <Button primary label="Save" testID="setup-prompt-save" onPress={() => {
                    const { step, action, values } = dialog;
                    setDialog(null);
                    void run(step, action, values);
                  }} />
                </View>
              </>
            ) : dialog?.kind === 'terminal' ? (
              <>
                <Text variant="md" style={{ fontWeight: fontWeights.semibold }}>{dialog.action.label}</Text>
                <Text variant="xs" style={{ color: colors.ink3 }}>Run this in a terminal on the mucli host{dialog.action.hint ? ` — ${dialog.action.hint}` : ''}.</Text>
                <Pre testID="setup-terminal-command" style={{ fontFamily: fonts.mono }}>{dialog.action.command || ''}</Pre>
                <View style={styles.row}>
                  <Button label="Copy" testID="setup-terminal-copy" onPress={() => {
                    void Clipboard.setStringAsync(dialog.action.command || '');
                    toast('Command copied', 'ok');
                  }} />
                  <Button label="Close" onPress={() => setDialog(null)} />
                  <Button primary label="I ran it — check again" testID="setup-terminal-done" onPress={() => {
                    const { step, action } = dialog;
                    setDialog(null);
                    void run(step, action);
                  }} />
                </View>
              </>
            ) : null}
          </View>
        </View>
      </ModalFrame>
    </SafeAreaView>
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
  backdrop: { flex: 1, justifyContent: 'center', padding: spacing.base },
  sheet: { borderWidth: 1, borderRadius: 12, padding: spacing.base, gap: spacing.md },
});
