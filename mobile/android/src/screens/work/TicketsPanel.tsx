// Work → Tickets: the Jira browser (link, boards, sprints, tickets, import). Tickets already linked
// to a job open that job.

import React from 'react';
import { ScrollView, StyleSheet } from 'react-native';

import { JiraBrowser } from '../../components/JiraSheet';
import { useConnectionStore } from '../../store/connection';
import { spacing } from '../../theme/tokens';
import { Text, useToast } from '../../ui';

export function TicketsPanel({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
  const toast = useToast();
  const sessionName = useConnectionStore(state => state.activeSessionName);
  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" testID="work-tickets">
      <Text variant="caption" tone="ink3">Pick Jira tickets and create jobs from them. Tickets already linked to a job open that job.</Text>
      <JiraBrowser
        active
        sessionName={sessionName}
        onImported={ids => { if (ids.length) toast(`Created ${ids.length} job${ids.length === 1 ? '' : 's'}`, 'ok'); }}
        onOpenJob={onOpenJob}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['3xl'] },
});
