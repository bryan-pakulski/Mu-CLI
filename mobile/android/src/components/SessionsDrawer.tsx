import React, { useState, useCallback, useEffect } from 'react';
import { View, StyleSheet, TouchableOpacity, FlatList, RefreshControl, Alert } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { sessionsApi, SessionSummary } from '../api/sessions';
import { Text } from './Text';
import { SafeAreaModal } from './SafeAreaModal';
import { SessionStatusBadge, sortSessions } from './SessionStatusBadge';
import { elevation, fontSizes, fontWeights, radii, spacing, touchTarget } from '../theme/tokens';

export type SessionsDrawerProps = {
  visible: boolean;
  onClose: () => void;
};

export function SessionsDrawer({ visible, onClose }: SessionsDrawerProps) {
  const { colors, isDark } = useTheme();
  const { activeSessionName, setActiveSession } = useConnectionStore();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await sessionsApi.list();
      setSessions(res.sessions);
    } catch {
      // best effort
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  const switchSession = async (name: string) => {
    try {
      const session = sessions.find(s => s.name === name);
      if (session && !session.is_loaded) {
        await sessionsApi.load(name);
      } else {
        await sessionsApi.focus(name);
      }
      setActiveSession(name);
      onClose();
    } catch (e) {
      Alert.alert('Switch failed', String(e));
    }
  };

  const unloadSession = async (name: string) => {
    Alert.alert('Unload session?', `Unload "${name}" from memory?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Unload', style: 'destructive', onPress: async () => {
        try {
          await sessionsApi.unload(name);
          load();
        } catch (e) { Alert.alert('Failed', String(e)); }
      }},
    ]);
  };

  const deleteSession = (name: string) => {
    Alert.alert('Delete session?', `Permanently delete "${name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        try {
          await sessionsApi.delete(name);
          load();
        } catch (e) { Alert.alert('Failed', String(e)); }
      }},
    ]);
  };

  const newSession = async () => {
    try {
      await sessionsApi.unloadActive();
      setActiveSession(null);
      onClose();
    } catch {
      // best effort
    }
  };

  return (
    <SafeAreaModal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <TouchableOpacity style={[styles.backdrop, { backgroundColor: colors.scrim }]} onPress={onClose} activeOpacity={1} />
        <View style={[styles.drawer, elevation(2, colors, isDark), { borderWidth: 0, borderLeftWidth: 1, paddingTop: spacing.base }]}>
          <View style={styles.header}>
            <View><Text style={[styles.title, { color: colors.text }]}>Sessions</Text><Text variant="xs" dim>Tap a row to open it.</Text></View>
            <View style={styles.headerActions}>
              <TouchableOpacity onPress={newSession} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn} accessibilityLabel="Create session">
                <Ionicons name="add" size={22} color={colors.textSoft} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn} accessibilityLabel="Close sessions">
                <Ionicons name="close" size={22} color={colors.textDim} />
              </TouchableOpacity>
            </View>
          </View>

          <FlatList
            data={sortSessions(sessions, activeSessionName)}
            keyExtractor={item => item.name}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
            contentContainerStyle={{ paddingVertical: spacing.sm }}
            ListEmptyComponent={
              loading ? null : (
                <View style={{ padding: spacing.lg, alignItems: 'center' }}>
                  <Text style={{ color: colors.textDim, fontSize: fontSizes.base }}>No sessions</Text>
                </View>
              )
            }
            renderItem={({ item }) => (
              <TouchableOpacity
                onPress={() => switchSession(item.name)}
                style={[
                  styles.row,
                  activeSessionName === item.name && { backgroundColor: colors.accentSubtle },
                ]}
              >
                <Text
                  style={[styles.rowName, { color: colors.text }, activeSessionName === item.name && styles.rowNameActive]}
                  numberOfLines={1}
                  ellipsizeMode="middle"
                >
                  {item.name}
                </Text>
                <SessionStatusBadge session={item} active={activeSessionName === item.name} size="sm" />
                <View style={styles.rowActions}>
                  <TouchableOpacity onPress={() => unloadSession(item.name)} hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }} style={styles.actionBtn}>
                    <Ionicons name="remove-circle-outline" size={20} color={colors.textDim} />
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => deleteSession(item.name)} hitSlop={{ top: 8, bottom: 8, left: 4, right: 4 }} style={styles.actionBtn}>
                    <Ionicons name="close-circle-outline" size={20} color={colors.textDim} />
                  </TouchableOpacity>
                </View>
              </TouchableOpacity>
            )}
          />

        </View>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    flexDirection: 'row-reverse',
  },
  backdrop: {
    flex: 1,
  },
  drawer: {
    width: '88%',
    maxWidth: 380,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.base,
  },
  title: {
    fontSize: fontSizes.lg,
    fontWeight: fontWeights.semibold,
  },
  headerActions: { flexDirection: 'row', gap: spacing.xs },
  closeBtn: {
    minHeight: touchTarget,
    minWidth: touchTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.base,
    minHeight: 52,
    borderRadius: radii.sm,
    marginHorizontal: spacing.sm,
    marginBottom: spacing.xs,
    gap: spacing.sm,
  },
  rowName: {
    flex: 1,
    fontSize: fontSizes.base,
  },
  rowNameActive: {
    fontWeight: fontWeights.semibold,
  },
  rowActions: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  actionBtn: {
    minHeight: touchTarget,
    minWidth: touchTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
});