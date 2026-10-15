import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import MarkdownRenderer from 'react-native-markdown-display';
import { openExternalUrl } from '../api/urlSafety';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { modesApi, ModeInfo } from '../api/modes';
import { inspectorApi, InspectorVariableGroup } from '../api/inspector';
import { Text, Skeleton, EmptyState, ErrorState } from '../components';
import { ModernBottomSheet } from '../components/ModernBottomSheet';
import { EffortControl } from '../components/EffortControl';
import { GeneratingIndicator } from '../components/GeneratingIndicator';
import { ArtifactStrip } from '../components/ArtifactStrip';
import { CodeBlock } from '../components/CodeBlock';
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
import { easings, fontSizes, fontWeights, fonts, motion, radii, spacing, tracking, typography } from '../theme/tokens';
import { useMotionPreference } from '../hooks/useMotionPreference';

const HIT = { top: spacing.sm, bottom: spacing.sm, left: spacing.sm, right: spacing.sm };
const HIT_SM = { top: spacing.xs, bottom: spacing.xs, left: spacing.xs, right: spacing.xs };

/**
 * Product presentation for the mobile conversation.
 *
 * Data/session behaviour remains owned by useChatSession. This component only
 * brings mobile presentation in line with the planos session transcript:
 * assistant bubbles raised + border with a squared top-left corner, user
 * bubbles accent-tinted on the right, compact interim disclosures folded to one
 * mono line that expands, and one glass composer pane with utilities outside.
 */
// Completed messages keep their parsed Markdown while new tokens arrive in
// another row. Streaming messages still use the lightweight Text path.
const Markdown = React.memo(MarkdownRenderer);

