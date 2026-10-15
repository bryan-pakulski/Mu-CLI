import React, { useCallback, useState } from 'react';
import {
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import { describeSessionLoadError, formatSessionLoadProblem, sessionsApi } from '../api/sessions';
import {
  threadsApi,
  type CreateThreadResponse,
  type ThreadActivityEvent,
  type ThreadStatus,
  type ThreadSummary,
} from '../api/threads';
import { EmptyState, Skeleton, Text } from '../components';
import { NewThreadSheet } from '../components/NewThreadSheet';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, radii, spacing, touchTarget } from '../theme/tokens';

export type ThreadsScreenProps = NativeStackScreenProps<RootStackParamList, 'Threads'>;

const ACTIVE_STATUSES: ThreadStatus[] = ['running', 'waiting_peer', 'awaiting_approval'];

function statusLabel(status: ThreadStatus): string {
  return status.replace(/_/g, ' ');
}

function eventTitle(kind: string): string {
  const labels: Record<string, string> = {
    thread_registered: 'Thread joined the group',
    thread_created: 'Thread created',
    thread_status: 'Status changed',
    thread_message: 'Message sent',
    thread_message_acknowledged: 'Message acknowledged',
    thread_claim: 'Paths claimed',
    thread_claim_released: 'Paths released',
    thread_claim_handoff: 'Path ownership handed off',
    thread_conflict: 'Path conflict detected',
    thread_claim_override: 'Path ownership overridden',
    thread_wake_started: 'Thread resumed',
    thread_wake_completed: 'Thread wake completed',
    thread_wake_failed: 'Thread wake failed',
  };
  return labels[kind] || kind.replace(/^thread_/, '').replace(/_/g, ' ');
}

function eventDetail(event: ThreadActivityEvent): string {
  const payload = event.payload || {};
  const preferred = [
    payload.content,
    payload.goal,
    payload.path,
    payload.rationale,
    payload.status,
    payload.title,
    payload.session_name,
  ].find(value => typeof value === 'string' && value.trim());
  if (typeof preferred === 'string') return preferred;
  const paths = payload.paths || payload.related_paths || payload.claimed_paths;
  if (Array.isArray(paths) && paths.length) return paths.map(String).join(', ');
  return '';
}

function formatTime(timestamp: number): string {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString();
}

