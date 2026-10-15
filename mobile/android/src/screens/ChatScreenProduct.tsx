import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, FlatList, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as Clipboard from 'expo-clipboard';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { modesApi, ModeInfo } from '../api/modes';
import { inspectorApi, InspectorVariableGroup } from '../api/inspector';
import { EffortControl } from '../components/EffortControl';
import { GeneratingIndicator } from '../components/GeneratingIndicator';
import { ArtifactStrip } from '../components/ArtifactStrip';
import { VisualizationCard } from '../components/VisualizationCard';
import { AttachmentSheet } from '../components/AttachmentSheet';
import type { AttachmentDescriptor } from '../api/attachments';
import { useChatSession, type ChatMessage } from '../hooks/useChatSession';
import { useCommandCompletion, type CompletionItem } from '../hooks/useCommandCompletion';
import { CommandSuggestionBar } from '../components/CommandSuggestionBar';
import { SubagentActivityPanel } from '../components/SubagentActivityPanel';
import { PLAN_MODE_PLACEHOLDER, PlanModeStrip } from '../components/PlanModeIndicator';
import {
  ConversationStageRail,
  conversationStages,
} from '../components/ConversationStageRail';
import { easings, fontWeights, motion, radii, spacing } from '../theme/tokens';
import { useMotionPreference } from '../hooks/useMotionPreference';
import {
  Chip, ChoiceRow, EmptyState, ErrorState, Icon, IconButton, Markdown, Pressable, Sheet, Skeleton, Text, ToolCard, ToolGroup,
  groupToolCalls, type ToolCallView,
  textStyle,
} from '../ui';
import { useGlass } from '../ui/Glass';
import { mode as modePigments } from '../theme/wash.generated';


/**
 * Product presentation for the mobile conversation (Wash G§9.6–§9.8).
 *
 * Data/session behaviour remains owned by useChatSession. This component only
 * renders it: assistant prose sits on canvas without a bubble, user turns are
 * accent-wash bubbles on the right, tool calls are ToolCards (mobile chat
 * shows tool calls), compact interim disclosures fold tool-heavy steps into
 * one "Worked · N steps" row, and the composer is one pill with the mode
 * chip, effort, attach and a 44dp send/stop.
 */

