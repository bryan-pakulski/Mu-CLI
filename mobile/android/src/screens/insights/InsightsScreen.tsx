// Insights (mobile_parity.md §6 Insights; web /trace, /jobs/trace, /models/costs): Runs, Jobs and
// Costs as header tabs of one page. The tab can be chosen by navigation (`{ tab: 'costs' }` from
// Settings › Areas › Model pricing).

import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useRoute } from '@react-navigation/native';
import { PageHeader } from '../../ui';
import { CostsScreen } from './CostsScreen';
import { JobsInsightsScreen } from './JobsInsightsScreen';
import { RunsScreen } from './RunsScreen';

export type InsightsTab = 'runs' | 'jobs' | 'costs';
export const INSIGHTS_TABS: Array<{ key: InsightsTab; label: string }> = [
  { key: 'runs', label: 'Runs' },
  { key: 'jobs', label: 'Jobs' },
  { key: 'costs', label: 'Costs' },
];

const asTab = (value: unknown): InsightsTab | null => (INSIGHTS_TABS.some(t => t.key === value) ? value as InsightsTab : null);

export function InsightsScreen() {
  const route = useRoute<any>();
  const requested = asTab(route.params?.tab);
  const [tab, setTab] = useState<InsightsTab>(requested || 'runs');
  useEffect(() => { if (requested) setTab(requested); }, [requested, route.params]);
  return (
    <View style={styles.page} testID="insights-screen">
      <PageHeader title="Insights" tabs={INSIGHTS_TABS} tab={tab} onTab={key => setTab(key as InsightsTab)} testID="insights-header" />
      <View style={styles.page}>
        {tab === 'runs' ? <RunsScreen /> : tab === 'jobs' ? <JobsInsightsScreen /> : <CostsScreen />}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({ page: { flex: 1 } });
