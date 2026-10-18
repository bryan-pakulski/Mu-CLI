import React from 'react';
import { ScrollView, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { JiraBrowser } from '../components/JiraSheet';
import { JobsNavBar, type JobsPage, switchJobsPage } from '../components/JobsChrome';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, spacing } from '../theme/tokens';
import { Text, useToast } from '../ui';

export type JobBoardsScreenProps = NativeStackScreenProps<RootStackParamList, 'JobBoards'>;

/** Wash Tickets page (route id JobBoards): the Jira browser (link, boards, sprints, tickets, import) as a full screen. */
export function JobBoardsScreen({ navigation }: JobBoardsScreenProps) {
  const { colors } = useTheme();
  const toast = useToast();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  const go = (page: JobsPage) => switchJobsPage(navigation, 'JobBoards', page);
  return (
    <SafeAreaView edges={['bottom']} style={[styles.root, { backgroundColor: 'transparent' }]}>
      <JobsNavBar current="JobBoards" onNavigate={go} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Text variant="xl" accessibilityRole="header" style={{ fontWeight: fontWeights.semibold }}>Tickets</Text>
        <Text variant="xs" style={{ color: colors.ink3 }}>Pick Jira tickets and create jobs from them. Tickets already linked to a job open that job.</Text>
        <JiraBrowser
          active
          sessionName={sessionName}
          onImported={ids => { if (ids.length) toast(`Created ${ids.length} job${ids.length === 1 ? '' : 's'}`, 'ok'); }}
          onOpenJob={jobId => navigation.navigate('JobDetail', { jobId })}
        />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['2xl'] },
});
