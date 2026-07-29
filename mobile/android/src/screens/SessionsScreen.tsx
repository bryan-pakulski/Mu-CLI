import React, { useState, useCallback } from 'react';
import { View, FlatList, RefreshControl, Alert, Modal, TextInput, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text, Button, Skeleton, ErrorState } from '../components';
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
        <View style={styles.emptyState}>
          <View style={[styles.emptyGlyph, { backgroundColor: colors.bgLift }]}><Ionicons name="chatbubbles-outline" size={24} color={colors.textDim} /></View>
          <Text variant="lg" style={styles.emptyTitle}>No sessions</Text>
          <Text variant="sm" style={{ color: colors.textDim }}>Create one when you are ready to begin.</Text>
          <TouchableOpacity onPress={() => setShowCreate(true)} style={styles.emptyAction}>
            <Ionicons name="add" size={18} color={colors.accent} /><Text variant="sm" style={{ color: colors.accent, fontWeight: '700' }}>Create session</Text>
          </TouchableOpacity>
        </View>
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
      <View style={styles.screenHeader}>
        <View>
          <Text variant="xl" style={styles.screenTitle}>Sessions</Text>
          <Text variant="xs" style={{ color: colors.textDim }}>Tap a session to open it.</Text>
        </View>
        <TouchableOpacity onPress={() => setShowCreate(true)} style={[styles.headerAction, { backgroundColor: colors.bgLift }]} accessibilityLabel="Create session">
          <Ionicons name="add" size={21} color={colors.accent} />
        </TouchableOpacity>
      </View>
      <FlatList
        data={sessions}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        contentContainerStyle={{ paddingHorizontal: spacing.base, paddingBottom: spacing.xl }}
        renderItem={({ item }) => (
          <TouchableOpacity
            onPress={() => switchSession(item.name)}
            activeOpacity={0.72}
            style={[styles.sessionRow, { backgroundColor: colors.bgLift, borderColor: colors.border }, activeSessionName === item.name && { backgroundColor: colors.bgHover }]}
          >
            <View style={[styles.sessionGlyph, { backgroundColor: colors.bgHover }]}>
              <Ionicons name="chatbubble-ellipses-outline" size={17} color={colors.accent} />
            </View>
            <View style={styles.sessionCopy}>
              <Text variant="base" style={styles.sessionName}>{item.name}</Text>
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 3 }}>
                {activeSessionName === item.name ? 'Active' : item.is_loaded ? 'Loaded' : 'Saved'}{item.modified_at ? ` · ${item.modified_at}` : ''}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
            <TouchableOpacity
              onPress={event => { event.stopPropagation(); deleteSession(item.name); }}
              hitSlop={{ top: 10, bottom: 10, left: 8, right: 8 }}
              style={styles.rowIcon}
              accessibilityLabel={`Delete session ${item.name}`}
            >
              <Ionicons name="trash-outline" size={18} color={colors.textDim} />
            </TouchableOpacity>
          </TouchableOpacity>
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
const styles = StyleSheet.create({
  screenHeader: { paddingHorizontal: spacing.base, paddingTop: spacing.base, paddingBottom: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  screenTitle: { fontWeight: '700', letterSpacing: -0.5 },
  headerAction: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  sessionRow: { minHeight: 68, borderWidth: StyleSheet.hairlineWidth, borderRadius: 17, paddingHorizontal: 12, marginBottom: 7, flexDirection: 'row', alignItems: 'center', gap: 10 },
  sessionGlyph: { width: 36, height: 36, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  sessionCopy: { flex: 1 }, sessionName: { fontWeight: '600' },
  rowIcon: { width: 38, height: 42, alignItems: 'center', justifyContent: 'center' },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  emptyGlyph: { width: 52, height: 52, borderRadius: 18, alignItems: 'center', justifyContent: 'center', marginBottom: 16 },
  emptyTitle: { fontWeight: '700', marginBottom: 6 },
  emptyAction: { marginTop: 20, minHeight: 40, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: 6 },
});
