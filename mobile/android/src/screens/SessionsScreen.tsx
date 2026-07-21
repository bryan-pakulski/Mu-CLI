import React, { useState, useCallback } from 'react';
import { View, FlatList, RefreshControl, Alert, Modal, TextInput, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text, Card, Button, Skeleton, ErrorState, EmptyState } from '../components';
import { sessionsApi, SessionSummary } from '../api/sessions';
import { providersApi } from '../api/providers';
import { spacing } from '../theme/tokens';

export function SessionsScreen() {
  const { colors } = useTheme();
  const { activeSessionName, setActiveSession, activeProvider, activeModel } = useConnectionStore();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await sessionsApi.list();
      setSessions(res.sessions);
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

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const switchSession = async (name: string) => {
    try {
      const session = sessions.find(s => s.name === name);
      if (session && !session.is_loaded) {
        await sessionsApi.load(name);
      } else {
        await sessionsApi.focus(name);
      }
      setActiveSession(name);
      load();
    } catch (e) {
      Alert.alert('Switch failed', String(e));
    }
  };

  const deleteSession = (name: string) => {
    Alert.alert('Delete session?', `Permanently delete "${name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await sessionsApi.delete(name);
            if (activeSessionName === name) setActiveSession(null);
            load();
          } catch (e) {
            Alert.alert('Delete failed', String(e));
          }
        },
      },
    ]);
  };

  const createSession = async () => {
    if (!newName.trim()) return;
    try {
      // Fetch current provider+model from server instead of guessing
      let provider = activeProvider;
      let model = activeModel;
      if (!provider || !model) {
        const cur = await providersApi.getCurrent();
        provider = cur.provider;
        model = cur.model;
      }
      if (!provider || !model) {
        Alert.alert(
          'No provider configured',
          'Set a provider and model in the Providers screen first, then create a session.',
        );
        return;
      }
      await sessionsApi.create(
        newName.trim(),
        provider,
        model,
      );
      setActiveSession(newName.trim());
      setNewName('');
      setShowCreate(false);
      load();
    } catch (e) {
      Alert.alert('Create failed', String(e));
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4, 5].map(i => (
            <Skeleton key={i} height={60} style={{ marginBottom: spacing.sm }} />
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

  if (sessions.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState
          title="No sessions"
          message="Create a new session to get started"
          actionLabel="Create Session"
          onAction={() => setShowCreate(true)}
        />
        <CreateSessionModal
          visible={showCreate}
          newName={newName}
          setNewName={setNewName}
          onCreate={createSession}
          onCancel={() => { setShowCreate(false); setNewName(''); }}
          colors={colors}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={sessions}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        contentContainerStyle={{ padding: spacing.base }}
        renderItem={({ item }) => (
          <Card
            style={{
              marginBottom: spacing.sm,
              minHeight: 44,
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <View style={{ flex: 1 }}>
              <Text variant="base" style={{ fontWeight: '500' }}>
                {item.name}
                {activeSessionName === item.name && ' (active)'}
              </Text>
              {item.modified_at && (
                <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>
                  {item.modified_at}
                </Text>
              )}
            </View>
            <View style={{ flexDirection: 'row', gap: 6 }}>
              {activeSessionName !== item.name && (
                <Button title="Switch" variant="ghost" onPress={() => switchSession(item.name)} />
              )}
              <Button title="Delete" variant="ghost" onPress={() => deleteSession(item.name)} />
            </View>
          </Card>
        )}
      />
      <CreateSessionModal
        visible={showCreate}
        newName={newName}
        setNewName={setNewName}
        onCreate={createSession}
        onCancel={() => { setShowCreate(false); setNewName(''); }}
        colors={colors}
      />
      <View style={{ padding: spacing.base }}>
        <Button title="New Session" onPress={() => setShowCreate(true)} />
      </View>
    </SafeAreaView>
  );
}

interface CreateSessionModalProps {
  visible: boolean;
  newName: string;
  setNewName: (s: string) => void;
  onCreate: () => void;
  onCancel: () => void;
  colors: { bg: string; bgLift: string; text: string; textDim: string; accent: string; border: string };
}

function CreateSessionModal({ visible, newName, setNewName, onCreate, onCancel, colors }: CreateSessionModalProps) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onCancel}>
      <TouchableOpacity style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)' }} onPress={onCancel} activeOpacity={1}>
        <View style={{
          margin: spacing.base,
          marginTop: 80,
          backgroundColor: colors.bgLift,
          borderRadius: 12,
          padding: spacing.base,
        }}>
          <Text variant="lg" style={{ marginBottom: spacing.base }}>New Session</Text>
          <TextInput
            value={newName}
            onChangeText={setNewName}
            placeholder="session name"
            placeholderTextColor={colors.textDim}
            autoCapitalize="none"
            autoCorrect={false}
            style={{
              color: colors.text,
              borderWidth: 1,
              borderColor: colors.border,
              borderRadius: 6,
              padding: 12,
              minHeight: 44,
              marginBottom: spacing.base,
            }}
          />
          <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'flex-end' }}>
            <Button title="Cancel" variant="ghost" onPress={onCancel} />
            <Button title="Create" onPress={onCreate} disabled={!newName.trim()} />
          </View>
        </View>
      </TouchableOpacity>
    </Modal>
  );
}