export function ChatScreenProduct() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
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

  // Safe link handler: only http(s) URLs reach the OS. Everything else is
  // blocked (with an alert) instead of being handed to Linking.
  const handleMarkdownLinkPress = useCallback((url: string) => {
    void openExternalUrl(url);
    return false;
  }, []);

  const markdownRules = useMemo(() => ({
    fence: (node: any) => (
      <CodeBlock key={node.key} code={node.content} language={(node.sourceInfo || '').trim()} colors={colors} />
    ),
    code_block: (node: any) => (
      <CodeBlock key={node.key} code={node.content} colors={colors} />
    ),
    code_inline: (node: any) => (
      <Text
        key={node.key}
        style={{
          color: colors.syntax.keyword,
          fontFamily: fonts.mono,
          fontSize: fontSizes.xs,
          backgroundColor: colors.code,
          borderRadius: radii.sm,
          paddingHorizontal: spacing.xs,
        }}
      >
        {node.content}
      </Text>
    ),
  }), [colors]);
  const mdStyles = useMemo(() => markdownStyles(colors), [colors]);
  const compactMdStyles = useMemo(() => compactMarkdownStyles(colors), [colors]);

  const renderAssistantBody = useCallback((message: ChatMessage, compact = false) => {
    if (message.streaming) {
      return (
        <Text style={{ color: compact ? colors.ink2 : colors.ink, fontSize: compact ? fontSizes.xs : fontSizes.base, lineHeight: compact ? typography.xs.lineHeight : typography.base.lineHeight }}>
          {message.text}
        </Text>
      );
    }
    return (
      <Markdown style={compact ? compactMdStyles : mdStyles} rules={markdownRules} onLinkPress={handleMarkdownLinkPress}>
        {message.text}
      </Markdown>
    );
  }, [colors, compactMdStyles, handleMarkdownLinkPress, markdownRules, mdStyles]);

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

    if (item.role === 'collapse') {
      const count = item.collapseCount || item.childTurns?.length || 0;
      return (
        <View style={styles.interimGroup}>
          <TouchableOpacity
            onPress={() => setMessages(current => current.map(message =>
              message.id === item.id ? { ...message, collapseOpen: !message.collapseOpen } : message,
            ))}
            style={[styles.interimHeader, { backgroundColor: colors.surface, borderColor: colors.hairline }]}
            activeOpacity={0.7}
            hitSlop={HIT}
            accessibilityRole="button"
            accessibilityState={{ expanded: !!item.collapseOpen }}
          >
            <Ionicons
              name={item.collapseOpen ? 'chevron-down' : 'chevron-forward'}
              size={13}
              color={colors.ink3}
            />
            <Text variant="xs" numberOfLines={1} style={{ color: colors.ink3, fontFamily: fonts.mono, fontVariant: ['tabular-nums'], flexShrink: 1 }}>
              › {count} interim update{count !== 1 ? 's' : ''}
              {item.collapseElapsed ? ` · ${item.collapseElapsed}` : ''}
              {item.collapseTokens ? ` · ${item.collapseTokens}` : ''}
            </Text>
          </TouchableOpacity>
          {item.collapseOpen ? (
            <View style={[styles.interimBody, { borderLeftColor: colors.hairline }]}>
              {item.childTurns?.map(child => {
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
            isUser
              ? { backgroundColor: colors.accent.wash, borderColor: colors.accent.ink, borderRightColor: colors.accent.ink }
              : { backgroundColor: colors.raised, borderColor: colors.hairline },
          ]}
        >
          {isUser ? (
            <Text style={{ color: colors.ink, fontSize: fontSizes.base, lineHeight: typography.base.lineHeight }}>{item.text}</Text>
          ) : (
            renderAssistantBody(item)
          )}

          {isUser && item.attachments?.length ? (
            <View style={styles.messageAttachments}>
              {item.attachments.map(attachment => (
                <View key={attachment.attachment_id} style={[styles.messageAttachment, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
                  <Ionicons name="document-outline" size={13} color={colors.ink3} />
                  <Text variant="xs" numberOfLines={1} style={{ color: colors.ink2, maxWidth: 210 }}>{attachment.name}</Text>
                </View>
              ))}
            </View>
          ) : null}

          {isAssistant && item.text.length > 0 ? (
            <View style={styles.assistantMeta}>
              {workedMs ? (
                <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>{formatWorkedDuration(workedMs)}</Text>
              ) : <View />}
              <TouchableOpacity
                onPress={() => copyMessage(item.text)}
                hitSlop={HIT}
                style={styles.copyButton}
              >
                <Ionicons name="copy-outline" size={13} color={colors.ink3} />
              </TouchableOpacity>
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
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={insets.bottom}
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
                <View accessibilityRole="alert" style={[styles.inlineError, { backgroundColor: colors.danger.wash, borderColor: colors.danger.ink }]}>
                  <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
                  <Text variant="xs" style={{ color: colors.ink, flex: 1 }}>{error}</Text>
                  <TouchableOpacity onPress={retry} hitSlop={HIT}>
                    <Text variant="xs" style={{ color: colors.accent.ink, fontWeight: fontWeights.semibold }}>Retry</Text>
                  </TouchableOpacity>
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
                icon="sparkles-outline"
                title="What are you working on?"
                message="Ask MuCLI to inspect, explain, debug, or change your workspace."
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
          bottomInset={insets.bottom}
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
  const planMode = useConnectionStore(state => state.planMode);
  const canSend = input.trim().length > 0 || selectedAttachments.length > 0;
  return (
    <View style={[styles.composerArea, { paddingBottom: Math.max(bottomInset, spacing.sm) }]}>
      <PlanModeStrip />
      {selectedAttachments.length > 0 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.selectedAttachments}>
          {selectedAttachments.map(item => (
            <View key={item.attachment_id} style={[styles.selectedAttachment, { backgroundColor: colors.raised, borderColor: colors.hairline }]}>
              <Text variant="xs" numberOfLines={1} style={{ maxWidth: 180, color: colors.ink2 }}>{item.name}</Text>
              <TouchableOpacity onPress={() => onRemoveAttachment(item.attachment_id)} hitSlop={HIT}>
                <Ionicons name="close" size={14} color={colors.ink3} />
              </TouchableOpacity>
            </View>
          ))}
        </ScrollView>
      ) : null}

      <View style={styles.composerUtilities}>
        <TouchableOpacity onPress={onModePress} style={styles.utilityButton} hitSlop={HIT_SM}>
          <Text variant="xs" style={{ color: colors.ink3, textTransform: 'capitalize' }}>{activeMode}</Text>
          <Ionicons name="chevron-down" size={13} color={colors.ink3} />
        </TouchableOpacity>
        <EffortControl variant="chip" />
        <TouchableOpacity
          onPress={onToggleArtifacts}
          style={styles.utilityIconButton}
          hitSlop={HIT_SM}
          accessibilityLabel={artifactsVisible ? 'Hide artifacts panel' : 'Show artifacts panel'}
        >
          <Ionicons
            name={artifactsVisible ? 'eye' : 'eye-off'}
            size={16}
            color={artifactsVisible ? colors.accent.ink : colors.ink3}
          />
        </TouchableOpacity>
        <TouchableOpacity onPress={onSettingsPress} style={styles.utilityIconButton} hitSlop={HIT_SM} accessibilityLabel="Session settings">
          <Ionicons name="settings-outline" size={16} color={colors.ink3} />
        </TouchableOpacity>
      </View>

      <View style={[styles.composerPane, { backgroundColor: colors.raised, borderColor: colors.hairline }]}>
        <TouchableOpacity
          onPress={onAttachmentsPress}
          style={styles.composerIconButton}
          hitSlop={HIT_SM}
          accessibilityLabel="Attach files"
        >
          <Ionicons name="attach" size={19} color={selectedAttachments.length ? colors.accent.ink : colors.ink3} />
        </TouchableOpacity>
        {/* Keep focus and the draft keyboard open while a turn is running. */}
        <TextInput
          value={input}
          onChangeText={setInput}
          placeholder={planMode ? PLAN_MODE_PLACEHOLDER : 'Message MuCLI'}
          placeholderTextColor={colors.ink3}
          multiline
          style={[styles.input, { color: colors.ink }]}
        />
        {streaming ? (
          <TouchableOpacity onPress={onStop} style={styles.sendButton} hitSlop={HIT_SM} accessibilityLabel="Stop generation">
            <Ionicons name="stop" size={16} color={colors.danger.ink} />
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            onPress={onSend}
            disabled={!canSend}
            style={[styles.sendButton, canSend && { backgroundColor: colors.accent.ink }]}
            hitSlop={HIT_SM}
            accessibilityLabel="Send message"
          >
            <Ionicons name="arrow-up" size={18} color={canSend ? colors.onInk : colors.ink3} />
          </TouchableOpacity>
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
    <ModernBottomSheet visible={visible} onClose={onClose} title="Mode">
      <View style={styles.optionList}>
        {modes.map(mode => (
          <TouchableOpacity
            key={mode.name}
            onPress={() => onSelect(mode.name)}
            disabled={mode.disabled}
            style={[styles.optionRow, { borderColor: activeMode === mode.name ? colors.accent.ink : colors.hairline, backgroundColor: activeMode === mode.name ? colors.accent.wash : colors.raised }, mode.disabled && styles.disabledOption]}
          >
            <View style={styles.optionCopy}>
              <Text variant="sm" style={{ color: colors.ink, fontWeight: fontWeights.semibold }}>{mode.display_name}</Text>
              {mode.description ? <Text variant="xs" dim>{mode.description}</Text> : null}
            </View>
            {activeMode === mode.name ? <Ionicons name="checkmark" size={18} color={colors.accent.ink} /> : null}
          </TouchableOpacity>
        ))}
      </View>
    </ModernBottomSheet>
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
    <ModernBottomSheet visible={visible} onClose={onClose} title="Session settings">
      <View style={[styles.settingsSummary, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
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
          <View key={group.name} style={[styles.settingsGroup, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
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
    </ModernBottomSheet>
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

function markdownStyles(colors: any) {
  return {
    body: { color: colors.ink, fontSize: fontSizes.base, lineHeight: typography.base.lineHeight },
    paragraph: { marginTop: 0, marginBottom: spacing.sm },
    heading1: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight, marginTop: spacing.md, marginBottom: spacing.sm },
    heading2: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold, marginTop: spacing.sm, marginBottom: spacing.xs },
    heading3: { fontSize: fontSizes.md, fontWeight: fontWeights.semibold, marginTop: spacing.sm, marginBottom: spacing.xs },
    code_inline: { backgroundColor: colors.code, borderRadius: radii.sm, paddingHorizontal: spacing.xs, fontFamily: fonts.mono },
    fence: { backgroundColor: colors.code, borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm, padding: spacing.md, marginTop: spacing.sm, marginBottom: spacing.sm, fontFamily: fonts.mono },
    code_block: { backgroundColor: colors.code, borderColor: colors.hairline, borderWidth: 1, borderRadius: radii.sm, padding: spacing.md, marginTop: spacing.sm, marginBottom: spacing.sm, fontFamily: fonts.mono },
    link: { color: colors.accent.ink, textDecorationLine: 'underline' as const },
    blockquote: { borderLeftWidth: 2, borderLeftColor: colors.hairline, paddingLeft: spacing.md, marginLeft: 0, marginTop: spacing.sm, marginBottom: spacing.sm },
    list_item: { marginTop: 2, marginBottom: 2 },
  };
}

function compactMarkdownStyles(colors: any) {
  return {
    ...markdownStyles(colors),
    body: { color: colors.ink2, fontSize: fontSizes.xs, lineHeight: typography.xs.lineHeight },
    paragraph: { marginTop: 0, marginBottom: spacing.xs },
    heading1: { fontSize: fontSizes.base, fontWeight: fontWeights.semibold, marginTop: spacing.xs, marginBottom: spacing.xs },
    heading2: { fontSize: fontSizes.base, fontWeight: fontWeights.semibold, marginTop: spacing.xs, marginBottom: spacing.xs },
    heading3: { fontSize: fontSizes.sm, fontWeight: fontWeights.semibold, marginTop: spacing.xs, marginBottom: spacing.xs },
  };
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
  // planos transcript: user = accent-tinted bubble on the right; assistant =
  // raised + border bubble with a squared top-left corner.
  userMessage: { maxWidth: '86%', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderWidth: 1, borderRightWidth: 1, borderRadius: radii.panel, borderTopRightRadius: 2 },
  assistantMessage: { width: '100%', paddingTop: spacing.sm, paddingHorizontal: spacing.md, paddingBottom: spacing.xs, borderWidth: 1, borderRadius: radii.panel, borderTopLeftRadius: 2 },
  assistantMeta: { minHeight: 26, marginTop: spacing.xs, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  copyButton: { width: 32, height: 28, alignItems: 'center', justifyContent: 'center' },
  visualizationWrap: { marginBottom: spacing.base },
  interimGroup: { marginTop: -1, marginBottom: spacing.md },
  interimHeader: { minHeight: 36, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingVertical: spacing.xs, paddingHorizontal: spacing.sm, borderWidth: 1, borderRadius: radii.control, alignSelf: 'flex-start', maxWidth: '100%' },
  interimBody: { marginLeft: spacing.xs, marginTop: spacing.xs, paddingLeft: spacing.md, borderLeftWidth: 1 },
  interimMessage: { paddingVertical: spacing.xs, marginBottom: spacing.xs },
  messageAttachments: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.sm },
  messageAttachment: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, borderWidth: 1, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  inlineError: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm, paddingHorizontal: spacing.md, marginBottom: spacing.md, borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control },
  historyLoading: { paddingTop: spacing.lg, paddingHorizontal: spacing.xs },
  loadingOlderIndicator: { paddingVertical: spacing.sm, alignItems: 'center' },
  listFooter: { minHeight: 10, paddingTop: 2 },
  composerArea: { paddingHorizontal: spacing.md, paddingTop: spacing.xs },
  composerUtilities: { minHeight: 30, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: spacing.xs, paddingHorizontal: 2, marginBottom: spacing.sm },
  utilityButton: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm },
  utilityIconButton: { width: 36, height: 32, alignItems: 'center', justifyContent: 'center' },
  composerPane: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.xs, padding: spacing.xs, borderWidth: 1, borderRadius: radii.panel },
  composerIconButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', marginBottom: 1 },
  input: { flex: 1, borderWidth: 0, paddingHorizontal: spacing.sm, paddingVertical: spacing.sm, maxHeight: 130, minHeight: 40, fontSize: fontSizes.md, lineHeight: typography.md.lineHeight, textAlignVertical: 'top' },
  sendButton: { width: 40, height: 40, borderRadius: radii.sm, justifyContent: 'center', alignItems: 'center', marginBottom: 1 },
  selectedAttachments: { paddingHorizontal: 2, paddingBottom: spacing.sm, gap: spacing.xs },
  selectedAttachment: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, borderWidth: 1, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  optionList: { gap: spacing.xs },
  optionRow: { minHeight: 56, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.control },
  disabledOption: { opacity: 0.35 },
  optionCopy: { flex: 1, paddingRight: spacing.md },
  settingsSummary: { borderWidth: 1, borderRadius: radii.panel, paddingHorizontal: spacing.md },
  settingLine: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.base },
  settingsSectionLabel: { marginTop: spacing.lg, marginBottom: spacing.sm, fontWeight: fontWeights.medium },
  settingsLoading: { paddingVertical: spacing.base },
  settingsGroup: { marginTop: spacing.md, borderWidth: 1, borderRadius: radii.panel, overflow: 'hidden' },
  settingsGroupTitle: { paddingVertical: spacing.md, paddingHorizontal: spacing.md, fontWeight: fontWeights.semibold, borderBottomWidth: 1 },
  settingsVariable: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm, paddingHorizontal: spacing.md },
  settingsVariableCopy: { flex: 1 },
});
