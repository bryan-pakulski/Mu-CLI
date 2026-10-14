import React, { useState, useCallback } from 'react';
import { View, FlatList, RefreshControl, Alert, TextInput, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Text, Button, Skeleton, ErrorState, SessionStatusBadge, sortSessions } from '../components';
import { sessionsApi, SessionSummary } from '../api/sessions';
import { providersApi } from '../api/providers';
import { spacing, fontWeights, radii, tracking, touchTarget, elevation, type ThemeColors } from '../theme/tokens';
import { SafeAreaModal } from '../components/SafeAreaModal';

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
          <View style={[styles.emptyGlyph, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]}><Ionicons name="chatbubbles-outline" size={24} color={colors.muted} /></View>
          <Text variant="lg" style={styles.emptyTitle}>No sessions</Text>
          <Text variant="sm" style={{ color: colors.muted }}>Create one when you are ready to begin.</Text>
          <TouchableOpacity onPress={() => setShowCreate(true)} style={styles.emptyAction}>
            <Ionicons name="add" size={18} color={colors.accentFg} /><Text variant="sm" style={{ color: colors.accentFg, fontWeight: fontWeights.medium }}>Create session</Text>
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
          <Text variant="xs" style={{ color: colors.muted }}>Tap a session to open it.</Text>
        </View>
        <TouchableOpacity onPress={() => setShowCreate(true)} style={[styles.headerAction, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border }]} accessibilityLabel="Create session">
          <Ionicons name="add" size={20} color={colors.accentFg} />
        </TouchableOpacity>
      </View>
      <FlatList
        data={sortSessions(sessions, activeSessionName)}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        contentContainerStyle={{ paddingHorizontal: spacing.base, paddingBottom: spacing.xl }}
        renderItem={({ item }) => (
          <TouchableOpacity
            onPress={() => switchSession(item.name)}
            activeOpacity={0.72}
            style={[styles.sessionRow, { backgroundColor: colors.surface, borderColor: colors.border }, activeSessionName === item.name && { backgroundColor: colors.accentSubtle, borderColor: colors.accent }]}
          >
            <View style={[styles.sessionGlyph, { backgroundColor: colors.hover }]}>
              <Ionicons name="chatbubble-ellipses-outline" size={16} color={colors.textSoft} />
            </View>
            <View style={styles.sessionCopy}>
              <View style={styles.sessionTitleRow}>
                <Text variant="base" style={[styles.sessionName, activeSessionName === item.name && styles.sessionNameActive]} numberOfLines={1}>{item.name}</Text>
                <SessionStatusBadge session={item} active={activeSessionName === item.name} size="sm" />
              </View>
              <Text variant="xs" style={{ color: colors.muted, marginTop: spacing.xs, fontVariant: ['tabular-nums'] }}>
                {item.modified_at || (activeSessionName === item.name ? 'Active' : item.is_loaded ? 'Loaded' : 'Saved')}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.muted} />
            <TouchableOpacity
              onPress={event => { event.stopPropagation(); deleteSession(item.name); }}
              hitSlop={{ top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm }}
              style={styles.rowIcon}
              accessibilityLabel={`Delete session ${item.name}`}
            >
              <Ionicons name="trash-outline" size={18} color={colors.muted} />
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
  colors: ThemeColors;
}

function CreateSessionModal({ visible, newName, setNewName, onCreate, onCancel, colors }: CreateSessionModalProps) {
  const { isDark } = useTheme();
  return (
    <SafeAreaModal visible={visible} transparent animationType="slide" onRequestClose={onCancel}>
      <TouchableOpacity style={{ flex: 1, backgroundColor: colors.scrim }} onPress={onCancel} activeOpacity={1}>
        <View style={[elevation(2, colors, isDark), {
          margin: spacing.base,
          marginTop: spacing['3xl'],
          borderRadius: radii.panel,
          padding: spacing.base,
        }]}>
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
              backgroundColor: colors.bg,
              borderWidth: 1,
              borderColor: colors.border,
              borderRadius: radii.sm,
              padding: spacing.md,
              minHeight: touchTarget,
              marginBottom: spacing.base,
            }}
          />
          <View style={{ flexDirection: 'row', gap: spacing.sm, justifyContent: 'flex-end' }}>
            <Button title="Cancel" variant="ghost" onPress={onCancel} />
            <Button title="Create" onPress={onCreate} disabled={!newName.trim()} />
          </View>
        </View>
      </TouchableOpacity>
    </SafeAreaModal>
  );
}
const styles = StyleSheet.create({
  screenHeader: { paddingHorizontal: spacing.base, paddingTop: spacing.base, paddingBottom: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  screenTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  headerAction: { width: touchTarget, height: touchTarget, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  sessionRow: { minHeight: 64, borderWidth: 1, borderRadius: radii.panel, paddingHorizontal: spacing.md, marginBottom: spacing.sm, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sessionGlyph: { width: 36, height: 36, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' },
  sessionCopy: { flex: 1 }, sessionName: { fontWeight: fontWeights.semibold, flexShrink: 1 }, sessionNameActive: { fontWeight: fontWeights.semibold },
  sessionTitleRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  rowIcon: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  emptyGlyph: { width: 48, height: 48, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.base },
  emptyTitle: { fontWeight: fontWeights.semibold, marginBottom: spacing.xs },
  emptyAction: { marginTop: spacing.base, minHeight: touchTarget, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
});