export function ThreadsScreen({ navigation }: Partial<ThreadsScreenProps> = {}) {
  const { colors, spacing } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const setActiveSession = useConnectionStore(state => state.setActiveSession);
  const [groupId, setGroupId] = useState('');
  const [currentThreadId, setCurrentThreadId] = useState('');
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activity, setActivity] = useState<ThreadActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switchingName, setSwitchingName] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async (showLoading = true) => {
    if (!activeSessionName) {
      setThreads([]);
      setActivity([]);
      setLoading(false);
      setRefreshing(false);
      return;
    }
    if (showLoading) setLoading(true);
    try {
      const [roster, events] = await Promise.all([
        threadsApi.list(activeSessionName, { timeoutMs: 8_000 }),
        threadsApi.activity(activeSessionName, { afterId: 0, limit: 300, timeoutMs: 8_000 }),
      ]);
      setGroupId(roster.thread_group_id);
      setCurrentThreadId(roster.current_thread_id);
      setThreads(roster.threads || []);
      setActivity(events?.events || []);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [activeSessionName]);

  useFocusEffect(useCallback(() => {
    void load();
    const poll = setInterval(() => void load(false), 3_000);
    return () => clearInterval(poll);
  }, [load]));

  const statusColor = (status: ThreadStatus) => {
    if (status === 'error') return colors.danger.ink;
    if (status === 'interrupted') return colors.attention.ink;
    if (ACTIVE_STATUSES.includes(status)) return colors.accent.ink;
    return colors.ink3;
  };

  const switchThread = async (thread: ThreadSummary) => {
    if (switchingName || thread.session_name === activeSessionName) {
      if (thread.session_name === activeSessionName) navigation?.navigate('Chat');
      return;
    }
    setSwitchingName(thread.session_name);
    try {
      // The load route is idempotent for already-loaded sessions and focuses
      // the requested session, so one call covers both saved and live peers.
      await sessionsApi.load(thread.session_name);
      setActiveSession(thread.session_name);
      navigation?.navigate('Chat');
    } catch (cause) {
      const problem = describeSessionLoadError(cause);
      setError(`${problem.title}: ${formatSessionLoadProblem(problem)}`);
    } finally {
      setSwitchingName(null);
    }
  };

  const threadCreated = (thread: CreateThreadResponse) => {
    setActiveSession(thread.session_name);
    setCreateOpen(false);
    navigation?.navigate('Chat');
  };

  const deleteThread = (thread: ThreadSummary) => {
    if (thread.thread_id === currentThreadId) {
      Alert.alert('Not allowed', 'Switch to a different thread before deleting this one.');
      return;
    }
    Alert.alert(
      'Delete thread?',
      `Permanently delete "${thread.title || thread.session_name}" and its history?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => {
            void (async () => {
              try {
                const list = await sessionsApi.list({ timeoutMs: 8_000 });
                const session = list.sessions.find(item => item.name === thread.session_name);
                if (session?.is_loaded) await sessionsApi.unload(thread.session_name);
                await threadsApi.remove(thread.thread_id, activeSessionName || undefined, { timeoutMs: 12_000 });
                await load(false);
              } catch (cause) {
                Alert.alert('Delete failed', cause instanceof Error ? cause.message : String(cause));
              }
            })();
          },
        },
      ],
    );
  };

  if (!activeSessionName) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }] }>
        <EmptyState
          title="No active session"
          message="Load a session before creating or reviewing agent threads."
          icon="git-branch-outline"
          actionLabel="Back to chat"
          onAction={() => navigation?.navigate('Chat')}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }] }>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}
        contentContainerStyle={[styles.content, { padding: spacing.base }]}
      >
        <View style={styles.pageHeader}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xs" style={[styles.kicker, { color: colors.ink3 }]}>Agent conversations</Text>
            <Text variant="sm" dim numberOfLines={1}>{groupId || activeSessionName}</Text>
          </View>
          <TouchableOpacity
            testID="create-thread"
            onPress={() => setCreateOpen(true)}
            activeOpacity={0.72}
            style={[styles.createButton, { backgroundColor: colors.accent.ink }]}
            accessibilityLabel="Create thread"
          >
            <Ionicons name="add" size={18} color={colors.onInk} />
            <Text variant="sm" style={{ color: colors.onInk, fontWeight: fontWeights.medium }}>Thread</Text>
          </TouchableOpacity>
        </View>

        {error ? (
          <TouchableOpacity onPress={() => void load()} style={[styles.errorBanner, { backgroundColor: colors.danger.wash }]}>
            <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
            <Text variant="xs" style={[styles.errorCopy, { color: colors.danger.ink }]}>{error}</Text>
            <Text variant="xs" style={{ color: colors.danger.ink, fontWeight: fontWeights.semibold }}>Retry</Text>
          </TouchableOpacity>
        ) : null}

        <View style={styles.sectionHeader}>
          <Text variant="xs" style={[styles.sectionTitle, { color: colors.ink3 }]}>Thread group</Text>
          <Text variant="xs" dim style={styles.num}>{threads.length}</Text>
        </View>
        {loading && threads.length === 0 ? (
          <View style={styles.loadingBlock}>
            <Skeleton height={58} style={styles.skeleton} />
            <Skeleton height={58} style={styles.skeleton} />
          </View>
        ) : threads.map(thread => (
          <TouchableOpacity
            key={thread.thread_id}
            testID={`thread-row-${thread.thread_id}`}
            onPress={() => void switchThread(thread)}
            onLongPress={() => deleteThread(thread)}
            activeOpacity={0.7}
            style={[
              styles.threadRow,
              thread.thread_id === currentThreadId && { backgroundColor: colors.accent.wash },
            ]}
          >
            <View style={[styles.statusDot, { backgroundColor: statusColor(thread.status) }]} />
            <View style={styles.threadCopy}>
              <View style={styles.threadTitleRow}>
                <Text variant="sm" style={styles.threadTitle} numberOfLines={1}>
                  {thread.title || thread.session_name}
                </Text>
                <Text variant="xs" dim>{statusLabel(thread.status)}</Text>
              </View>
              <Text variant="xs" dim numberOfLines={2}>
                {switchingName === thread.session_name ? 'Opening…' : thread.current_goal || thread.session_name}
              </Text>
              {thread.claimed_paths?.length ? (
                <Text variant="xs" style={[styles.claims, { color: colors.ink2 }]} numberOfLines={2}>
                  owns {thread.claimed_paths.join(', ')}
                </Text>
              ) : null}
            </View>
            {thread.unread_count > 0 ? (
              <View style={[styles.unreadBadge, { backgroundColor: colors.accent.wash }]}>
                <Text variant="xs" style={[styles.num, { color: colors.accent.ink, fontWeight: fontWeights.semibold }]}>{thread.unread_count}</Text>
              </View>
            ) : null}
            {thread.thread_id === currentThreadId ? (
              <Ionicons name="checkmark" size={18} color={colors.accent.ink} />
            ) : !ACTIVE_STATUSES.includes(thread.status) ? (
              <TouchableOpacity
                onPress={() => deleteThread(thread)}
                style={styles.deleteButton}
                accessibilityLabel={`Delete thread ${thread.title || thread.session_name}`}
              >
                <Ionicons name="trash-outline" size={17} color={colors.ink3} />
              </TouchableOpacity>
            ) : (
              <Ionicons name="chevron-forward" size={17} color={colors.ink3} />
            )}
          </TouchableOpacity>
        ))}

        <View style={[styles.sectionHeader, styles.activityHeader]}>
          <Text variant="xs" style={[styles.sectionTitle, { color: colors.ink3 }]}>Coordination activity</Text>
          <Text variant="xs" dim style={styles.num}>{activity.length}</Text>
        </View>
        {!loading && activity.length === 0 ? (
          <View style={styles.activityEmpty}>
            <Ionicons name="pulse-outline" size={22} color={colors.ink3} />
            <Text variant="sm" dim style={styles.activityEmptyText}>
              Messages, path claims, status changes, and conflicts will appear here.
            </Text>
          </View>
        ) : activity.slice().reverse().map(event => {
          const detail = eventDetail(event);
          const actor = event.actor_title || event.actor_thread_id || 'system';
          const target = event.target_title || event.target_thread_id;
          const conflict = event.kind === 'thread_conflict' || event.kind === 'thread_claim_override';
          return (
            <View
              key={event.event_id}
              style={[
                styles.eventRow,
                conflict && { borderLeftColor: colors.danger.ink, borderLeftWidth: 2, paddingLeft: spacing.md },
              ]}
            >
              <View style={styles.eventMeta}>
                <Text variant="xs" dim numberOfLines={1} style={styles.eventActors}>
                  {actor}{target ? ` → ${target}` : ''}
                </Text>
                <Text variant="xs" dim style={styles.num}>{formatTime(event.created_at)}</Text>
              </View>
              <Text variant="sm" style={styles.eventTitle}>{eventTitle(event.kind)}</Text>
              {detail ? <Text variant="xs" dim style={styles.eventDetail}>{detail}</Text> : null}
            </View>
          );
        })}
      </ScrollView>
      <NewThreadSheet
        visible={createOpen}
        parentSessionName={activeSessionName}
        onClose={() => setCreateOpen(false)}
        onCreated={threadCreated}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { paddingBottom: spacing['2xl'] },
  pageHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: spacing.lg },
  pageHeaderCopy: { flex: 1, minWidth: 0 },
  kicker: { fontWeight: fontWeights.semibold, marginBottom: spacing.xs },
  createButton: {
    minHeight: touchTarget,
    paddingHorizontal: spacing.md,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  errorBanner: {
    minHeight: touchTarget,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: spacing.base,
  },
  errorCopy: { flex: 1 },
  sectionHeader: {
    minHeight: 32,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  sectionTitle: { fontWeight: fontWeights.semibold },
  num: { fontVariant: ['tabular-nums'] },
  loadingBlock: { gap: spacing.sm },
  skeleton: { width: '100%' },
  threadRow: {
    minHeight: 66,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.md,
    borderRadius: radii.sm,
    flexDirection: 'row',
    alignItems: 'center',
  },
  statusDot: { width: 8, height: 8, borderRadius: radii.pill, marginRight: spacing.md },
  threadCopy: { flex: 1, minWidth: 0 },
  threadTitleRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: spacing.sm },
  threadTitle: { flex: 1, fontWeight: fontWeights.semibold },
  claims: { marginTop: spacing.xs, fontFamily: fonts.mono },
  unreadBadge: {
    minWidth: 24,
    height: 24,
    paddingHorizontal: spacing.sm,
    marginHorizontal: spacing.sm,
    borderRadius: radii.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  deleteButton: { width: touchTarget, height: touchTarget, alignItems: 'center', justifyContent: 'center' },
  activityHeader: { marginTop: spacing.xl },
  activityEmpty: { paddingVertical: spacing.xl, paddingHorizontal: spacing.lg, alignItems: 'center' },
  activityEmptyText: { marginTop: spacing.sm, textAlign: 'center', maxWidth: 300 },
  eventRow: { paddingVertical: spacing.md },
  eventMeta: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  eventActors: { flex: 1 },
  eventTitle: { fontWeight: fontWeights.semibold, marginTop: spacing.xs },
  eventDetail: { marginTop: spacing.xs },
});
