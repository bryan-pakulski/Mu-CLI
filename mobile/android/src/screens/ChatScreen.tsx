// The conversation (mobile_parity.md §6 Chat; web .w-transcript).
//
// Data and session behaviour (history window, streaming, offline queue, If-Match, eviction) stay
// in useChatSession; this screen renders them. Assistant prose sits on the page without a bubble;
// user turns are accent-wash bubbles. A finished turn folds into "Worked for 12s · 5 steps", and
// its tool calls fold into the web's summary line ("Read 3 files · ran 2 commands"), from the
// shared tool-list model. Approvals and questions sit inline at the end of the transcript (n of m)
// and leave a resolved breadcrumb; when the card is scrolled away a "needs you" pill brings it
// back. The composer foot carries mode, model, effort, plan, auto-approve and the context ring.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, FlatList, KeyboardAvoidingView, Platform, StyleSheet, View } from 'react-native';
import { useIsFocused } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import { audioApi } from '../api/audio';
import type { AttachmentDescriptor } from '../api/attachments';
import { useChatSession, type ChatMessage } from '../hooks/useChatSession';
import { useCommandCompletion, type CompletionItem } from '../hooks/useCommandCompletion';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { useConnectionStore } from '../store/connection';
import { useActivePrompt, usePromptStore, type ResolvedEntry } from '../store/prompts';
import { useTheme } from '../theme/ThemeContext';
import { easings, motion, radii, spacing } from '../theme/tokens';
import { ArtifactStrip } from '../components/ArtifactStrip';
import { AttachmentSheet } from '../components/AttachmentSheet';
import { ConversationStageRail, conversationStages } from '../components/ConversationStageRail';
import { EffortControl } from '../components/EffortControl';
import { GeneratingIndicator } from '../components/GeneratingIndicator';
import { usePlanMode } from '../components/PlanModeIndicator';
import { SubagentActivityPanel } from '../components/SubagentActivityPanel';
import { VisualizationCard } from '../components/VisualizationCard';
import { NeedsYouBanner } from '../components/PromptHost';
import { InlinePrompt, ResolvedLine } from '../components/chat/InlinePrompt';
import { useAutoApprove } from '../components/chat/useAutoApprove';
import { useContextFill } from '../components/chat/useContextFill';
import { Composer } from '../ui/Composer';
import { ToolList } from '../ui/ToolList';
import { cardFromCall, groupCards, type ToolListItem } from '../ui/toolList';
import { Button, IconButton } from '../ui/Button';
import { Chip } from '../ui/Chip';
import { EmptyState, ErrorState } from '../ui/Feedback';
import { Icon } from '../ui/Icon';
import { Markdown } from '../ui/Markdown';
import { Skeleton } from '../ui/Progress';
import { Pressable } from '../ui/Pressable';
import { Text } from '../ui/Text';
import { ToolCard } from '../ui/ToolCard';
import { useGlass } from '../ui/Glass';

export type ChatScreenProps = {
  mode: { name: string; label: string };
  onOpenModes: () => void;
  onOpenSettings: () => void;
  onOpenContext: () => void;
  artifactsVisible: boolean;
};

/** A collapse's children as rows: prose, and each run of tool calls as one tool list. */
export function interimRows(children: ChatMessage[]): Array<{ key: string; message?: ChatMessage; tools?: ToolListItem[] }> {
  const out: Array<{ key: string; message?: ChatMessage; tools?: ToolListItem[] }> = [];
  let run: ChatMessage[] = [];
  const flush = () => {
    if (run.length) out.push({ key: `tools-${run[0].id}`, tools: groupCards(run.map(m => cardFromCall(m.tool!))) });
    run = [];
  };
  for (const child of children) {
    if (child.role === 'tool' && child.tool) { run.push(child); continue; }
    flush();
    if (child.role === 'assistant') out.push({ key: child.id, message: child });
  }
  flush();
  return out;
}

