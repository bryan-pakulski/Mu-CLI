import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, PanResponder, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { describeSessionLoadError, formatSessionLoadProblem, sessionsApi, type SessionSummary } from '../api/sessions';
import { threadsApi, type ThreadStatus, type ThreadSummary } from '../api/threads';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { SessionStatusBadge, sortSessions } from './SessionStatusBadge';
import { NewSessionSheet } from './NewSessionSheet';
import { NewThreadSheet } from './NewThreadSheet';
import { fontSizes, fontWeights, radii, spacing, touchTarget, tracking } from '../theme/tokens';
import { ModalFrame, Pressable, Text, Icon, type IconName } from '../ui';
import { GlassBackdrop, useGlass } from '../ui/Glass';

export type SwipeSessionsDrawerProps = {
  visible: boolean;
  onClose: () => void;
  createRequestToken?: number;
};

const ACTIVE_THREAD_STATUSES: ThreadStatus[] = ['running', 'waiting_peer', 'awaiting_approval'];

function threadStatusLabel(status: ThreadStatus): string {
  return status.replace(/_/g, ' ');
}

export function SwipeSessionsDrawer({ visible, onClose, createRequestToken = 0 }: SwipeSessionsDrawerProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const { activeSessionName, setActiveSession, setActiveProviderModel } = useConnectionStore();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [createSessionOpen, setCreateSessionOpen] = useState(false);
  const [createThreadOpen, setCreateThreadOpen] = useState(false);
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

  const load = useCallback(async (showLoading = true) => {
    try {
      if (showLoading) setLoading(true);
      const currentSession = useConnectionStore.getState().activeSessionName;
      const [sessionResponse, threadResponse] = await Promise.all([
        sessionsApi.list({ timeoutMs: 8_000 }),
        currentSession
          ? threadsApi.list(currentSession, { timeoutMs: 8_000 }).catch(() => null)
          : Promise.resolve(null),
      ]);
      setSessions(sessionResponse.sessions);
      setThreads(threadResponse?.threads || []);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) return undefined;
    void load();
    const poll = setInterval(() => void load(false), 3_000);
    return () => clearInterval(poll);
  }, [activeSessionName, load, visible]);

  useEffect(() => {
    if (createRequestToken > 0) setCreateSessionOpen(true);
  }, [createRequestToken]);

  const hasThreadGroup = threads.length > 1;

  const switchSession = async (name: string) => {
    if (switchingName || name === activeSessionName) {
      if (name === activeSessionName) onClose();
      return;
    }
    setSwitchingName(name);
    onClose();
    try {
      const session = sessions.find(item => item.name === name);
      if (!session?.is_loaded) {
        await sessionsApi.load(name, undefined, undefined, { timeoutMs: 30_000 });
      } else {
        await sessionsApi.focus(name, { timeoutMs: 8_000 });
      }
      setActiveSession(name);
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
    setCreateSessionOpen(false);
    onClose();
  };

  const threadCreated = (thread: { session_name: string }) => {
    setActiveSession(thread.session_name);
    setCreateThreadOpen(false);
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

  const deleteThread = (thread: ThreadSummary) => {
    if (!activeSessionName || thread.session_name === activeSessionName) return;
    Alert.alert('Delete thread?', `Permanently delete “${thread.title || thread.session_name}” and its conversation?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            const session = sessions.find(item => item.name === thread.session_name);
            if (session?.is_loaded) await sessionsApi.unload(thread.session_name);
            await threadsApi.remove(thread.thread_id, activeSessionName, { timeoutMs: 12_000 });
            void load();
          } catch (cause) {
            Alert.alert('Thread could not be deleted', cause instanceof Error ? cause.message : String(cause));
          }
        },
      },
    ]);
  };

  const threadStatusColor = (status: ThreadStatus) => {
    if (status === 'error') return colors.danger.ink;
    if (status === 'interrupted') return colors.attention.ink;
    if (ACTIVE_THREAD_STATUSES.includes(status)) return colors.accent.ink;
    return colors.ink3;
  };

  const typeIcon = (session: SessionSummary): IconName => {
    if (session.session_type === 'container') return 'box';
    if (session.session_type === 'chat') return 'message-circle';
    return 'folder-open';
  };

  const drawerVisible = visible && !createSessionOpen && !createThreadOpen;

  return (
    <>
      <ModalFrame statusBarTranslucent={false} visible={drawerVisible} transparent animationType="fade" onRequestClose={onClose}>
        <View style={styles.overlay}>
          <View
            {...swipeResponder.panHandlers}
            style={[
              styles.drawer,
              // Side panel = level-1 glass chrome (G§3.3) with a neutral 1px edge.
              {
                borderWidth: 0,
                borderRightWidth: 1,
                backgroundColor: 'transparent',
                overflow: 'hidden',
                borderRightColor: colors.hairline,
                paddingTop: spacing.base,
              },
            ]}
          >
            <GlassBackdrop level={1} testID="sessions-drawer-glass" />
            <View style={[styles.header, { borderBottomColor: colors.hairline }] }>
              <View style={styles.headerCopy}>
                <Text style={[styles.title, { color: colors.ink }]}>Threads</Text>
                <Text variant="xs" dim>Agent conversations and sessions</Text>
              </View>
              <View style={styles.headerActions}>
                <Pressable
                  onPress={() => activeSessionName ? setCreateThreadOpen(true) : setCreateSessionOpen(true)}
                  style={styles.iconButton}
                  accessibilityLabel={activeSessionName ? 'Create thread' : 'Create session'}
                >
                  <Icon name="plus" size="md" color={colors.ink3} />
                </Pressable>
                <Pressable onPress={onClose} style={styles.iconButton} accessibilityLabel="Close threads">
                  <Icon name="x" size="md" color={colors.ink3} />
                </Pressable>
              </View>
            </View>

            <ScrollView
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
              contentContainerStyle={styles.listContent}
            >
              {activeSessionName ? (
                <Pressable
                  onPress={() => setCreateThreadOpen(true)}
                  activeOpacity={0.72}
                  style={[styles.primaryAction, { backgroundColor: colors.accent.wash }]}
                >
                  <Icon name="git-branch" size="md" color={colors.accent.ink} />
                  <Text variant="sm" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>New thread</Text>
                  <Icon name="plus" size="md" color={colors.accent.ink} />
                </Pressable>
              ) : null}

              {hasThreadGroup ? (
                <View style={styles.section}>
                  <SectionTitle label="THREAD GROUP" count={threads.length} />
                  {threads.map(thread => (
                    <Pressable
                      key={thread.thread_id}
                      onPress={() => void switchSession(thread.session_name)}
                      activeOpacity={0.7}
                      style={[
                        styles.row,
                        activeSessionName === thread.session_name && glass.activePill,
                      ]}
                    >
                      <View style={styles.typeIconWrap}>
                        <View style={[styles.threadDot, { backgroundColor: threadStatusColor(thread.status) }]} />
                      </View>
                      <View style={styles.rowCopy}>
                        <Text variant="sm" style={styles.rowName} numberOfLines={1}>
                          {thread.title || thread.session_name}
                        </Text>
                        <Text variant="xs" dim numberOfLines={2}>
                          {switchingName === thread.session_name ? 'Opening…' : threadStatusLabel(thread.status)}
                          {thread.current_goal ? ` · ${thread.current_goal}` : ''}
                        </Text>
                      </View>
                      {thread.unread_count > 0 ? (
                        <View style={[styles.unreadBadge, { backgroundColor: colors.accent.wash }] }>
                          <Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold, fontVariant: ['tabular-nums'] }}>{thread.unread_count}</Text>
                        </View>
                      ) : null}
                      {activeSessionName === thread.session_name ? (
                        <Icon name="check" size="sm" color={colors.accent.ink} />
                      ) : !ACTIVE_THREAD_STATUSES.includes(thread.status) ? (
                        <Pressable
                          onPress={() => deleteThread(thread)}
                          style={styles.rowAction}
                          accessibilityLabel={`Delete thread ${thread.title || thread.session_name}`}
                        >
                          <Icon name="trash-2" size="sm" color={colors.ink3} />
                        </Pressable>
                      ) : (
                        <Icon name="chevron-right" size="sm" color={colors.ink3} />
                      )}
                    </Pressable>
                  ))}
                </View>
              ) : null}

              <View style={styles.section}>
                <SectionTitle label="SESSIONS" count={sessions.length} />
                {!loading && sessions.length === 0 ? (
                  <View style={styles.empty}>
                    <Text variant="sm" dim>{loadError || 'No saved sessions'}</Text>
                  </View>
                ) : null}
                {loadError ? (
                  <Pressable onPress={() => void load()} style={styles.retryAction}>
                    <Text variant="sm" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>Retry</Text>
                  </Pressable>
                ) : null}
                {sortSessions(sessions, activeSessionName).map(session => (
                  <Pressable
                    key={session.name}
                    onPress={() => void switchSession(session.name)}
                    activeOpacity={0.7}
                    style={[
                      styles.row,
                      activeSessionName === session.name && glass.activePill,
                    ]}
                  >
                    <View style={styles.typeIconWrap}>
                      <Icon name={typeIcon(session)} size="sm" color={activeSessionName === session.name ? colors.accent.ink : session.is_loaded ? colors.ink2 : colors.ink3} />
                    </View>
                    <View style={styles.rowCopy}>
                      <Text variant="sm" style={styles.rowName} numberOfLines={1}>{session.name}</Text>
                      <Text variant="xs" dim>
                        {switchingName === session.name ? 'Opening…' : session.session_type || 'workspace'}
                      </Text>
                    </View>
                    <Pressable onPress={() => unloadSession(session)} style={styles.rowAction} accessibilityLabel={`Unload ${session.name}`}>
                      <Icon name="circle-minus" size="md" color={colors.ink3} />
                    </Pressable>
                    <Pressable onPress={() => deleteSession(session)} style={styles.rowAction} accessibilityLabel={`Delete ${session.name}`}>
                      <Icon name="trash-2" size="sm" color={colors.ink3} />
                    </Pressable>
                  </Pressable>
                ))}
              </View>

              <Pressable
                onPress={() => setCreateSessionOpen(true)}
                activeOpacity={0.72}
                style={[styles.secondaryAction, { borderColor: colors.hairline }]}
              >
                <Icon name="plus" size="sm" color={colors.ink3} />
                <Text variant="sm" dim style={{ fontWeight: fontWeights.semibold }}>New session</Text>
              </Pressable>
            </ScrollView>
          </View>
          <Pressable accessibilityLabel="Close sessions" style={[styles.backdrop, { backgroundColor: colors.scrim }]} onPress={onClose} activeOpacity={1} />
        </View>
      </ModalFrame>
      <NewSessionSheet
        visible={createSessionOpen}
        onClose={() => setCreateSessionOpen(false)}
        onCreated={sessionCreated}
      />
      <NewThreadSheet
        visible={createThreadOpen}
        parentSessionName={activeSessionName}
        onClose={() => setCreateThreadOpen(false)}
        onCreated={threadCreated}
      />
    </>
  );
}

function SectionTitle({ label, count }: { label: string; count: number }) {
  const { colors } = useTheme();
  return (
    <View style={styles.sectionTitleRow}>
      <Text variant="xs" style={[styles.sectionTitle, { color: colors.ink3 }]}>{label}</Text>
      <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{count}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, flexDirection: 'row' },
  backdrop: { flex: 1 },
  drawer: {
    width: '88%',
    maxWidth: 380,
    borderRightWidth: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.base,
    borderBottomWidth: 1,
  },
  headerCopy: { flex: 1 },
  headerActions: { flexDirection: 'row', gap: 2 },
  title: { fontSize: fontSizes.lg, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  iconButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  listContent: { paddingHorizontal: spacing.md, paddingBottom: spacing.lg },
  primaryAction: {
    minHeight: touchTarget,
    marginHorizontal: spacing.xs,
    marginTop: spacing.md,
    paddingHorizontal: spacing.md,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  secondaryAction: {
    minHeight: touchTarget,
    marginHorizontal: spacing.xs,
    marginTop: spacing.base,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  section: { marginTop: spacing.base },
  sectionTitleRow: {
    minHeight: 28,
    paddingHorizontal: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  sectionTitle: { fontWeight: fontWeights.semibold },
  empty: { paddingHorizontal: spacing.sm, paddingVertical: spacing.base, alignItems: 'center' },
  retryAction: { alignSelf: 'center', minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 58,
    paddingHorizontal: spacing.sm,
    borderRadius: radii.sm,
    // Same box as the active glass-strong pill, so selection never shifts layout.
    borderWidth: 1,
    borderColor: 'transparent',
  },
  typeIconWrap: { width: 28, alignItems: 'flex-start', justifyContent: 'center', marginRight: spacing.sm },
  threadDot: { width: 8, height: 8, borderRadius: radii.pill },
  rowCopy: { flex: 1, paddingVertical: spacing.sm },
  rowName: { fontWeight: fontWeights.semibold },
  rowAction: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  unreadBadge: {
    minWidth: 24,
    height: 24,
    paddingHorizontal: spacing.sm,
    marginHorizontal: spacing.sm,
    borderRadius: radii.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
