// Sessions and threads, one drawer (mobile_parity.md §6 Chat; the web rail's thread and session
// lists). Opens from the left edge or the header's menu button.
//
//   Threads   the open session's agent conversations: ● running, ◆ awaiting input, ✕ error,
//             unread count; delete never touches the current or a running thread
//   Sessions  every saved session: ● working, ◆ waiting for you (server awaiting_input), loaded/at rest;
//             long-press (or ⋯) to unload or delete
// Fleet-chat and job sessions never appear (the server filters them).

import React, { useCallback, useEffect, useState } from 'react';
import { Alert, PanResponder, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { describeSessionLoadError, formatSessionLoadProblem, sessionsApi, type SessionSummary } from '../../api/sessions';
import { threadsApi, type ThreadStatus, type ThreadSummary } from '../../api/threads';
import { useConnectionStore } from '../../store/connection';
import { usePromptStore } from '../../store/prompts';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { IconButton } from '../../ui/Button';
import { Chip } from '../../ui/Chip';
import { GlassBackdrop } from '../../ui/Glass';
import { ActionSheet, BoardSection, Row, type MenuItem } from '../../ui/Mu';
import { Pressable } from '../../ui/Pressable';
import { ModalFrame } from '../../ui/Sheet';
import { Button } from '../../ui/Button';
import { Text } from '../../ui/Text';
import { NewSessionSheet } from '../NewSessionSheet';
import { NewThreadSheet } from '../NewThreadSheet';
import { sortSessions } from '../SessionStatusBadge';

const ACTIVE_THREAD: ThreadStatus[] = ['running', 'waiting_peer', 'awaiting_approval'];

export function threadSign(status: ThreadStatus): string {
  if (status === 'awaiting_approval') return 'waiting';
  if (status === 'running' || status === 'waiting_peer') return 'active';
  if (status === 'error') return 'failed';
  return 'pending';
}

/** True when the session needs the person: the server's flag, or the open session's local queue. */
export function sessionNeedsYou(session: SessionSummary, active: boolean, needsYou: boolean): boolean {
  return !!session.awaiting_input || (active && needsYou);
}

export function sessionSign(session: SessionSummary, active: boolean, needsYou: boolean): string | undefined {
  if (sessionNeedsYou(session, active, needsYou)) return 'waiting';
  if (session.is_busy) return 'active';
  if (active) return 'done';
  return session.is_loaded ? 'pending' : undefined;
}

/** A thread may be deleted unless it is the open one or still running. */
export function canDeleteThread(thread: ThreadSummary, activeSessionName: string | null): boolean {
  return !!activeSessionName && thread.session_name !== activeSessionName && !ACTIVE_THREAD.includes(thread.status);
}

export function SessionsDrawer({ visible, onClose, createRequestToken = 0 }: { visible: boolean; onClose: () => void; createRequestToken?: number }) {
  const { colors } = useTheme();
  const { activeSessionName, setActiveSession, setActiveProviderModel } = useConnectionStore();
  const needsYou = usePromptStore(s => s.queue.length > 0);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);
  const [createSession, setCreateSession] = useState(false);
  const [createThread, setCreateThread] = useState(false);
  const [menu, setMenu] = useState<{ title: string; items: MenuItem[] } | null>(null);

  const swipe = React.useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_e, g) => g.dx < -10 && Math.abs(g.dx) > Math.abs(g.dy) * 1.2,
    onPanResponderRelease: (_e, g) => { if (g.dx < -64) onClose(); },
  }), [onClose]);

  const load = useCallback(async (spinner = true) => {
    try {
      if (spinner) setLoading(true);
      const current = useConnectionStore.getState().activeSessionName;
      const [list, threadList] = await Promise.all([
        sessionsApi.list({ timeoutMs: 8_000 }),
        current ? threadsApi.list(current, { timeoutMs: 8_000 }).catch(() => null) : Promise.resolve(null),
      ]);
      setSessions(list.sessions);
      setThreads(threadList?.threads || []);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
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

  useEffect(() => { if (createRequestToken > 0) setCreateSession(true); }, [createRequestToken]);

  const open = async (name: string) => {
    if (name === activeSessionName) { onClose(); return; }
    if (switching) return;
    setSwitching(name);
    onClose();
    try {
      const session = sessions.find(s => s.name === name);
      if (!session?.is_loaded) await sessionsApi.load(name, undefined, undefined, { timeoutMs: 30_000 });
      else await sessionsApi.focus(name, { timeoutMs: 8_000 });
      setActiveSession(name);
    } catch (cause) {
      const problem = describeSessionLoadError(cause);
      Alert.alert(problem.title, formatSessionLoadProblem(problem));
    } finally {
      setSwitching(null);
    }
  };

  const unload = (session: SessionSummary) => {
    Alert.alert('Unload session?', `Unload “${session.name}” from memory? Its history stays saved.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Unload', onPress: async () => {
        await sessionsApi.unload(session.name);
        if (activeSessionName === session.name) { setActiveSession(null); setActiveProviderModel(null, null); onClose(); }
        void load();
      } },
    ]);
  };

  const remove = (session: SessionSummary) => {
    Alert.alert('Delete session?', `Permanently delete “${session.name}”?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => { await sessionsApi.delete(session.name); void load(); } },
    ]);
  };

  const removeThread = (thread: ThreadSummary) => {
    if (!canDeleteThread(thread, activeSessionName)) return;
    Alert.alert('Delete thread?', `Permanently delete “${thread.title || thread.session_name}” and its conversation?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        try {
          const session = sessions.find(s => s.name === thread.session_name);
          if (session?.is_loaded) await sessionsApi.unload(thread.session_name);
          await threadsApi.remove(thread.thread_id, activeSessionName as string, { timeoutMs: 12_000 });
          void load();
        } catch (cause) {
          Alert.alert('Thread could not be deleted', cause instanceof Error ? cause.message : String(cause));
        }
      } },
    ]);
  };

  const sessionMenu = (session: SessionSummary) => setMenu({ title: session.name, items: [
    { key: 'unload', label: 'Unload', icon: 'circle-minus', disabled: !session.is_loaded, reason: 'Not loaded', onPress: () => { setMenu(null); unload(session); } },
    { key: 'delete', label: 'Delete', icon: 'trash-2', tone: 'danger', onPress: () => { setMenu(null); remove(session); } },
  ] });

  const threadMenu = (thread: ThreadSummary) => setMenu({ title: thread.title || thread.session_name, items: [
    { key: 'delete', label: 'Delete thread', icon: 'trash-2', tone: 'danger', disabled: !canDeleteThread(thread, activeSessionName),
      reason: thread.session_name === activeSessionName ? 'This is the open thread' : 'The thread is still running', onPress: () => { setMenu(null); removeThread(thread); } },
  ] });

  const drawerVisible = visible && !createSession && !createThread && !menu;

  return (
    <>
      <ModalFrame statusBarTranslucent={false} visible={drawerVisible} transparent animationType="fade" onRequestClose={onClose} testID="sessions-drawer">
        <View style={styles.overlay}>
          <View {...swipe.panHandlers} style={[styles.drawer, { borderRightColor: colors.hairline }]}>
            <GlassBackdrop level={1} testID="sessions-drawer-glass" />
            <View style={[styles.head, { borderBottomColor: colors.hairline }]}>
              <Text variant="title3" accessibilityRole="header" style={{ flex: 1 }}>Conversations</Text>
              <IconButton icon="plus" label={activeSessionName ? 'New thread' : 'New session'} onPress={() => (activeSessionName ? setCreateThread(true) : setCreateSession(true))} testID="drawer-new" />
              <IconButton icon="x" label="Close conversations" onPress={onClose} />
            </View>
            <ScrollView contentContainerStyle={styles.list}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} />}>
              {threads.length > 1 ? (
                <BoardSection title="Threads" count={threads.length} testID="drawer-threads">
                  {threads.map(thread => (
                    <Row key={thread.thread_id} testID={`thread-${thread.thread_id}`} status={threadSign(thread.status)}
                      title={thread.title || thread.session_name}
                      sub={switching === thread.session_name ? 'Opening…' : [thread.status.replace(/_/g, ' '), thread.current_goal].filter(Boolean).join(' · ')}
                      when={thread.unread_count > 0 ? `${thread.unread_count} new` : undefined}
                      selected={thread.session_name === activeSessionName}
                      accessibilityLabel={`${thread.title || thread.session_name}, ${thread.status.replace(/_/g, ' ')}${thread.unread_count ? `, ${thread.unread_count} unread` : ''}`}
                      onPress={() => void open(thread.session_name)} onLongPress={() => threadMenu(thread)} />
                  ))}
                </BoardSection>
              ) : null}
              <BoardSection title="Sessions" count={sessions.length} empty={loading ? undefined : error || 'No saved sessions'} testID="drawer-sessions"
                right={<IconButton icon="plus" label="New session" onPress={() => setCreateSession(true)} />}>
                {sortSessions(sessions, activeSessionName).map(session => {
                  const active = session.name === activeSessionName;
                  const waiting = sessionNeedsYou(session, active, needsYou);
                  return (
                    <Row key={session.name} testID={`session-${session.name}`} status={sessionSign(session, active, needsYou)}
                      title={session.name}
                      sub={switching === session.name ? 'Opening…' : [session.session_type || 'workspace', waiting ? 'needs you' : session.is_busy ? 'working' : session.is_loaded ? 'loaded' : ''].filter(Boolean).join(' · ')}
                      selected={active}
                      accessibilityLabel={`${session.name}${active ? ', open' : ''}${waiting ? ', needs you' : session.is_busy ? ', working' : ''}`}
                      onPress={() => void open(session.name)} onLongPress={() => sessionMenu(session)}
                      actions={active ? <Chip variant="meta">open</Chip> : undefined} />
                  );
                })}
              </BoardSection>
              {error ? <Button variant="ghost" label="Retry" onPress={() => void load()} /> : null}
            </ScrollView>
          </View>
          <Pressable accessibilityLabel="Close conversations" style={[styles.backdrop, { backgroundColor: colors.scrim }]} onPress={onClose} scale={false} />
        </View>
      </ModalFrame>
      <ActionSheet visible={!!menu} onClose={() => setMenu(null)} title={menu?.title} items={menu?.items || []} />
      <NewSessionSheet visible={createSession} onClose={() => setCreateSession(false)}
        onCreated={session => { setActiveSession(session.name); setActiveProviderModel(session.provider, session.model); setCreateSession(false); onClose(); }} />
      <NewThreadSheet visible={createThread} parentSessionName={activeSessionName} onClose={() => setCreateThread(false)}
        onCreated={thread => { setActiveSession(thread.session_name); setCreateThread(false); onClose(); }} />
    </>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, flexDirection: 'row' },
  backdrop: { flex: 1 },
  drawer: { width: '88%', maxWidth: 380, borderRightWidth: 1, overflow: 'hidden', paddingTop: spacing.base },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.base, paddingBottom: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth },
  list: { padding: spacing.base, paddingBottom: spacing['2xl'] },
});
