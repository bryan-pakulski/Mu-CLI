// Connection (More → Connection): the mucli GUI this phone talks to. The saved address only
// changes once the new one answers /healthz; signing in is pairing (Settings → Devices).

import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { refreshPendingMutations, useConnectionStore } from '../store/connection';
import { checkHealth } from '../api/client';
import { validateBaseUrl } from '../api/urlSafety';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { spacing } from '../theme/tokens';
import { Banner, Button, Field, PageHeader, Row, Rows, TextField } from '../ui';

const MAX_ATTEMPTS = 3;
const BACKOFF_MS = 1_500;

export function ConnectionScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { baseUrl, isConnected, pairingRequired, setBaseUrl, setConnected, loadFromStorage } = useConnectionStore();
  const [url, setUrl] = useState(baseUrl);
  const [testing, setTesting] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');

  useFocusEffect(
    useCallback(() => {
      if (loaded) return;
      void loadFromStorage().then(() => {
        setUrl(useConnectionStore.getState().baseUrl);
        setLoaded(true);
        // G5 (§3.6): hydrate the queued-mutations badge from storage.
        void refreshPendingMutations();
      });
    }, [loaded, loadFromStorage]),
  );

  const testConnection = async () => {
    if (testing) return;
    setError('');
    const validated = validateBaseUrl(url);
    if (!validated.ok) { setError(validated.error); return; }
    const candidate = validated.url;
    // Transactional host test: the persisted working host only changes once the candidate answers.
    setTesting(true);
    try {
      let reachable = false;
      for (let attempt = 1; attempt <= MAX_ATTEMPTS && !reachable; attempt++) {
        reachable = await checkHealth(candidate, { timeoutMs: 5_000 });
        if (!reachable && attempt < MAX_ATTEMPTS) await new Promise(resolve => setTimeout(resolve, BACKOFF_MS * attempt));
      }
      if (!reachable) { setError(`Couldn't reach ${candidate}. The saved address wasn't changed.`); return; }
      setBaseUrl(candidate);
      setConnected(true);
      // An explicit reconnection replays the offline queue too; the badge reports the outcome.
      useConnectionStore.getState().replayPending().catch(() => undefined);
      if (navigation.canGoBack()) navigation.goBack();
      else navigation.navigate('Tabs', { screen: 'Chat' });
    } finally {
      setTesting(false);
    }
  };

  const server = baseUrl.replace(/^https?:\/\//, '');
  return (
    <View style={{ flex: 1 }} testID="connection-screen">
      <PageHeader title="Connection" back={navigation.canGoBack() ? { label: 'Back', onPress: () => navigation.goBack() } : undefined}
        state={[{ text: isConnected ? `Connected to ${server}` : 'Not connected', tone: isConnected ? 'ok' : 'attention' }, pairingRequired ? { text: 'not paired', tone: 'attention' } : null]} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Field label="Server address" hint="The mucli GUI address, e.g. http://192.168.1.20:30311. The saved address only changes after it answers.">
          <TextField value={url} onChangeText={setUrl} placeholder="http://192.168.1.20:30311" autoCapitalize="none" autoCorrect={false} keyboardType="url" testID="connection-url" />
        </Field>
        {error ? <Banner tone="danger" title="Couldn't connect" testID="connection-error">{error}</Banner> : null}
        <Button label={testing ? 'Testing…' : 'Test and connect'} variant="primary" onPress={() => void testConnection()} disabled={testing} loading={testing} testID="connection-test" />
        <Rows>
          <Row title="Pair with mucli" status={pairingRequired ? 'waiting' : undefined} sub="Sign this phone in with the code from Settings → Devices on the computer" onPress={() => navigation.navigate('Pair', undefined)} />
        </Rows>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, gap: spacing.base, paddingBottom: spacing['2xl'] },
});