export function ChatScreen({ mode, onOpenModes, onOpenSettings, onOpenContext, artifactsVisible }: ChatScreenProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const focused = useIsFocused();
  const activeSessionName = useConnectionStore(s => s.activeSessionName);
  const activeProvider = useConnectionStore(s => s.activeProvider);
  const activeModel = useConnectionStore(s => s.activeModel);
  const isConnected = useConnectionStore(s => s.isConnected);
  const {
    messages, setMessages, streaming, waitingForFirstToken, historyLoading, activityLabel, sseConnected, error,
    artifactRevision, hasMore, loadingOlder, sendMessage, stop, retry, loadOlderHistory,
  } = useChatSession(activeSessionName);
  const plan = usePlanMode();
  const auto = useAutoApprove();
  const fill = useContextFill(activeSessionName, isConnected);
  const { prompt, index: promptIndex, total: promptTotal } = useActivePrompt();
  const submitting = usePromptStore(s => s.submitting);
  const promptError = usePromptStore(s => s.error);
  const answer = usePromptStore(s => s.answer);
  const cancel = usePromptStore(s => s.cancel);
  const setPromptIndex = usePromptStore(s => s.setIndex);
  const resolvedAll = usePromptStore(s => s.resolved[activeSessionName || '']);
  const setChatVisible = usePromptStore(s => s.setChatVisible);
  const setLastMessageId = usePromptStore(s => s.setLastMessageId);

  const [input, setInput] = useState('');
  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const [selectedAttachments, setSelectedAttachments] = useState<AttachmentDescriptor[]>([]);
  const [workedById, setWorkedById] = useState<Record<string, number>>({});
  const [activeMessageIndex, setActiveMessageIndex] = useState(0);
  const [atEnd, setAtEnd] = useState(true);

  const flatListRef = useRef<FlatList<ChatMessage>>(null);
  const loadingOlderRef = useRef(false);
  const checkpointRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingCheckpointIndexRef = useRef<number | null>(null);
  const checkpointJumpAttemptsRef = useRef(0);
  const olderCheckpointAnchorRef = useRef<string | null | undefined>(undefined);
  const followOutputRef = useRef(true);
  const scrollThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visualizationActiveRef = useRef(false);
  const previousStreamingRef = useRef(false);
  const turnStartedAtRef = useRef<number | null>(null);
  const completion = useCommandCompletion();

  // The inline card is the surface while the transcript is on screen.
  useEffect(() => {
    setChatVisible(focused);
    return () => setChatVisible(false);
  }, [focused, setChatVisible]);
  useEffect(() => { setLastMessageId(messages.length ? messages[messages.length - 1].id : null); }, [messages, setLastMessageId]);

  // Breadcrumbs follow the message that was last when they were answered; unknown anchors trail.
  const { resolvedAfter, resolvedTrailing } = useMemo(() => {
    const ids = new Set(messages.map(m => m.id));
    const after: Record<string, ResolvedEntry[]> = {};
    const trailing: ResolvedEntry[] = [];
    for (const entry of resolvedAll || []) {
      if (entry.after && ids.has(entry.after)) (after[entry.after] ||= []).push(entry);
      else trailing.push(entry);
    }
    return { resolvedAfter: after, resolvedTrailing: trailing };
  }, [messages, resolvedAll]);

  const viewabilityRef = useRef(({ viewableItems }: { viewableItems: Array<{ index: number | null }> }) => {
    const indexes = viewableItems.map(item => item.index).filter((i): i is number => i !== null).sort((a, b) => a - b);
    if (!indexes.length) return;
    const pending = pendingCheckpointIndexRef.current;
    if (pending !== null && indexes.includes(pending)) {
      setActiveMessageIndex(pending);
      pendingCheckpointIndexRef.current = null;
      checkpointJumpAttemptsRef.current = 0;
      if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
      checkpointRetryTimerRef.current = null;
    } else {
      setActiveMessageIndex(indexes[0]);
    }
  });
  // FlatList requires a stable viewabilityConfig identity.
  const viewabilityConfigRef = useRef({ itemVisiblePercentThreshold: 20 });

  const jumpToCheckpoint = useCallback((messageIndex: number) => {
    followOutputRef.current = false;
    pendingCheckpointIndexRef.current = messageIndex;
    checkpointJumpAttemptsRef.current = 0;
    setActiveMessageIndex(messageIndex);
    flatListRef.current?.scrollToIndex({ index: messageIndex, animated: false, viewPosition: 0 });
  }, []);

  const onCheckpointScrollFailed = useCallback(({ index, averageItemLength }: { index: number; averageItemLength: number }) => {
    if (pendingCheckpointIndexRef.current !== index) return;
    checkpointJumpAttemptsRef.current += 1;
    flatListRef.current?.scrollToOffset({ offset: Math.max(0, index * Math.max(1, averageItemLength)), animated: false });
    if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
    if (checkpointJumpAttemptsRef.current >= 6) return;
    checkpointRetryTimerRef.current = setTimeout(() => {
      if (pendingCheckpointIndexRef.current !== index) return;
      flatListRef.current?.scrollToIndex({ index, animated: false, viewPosition: 0 });
    }, 96);
  }, []);

  const loadOlderCheckpoints = useCallback(() => {
    if (loadingOlder) return;
    const stages = conversationStages(messages);
    olderCheckpointAnchorRef.current = stages[0]?.key ?? null;
    void loadOlderHistory().then(count => { if (count <= 0) olderCheckpointAnchorRef.current = undefined; });
  }, [loadOlderHistory, loadingOlder, messages]);

  useEffect(() => {
    const anchorKey = olderCheckpointAnchorRef.current;
    if (anchorKey === undefined) return;
    const stages = conversationStages(messages);
    const anchorIndex = anchorKey === null ? stages.length : stages.findIndex(stage => stage.key === anchorKey);
    if (anchorIndex <= 0) return;
    olderCheckpointAnchorRef.current = undefined;
    jumpToCheckpoint(stages[anchorIndex - 1].messageIndex);
  }, [jumpToCheckpoint, messages]);

  useEffect(() => () => {
    if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
    if (scrollThrottleRef.current) clearTimeout(scrollThrottleRef.current);
  }, []);

  const streamingRef = useRef(streaming);
  streamingRef.current = streaming;
  // Streaming grows the list many times per second: follow it, throttled.
  const scrollToBottom = useCallback(() => {
    if (!streamingRef.current || !followOutputRef.current || visualizationActiveRef.current) return;
    if (scrollThrottleRef.current) return;
    scrollThrottleRef.current = setTimeout(() => {
      scrollThrottleRef.current = null;
      if (streamingRef.current && followOutputRef.current && !visualizationActiveRef.current) flatListRef.current?.scrollToEnd({ animated: false });
    }, 48);
  }, []);

  const goToEnd = useCallback(() => {
    followOutputRef.current = true;
    setAtEnd(true);
    flatListRef.current?.scrollToEnd({ animated: true });
  }, []);

  // Wall-clock "Worked for" breadcrumbs, only for turns this client watched.
  useEffect(() => {
    const was = previousStreamingRef.current;
    if (!was && streaming) turnStartedAtRef.current = Date.now();
    if (was && !streaming && turnStartedAtRef.current) {
      const completed = [...messages].reverse().find(m => m.role === 'assistant' && !m.streaming && m.text.trim().length > 0);
      if (completed) {
        const elapsed = Math.max(0, Date.now() - turnStartedAtRef.current);
        setWorkedById(current => ({ ...current, [completed.id]: elapsed }));
      }
      turnStartedAtRef.current = null;
    }
    previousStreamingRef.current = streaming;
  }, [messages, streaming]);

  useEffect(() => {
    setWorkedById({});
    previousStreamingRef.current = false;
    turnStartedAtRef.current = null;
    pendingCheckpointIndexRef.current = null;
    checkpointJumpAttemptsRef.current = 0;
    olderCheckpointAnchorRef.current = undefined;
    if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
    checkpointRetryTimerRef.current = null;
  }, [activeSessionName]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text && selectedAttachments.length === 0) return;
    if (streaming && !text.startsWith('/')) return;
    followOutputRef.current = true;
    const sent = await sendMessage(text, selectedAttachments);
    if (!sent) return;
    setInput('');
    setSelectedAttachments([]);
    completion.close();
    requestAnimationFrame(() => flatListRef.current?.scrollToEnd({ animated: false }));
  }, [completion, input, selectedAttachments, sendMessage, streaming]);

  const onInputChange = useCallback((text: string) => {
    setInput(text);
    if (text.startsWith('/') && !text.includes('\n')) completion.update(text);
    else if (completion.visible) completion.close();
  }, [completion]);

  const onAcceptCompletion = useCallback((item: CompletionItem) => {
    const next = `${item.value} `;
    setInput(next);
    if (item.level === 0) completion.update(next);
    else completion.close();
  }, [completion]);

  const onVisualizationInteractionChange = useCallback((active: boolean) => {
    visualizationActiveRef.current = active;
    if (active) followOutputRef.current = false;
  }, []);

  const transcribe = useCallback(async (uri: string) => (await audioApi.stt(uri, activeSessionName || undefined)).text, [activeSessionName]);

  const renderAssistantBody = useCallback((message: ChatMessage, compact = false) => (
    message.streaming
      ? <Text variant={compact ? 'caption' : 'bodyLg'} tone={compact ? 'ink2' : 'ink'}>{message.text}</Text>
      : <Markdown variant={compact ? 'compact' : 'chat'}>{message.text}</Markdown>
  ), []);

  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    const crumbs = resolvedAfter[item.id];
    const after = crumbs ? <View style={styles.crumbs}>{crumbs.map(r => <ResolvedLine key={r.id} record={r} />)}</View> : null;

    if (item.role === 'visualization' && item.artifact && activeSessionName) {
      return <View style={styles.block}><VisualizationCard artifact={item.artifact} sessionName={activeSessionName} onInteractionChange={onVisualizationInteractionChange} />{after}</View>;
    }
    if (item.role === 'subagent_panel' && item.subagents?.length) {
      return <View style={styles.block}><SubagentActivityPanel agents={item.subagents} />{after}</View>;
    }
    if (item.role === 'tool' && item.tool) {
      return <View style={styles.tool}><ToolCard call={item.tool} testID={`tool-card-${item.id}`} />{after}</View>;
    }
    if (item.role === 'collapse') {
      const count = item.collapseCount || item.childTurns?.length || 0;
      const label = `Worked${item.collapseElapsed ? ` for ${item.collapseElapsed}` : ''} · ${count} step${count !== 1 ? 's' : ''}${item.collapseTokens ? ` · ${item.collapseTokens}` : ''}`;
      return (
        <View style={styles.worked}>
          <Pressable onPress={() => setMessages(current => current.map(m => (m.id === item.id ? { ...m, collapseOpen: !m.collapseOpen } : m)))}
            scale={false} accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ expanded: !!item.collapseOpen }} testID={`worked-${item.id}`}
            style={({ pressed }) => [styles.workedHead, { backgroundColor: pressed ? colors.hairline : colors.sunken }]}>
            <Text variant="label" tone="ink2" numberOfLines={1} numeric style={{ flexShrink: 1 }}>{label}</Text>
            <Icon name={item.collapseOpen ? 'chevron-down' : 'chevron-right'} color={colors.ink3} />
          </Pressable>
          {item.collapseOpen ? (
            <View style={[styles.workedBody, { borderLeftColor: colors.hairline }]}>
              {interimRows(item.childTurns || []).map(row => (
                <View key={row.key} style={styles.workedRow}>
                  {row.tools ? <ToolList items={row.tools} running={!!item.collapseLive} testID={`tool-list-${row.key}`} /> : renderAssistantBody(row.message!, true)}
                </View>
              ))}
            </View>
          ) : null}
          {after}
        </View>
      );
    }

    const isUser = item.role === 'user';
    const workedMs = workedById[item.id];
    return (
      <MessageHandoff phase={item.role === 'assistant' ? item.handoff : undefined}>
        <View style={[styles.msgRow, isUser ? styles.userRow : null]}>
          <View style={[isUser ? styles.userMessage : styles.assistantMessage, isUser ? { backgroundColor: colors.accent.wash } : null]}>
            {isUser ? <Text variant="bodyLg">{item.text}</Text> : renderAssistantBody(item)}
            {isUser && item.attachments?.length ? (
              <View style={styles.attachments}>
                {item.attachments.map(a => <Chip key={a.attachment_id} variant="meta" label={a.name} />)}
              </View>
            ) : null}
            {!isUser && item.text.length > 0 ? (
              <View style={styles.meta}>
                {workedMs ? <Text variant="caption" tone="ink3" numeric>{formatWorked(workedMs)}</Text> : <View />}
                <IconButton icon="copy" label="Copy message" onPress={() => { void Clipboard.setStringAsync(item.text); }} />
              </View>
            ) : null}
          </View>
        </View>
        {after}
      </MessageHandoff>
    );
  }, [activeSessionName, colors, onVisualizationInteractionChange, renderAssistantBody, resolvedAfter, setMessages, workedById]);

  const onScroll = useCallback((event: any) => {
    const native = event?.nativeEvent;
    if (!native) return;
    const distanceFromEnd = Math.max(0, native.contentSize.height - native.layoutMeasurement.height - native.contentOffset.y);
    if (native.contentOffset.y < 100 && hasMore && !loadingOlder && !loadingOlderRef.current) {
      loadingOlderRef.current = true;
      void loadOlderHistory().finally(() => { loadingOlderRef.current = false; });
    }
    const end = distanceFromEnd <= 100;
    if (!visualizationActiveRef.current) followOutputRef.current = end;
    setAtEnd(end);
  }, [hasMore, loadOlderHistory, loadingOlder]);

  const hint = prompt ? (
    <View style={styles.hintRow}>
      <Text variant="caption" style={{ color: colors.attention.ink }}>◆ Waiting for your answer above</Text>
      <Button variant="ghost" label="Review" onPress={goToEnd} testID="composer-review" />
    </View>
  ) : streaming ? (
    <Text variant="caption" tone="ink3">{sseConnected ? 'Working · ■ to stop' : 'Reconnecting…'}</Text>
  ) : null;

  return (
    <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={styles.timeline}>
        <FlatList
          testID="chat-transcript"
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={renderMessage}
          initialNumToRender={20}
          maxToRenderPerBatch={10}
          updateCellsBatchingPeriod={32}
          windowSize={9}
          removeClippedSubviews={false}
          contentContainerStyle={[styles.list, messages.length === 0 && !prompt ? styles.listEmpty : null]}
          maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
          keyboardShouldPersistTaps="always"
          keyboardDismissMode="none"
          onScroll={onScroll}
          onViewableItemsChanged={viewabilityRef.current}
          viewabilityConfig={viewabilityConfigRef.current}
          onScrollToIndexFailed={onCheckpointScrollFailed}
          scrollEventThrottle={32}
          onScrollBeginDrag={() => { followOutputRef.current = false; }}
          onContentSizeChange={scrollToBottom}
          onLayout={scrollToBottom}
          ListHeaderComponent={
            <View>
              {loadingOlder ? <View style={styles.loadingOlder}><GeneratingIndicator label="Loading older messages" /></View> : null}
              {error && messages.length > 0 ? (
                <View accessibilityRole="alert" style={[styles.inlineError, { backgroundColor: glass.tint('danger'), borderColor: glass.edge }]}>
                  <Text variant="body" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
                  <Text variant="caption" style={{ flex: 1 }}>{error}</Text>
                  <Button variant="ghost" label="Retry" onPress={retry} />
                </View>
              ) : null}
            </View>
          }
          ListEmptyComponent={
            historyLoading ? (
              <View style={styles.historyLoading}>
                <GeneratingIndicator label="Loading conversation history" />
                <Skeleton height={16} style={{ marginBottom: spacing.sm, width: '72%' }} />
                <Skeleton height={16} style={{ marginBottom: spacing.sm, width: '88%' }} />
                <Skeleton height={16} style={{ width: '56%' }} />
              </View>
            ) : error ? <ErrorState message={error} onRetry={retry} /> : prompt ? null : (
              <EmptyState icon="sparkles" pigment="apricot" title="What are we building today?"
                message="Ask μcli to inspect, explain, debug, or change your workspace." actionLabel={`Mode: ${mode.label}`} onAction={onOpenModes} />
            )
          }
          ListFooterComponent={
            <View style={styles.footer}>
              {resolvedTrailing.length ? <View style={styles.crumbs}>{resolvedTrailing.map(r => <ResolvedLine key={r.id} record={r} />)}</View> : null}
              {streaming && waitingForFirstToken && !prompt ? <GeneratingIndicator label={sseConnected ? activityLabel : 'Reconnecting to session'} /> : null}
              {prompt ? (
                <InlinePrompt prompt={prompt} index={promptIndex} total={promptTotal} submitting={submitting} error={promptError}
                  onAnswer={(p, payload) => { void answer(p, payload); }} onCancel={p => { void cancel(p); }} onIndex={setPromptIndex} />
              ) : null}
            </View>
          }
        />
        <ConversationStageRail messages={messages} activeMessageIndex={activeMessageIndex} hasMoreHistory={hasMore} loadingHistory={loadingOlder}
          onLoadOlder={loadOlderCheckpoints} onJump={jumpToCheckpoint} />
        {prompt && !atEnd ? (
          <View style={styles.needsYou} pointerEvents="box-none">
            <NeedsYouBanner testID="chat-needs-you" onPress={goToEnd} />
          </View>
        ) : null}
      </View>

      {artifactsVisible ? <ArtifactStrip sessionName={activeSessionName} refreshKey={artifactRevision} /> : null}

      <Composer
        value={input}
        onChangeText={onInputChange}
        onSend={() => { void send(); }}
        onStop={stop}
        busy={streaming}
        placeholder="Ask μcli anything…  / for commands"
        attachments={selectedAttachments.map(a => ({ id: a.attachment_id, name: a.name }))}
        onAttach={() => setAttachmentsOpen(true)}
        onRemoveAttachment={id => setSelectedAttachments(current => current.filter(a => a.attachment_id !== id))}
        slash={{ visible: completion.visible, items: completion.items, selectedIdx: completion.selectedIdx, onSelect: item => onAcceptCompletion(item as CompletionItem) }}
        foot={{
          mode: { name: mode.name, label: mode.label, onPress: onOpenModes },
          model: { label: activeModel || activeProvider || 'Model', onPress: onOpenSettings },
          effort: <EffortControl variant="chip" />,
          plan: { on: plan.planMode, onPress: plan.confirmTurnOff, busy: plan.busy },
          auto: { on: auto.yolo, onPress: () => { void auto.update(false); } },
          context: { fill, onPress: onOpenContext },
        }}
        transcribe={transcribe}
        hint={hint}
      />

      <AttachmentSheet visible={attachmentsOpen} sessionName={activeSessionName || ''} selected={selectedAttachments}
        onSelectedChange={setSelectedAttachments} onClose={() => setAttachmentsOpen(false)} />
    </KeyboardAvoidingView>
  );
}

