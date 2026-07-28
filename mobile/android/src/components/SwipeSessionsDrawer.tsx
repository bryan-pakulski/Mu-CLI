import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  FlatList,
  Modal,
  PanResponder,
  RefreshControl,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { sessionsApi, SessionSummary } from '../api/sessions';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { NewSessionSheet } from './NewSessionSheet';

export type SwipeSessionsDrawerProps = {
  visible: boolean;
  onClose: () => void;
  createRequestToken?: number;
};

export function SwipeSessionsDrawer({ visible, onClose, createRequestToken = 0 }: SwipeSessionsDrawerProps) {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const { activeSessionName, setActiveSession, setActiveProviderModel } = useConnectionStore();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const swipeResponder = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_event, gesture) =>
          gesture.dx < -10 && Math.abs(gesture.dx) > Math.abs(gesture.dy) * 1.2,
        onPanResponderRelease: (_event, gesture) => {
          if (gesture.dx < -64) onClose();
        },
      }),
    [onClose],
  );

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const response = await sessionsApi.list();
      setSessions(response.sessions);
    } catch {
      // The disconnected prompt handles connection failures.
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (visible) load();
  }, [load, visible]);

  useEffect(() => {
    if (createRequestToken > 0) setCreateOpen(true);
  }, [createRequestToken]);

  const switchSession = async (session: SessionSummary) => {
    try {
      if (!session.is_loaded) await sessionsApi.load(session.name);
      else await sessionsApi.focus(session.name);
      setActiveSession(session.name);
      onClose();
    } catch (error) {
      Alert.alert('Could not open session', String(error));
    }
  };

  const newSession = () => {
    setCreateOpen(true);
  };

  const sessionCreated = (session: { name: string; provider: string; model: string }) => {
    setActiveSession(session.name);
    setActiveProviderModel(session.provider, session.model);
    setCreateOpen(false);
    onClose();
  };

  const unloadSession = (session: SessionSummary) => {
    Alert.alert('Unload session?', `Unload “${session.name}” from memory?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Unload',
        onPress: async () => {
          await sessionsApi.unload(session.name);
          if (activeSessionName === session.name) {
            setActiveSession(null);
            setActiveProviderModel(null, null);
            onClose();
          }
          load();
        },
      },
    ]);
  };

  const deleteSession = (session: SessionSummary) => {
    Alert.alert('Delete session?', `Permanently delete “${session.name}”?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await sessionsApi.delete(session.name);
          load();
        },
      },
    ]);
  };

  const statusColor = (session: SessionSummary) => {
    if (session.is_busy) return colors.warning;
    if (session.is_loaded) return colors.success;
    return colors.textDim;
  };

  return (
    <>
    <Modal visible={visible && !createOpen} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.overlay}>
        <View
          {...swipeResponder.panHandlers}
          style={[styles.drawer, { backgroundColor: colors.bg, paddingTop: Math.max(insets.top, 16) }]}
        >
          <View style={styles.header}>
            <View>
              <Text style={[styles.title, { color: colors.text }]}>Sessions</Text>
              <Text variant="xs" dim>Swipe left to close</Text>
            </View>
            <TouchableOpacity onPress={onClose} style={[styles.iconButton, { backgroundColor: colors.bgHover }]}>
              <Ionicons name="close" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          <FlatList
            data={sessions}
            keyExtractor={item => item.name}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
            contentContainerStyle={styles.listContent}
            ListEmptyComponent={
              loading ? null : (
                <View style={styles.empty}>
                  <Text variant="sm" dim>No saved sessions</Text>
                </View>
              )
            }
            renderItem={({ item }) => (
              <TouchableOpacity
                onPress={() => switchSession(item)}
                activeOpacity={0.7}
                style={[
                  styles.row,
                  activeSessionName === item.name && { backgroundColor: colors.bgHover },
                ]}
              >
                <View style={[styles.statusDot, { backgroundColor: statusColor(item) }]} />
                <View style={styles.rowCopy}>
                  <Text variant="sm" style={styles.rowName} numberOfLines={1}>{item.name}</Text>
                  <Text variant="xs" dim>{item.is_busy ? 'Running' : item.is_loaded ? 'Loaded' : 'Saved'}</Text>
                </View>
                <TouchableOpacity onPress={() => unloadSession(item)} style={styles.rowAction}>
                  <Ionicons name="remove-circle-outline" size={19} color={colors.textDim} />
                </TouchableOpacity>
                <TouchableOpacity onPress={() => deleteSession(item)} style={styles.rowAction}>
                  <Ionicons name="trash-outline" size={18} color={colors.textDim} />
                </TouchableOpacity>
              </TouchableOpacity>
            )}
          />

          <View style={styles.footer}>
            <TouchableOpacity onPress={newSession} style={[styles.newButton, { backgroundColor: colors.accent }]}>
              <Ionicons name="add" size={21} color={colors.accentText} />
              <Text style={[styles.newButtonText, { color: colors.accentText }]}>New session</Text>
            </TouchableOpacity>
          </View>
        </View>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />
      </View>
    </Modal>
    <NewSessionSheet
      visible={createOpen}
      onClose={() => setCreateOpen(false)}
      onCreated={sessionCreated}
    />
    </>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, flexDirection: 'row' },
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.32)' },
  drawer: { width: '88%', maxWidth: 380, elevation: 10, shadowColor: '#000', shadowOpacity: 0.16, shadowRadius: 24, shadowOffset: { width: 8, height: 0 } },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 18, paddingVertical: 16 },
  title: { fontSize: 22, fontWeight: '700', letterSpacing: -0.4 },
  iconButton: { width: 40, height: 40, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  listContent: { paddingHorizontal: 8, paddingBottom: 12 },
  empty: { padding: 28, alignItems: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 62, borderRadius: 15, paddingHorizontal: 12, marginBottom: 4 },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 11 },
  rowCopy: { flex: 1 },
  rowName: { fontWeight: '600' },
  rowAction: { width: 40, height: 44, alignItems: 'center', justifyContent: 'center' },
  footer: { padding: 16 },
  newButton: { minHeight: 48, borderRadius: 15, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7 },
  newButtonText: { fontSize: 14, fontWeight: '700' },
});
