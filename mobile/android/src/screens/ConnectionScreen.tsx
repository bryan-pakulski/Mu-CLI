import React, { useState, useCallback } from 'react';
import { View, ActivityIndicator, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text, Input, Button, Card } from '../components';
import { api } from '../api/client';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { spacing } from '../theme/tokens';

export function ConnectionScreen() {
  const { colors } = useTheme();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { baseUrl, isConnected, setBaseUrl, setConnected, loadFromStorage } = useConnectionStore();
  const [url, setUrl] = useState(baseUrl);
  const [testing, setTesting] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useFocusEffect(
    useCallback(() => {
      if (!loaded) {
        loadFromStorage().then(() => {
          setUrl(useConnectionStore.getState().baseUrl);
          setLoaded(true);
        });
      }
    }, [loaded, loadFromStorage]),
  );

  const testConnection = async () => {
    if (testing) return;
    setTesting(true);
    setBaseUrl(url);
    // Retry with backoff: the host may be busy running an agent turn
    // and a single 5s probe can race a transient slow response.
    const MAX_ATTEMPTS = 3;
    const BACKOFF_MS = 1_500;
    let lastError: unknown = null;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        const res = await api.get<{ ok: boolean }>('/healthz', { timeoutMs: 10_000 });
        if (!res.ok) throw new Error('MuCLI health check did not return ok');
        setConnected(true);
        if (navigation.canGoBack()) navigation.goBack();
        else navigation.navigate('Chat');
        return;
      } catch (error) {
        lastError = error;
        if (attempt < MAX_ATTEMPTS) {
          await new Promise(resolve => setTimeout(resolve, BACKOFF_MS * attempt));
        }
      }
    }
    setConnected(false);
    Alert.alert('Connection failed', String(lastError));
    setTesting(false);
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={{ flex: 1, padding: spacing.base }}>
        <Text variant="lg" style={{ marginBottom: spacing.base }}>
          Connection Settings
        </Text>
        <Card style={{ marginBottom: spacing.base }}>
          <Text variant="sm" style={{ color: colors.textDim, marginBottom: spacing.xs }}>
            Server URL
          </Text>
          <Input
            value={url}
            onChangeText={setUrl}
            placeholder="http://localhost:30311"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
          />
          <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: spacing.sm, gap: 6 }}>
            <View style={{
              width: 8, height: 8, borderRadius: 4,
              backgroundColor: isConnected ? colors.success : colors.error,
            }} />
            <Text variant="xs" style={{ color: colors.textDim }}>
              {isConnected ? 'Connected' : 'Not connected'}
            </Text>
          </View>
        </Card>
        <Button title={testing ? 'Testing…' : 'Test Connection'} onPress={testConnection} disabled={testing} />
        {testing && <ActivityIndicator color={colors.accent} style={{ marginTop: spacing.sm }} />}
      </View>
    </SafeAreaView>
  );
}
