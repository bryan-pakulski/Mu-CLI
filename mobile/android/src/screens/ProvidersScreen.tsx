// Provider and model (More → Provider and model): the model for the active session. Offline
// switches join the outbound queue and replay on reconnect (G5 §3.6).

import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { queueIfOffline, useConnectionStore } from '../store/connection';
import { providersApi, type ProviderInfo } from '../api/providers';
import { spacing } from '../theme/tokens';
import { EmptyState, ErrorState, PageHeader, Row, Rows, Skeleton, Text, useToast } from '../ui';

export function ProvidersScreen() {
  const navigation = useNavigation<any>();
  const toast = useToast();
  const { activeProvider, activeModel, setActiveProviderModel } = useConnectionStore();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [provider, setProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingModels, setLoadingModels] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadModels = useCallback(async (name: string) => {
    setLoadingModels(true);
    try {
      setModels((await providersApi.listModels(name)).models || []);
    } catch (e) {
      setModels([]);
      toast(`Couldn't load models: ${e instanceof Error ? e.message : String(e)}`, 'danger');
    } finally {
      setLoadingModels(false);
    }
  }, [toast]);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [list, current] = await Promise.all([providersApi.list(), providersApi.getCurrent()]);
      setProviders(list.providers);
      setProvider(current.provider || null);
      setModel(current.model || null);
      if (current.provider) void loadModels(current.provider);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [loadModels]);

  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const chooseProvider = (name: string) => {
    if (name === provider) return;
    setProvider(name);
    setModel(null);
    setModels([]);
    void loadModels(name);
  };

  const chooseModel = async (name: string) => {
    if (!provider) return;
    setModel(name);
    if (await queueIfOffline('provider_switch', { provider, model: name })) {
      setActiveProviderModel(provider, name);
      toast(`Offline: switching to ${provider} · ${name} when reconnected`, 'attention');
      return;
    }
    try {
      await providersApi.switch(provider, name);
      setActiveProviderModel(provider, name);
      toast(`Now using ${provider} · ${name}`, 'ok');
    } catch (e) {
      toast(`Couldn't switch: ${e instanceof Error ? e.message : String(e)}`, 'danger');
    }
  };

  const header = (
    <PageHeader title="Provider and model" back={navigation.canGoBack() ? { label: 'Back', onPress: () => navigation.goBack() } : undefined}
      state={[activeProvider && activeModel ? { text: `${activeProvider} · ${activeModel}` } : { text: 'Nothing selected', tone: 'quiet' }]} />
  );

  let body: React.ReactNode;
  if (loading) body = <View style={styles.content}>{[1, 2, 3].map(i => <Skeleton key={i} height={48} />)}</View>;
  else if (error) body = <ErrorState message={error} onRetry={() => void load()} />;
  else if (!providers.length) body = <EmptyState title="No providers" message="Configure a provider key (or Ollama) on the computer running mucli." />;
  else {
    body = (
      <ScrollView contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}>
        <Text variant="label" tone="ink2" accessibilityRole="header">Provider</Text>
        <Rows>
          {providers.map(p => (
            <Row key={p.name} testID={`provider-${p.name}`} selected={p.name === provider} status={p.name === provider ? 'done' : undefined}
              title={p.name} sub={p.configured ? 'Ready' : `Needs ${p.requires || 'configuration'}`}
              onPress={p.configured ? () => chooseProvider(p.name) : undefined} />
          ))}
        </Rows>
        {provider ? (
          <>
            <Text variant="label" tone="ink2" accessibilityRole="header">Model</Text>
            <Rows empty={loadingModels ? 'Loading models…' : 'No models available'}>
              {loadingModels ? [] : models.map(m => (
                <Row key={m} testID={`model-${m}`} selected={m === model} status={m === model ? 'done' : undefined} title={m} onPress={() => void chooseModel(m)} />
              ))}
            </Rows>
          </>
        ) : null}
      </ScrollView>
    );
  }
  return <View style={{ flex: 1 }} testID="providers-screen">{header}{body}</View>;
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.sm, paddingBottom: spacing['2xl'] },
});
