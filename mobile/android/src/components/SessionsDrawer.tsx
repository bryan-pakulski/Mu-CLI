import React, { useState, useCallback, useEffect } from 'react';
import { View, StyleSheet, TouchableOpacity, FlatList, RefreshControl, Alert } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { sessionsApi, SessionSummary } from '../api/sessions';
import { Text } from './Text';
import { SafeAreaModal } from './SafeAreaModal';

export type SessionsDrawerProps = {
  visible: boolean;
  onClose: () => void;
};

export function SessionsDrawer({ visible, onClose }: SessionsDrawerProps) {
  const { colors } = useTheme();
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

  const statusColor = (s: SessionSummary) => {
    if (s.is_busy) return colors.warning;
    if (s.is_loaded) return colors.success;
    return colors.textDim;
  };

  return (
    <SafeAreaModal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />
        <View style={[styles.drawer, { backgroundColor: colors.bg, paddingTop: 16 }]}>
          <View style={[styles.header, { borderBottomColor: colors.border }]}>
            <View><Text style={[styles.title, { color: colors.text }]}>Sessions</Text><Text variant="xs" dim>Tap a row to open it.</Text></View>
            <View style={styles.headerActions}>
              <TouchableOpacity onPress={newSession} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn} accessibilityLabel="Create session">
                <Ionicons name="add" size={22} color={colors.accent} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={styles.closeBtn} accessibilityLabel="Close sessions">
                <Ionicons name="close" size={22} color={colors.textDim} />
              </TouchableOpacity>
            </View>
          </View>

          <FlatList
            data={sessions}
            keyExtractor={item => item.name}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
            contentContainerStyle={{ paddingVertical: 8 }}
            ListEmptyComponent={
              loading ? null : (
                <View style={{ padding: 24, alignItems: 'center' }}>
                  <Text style={{ color: colors.textDim, fontSize: 14 }}>No sessions</Text>
                </View>
              )
            }
            renderItem={({ item }) => (
              <TouchableOpacity
                onPress={() => switchSession(item.name)}
                style={[
                  styles.row,
                  { borderBottomColor: colors.border },
                  activeSessionName === item.name && { backgroundColor: colors.bgHover },
                ]}
              >
                <View style={[styles.statusDot, { backgroundColor: statusColor(item) }]} />
                <Text
                  style={[styles.rowName, { color: colors.text }]}
                  numberOfLines={1}
                  ellipsizeMode="middle"
                >
                  {item.name}
                </Text>
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
    backgroundColor: 'rgba(0,0,0,0.32)',
  },
  drawer: {
    width: '88%',
    maxWidth: 380,
    shadowColor: '#000',
    shadowOpacity: 0.14,
    shadowRadius: 24,
    shadowOffset: { width: 8, height: 0 },
    elevation: 8,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 16,
    borderBottomWidth: 0,
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
  },
  headerActions: { flexDirection: 'row', gap: 4 },
  closeBtn: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    minHeight: 52,
    borderBottomWidth: 0,
    borderRadius: 14,
    marginHorizontal: 8,
    marginBottom: 4,
    gap: 8,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  rowName: {
    flex: 1,
    fontSize: 14,
  },
  rowActions: {
    flexDirection: 'row',
    gap: 4,
  },
  actionBtn: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
});