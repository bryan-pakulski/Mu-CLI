import React, { useState, useCallback } from 'react';
import { View, ActivityIndicator, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text, Input, Button, Card } from '../components';
import { api } from '../api/client';
import { spacing } from '../theme/tokens';

export function ConnectionScreen() {
  const { colors } = useTheme();
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
    setTesting(true);
    try {
      setBaseUrl(url);
      const res = await api.get<{ ok: boolean }>('/healthz');
      setConnected(res.ok);
      Alert.alert('Connected', `Server reachable at ${url}`);
    } catch (e) {
      setConnected(false);
      Alert.alert('Connection failed', String(e));
    } finally {
      setTesting(false);
    }
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