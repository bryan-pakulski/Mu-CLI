// Environments (Mu §11b, mobile_parity.md §6 Environments; web /containers): environments and
// templates as rows with live CPU and memory; an environment opens its page, a template its
// editor. New environment is the page's one primary action.

import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import { containersApi, type ContainerListing, type ContainerStatsResponse } from '../../api/containers';
import { useConnectionStore } from '../../store/connection';
import { spacing } from '../../theme/tokens';
import { Board, BoardSection, EmptyState, ErrorState, PageHeader, PrimaryPill, Row, useInterval } from '../../ui';
import { envRowSub, envStatus, listState, templateRowSub } from './envModel';

export function EnvironmentsScreen() {
  const navigation = useNavigation<any>();
  const focused = useIsFocused();
  const isConnected = useConnectionStore(s => s.isConnected);
  const [listing, setListing] = useState<ContainerListing | null>(null);
  const [stats, setStats] = useState<ContainerStatsResponse | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([containersApi.list(), containersApi.stats().catch(() => null)]);
      setListing(l);
      setStats(s);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect(() => { if (isConnected && focused) void load(); }, [isConnected, focused, load]);
  const live = isConnected && focused && !!listing?.containers.some(c => c.status === 'running');
  useInterval(() => { void containersApi.stats().then(setStats).catch(() => undefined); }, Math.max(1500, stats?.poll_after_ms || 2500), live);

  const containers = listing?.containers || [];
  const templates = listing?.templates || [];

  return (
    <View style={styles.page} testID="environments-screen">
      <PageHeader title="Environments" state={listState(containers, templates).map((text, i) => ({ text, tone: i === 1 && text.endsWith('stopped') ? 'quiet' : '' }))}
        menu={[{ key: 'refresh', label: 'Refresh', icon: 'refresh-cw', onPress: () => void load() }]} testID="environments-header" />
      <ScrollView contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} />}>
        {error ? <ErrorState title="Couldn't load environments" message={error} onRetry={() => void load()} /> : null}
        {!error && listing && !containers.length && !templates.length ? (
          <EmptyState title="No environments yet" message="Create one from a Dockerfile or a template, then attach sessions when you need them." icon="box" pigment="meadow"
            actionLabel="New environment" onAction={() => navigation.navigate('EnvironmentForm', { mode: 'create' })} />
        ) : null}
        {listing && (containers.length || templates.length) ? (
          <Board>
            <BoardSection title="Environments" count={containers.length} empty="No environments yet.">
              {containers.map(c => (
                <Row key={c.name} testID={`environment-${c.name}`} status={envStatus(c)} title={c.name} sub={envRowSub(c, stats?.containers?.[c.name])}
                  accessibilityLabel={`Open environment ${c.name}`} onPress={() => navigation.navigate('EnvironmentDetail', { name: c.name })} />
              ))}
            </BoardSection>
            <BoardSection title="Templates" count={templates.length} empty="Snapshots of an environment appear here.">
              {templates.map(t => (
                <Row key={t.name} testID={`template-${t.name}`} status="pending" title={t.name} sub={templateRowSub(t)}
                  accessibilityLabel={`Open template ${t.name}`} onPress={() => navigation.navigate('TemplateDetail', { name: t.name })} />
              ))}
            </BoardSection>
          </Board>
        ) : null}
      </ScrollView>
      <PrimaryPill label="New environment" onPress={() => navigation.navigate('EnvironmentForm', { mode: 'create' })} testID="environments-new" />
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['4xl'] },
});
