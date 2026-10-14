import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, Alert, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { queueIfOffline, useConnectionStore } from '../store/connection';
import { Text, Card, Button, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { providersApi, ProviderInfo } from '../api/providers';
import { spacing, fontWeights, fonts } from '../theme/tokens';
import { Panel, StatusIcon } from '../components/planos';

export function ProvidersScreen() {
  const { colors } = useTheme();
  const { activeProvider, activeModel, setActiveProviderModel } = useConnectionStore();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingModels, setLoadingModels] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await providersApi.list();
      setProviders(res.providers);
      const current = await providersApi.getCurrent();
      if (current.provider) setSelectedProvider(current.provider);
      if (current.model) setSelectedModel(current.model);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  const selectProvider = async (name: string) => {
    setSelectedProvider(name);
    setSelectedModel(null);
    setModels([]);
    setLoadingModels(true);
    try {
      const res = await providersApi.listModels(name);
      setModels(res.models || []);
    } catch (e) {
      Alert.alert('Failed to load models', String(e));
    } finally {
      setLoadingModels(false);
    }
  };

  const selectModel = async (model: string) => {
    if (!selectedProvider) return;
    setSelectedModel(model);
    // G5 (§3.6): offline switches join the outbound queue and replay on
    // reconnect; the local selection stays optimistic.
    if (await queueIfOffline('provider_switch', {
      provider: selectedProvider,
      model,
    })) {
      setActiveProviderModel(selectedProvider, model);
      Alert.alert('Queued (offline)', `Switch to ${selectedProvider} · ${model} replays on reconnect`);
      return;
    }
    try {
      await providersApi.switch(selectedProvider, model);
      setActiveProviderModel(selectedProvider, model);
      Alert.alert('Provider switched', `${selectedProvider} · ${model}`);
    } catch (e) {
      Alert.alert('Switch failed', String(e));
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3].map(i => (
            <Skeleton key={i} height={80} style={{ marginBottom: spacing.sm }} />
          ))}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (providers.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No providers" message="No LLM providers configured" />
      </SafeAreaView>
    );
  }

  const renderProvider = ({ item }: { item: ProviderInfo }) => {
    const isSelected = selectedProvider === item.name;
    return (
      <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flex: 1 }}>
            <Text variant="base" style={{ fontWeight: fontWeights.medium }}>
              {item.name}
            </Text>
            {item.requires && (
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                Requires: {item.requires}
              </Text>
            )}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
            {item.configured && <Badge label="✓ Ready" variant="success" />}
            {isSelected && <Badge label="Selected" variant="accent" />}
            <Button
              title={isSelected ? 'Selected' : 'Select'}
              variant={isSelected ? 'ghost' : 'primary'}
              disabled={isSelected || !item.configured}
              onPress={() => selectProvider(item.name)}
            />
          </View>
        </View>
        {isSelected && (
          <View style={{ marginTop: spacing.sm, paddingTop: spacing.xs }}>
            <Text variant="xs" style={{ color: colors.textDim, marginBottom: spacing.xs, fontVariant: ['tabular-nums'] }}>
              {loadingModels ? 'Loading models…' : `Models (${models.length})`}
            </Text>
            {models.map(m => (
              <TouchableOpacity
                key={m}
                onPress={() => selectModel(m)}
                accessibilityRole="button"
                accessibilityState={{ selected: selectedModel === m }}
                style={{
                  flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
                  paddingVertical: spacing.md, minHeight: 44,
                  borderTopWidth: 1, borderTopColor: colors.border,
                }}
              >
                <Text variant="sm" style={{ color: selectedModel === m ? colors.accentFg : colors.text, fontWeight: selectedModel === m ? fontWeights.medium : fontWeights.regular }}>
                  {m}
                </Text>
                {selectedModel === m && <Badge label="Active" variant="accent" />}
              </TouchableOpacity>
            ))}
            {!loadingModels && models.length === 0 && (
              <Text variant="xs" style={{ color: colors.textDim }}>No models available</Text>
            )}
          </View>
        )}
      </Card>
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      {activeProvider && activeModel && (
        <View style={{ padding: spacing.base, paddingBottom: 0 }}>
          <Panel title="Current" right={<StatusIcon status="done" label="Active" showLabel size="xs" />}>
            <Text variant="base" style={{ fontWeight: fontWeights.semibold, fontFamily: fonts.mono }}>
              {activeProvider} · {activeModel}
            </Text>
          </Panel>
        </View>
      )}
      <FlatList
        data={providers}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={renderProvider}
      />
    </SafeAreaView>
  );
}