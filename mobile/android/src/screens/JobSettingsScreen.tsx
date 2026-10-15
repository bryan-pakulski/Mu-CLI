import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { JiraBrowser } from '../components/JiraSheet';
import { JobRulesForm } from '../components/JobRulesForm';
import { BuildEnvironmentsSettings } from '../components/JobEnvironments';
import { AgentsSettings } from '../components/JobAgents';
import { JobStageModelsForm } from '../components/JobStageModelsSheet';
import { JobsNavBar, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import { PlanosButton, SwitchRow, useToast } from '../components/planos';
import { loadNotifyEnabled, setNotifyEnabled } from '../api/jobNotifications';
import { Text } from '../components/Text';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, spacing } from '../theme/tokens';

export type JobSettingsScreenProps = NativeStackScreenProps<RootStackParamList, 'JobSettings'>;

/** planos Settings page for jobs: stage models, plan approval, run rules and the Jira link. */
export function JobSettingsScreen({ navigation }: JobSettingsScreenProps) {
  const { colors } = useTheme();
  const toast = useToast();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  const go = (page: JobsPage) => switchJobsPage(navigation, 'JobSettings', page);
  const [notify, setNotify] = useState(true);
  useEffect(() => { void loadNotifyEnabled().then(setNotify); }, []);
  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: colors.canvas }]}>
      <JobsNavBar current="JobSettings" onNavigate={go} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Settings</Text>
        <Text variant="xs" style={{ color: colors.ink3 }}>Defaults for every new job. A model set on a job itself takes priority.</Text>
        <View style={styles.tools}>
          <PlanosButton small label="Setup" accessibilityHint="Guided checklist and Doctor: sets up and tests everything jobs need" onPress={() => navigation.navigate('JobSetup')} testID="job-settings-setup" />
        </View>
        <SwitchRow label="Notify me when a job needs me" hint="Android notification when a job waits for plan approval, finished review, input, or its PR was closed (while the app is running)."
          value={notify} onValueChange={on => { setNotify(on); void setNotifyEnabled(on); }} testID="job-settings-notify" />
        <JobStageModelsForm active onSaved={missing => toast(missing.length ? `Saved — ${missing.join(', ')} use the session model` : 'Job settings saved', 'ok')} />
        <JobRulesForm active />
        <BuildEnvironmentsSettings active />
        <AgentsSettings active />
        <Text variant="md" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.sm }}>Jira</Text>
        <JiraBrowser active section="link" sessionName={sessionName} />
        <View style={styles.row}>
          <PlanosButton label="Open tickets →" onPress={() => go('JobBoards')} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  row: { flexDirection: 'row' },
  tools: { flexDirection: 'row', gap: spacing.sm },
});
