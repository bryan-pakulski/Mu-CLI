import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  FlatList,
  PanResponder,
  RefreshControl,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { describeSessionLoadError, formatSessionLoadProblem, sessionsApi, SessionSummary } from '../api/sessions';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { NewSessionSheet } from './NewSessionSheet';
import { SafeAreaModal } from './SafeAreaModal';

export type SwipeSessionsDrawerProps = {
  visible: boolean;
  onClose: () => void;
  createRequestToken?: number;
};

export function SwipeSessionsDrawer({ visible, onClose, createRequestToken = 0 }: SwipeSessionsDrawerProps) {
  const { colors } = useTheme();
  const { activeSessionName, setActiveSession, setActiveProviderModel } = useConnectionStore();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [switchingName, setSwitchingName] = useState<string | null>(null);

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
      const response = await sessionsApi.list({ timeoutMs: 8_000 });
      setSessions(response.sessions);
      setLoadError(null);
    } catch (error) {
      setLoadError(String(error));
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
    if (switchingName) return;
    setSwitchingName(session.name);
    // Release the native modal immediately. Loading/focusing continues in the
    // background and cannot hold the navigation surface hostage.
    onClose();
    try {
      if (!session.is_loaded) {
        await sessionsApi.load(session.name, undefined, undefined, { timeoutMs: 30_000 });
      } else {
        await sessionsApi.focus(session.name, { timeoutMs: 8_000 });
      }
      setActiveSession(session.name);
    } catch (error) {
      const problem = describeSessionLoadError(error);
      Alert.alert(problem.title, formatSessionLoadProblem(problem));
    } finally {
      setSwitchingName(null);
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

  const typeIcon = (session: SessionSummary): keyof typeof Ionicons.glyphMap => {
    const t = session.session_type;
    if (t === 'container') return 'cube-outline';
    if (t === 'chat') return 'chatbubble-ellipses-outline';
    return 'folder-open-outline';
  };

  return (
    <>
    <SafeAreaModal visible={visible && !createOpen} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View
          {...swipeResponder.panHandlers}
          style={[styles.drawer, { backgroundColor: colors.bg, paddingTop: 16 }]}
        >
          <View style={styles.header}>
            <View>
              <Text style={[styles.title, { color: colors.text }]}>Sessions</Text>
              <Text variant="xs" dim>Tap a row to open it.</Text>
            </View>
            <View style={styles.headerActions}>
              <TouchableOpacity onPress={newSession} style={[styles.iconButton, { backgroundColor: colors.bgHover }]} accessibilityLabel="Create session">
                <Ionicons name="add" size={20} color={colors.accent} />
              </TouchableOpacity>
              <TouchableOpacity onPress={onClose} style={[styles.iconButton, { backgroundColor: colors.bgHover }]} accessibilityLabel="Close sessions">
                <Ionicons name="close" size={20} color={colors.text} />
              </TouchableOpacity>
            </View>
          </View>

          <FlatList
            data={sessions}
            keyExtractor={item => item.name}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
            contentContainerStyle={styles.listContent}
            ListEmptyComponent={
              loading ? null : (
                <View style={styles.empty}>
                  <Text variant="sm" dim>{loadError || 'No saved sessions'}</Text>
                  {loadError ? (
                    <TouchableOpacity onPress={load} style={{ marginTop: 12 }}>
                      <Text variant="sm" style={{ color: colors.accent, fontWeight: '600' }}>Retry</Text>
                    </TouchableOpacity>
                  ) : null}
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
                <View style={[styles.typeIconWrap, { backgroundColor: colors.bgHover }]}>
                  <Ionicons name={typeIcon(item)} size={17} color={statusColor(item)} />
                </View>
                <View style={styles.rowCopy}>
                  <Text variant="sm" style={styles.rowName} numberOfLines={1}>{item.name}</Text>
                  <Text variant="xs" dim>{switchingName === item.name ? 'Opening…' : item.is_busy ? 'Running' : item.is_loaded ? 'Loaded' : 'Saved'}{item.session_type ? ` · ${item.session_type}` : ''}</Text>
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

        </View>
        <TouchableOpacity style={styles.backdrop} onPress={onClose} activeOpacity={1} />
      </View>
    </SafeAreaModal>
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
  headerActions: { flexDirection: 'row', gap: 7 },
  title: { fontSize: 22, fontWeight: '700', letterSpacing: -0.4 },
  iconButton: { width: 40, height: 40, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  listContent: { paddingHorizontal: 8, paddingBottom: 12 },
  empty: { padding: 28, alignItems: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', minHeight: 62, borderRadius: 15, paddingHorizontal: 12, marginBottom: 4 },
  statusDot: { width: 8, height: 8, borderRadius: 4, marginRight: 11 },
  typeIconWrap: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  rowCopy: { flex: 1 },
  rowName: { fontWeight: '600' },
  rowAction: { width: 40, height: 44, alignItems: 'center', justifyContent: 'center' },
});
