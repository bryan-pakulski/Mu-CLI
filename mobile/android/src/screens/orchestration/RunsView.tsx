// Runs (web runs.js renderList): every run as a row, filtered by state.

import React, { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { orchestrationApi } from '../../api/orchestration';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, Button, Loading, Rows } from '../../ui';
import { RunRow } from './parts';
import { useOrchData } from './useOrchData';

export const STATE_FILTERS: Array<[string, string]> = [
  ['', 'All'], ['proposed', 'Awaiting approval'], ['running,approved,rolling_back', 'Running'], ['paused', 'Paused'],
  ['succeeded', 'Succeeded'], ['failed', 'Failed'], ['cancelled,rejected,rolled_back,expired', 'Stopped'],
];

export function RunsView({ openRun }: { openRun: (id: string) => void }) {
  const { colors } = useTheme();
  const [state, setState] = useState('');
  const { data, error, refreshing, refresh } = useOrchData(
    () => orchestrationApi.runs(state ? state.split(',') : undefined, 200), ['run'], [state]);
  return (
    <ScrollView contentContainerStyle={styles.pad} testID="orch-runs"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.ink3} />}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters} accessibilityLabel="Filter by state">
        {STATE_FILTERS.map(([key, label]) => (
          <Button key={label} size="md" variant={state === key ? 'tonal' : 'ghost'} selected={state === key} label={label}
            onPress={() => setState(key)} testID={`orch-runs-filter-${label.toLowerCase().replace(/\s+/g, '-')}`} />
        ))}
      </ScrollView>
      {error ? <Banner tone="danger" glyph="✗" role="alert" title={error} /> : null}
      {!data && !error ? <Loading label="Loading runs…" /> : null}
      {data ? (
        <View>
          <Rows label="Runs" empty={state ? 'No runs in this state.' : 'No runs yet. Start one from a published tool.'}>
            {data.runs.map(r => <RunRow key={r.id} run={r} onPress={() => openRun(r.id)} />)}
          </Rows>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  pad: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.sm },
  filters: { gap: spacing.xs },
});