function MessageHandoff({ phase, children }: { phase?: ChatMessage['handoff']; children: React.ReactNode }) {
  const { reducedMotion } = useMotionPreference();
  const opacity = useRef(new Animated.Value(phase === 'entering' ? 0 : 1)).current;
  const translateY = useRef(new Animated.Value(phase === 'entering' ? 4 : 0)).current;
  useEffect(() => {
    const leaving = phase === 'leaving';
    const entering = phase === 'entering';
    if (!leaving && !entering) { opacity.setValue(1); translateY.setValue(0); return; }
    if (reducedMotion) { opacity.setValue(leaving ? 0 : 1); translateY.setValue(0); return; }
    Animated.parallel([
      Animated.timing(opacity, { toValue: leaving ? 0 : 1, duration: motion.small, easing: leaving ? easings.in : easings.out, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: leaving ? -4 : 0, duration: motion.small, easing: leaving ? easings.in : easings.out, useNativeDriver: true }),
    ]).start();
  }, [opacity, phase, reducedMotion, translateY]);
  return <Animated.View style={{ opacity, transform: [{ translateY }] }}>{children}</Animated.View>;
}

function formatWorked(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `Worked for ${minutes}m ${seconds % 60}s` : `Worked for ${seconds}s`;
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  timeline: { flex: 1, position: 'relative' },
  list: { paddingLeft: spacing.base, paddingRight: spacing['2xl'], paddingTop: spacing.lg, paddingBottom: spacing.md },
  listEmpty: { flexGrow: 1 },
  msgRow: { flexDirection: 'row', marginBottom: spacing.base },
  userRow: { justifyContent: 'flex-end' },
  userMessage: { maxWidth: '80%', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.lg, borderBottomRightRadius: 6 },
  assistantMessage: { width: '100%', paddingTop: spacing.xs },
  meta: { minHeight: 26, marginTop: spacing.xs, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  attachments: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.sm },
  block: { marginBottom: spacing.base },
  tool: { marginBottom: spacing.sm },
  worked: { marginBottom: spacing.md },
  workedHead: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.md, alignSelf: 'flex-start', maxWidth: '100%' },
  workedBody: { marginLeft: spacing.xs, marginTop: spacing.sm, paddingLeft: spacing.md, borderLeftWidth: 1, gap: spacing.xs },
  workedRow: { paddingVertical: spacing.xs },
  crumbs: { marginBottom: spacing.sm },
  inlineError: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm, paddingHorizontal: spacing.md, marginBottom: spacing.md, borderWidth: 1, borderRadius: radii.md },
  historyLoading: { paddingTop: spacing.lg, paddingHorizontal: spacing.xs },
  loadingOlder: { paddingVertical: spacing.sm, alignItems: 'center' },
  footer: { minHeight: 10, paddingTop: 2 },
  needsYou: { position: 'absolute', left: spacing.base, right: spacing['2xl'], bottom: spacing.sm },
  hintRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
