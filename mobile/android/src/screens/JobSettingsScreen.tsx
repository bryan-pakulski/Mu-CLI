import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { JiraBrowser } from '../components/JiraSheet';
import { JobRulesForm } from '../components/JobRulesForm';
import { BuildEnvironmentsSettings } from '../components/JobEnvironments';
import { AgentsSettings } from '../components/JobAgents';
import { JobStageModelsForm } from '../components/JobStageModelsSheet';
import { loadNotifyEnabled, setNotifyEnabled } from '../api/jobNotifications';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { spacing } from '../theme/tokens';
import { Button, PageHeader, SwitchRow, Text, useToast } from '../ui';

export type JobSettingsScreenProps = NativeStackScreenProps<RootStackParamList, 'JobSettings'>;

/** Wash Settings page for jobs: stage models, plan approval, run rules and the Jira link. */
export function JobSettingsScreen({ navigation }: JobSettingsScreenProps) {
  const toast = useToast();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  const [notify, setNotify] = useState(true);
  useEffect(() => { void loadNotifyEnabled().then(setNotify); }, []);
  return (
    <View style={styles.root}>
      <PageHeader title="Work settings" back={{ label: 'Back', onPress: () => navigation.goBack() }} testID="job-settings-header"
        state={[{ text: 'Defaults for every new job; a model set on a job wins', tone: 'quiet' }]} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.tools}>
          <Button small label="Setup" accessibilityHint="Guided checklist and Doctor: sets up and tests everything jobs need" onPress={() => navigation.navigate('JobSetup')} testID="job-settings-setup" />
        </View>
        <SwitchRow label="Notify me when a job needs me" hint="Android notification when a job waits for plan approval, finished review, input, or its PR was closed (while the app is running)."
          value={notify} onValueChange={on => { setNotify(on); void setNotifyEnabled(on); }} testID="job-settings-notify" />
        <JobStageModelsForm active onSaved={missing => toast(missing.length ? `Saved — ${missing.join(', ')} use the session model` : 'Job settings saved', 'ok')} />
        <JobRulesForm active />
        <BuildEnvironmentsSettings active />
        <AgentsSettings active />
        <Text variant="title3" accessibilityRole="header">Jira</Text>
        <JiraBrowser active section="link" sessionName={sessionName} />
        <View style={styles.row}>
          <Button variant="secondary" navigates label="Open tickets" onPress={() => navigation.navigate('Tabs', { screen: 'Work', params: { tab: 'tickets' } })} />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.md, paddingBottom: spacing['2xl'] },
  row: { flexDirection: 'row' },
  tools: { flexDirection: 'row', gap: spacing.sm },
});