export function ChatScreenProduct() {
  const { colors } = useTheme();
  const glass = useGlass();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const yolo = useConnectionStore(state => state.yolo);
  const {
    messages,
    setMessages,
    streaming,
    waitingForFirstToken,
    historyLoading,
    activityLabel,
    sseConnected,
    error,
    artifactRevision,
    hasMore,
    loadingOlder,
    sendMessage,
    stop,
    retry,
    loadOlderHistory,
  } = useChatSession(activeSessionName);

  const [input, setInput] = useState('');
  const [modeSheetOpen, setModeSheetOpen] = useState(false);
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [activeMode, setActiveMode] = useState<string | null>(null);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [varGroups, setVarGroups] = useState<InspectorVariableGroup[]>([]);
  const [varsLoading, setVarsLoading] = useState(false);
  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  // Round-51 UI: artifacts/visualizations strip is toggleable to reduce clutter.
  const [artifactsVisible, setArtifactsVisible] = useState(true);
  const [selectedAttachments, setSelectedAttachments] = useState<AttachmentDescriptor[]>([]);
  const [workedById, setWorkedById] = useState<Record<string, number>>({});
  const [activeMessageIndex, setActiveMessageIndex] = useState(0);

  const flatListRef = useRef<FlatList<ChatMessage>>(null);
  const loadingOlderRef = useRef(false);
  const checkpointRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingCheckpointIndexRef = useRef<number | null>(null);
  const checkpointJumpAttemptsRef = useRef(0);
  const olderCheckpointAnchorRef = useRef<string | null | undefined>(undefined);
  const followOutputRef = useRef(true);
  // Streaming grows the list many times per second; scrollToEnd is throttled
  // so the list follows the output without one native scroll per token.
  const scrollThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visualizationActiveRef = useRef(false);
  const previousStreamingRef = useRef(false);
  const turnStartedAtRef = useRef<number | null>(null);
  const completion = useCommandCompletion();
  const viewabilityRef = useRef(({ viewableItems }: { viewableItems: Array<{ index: number | null }> }) => {
    const indexes = viewableItems
      .map(item => item.index)
      .filter((index): index is number => index !== null)
      .sort((left, right) => left - right);
    if (indexes.length > 0) {
      const pendingIndex = pendingCheckpointIndexRef.current;
      if (pendingIndex !== null && indexes.includes(pendingIndex)) {
        setActiveMessageIndex(pendingIndex);
        pendingCheckpointIndexRef.current = null;
        checkpointJumpAttemptsRef.current = 0;
        if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
        checkpointRetryTimerRef.current = null;
      } else {
        setActiveMessageIndex(indexes[0]);
      }
    }
  });
  // FlatList requires viewabilityConfig identity to remain stable. Recreating
  // it when the active marker changes can stop viewability callbacks entirely.
  const stageViewabilityConfigRef = useRef({ itemVisiblePercentThreshold: 20 });

  const jumpToCheckpoint = useCallback((messageIndex: number) => {
    followOutputRef.current = false;
    pendingCheckpointIndexRef.current = messageIndex;
    checkpointJumpAttemptsRef.current = 0;
    setActiveMessageIndex(messageIndex);
    flatListRef.current?.scrollToIndex({
      index: messageIndex,
      animated: false,
      viewPosition: 0,
    });
  }, []);

  const onCheckpointScrollFailed = useCallback(({
    index,
    averageItemLength,
  }: {
    index: number;
    averageItemLength: number;
  }) => {
    if (pendingCheckpointIndexRef.current !== index) return;
    checkpointJumpAttemptsRef.current += 1;
    flatListRef.current?.scrollToOffset({
      offset: Math.max(0, index * Math.max(1, averageItemLength)),
      animated: false,
    });
    if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
    if (checkpointJumpAttemptsRef.current >= 6) return;
    checkpointRetryTimerRef.current = setTimeout(() => {
      if (pendingCheckpointIndexRef.current !== index) return;
      flatListRef.current?.scrollToIndex({
        index,
        animated: false,
        viewPosition: 0,
      });
    }, 96);
  }, []);

  const loadOlderCheckpoints = useCallback(() => {
    if (loadingOlder) return;
    const stages = conversationStages(messages);
    olderCheckpointAnchorRef.current = stages[0]?.key ?? null;
    void loadOlderHistory().then(checkpointCount => {
      if (checkpointCount <= 0) olderCheckpointAnchorRef.current = undefined;
    });
  }, [loadOlderHistory, loadingOlder, messages]);

  useEffect(() => {
    const anchorKey = olderCheckpointAnchorRef.current;
    if (anchorKey === undefined) return;
    const stages = conversationStages(messages);
    const anchorIndex = anchorKey === null
      ? stages.length
      : stages.findIndex(stage => stage.key === anchorKey);
    if (anchorIndex <= 0) return;
    const target = stages[anchorIndex - 1];
    olderCheckpointAnchorRef.current = undefined;
    jumpToCheckpoint(target.messageIndex);
  }, [jumpToCheckpoint, messages]);

  useEffect(() => () => {
    if (checkpointRetryTimerRef.current) clearTimeout(checkpointRetryTimerRef.current);
    if (scrollThrottleRef.current) clearTimeout(scrollThrottleRef.current);
  }, []);

  const streamingRef = useRef(streaming);
  streamingRef.current = streaming;
  const scrollToBottom = useCallback(() => {
    if (!streamingRef.current || !followOutputRef.current || visualizationActiveRef.current) return;
    if (scrollThrottleRef.current) return;
    scrollThrottleRef.current = setTimeout(() => {
      scrollThrottleRef.current = null;
      if (streamingRef.current && followOutputRef.current && !visualizationActiveRef.current) {
        flatListRef.current?.scrollToEnd({ animated: false });
      }
    }, 48);
  }, []);

  const copyMessage = useCallback((text: string) => {
    void Clipboard.setStringAsync(text);
  }, []);

  const loadModes = useCallback(async () => {
    try {
      const response = await modesApi.list();
      setModes(response.modes || []);
      setActiveMode(response.current || 'default');
    } catch {
      // Session transport errors remain owned by the connection/session layer.
    }
  }, []);

  const loadVariables = useCallback(async () => {
    setVarsLoading(true);
    try {
      const response = await inspectorApi.getVariables();
      setVarGroups(response.groups || []);
    } catch {
      setVarGroups([]);
    } finally {
      setVarsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModes();
  }, [activeSessionName, loadModes]);

  useEffect(() => {
    // Wall-clock breadcrumb parity with web. Historical timings are not
    // fabricated; only turns observed by this mobile client receive a value.
    const wasStreaming = previousStreamingRef.current;
    if (!wasStreaming && streaming) turnStartedAtRef.current = Date.now();
    if (wasStreaming && !streaming && turnStartedAtRef.current) {
      const completed = [...messages].reverse().find(message =>
        message.role === 'assistant' && !message.streaming && message.text.trim().length > 0,
      );
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
    if ((!text && selectedAttachments.length === 0) || streaming) return;
    followOutputRef.current = true;
    const sent = await sendMessage(text, selectedAttachments);
    if (!sent) return;
    setInput('');
    setSelectedAttachments([]);
    completion.close();
    requestAnimationFrame(() => flatListRef.current?.scrollToEnd({ animated: false }));
  }, [completion, input, selectedAttachments, sendMessage, streaming]);

  const selectMode = useCallback(async (name: string) => {
    try {
      await modesApi.set(name);
      setActiveMode(name);
      setModeSheetOpen(false);
    } catch {
      // Existing session error presentation remains authoritative.
    }
  }, []);

  const onInputChange = useCallback((text: string) => {
    setInput(text);
    if (text.startsWith('/')) completion.update(text);
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

  // Streaming text uses the lightweight Text path; completed messages are
  // parsed once by the shared Markdown module (links go through the http(s)
  // allow-list, code through the Wash CodeBlock).
  const renderAssistantBody = useCallback((message: ChatMessage, compact = false) => {
    if (message.streaming) {
      return (
        <Text variant={compact ? 'caption' : 'bodyLg'} tone={compact ? 'ink2' : 'ink'}>
          {message.text}
        </Text>
      );
    }
    return <Markdown variant={compact ? 'compact' : 'chat'}>{message.text}</Markdown>;
  }, []);

  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    if (item.role === 'visualization' && item.artifact && activeSessionName) {
      return (
        <View style={styles.visualizationWrap}>
          <VisualizationCard
            artifact={item.artifact}
            sessionName={activeSessionName}
            onInteractionChange={onVisualizationInteractionChange}
          />
        </View>
      );
    }

    if (item.role === 'subagent_panel' && item.subagents?.length) {
      return (
        <View style={styles.visualizationWrap}>
          <SubagentActivityPanel agents={item.subagents} />
        </View>
      );
    }

    if (item.role === 'tool' && item.tool) {
      return (
        <View style={styles.toolWrap}>
          <ToolCard call={item.tool} testID={`tool-card-${item.id}`} />
        </View>
      );
    }

    if (item.role === 'collapse') {
      const count = item.collapseCount || item.childTurns?.length || 0;
      return (
        <View style={styles.interimGroup}>
          <Pressable
            onPress={() => setMessages(current => current.map(message =>
              message.id === item.id ? { ...message, collapseOpen: !message.collapseOpen } : message,
            ))}
            scale={false}
            style={({ pressed }) => [styles.interimHeader, { backgroundColor: pressed ? colors.hairline : colors.sunken }]}
            accessibilityLabel={`Worked · ${count} step${count !== 1 ? 's' : ''}`}
            accessibilityState={{ expanded: !!item.collapseOpen }}
          >
            <Text variant="label" tone="ink2" numberOfLines={1} numeric style={{ flexShrink: 1 }}>
              Worked · {count} step{count !== 1 ? 's' : ''}
              {item.collapseElapsed ? ` · ${item.collapseElapsed}` : ''}
              {item.collapseTokens ? ` · ${item.collapseTokens}` : ''}
            </Text>
            <Icon name={item.collapseOpen ? 'chevron-down' : 'chevron-right'} size="sm" color={colors.ink3} />
          </Pressable>
          {item.collapseOpen ? (
            <View style={[styles.interimBody, { borderLeftColor: colors.hairline }]}>
              {interimChildren(item.childTurns || []).map(child => {
                if (Array.isArray(child)) {
                  return <View key={child[0].id} style={styles.interimMessage}><ToolGroup calls={child} /></View>;
                }
                if ('status' in child) {
                  return <View key={child.id} style={styles.interimMessage}><ToolCard call={child} /></View>;
                }
                if (child.role !== 'assistant') return null;
                return (
                  <View key={child.id} style={styles.interimMessage}>
                    {renderAssistantBody(child, true)}
                  </View>
                );
              })}
            </View>
          ) : null}
        </View>
      );
    }

    const isUser = item.role === 'user';
    const isAssistant = item.role === 'assistant';
    const workedMs = workedById[item.id];

    return (
      <MessageHandoff phase={isAssistant ? item.handoff : undefined}>
        <View style={[styles.msgRow, isUser ? styles.userRow : styles.assistantRow]}>
        <View
          style={[
            isUser ? styles.userMessage : styles.assistantMessage,
            // Wash: the user turn is an accent-wash bubble; assistant prose sits on canvas.
            isUser ? { backgroundColor: colors.accent.wash } : null,
          ]}
        >
          {isUser ? (
            <Text variant="bodyLg">{item.text}</Text>
          ) : (
            renderAssistantBody(item)
          )}

          {isUser && item.attachments?.length ? (
            <View style={styles.messageAttachments}>
              {item.attachments.map(attachment => (
                <Chip key={attachment.attachment_id} variant="meta">
                  <View style={styles.attachmentChip}>
                    <Icon name="file" size="sm" color={colors.ink3} />
                    <Text variant="caption" tone="ink2" numberOfLines={1} style={{ maxWidth: 210 }}>{attachment.name}</Text>
                  </View>
                </Chip>
              ))}
            </View>
          ) : null}

          {isAssistant && item.text.length > 0 ? (
            <View style={styles.assistantMeta}>
              {workedMs ? (
                <Text variant="caption" tone="ink3" numeric>{formatWorkedDuration(workedMs)}</Text>
              ) : <View />}
              <IconButton icon="copy" label="Copy message" onPress={() => copyMessage(item.text)} style={styles.copyButton} />
            </View>
          ) : null}
        </View>
        </View>
      </MessageHandoff>
    );
  }, [activeSessionName, colors, copyMessage, onVisualizationInteractionChange, renderAssistantBody, setMessages, workedById]);

  const onScroll = useCallback((event: any) => {
    const native = event?.nativeEvent;
    if (!native) return;
    const distanceFromEnd = Math.max(0, native.contentSize.height - native.layoutMeasurement.height - native.contentOffset.y);
    if (native.contentOffset.y < 100 && hasMore && !loadingOlder && !loadingOlderRef.current) {
      loadingOlderRef.current = true;
      void loadOlderHistory().finally(() => { loadingOlderRef.current = false; });
    }
    if (!visualizationActiveRef.current) followOutputRef.current = distanceFromEnd <= 100;
  }, [hasMore, loadOlderHistory, loadingOlder]);

  return (
    <SafeAreaView edges={[]} style={[styles.safeArea, { backgroundColor: 'transparent' }]}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* Round-44 F13: match ChatScreen's list tuning so both chat
            surfaces behave identically at scale — bounded initial/batch
            render + window size keep long loaded histories responsive. */}
        <View style={styles.timeline}>
        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={renderMessage}
          initialNumToRender={20}
          maxToRenderPerBatch={10}
          updateCellsBatchingPeriod={32}
          windowSize={9}
          removeClippedSubviews={false}
          contentContainerStyle={[styles.messageList, messages.length === 0 && styles.messageListEmpty]}
          maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
          keyboardShouldPersistTaps="always"
          keyboardDismissMode="none"
          onScroll={onScroll}
          onViewableItemsChanged={viewabilityRef.current}
          viewabilityConfig={stageViewabilityConfigRef.current}
          onScrollToIndexFailed={onCheckpointScrollFailed}
          scrollEventThrottle={32}
          onScrollBeginDrag={() => { followOutputRef.current = false; }}
          onContentSizeChange={scrollToBottom}
          onLayout={scrollToBottom}
          ListHeaderComponent={
            <View>
              {loadingOlder ? (
                <View style={styles.loadingOlderIndicator}>
                  <GeneratingIndicator label="Loading older messages" />
                </View>
              ) : null}
              {error && messages.length > 0 ? (
                <View accessibilityRole="alert" style={[styles.inlineError, { backgroundColor: glass.tint('danger'), borderColor: glass.edge }]}>
                  <Text variant="body" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
                  <Text variant="caption" style={{ color: colors.ink, flex: 1 }}>{error}</Text>
                  <Pressable onPress={retry} accessibilityLabel="Retry">
                    <Text variant="label" style={{ color: colors.danger.ink }}>Retry</Text>
                  </Pressable>
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
            ) : error ? (
              <ErrorState message={error} onRetry={retry} />
            ) : (
              <EmptyState
                icon="sparkles"
                pigment="apricot"
                title="What are we building today?"
                message="Ask Mucli to inspect, explain, debug, or change your workspace."
                actionLabel={activeMode ? `Mode: ${activeMode}` : 'Select mode'}
                onAction={() => { void loadModes(); setModeSheetOpen(true); }}
              />
            )
          }
          ListFooterComponent={
            <View style={styles.listFooter}>
              {streaming && waitingForFirstToken ? (
                <GeneratingIndicator label={sseConnected ? activityLabel : 'Reconnecting to session'} />
              ) : null}
            </View>
          }
        />
        <ConversationStageRail
          messages={messages}
          activeMessageIndex={activeMessageIndex}
          hasMoreHistory={hasMore}
          loadingHistory={loadingOlder}
          onLoadOlder={loadOlderCheckpoints}
          onJump={jumpToCheckpoint}
        />
        </View>

        {artifactsVisible ? (
          <ArtifactStrip sessionName={activeSessionName} refreshKey={artifactRevision} />
        ) : null}
        <CommandSuggestionBar
          visible={completion.visible}
          items={completion.items}
          selectedIdx={completion.selectedIdx}
          onSelect={onAcceptCompletion}
        />

        <Composer
          input={input}
          setInput={onInputChange}
          onSend={send}
          onStop={stop}
          streaming={streaming}
          activeMode={activeMode || 'default'}
          onModePress={() => { void loadModes(); setModeSheetOpen(true); }}
          onSettingsPress={() => { void loadVariables(); setSettingsSheetOpen(true); }}
          onAttachmentsPress={() => setAttachmentsOpen(true)}
          selectedAttachments={selectedAttachments}
          onRemoveAttachment={attachmentId => setSelectedAttachments(current => current.filter(item => item.attachment_id !== attachmentId))}
          bottomInset={0}
          artifactsVisible={artifactsVisible}
          onToggleArtifacts={() => setArtifactsVisible(current => !current)}
        />

        <AttachmentSheet
          visible={attachmentsOpen}
          sessionName={activeSessionName || ''}
          selected={selectedAttachments}
          onSelectedChange={setSelectedAttachments}
          onClose={() => setAttachmentsOpen(false)}
        />
        <ModeSheet
          visible={modeSheetOpen}
          onClose={() => setModeSheetOpen(false)}
          modes={modes}
          activeMode={activeMode}
          onSelect={selectMode}
        />
        <SessionSettingsSheet
          visible={settingsSheetOpen}
          onClose={() => setSettingsSheetOpen(false)}
          groups={varGroups}
          loading={varsLoading}
          provider={activeProvider}
          model={activeModel}
          yolo={yolo}
        />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Composer({
  input,
  setInput,
  onSend,
  onStop,
  streaming,
  activeMode,
  onModePress,
  onSettingsPress,
  onAttachmentsPress,
  selectedAttachments,
  onRemoveAttachment,
  bottomInset,
  artifactsVisible,
  onToggleArtifacts,
}: {
  input: string;
  setInput: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  activeMode: string;
  onModePress: () => void;
  onSettingsPress: () => void;
  onAttachmentsPress: () => void;
  selectedAttachments: AttachmentDescriptor[];
  onRemoveAttachment: (attachmentId: string) => void;
  bottomInset: number;
  artifactsVisible: boolean;
  onToggleArtifacts: () => void;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  const planMode = useConnectionStore(state => state.planMode);
  const canSend = input.trim().length > 0 || selectedAttachments.length > 0;
  const modeTone = colors.mode[(activeMode in modePigments ? activeMode : 'default') as keyof typeof modePigments];
  return (
    <View style={[styles.composerArea, { paddingBottom: Math.max(bottomInset, spacing.sm) }]}>
      <PlanModeStrip />
      {selectedAttachments.length > 0 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.selectedAttachments}>
          {selectedAttachments.map(item => (
            <Chip key={item.attachment_id} variant="meta" label={item.name} onRemove={() => onRemoveAttachment(item.attachment_id)} removeLabel={`Remove ${item.name}`} />
          ))}
        </ScrollView>
      ) : null}

      <View style={styles.composerUtilities}>
        <Pressable onPress={onModePress} accessibilityLabel={`Mode: ${activeMode}`} accessibilityHint="Choose the agent mode"
          style={[styles.utilityButton, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
          <View style={[styles.modeDot, { backgroundColor: modeTone.pigment }]} />
          <Text variant="caption" tone="ink2" style={{ textTransform: 'capitalize' }}>{activeMode}</Text>
          <Text variant="caption" tone="ink3" importantForAccessibility="no">▾</Text>
        </Pressable>
        <EffortControl variant="chip" />
        <View style={{ flex: 1 }} />
        <IconButton
          icon={artifactsVisible ? 'eye' : 'eye-off'}
          label={artifactsVisible ? 'Hide artifacts panel' : 'Show artifacts panel'}
          onPress={onToggleArtifacts}
          selected={artifactsVisible}
        />
        <IconButton icon="settings" label="Session settings" onPress={onSettingsPress} />
      </View>

      {/* Wash composer (G§9.6): one level-1 glass pill, a 3px wash glow in
          the mode pigment along the top edge, plan mode adds a lilac dashed
          outline. */}
      <View style={[
        styles.composerPane,
        {
          backgroundColor: glass.style(1).backgroundColor,
          borderColor: planMode ? colors.plan.pigment : colors.hairline,
          borderStyle: planMode ? 'dashed' : 'solid',
          borderTopColor: planMode ? colors.plan.pigment : modeTone.pigment,
        },
      ]}>
        <IconButton icon="plus" label="Attach files" onPress={onAttachmentsPress} selected={selectedAttachments.length > 0} />
        {/* Keep focus and the draft keyboard open while a turn is running. */}
        <TextInput
          value={input}
          onChangeText={setInput}
          placeholder={planMode ? PLAN_MODE_PLACEHOLDER : 'Ask Mucli anything…'}
          placeholderTextColor={colors.ink3}
          multiline
          maxFontSizeMultiplier={2}
          accessibilityLabel="Message"
          style={[styles.input, textStyle('bodyLg'), { color: colors.ink }]}
        />
        {streaming ? (
          <Pressable onPress={onStop} accessibilityLabel="Stop generation" haptic="light"
            style={[styles.sendButton, { backgroundColor: colors.surface, borderColor: colors.hairlineStrong, borderWidth: 1 }]}>
            <View style={[styles.stopGlyph, { backgroundColor: colors.danger.ink }]} />
          </Pressable>
        ) : (
          <Pressable
            onPress={onSend}
            disabled={!canSend}
            haptic="light"
            style={[styles.sendButton, { backgroundColor: canSend ? colors.ink : colors.sunken }]}
            accessibilityLabel="Send message"
          >
            <Icon name="arrow-up" color={canSend ? colors.onInk : colors.ink3} />
          </Pressable>
        )}
      </View>
    </View>
  );
}

function ModeSheet({
  visible,
  onClose,
  modes,
  activeMode,
  onSelect,
}: {
  visible: boolean;
  onClose: () => void;
  modes: ModeInfo[];
  activeMode: string | null;
  onSelect: (name: string) => void;
}) {
  const { colors } = useTheme();
  return (
    <Sheet visible={visible} onClose={onClose} title="Mode">
      <View style={styles.optionList}>
        {modes.map(mode => (
          <ChoiceRow
            key={mode.name}
            label={mode.display_name}
            description={mode.description || undefined}
            selected={activeMode === mode.name}
            disabled={mode.disabled}
            onPress={() => onSelect(mode.name)}
          />
        ))}
      </View>
    </Sheet>
  );
}

function SessionSettingsSheet({
  visible,
  onClose,
  groups,
  loading,
  provider,
  model,
  yolo,
}: {
  visible: boolean;
  onClose: () => void;
  groups: InspectorVariableGroup[];
  loading: boolean;
  provider: string | null;
  model: string | null;
  yolo: boolean;
}) {
  const { colors } = useTheme();
  return (
    <Sheet visible={visible} onClose={onClose} title="Session settings">
      <View style={[styles.settingsSummary, { backgroundColor: colors.sunken }]}>
        <SettingLine label="Provider" value={provider || '—'} />
        <SettingLine label="Model" value={model || '—'} />
        <SettingLine label="Auto-approve" value={yolo ? 'On' : 'Off'} />
      </View>
      <Text variant="xs" style={[styles.settingsSectionLabel, { color: colors.ink3 }]}>Variables</Text>
      {loading ? (
        <Text variant="xs" dim style={styles.settingsLoading}>Loading…</Text>
      ) : groups.length === 0 ? (
        <Text variant="xs" dim style={styles.settingsLoading}>No variables configured</Text>
      ) : (
        groups.map(group => (
          <View key={group.name} style={[styles.settingsGroup, { backgroundColor: colors.sunken }]}>
            <Text variant="sm" accessibilityRole="header" style={[styles.settingsGroupTitle, { borderBottomColor: colors.hairline }]}>{group.name}</Text>
            {group.variables.map(variable => (
              <View key={variable.key} style={styles.settingsVariable}>
                <View style={styles.settingsVariableCopy}>
                  <Text variant="xs" style={{ color: colors.ink2 }}>{variable.key}</Text>
                  {variable.help ? <Text variant="xs" dim numberOfLines={2}>{variable.help}</Text> : null}
                </View>
                <Text variant="xs" style={{ color: variable.is_default ? colors.ink3 : colors.ink, fontWeight: variable.is_default ? fontWeights.regular : fontWeights.medium }} numberOfLines={1}>
                  {String(variable.value)}
                </Text>
              </View>
            ))}
          </View>
        ))
      )}
    </Sheet>
  );
}

function SettingLine({ label, value }: { label: string; value: string }) {
  const { colors } = useTheme();
  return (
    <View style={styles.settingLine}>
      <Text variant="xs" dim>{label}</Text>
      <Text variant="sm" style={{ color: colors.ink2 }} numberOfLines={1}>{value}</Text>
    </View>
  );
}

function MessageHandoff({
  phase,
  children,
}: {
  phase?: ChatMessage['handoff'];
  children: React.ReactNode;
}) {
  const { reducedMotion } = useMotionPreference();
  const opacity = useRef(new Animated.Value(phase === 'entering' ? 0 : 1)).current;
  const translateY = useRef(new Animated.Value(phase === 'entering' ? 4 : 0)).current;

  useEffect(() => {
    const leaving = phase === 'leaving';
    const entering = phase === 'entering';
    if (!leaving && !entering) {
      opacity.setValue(1);
      translateY.setValue(0);
      return;
    }
    if (reducedMotion) {
      opacity.setValue(leaving ? 0 : 1);
      translateY.setValue(0);
      return;
    }
    Animated.parallel([
      Animated.timing(opacity, {
        toValue: leaving ? 0 : 1,
        duration: motion.small,
        easing: leaving ? easings.in : easings.out,
        useNativeDriver: true,
      }),
      Animated.timing(translateY, {
        toValue: leaving ? -4 : 0,
        duration: motion.small,
        easing: leaving ? easings.in : easings.out,
        useNativeDriver: true,
      }),
    ]).start();
  }, [opacity, phase, reducedMotion, translateY]);

  return <Animated.View style={{ opacity, transform: [{ translateY }] }}>{children}</Animated.View>;
}

function formatWorkedDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `Worked for ${minutes}m ${remainder}s` : `Worked for ${remainder}s`;
}

/** Interim children: assistant prose, single tool cards, and 3+ same-kind runs as groups. */
function interimChildren(children: ChatMessage[]): Array<ChatMessage | ToolCallView | ToolCallView[]> {
  const out: Array<ChatMessage | ToolCallView | ToolCallView[]> = [];
  let run: ToolCallView[] = [];
  const flush = () => {
    if (run.length) out.push(...groupToolCalls(run));
    run = [];
  };
  for (const child of children) {
    if (child.role === 'tool' && child.tool) {
      run.push(child.tool);
      continue;
    }
    flush();
    out.push(child);
  }
  flush();
  return out;
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  timeline: { flex: 1, position: 'relative' },
  messageList: { paddingLeft: spacing.base, paddingRight: spacing['2xl'], paddingTop: spacing.lg, paddingBottom: spacing.md },
  messageListEmpty: { flexGrow: 1 },
  msgRow: { flexDirection: 'row', marginBottom: spacing.base },
  userRow: { justifyContent: 'flex-end' },
  assistantRow: { justifyContent: 'flex-start' },
  // Wash transcript (G§9.7): the user turn is an accent-wash bubble on the
  // right, radius-lg with a 6px bottom-right corner, at most 80% wide; the
  // assistant has no bubble — full-width body-lg prose on canvas.
  userMessage: { maxWidth: '80%', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.lg, borderBottomRightRadius: 6 },
  assistantMessage: { width: '100%', paddingTop: spacing.xs },
  assistantMeta: { minHeight: 26, marginTop: spacing.xs, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  copyButton: { marginRight: -spacing.sm },
  visualizationWrap: { marginBottom: spacing.base },
  toolWrap: { marginBottom: spacing.sm },
  // Compact interim disclosures: one "Worked · N steps" row (sunken, radius-md).
  interimGroup: { marginBottom: spacing.md },
  interimHeader: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.md, alignSelf: 'flex-start', maxWidth: '100%' },
  interimBody: { marginLeft: spacing.xs, marginTop: spacing.sm, paddingLeft: spacing.md, borderLeftWidth: 1, gap: spacing.xs },
  interimMessage: { paddingVertical: spacing.xs },
  messageAttachments: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.sm },
  attachmentChip: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  inlineError: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm, paddingHorizontal: spacing.md, marginBottom: spacing.md, borderWidth: 1, borderRadius: radii.md },
  historyLoading: { paddingTop: spacing.lg, paddingHorizontal: spacing.xs },
  loadingOlderIndicator: { paddingVertical: spacing.sm, alignItems: 'center' },
  listFooter: { minHeight: 10, paddingTop: 2 },
  composerArea: { paddingHorizontal: spacing.md, paddingTop: spacing.xs },
  composerUtilities: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: 2, marginBottom: spacing.xs },
  utilityButton: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.pill },
  modeDot: { width: 8, height: 8, borderRadius: 4 },
  composerPane: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.xs, padding: spacing.xs, borderWidth: 1, borderTopWidth: 3, borderRadius: radii.lg },
  input: { flex: 1, borderWidth: 0, paddingHorizontal: spacing.xs, paddingVertical: 10, maxHeight: 288, minHeight: 44, textAlignVertical: 'top' },
  sendButton: { width: 44, height: 44, borderRadius: radii.pill, justifyContent: 'center', alignItems: 'center' },
  stopGlyph: { width: 14, height: 14, borderRadius: 3 },
  selectedAttachments: { paddingHorizontal: 2, paddingBottom: spacing.sm, gap: spacing.xs },
  optionList: { gap: spacing.xs },
  settingsSummary: { borderRadius: radii.lg, paddingHorizontal: spacing.md },
  settingLine: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.base },
  settingsSectionLabel: { marginTop: spacing.lg, marginBottom: spacing.sm, fontWeight: fontWeights.medium },
  settingsLoading: { paddingVertical: spacing.base },
  settingsGroup: { marginTop: spacing.md, borderRadius: radii.lg, overflow: 'hidden' },
  settingsGroupTitle: { paddingVertical: spacing.md, paddingHorizontal: spacing.md, fontWeight: fontWeights.semibold, borderBottomWidth: 1 },
  settingsVariable: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm, paddingHorizontal: spacing.md },
  settingsVariableCopy: { flex: 1 },
});
