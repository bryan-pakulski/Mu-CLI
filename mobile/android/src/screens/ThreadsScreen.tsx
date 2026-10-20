// Agent conversations (web threads_panel.html): the open session's thread group — status,
// current goal, claimed paths and unread messages — and the coordination activity between them.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { useFocusEffect } from '@react-navigation/native';
import { describeSessionLoadError, formatSessionLoadProblem, sessionsApi } from '../api/sessions';
import {
  threadsApi,
  type CreateThreadResponse,
  type ThreadActivityEvent,
  type ThreadStatus,
  type ThreadSummary,
} from '../api/threads';
import { NewThreadSheet } from '../components/NewThreadSheet';
import type { RootStackParamList } from '../navigation/AppNavigator';
import { useConnectionStore } from '../store/connection';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Banner, Chip, CountBadge, IconButton, PrimaryPill, Row, Text, useServerActions, useToast } from '../ui';

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
  const toast = useToast();
  const actions = useServerActions();
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

  const switchThread = async (thread: ThreadSummary) => {
    if (switchingName || thread.session_name === activeSessionName) {
      if (thread.session_name === activeSessionName) navigation?.navigate('Tabs', { screen: 'Chat' });
      return;
    }
    setSwitchingName(thread.session_name);
    try {
      // The load route is idempotent for already-loaded sessions and focuses
      // the requested session, so one call covers both saved and live peers.
      await sessionsApi.load(thread.session_name);
      setActiveSession(thread.session_name);
      navigation?.navigate('Tabs', { screen: 'Chat' });
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
    navigation?.navigate('Tabs', { screen: 'Chat' });
  };

  const deleteThread = async (thread: ThreadSummary) => {
    if (thread.thread_id === currentThreadId) {
      toast('Switch to a different thread before deleting this one.', 'attention');
      return;
    }
    const name = thread.title || thread.session_name;
    if (!(await actions.ask({ id: 'delete', label: 'Delete', danger: true, confirm: `Permanently delete "${name}" and its history?` }))) return;
    try {
      const list = await sessionsApi.list({ timeoutMs: 8_000 });
      const session = list.sessions.find(item => item.name === thread.session_name);
      if (session?.is_loaded) await sessionsApi.unload(thread.session_name);
      await threadsApi.remove(thread.thread_id, activeSessionName || undefined, { timeoutMs: 12_000 });
      await load(false);
    } catch (cause) {
      toast(`Couldn't delete: ${cause instanceof Error ? cause.message : String(cause)}`, 'danger');
    }
  };

  const running = threads.filter(t => ACTIVE_STATUSES.includes(t.status)).length;
  const unread = threads.reduce((sum, t) => sum + (t.unread_count || 0), 0);
  return (
    <PanelPage testID="threads-screen" title="Agent conversations"
      state={[
        { text: groupId || activeSessionName || 'No session', tone: 'quiet' },
        threads.length ? { text: `${threads.length} threads` } : null,
        running ? { text: `${running} running` } : null,
        unread ? { text: `${unread} new`, tone: 'attention' } : null,
      ]}
      loading={loading && threads.length === 0 && !!activeSessionName}
      empty={!activeSessionName ? {
        title: 'No active session', message: 'Load a session before creating or reviewing agent threads.',
        actionLabel: 'Back to chat', onAction: () => navigation?.navigate('Tabs', { screen: 'Chat' }),
      } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>
        {activeSessionName ? <PrimaryPill label="New thread" icon="plus" onPress={() => setCreateOpen(true)} testID="create-thread" /> : null}
        {actions.element}
        <NewThreadSheet visible={createOpen} parentSessionName={activeSessionName} onClose={() => setCreateOpen(false)} onCreated={threadCreated} />
      </>}>
      {error ? (
        <Banner tone="danger" title="Couldn't update the threads" action={{ label: 'Retry', onPress: () => void load() }} testID="threads-error">{error}</Banner>
      ) : null}
      <PanelSection title="Thread group" count={threads.length} empty="No threads in this group yet.">
        {threads.map(thread => {
          const current = thread.thread_id === currentThreadId;
          const name = thread.title || thread.session_name;
          return (
            <Row key={thread.thread_id} testID={`thread-row-${thread.thread_id}`} status={threadStatusSign(thread.status)} title={name}
              when={statusLabel(thread.status)} selected={current}
              accessibilityLabel={`${name}, ${statusLabel(thread.status)}${current ? ', open' : ''}${thread.unread_count ? `, ${thread.unread_count} new` : ''}`}
              onPress={() => void switchThread(thread)} onLongPress={() => void deleteThread(thread)}
              sub={(
                <View style={styles.sub}>
                  <Text variant="caption" tone="ink3" numberOfLines={2}>{switchingName === thread.session_name ? 'Opening…' : thread.current_goal || thread.session_name}</Text>
                  {thread.claimed_paths?.length ? <Text variant="monoSm" tone="ink2" numberOfLines={2}>owns {thread.claimed_paths.join(', ')}</Text> : null}
                </View>
              )}
              actions={current || thread.unread_count > 0 || !ACTIVE_STATUSES.includes(thread.status) ? (
                <View style={styles.actions}>
                  {thread.unread_count > 0 ? <CountBadge tone="accent">{thread.unread_count}</CountBadge> : null}
                  {current ? <Chip variant="meta" label="open" /> : !ACTIVE_STATUSES.includes(thread.status) ? (
                    <IconButton icon="trash-2" label={`Delete thread ${name}`} onPress={() => void deleteThread(thread)} />
                  ) : null}
                </View>
              ) : undefined} />
          );
        })}
      </PanelSection>
      <PanelSection title="Coordination activity" count={activity.length}
        empty={loading ? undefined : 'Messages, path claims, status changes and conflicts will appear here.'}>
        {activity.slice().reverse().map(event => {
          const detail = eventDetail(event);
          const actor = event.actor_title || event.actor_thread_id || 'system';
          const target = event.target_title || event.target_thread_id;
          const conflict = event.kind === 'thread_conflict' || event.kind === 'thread_claim_override';
          return (
            <Row key={event.event_id} status={conflict ? 'failed' : undefined} title={eventTitle(event.kind)} when={formatTime(event.created_at)}
              accessibilityLabel={`${conflict ? 'Conflict: ' : ''}${eventTitle(event.kind)}`}
              sub={[`${actor}${target ? ` → ${target}` : ''}`, detail].filter(Boolean).join(' · ')} />
          );
        })}
      </PanelSection>
    </PanelPage>
  );
}

/** Thread status → the shared status vocabulary. */
export function threadStatusSign(status: ThreadStatus): string {
  if (status === 'error') return 'failed';
  if (status === 'awaiting_approval' || status === 'interrupted') return 'waiting';
  if (ACTIVE_STATUSES.includes(status)) return 'active';
  return 'pending';
}

const styles = StyleSheet.create({
  sub: { gap: spacing.xs },
  actions: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
