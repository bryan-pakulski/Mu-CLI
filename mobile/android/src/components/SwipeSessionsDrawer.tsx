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
    if (visible) void load();
  }, [load, visible]);

  useEffect(() => {
    if (createRequestToken > 0) setCreateOpen(true);
  }, [createRequestToken]);

  const switchSession = async (session: SessionSummary) => {
    if (switchingName) return;
    setSwitchingName(session.name);
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
          void load();
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
          void load();
        },
      },
    ]);
  };

  const statusColor = (session: SessionSummary) => {
    if (session.is_busy) return colors.accent;
    if (session.is_loaded) return colors.textSoft;
    return colors.textDim;
  };

  const typeIcon = (session: SessionSummary): keyof typeof Ionicons.glyphMap => {
    if (session.session_type === 'container') return 'cube-outline';
    if (session.session_type === 'chat') return 'chatbubble-ellipses-outline';
    return 'folder-open-outline';
  };

  return (
    <>
      <SafeAreaModal visible={visible && !createOpen} transparent animationType="fade" onRequestClose={onClose}>
        <View style={styles.overlay}>
          <View
            {...swipeResponder.panHandlers}
            style={[
              styles.drawer,
              {
                backgroundColor: colors.glassStrong,
                borderRightColor: colors.hairline,
                paddingTop: 16,
              },
            ]}
          >
            <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
              <View>
                <Text style={[styles.title, { color: colors.text }]}>Sessions</Text>
                <Text variant="xs" dim>Saved and running work</Text>
              </View>
              <View style={styles.headerActions}>
                <TouchableOpacity onPress={() => setCreateOpen(true)} style={styles.iconButton} accessibilityLabel="Create session">
                  <Ionicons name="add" size={20} color={colors.textDim} />
                </TouchableOpacity>
                <TouchableOpacity onPress={onClose} style={styles.iconButton} accessibilityLabel="Close sessions">
                  <Ionicons name="close" size={20} color={colors.textDim} />
                </TouchableOpacity>
              </View>
            </View>

            <FlatList
              data={sessions}
              keyExtractor={item => item.name}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
              contentContainerStyle={styles.listContent}
              ListEmptyComponent={
                loading ? null : (
                  <View style={styles.empty}>
                    <Text variant="sm" dim>{loadError || 'No saved sessions'}</Text>
                    {loadError ? (
                      <TouchableOpacity onPress={() => void load()} style={{ marginTop: 12 }}>
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
                    { borderBottomColor: colors.hairline },
                    activeSessionName === item.name && { backgroundColor: colors.bgHover },
                  ]}
                >
                  <View style={styles.typeIconWrap}>
                    <Ionicons name={typeIcon(item)} size={17} color={statusColor(item)} />
                  </View>
                  <View style={styles.rowCopy}>
                    <Text variant="sm" style={styles.rowName} numberOfLines={1}>{item.name}</Text>
                    <Text variant="xs" dim>
                      {switchingName === item.name ? 'Opening…' : item.is_busy ? 'Working' : item.is_loaded ? 'Loaded' : 'Saved'}
                      {item.session_type ? ` · ${item.session_type}` : ''}
                    </Text>
                  </View>
                  <TouchableOpacity onPress={() => unloadSession(item)} style={styles.rowAction} accessibilityLabel={`Unload ${item.name}`}>
                    <Ionicons name="remove-circle-outline" size={18} color={colors.textDim} />
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => deleteSession(item)} style={styles.rowAction} accessibilityLabel={`Delete ${item.name}`}>
                    <Ionicons name="trash-outline" size={17} color={colors.textDim} />
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
  backdrop: { flex: 1, backgroundColor: 'rgba(5,10,16,0.42)' },
  drawer: {
    width: '88%',
    maxWidth: 380,
    borderRightWidth: StyleSheet.hairlineWidth,
    elevation: 4,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 18,
    shadowOffset: { width: 6, height: 0 },
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 18,
    paddingVertical: 15,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  headerActions: { flexDirection: 'row', gap: 2 },
  title: { fontSize: 19, fontWeight: '600', letterSpacing: -0.25 },
  iconButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  listContent: { paddingHorizontal: 10, paddingBottom: 12 },
  empty: { padding: 28, alignItems: 'center' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 56,
    paddingHorizontal: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  typeIconWrap: { width: 28, alignItems: 'flex-start', justifyContent: 'center', marginRight: 8 },
  rowCopy: { flex: 1 },
  rowName: { fontWeight: '600' },
  rowAction: { width: 38, height: 44, alignItems: 'center', justifyContent: 'center' },
});
