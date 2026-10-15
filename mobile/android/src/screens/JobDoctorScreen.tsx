import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { doctorStatus, doctorSummary, setupApi, type DoctorReport } from '../api/setup';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing, touchTarget } from '../theme/tokens';
import { Banner, Button, Loading, Panel, StatusIcon, Text } from '../ui';
import { STATUS } from '../theme/status';

export type JobDoctorScreenProps = NativeStackScreenProps<RootStackParamList, 'JobDoctor'>;

/** Wash Doctor: everything job runs depend on, pass / warn / fail; deep adds the sandbox self-test.
 * Rendered as the Doctor tab of Setup; `onOpenChecklist` switches to the Checklist tab. */
export function DoctorPanel({ onOpenChecklist }: { onOpenChecklist: () => void }) {
  const { colors } = useTheme();
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [running, setRunning] = useState<'' | 'quick' | 'deep'>('');
  const [error, setError] = useState('');

  const run = useCallback(async (deep = false) => {
    setRunning(deep ? 'deep' : 'quick');
    try {
      setReport(await setupApi.doctor(deep));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning('');
    }
  }, []);
  useEffect(() => { void run(); }, [run]);

  const tone = report ? (report.failed ? 'danger' : report.warnings ? 'warn' : 'ok') : '';
  return (
    <View style={styles.panel} testID="job-doctor">
      <Text variant="xs" style={{ color: colors.ink3 }}>Checks Docker, the runtime image, models, Claude Code / Codex, the sandbox, Jira and more.</Text>
      <View style={styles.row}>
        <Button small label="Run again" loading={running === 'quick'} disabled={Boolean(running)} onPress={() => void run(false)} testID="job-doctor-rerun" />
        <Button small label="Deep check (sandbox self-test)" loading={running === 'deep'} disabled={Boolean(running)}
          accessibilityHint="Starts real stage containers; takes 10 to 20 seconds" onPress={() => void run(true)} testID="job-doctor-deep" />
      </View>
      {running === 'deep' ? <Text variant="xs" style={{ color: colors.ink3 }}>Starting real stage containers for every ready agent…</Text> : null}
      {error ? <Banner tone="danger" glyph="✗" title="Doctor failed to run" role="alert"><Text variant="xs">{error}</Text></Banner> : null}
      {report && (report.failed || report.needs_setup) ? (
        <Banner tone={report.failed ? 'danger' : 'warn'} glyph={report.failed ? STATUS.failed.icon : STATUS.waiting.icon} testID="job-doctor-setup-cta"
          title={report.failed ? 'Some checks fail: the Checklist offers the actions that fix them' : 'Setup has not been finished yet'}
          right={<Button small primary label="Open checklist" onPress={onOpenChecklist} testID="job-doctor-open-setup" />} />
      ) : null}
      {!report && !error ? <Loading label="Running checks…" /> : null}
      {report ? (
        <Panel title="Checks" badge={doctorSummary(report)} badgeTone={tone} testID="job-doctor-checks">
          {report.checks.map(check => (
            <View key={check.name} style={[styles.check, { borderBottomColor: colors.hairline }]} testID={`doctor-check-${check.name}`}>
              <StatusIcon status={doctorStatus(check.status)} label={check.status} />
              <View style={{ flex: 1 }}>
                <Text variant="sm" style={{ fontFamily: fonts.mono, fontWeight: fontWeights.semibold }}>{check.name}</Text>
                <Text variant="xs" style={{ color: check.status === 'fail' ? colors.danger.ink : check.status === 'warn' ? colors.attention.ink : colors.ink3 }}>{check.detail}</Text>
              </View>
            </View>
          ))}
        </Panel>
      ) : null}
    </View>
  );
}

/** Legacy JobDoctor route (deep links): the same Doctor panel; its checklist link opens Setup. */
export function JobDoctorScreen({ navigation }: JobDoctorScreenProps) {
  const { colors } = useTheme();
  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: 'transparent' }]}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Doctor</Text>
        <DoctorPanel onOpenChecklist={() => navigation.navigate('JobSetup')} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  panel: { gap: spacing.md },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  check: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm, paddingVertical: spacing.xs, borderBottomWidth: StyleSheet.hairlineWidth, minHeight: touchTarget },
});
