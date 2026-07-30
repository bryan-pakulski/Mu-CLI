import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import {
  View,
  FlatList,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import Markdown from 'react-native-markdown-display';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { modesApi, ModeInfo } from '../api/modes';
import { inspectorApi, InspectorVariableGroup } from '../api/inspector';
import { Text, Button, Card, Skeleton, EmptyState, ErrorState } from '../components';
import { BottomSheet } from '../components/BottomSheet';
import { GeneratingIndicator } from '../components/GeneratingIndicator';
import { ArtifactStrip } from '../components/ArtifactStrip';
import { CodeBlock } from '../components/CodeBlock';
import { VisualizationCard } from '../components/VisualizationCard';
import { AttachmentSheet } from '../components/AttachmentSheet';
import type { AttachmentDescriptor } from '../api/attachments';
import { useChatSession, type ChatMessage } from '../hooks/useChatSession';
import { useCommandCompletion, type CompletionItem } from '../hooks/useCommandCompletion';
import { CommandSuggestionBar } from '../components/CommandSuggestionBar';
import { spacing } from '../theme/tokens';

export function ChatScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const {
    messages,
    streaming,
    waitingForFirstToken,
    historyLoading,
    activityLabel,
    sseConnected,
    error,
    artifactRevision,
    sendMessage,
    stop,
    retry,
  } = useChatSession(activeSessionName);
  const [input, setInput] = useState('');
  const [modeSheetOpen, setModeSheetOpen] = useState(false);
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [activeMode, setActiveMode] = useState<string | null>(null);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [varGroups, setVarGroups] = useState<InspectorVariableGroup[]>([]);
  const [varsLoading, setVarsLoading] = useState(false);
  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const [selectedAttachments, setSelectedAttachments] = useState<AttachmentDescriptor[]>([]);
  const [visualizationGestureActive, setVisualizationGestureActive] = useState(false);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const yolo = useConnectionStore(state => state.yolo);
  const connection = { activeProvider, activeModel, yolo };
  const flatListRef = useRef<FlatList<ChatMessage>>(null);
  const scrollThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const followOutputRef = useRef(true);
  // MUCLI_MOBILE_VISUALIZATION_CONTROLS_V1: inline WebViews temporarily own vertical drags.
  const onVisualizationInteractionChange = useCallback((active: boolean) => {
    setVisualizationGestureActive(active);
    if (active) followOutputRef.current = false;
  }, []);
  const completion = useCommandCompletion();
  // Always clip offscreen cells on Android — disabling removeClippedSubviews
  // when visualizations are present causes ALL cells to mount simultaneously
  // on initial history load (80+ turns), exploding the native view tree and
  // triggering OOM SIGKILL by lmkd. Visualization cards are React.memo'd and
  // only mount their WebView when expanded, so clipping is safe.
  const removeClipped = Platform.OS === 'android';

  // Throttled auto-scroll: coalesce rapid content-size changes (one per
  // streaming token) into a single non-animated scrollToEnd per ~100ms.
  // Animated scroll on every token was a major perf bottleneck on mobile.
  const scrollToBottom = useCallback((force = false) => {
    if (!force && !followOutputRef.current) return;
    if (scrollThrottleRef.current) return;
    scrollThrottleRef.current = setTimeout(() => {
      scrollThrottleRef.current = null;
      if (force || followOutputRef.current) {
        flatListRef.current?.scrollToEnd({ animated: false });
      }
    }, 100);
  }, []);

  const onChatScroll = useCallback((event: any) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const distanceFromEnd = contentSize.height - layoutMeasurement.height - contentOffset.y;
    followOutputRef.current = distanceFromEnd < 96;
  }, []);

  // Clear any pending throttle on unmount
  useEffect(() => () => { if (scrollThrottleRef.current) clearTimeout(scrollThrottleRef.current); }, []);

  const send = async () => {
    const text = input.trim();
    if ((!text && selectedAttachments.length === 0) || streaming) return;
    const sent = await sendMessage(text, selectedAttachments);
    if (sent) {
      setInput('');
      setSelectedAttachments([]);
      completion.close();
    }
  };

  const onInputChange = (text: string) => {
    setInput(text);
    if (text.startsWith('/')) {
      completion.update(text);
    } else if (completion.visible) {
      completion.close();
    }
  };

  const onAcceptCompletion = (item: CompletionItem) => {
    const newText = item.value + ' ';
    setInput(newText);
    // If the command has subcommands, keep the dropdown open for next level.
    if (item.level === 0) {
      completion.update(newText);
    } else {
      completion.close();
    }
  };

  const loadModes = useCallback(async () => {
    try {
      const res = await modesApi.list();
      setModes(res.modes);
      setActiveMode(res.current);
    } catch { /* ignore */ }
  }, []);

  const selectMode = async (name: string) => {
    try {
      await modesApi.set(name);
      setActiveMode(name);
      setModeSheetOpen(false);
    } catch { /* hook will surface session errors */ }
  };

  const loadVariables = useCallback(async () => {
    setVarsLoading(true);
    try {
      const res = await inspectorApi.getVariables();
      setVarGroups(res.groups);
    } catch { /* ignore */ }
    setVarsLoading(false);
  }, []);

  const setVariable = async (key: string, value: unknown) => {
    try { await inspectorApi.setVariable(key, value); } catch { /* ignore */ }
  };

  const copyMessage = useCallback((text: string) => {
    Clipboard.setStringAsync(text);
  }, []);

  // Cap message text length for non-streaming history messages. Long
  // assistant messages (tool output, code dumps) explode the Markdown AST
  // parser and native view tree. Truncate to a render-safe limit; the full
  // text is still copyable via the copy button.
  const MAX_MESSAGE_CHARS = 6000;

  const markdownRules = useMemo(
    () => ({
      fence: (node: any) => {
        const code = node.content;
        const lang = (node.sourceInfo || '').trim();
        return (
          <CodeBlock
            key={node.key}
            code={code}
            language={lang}
            colors={colors}
          />
        );
      },
      code_block: (node: any) => {
        const code = node.content;
        return (
          <CodeBlock
            key={node.key}
            code={code}
            colors={colors}
          />
        );
      },
      code_inline: (node: any) => {
        return (
          <Text key={node.key} style={{ color: colors.syntax.keyword, fontFamily: 'monospace', fontSize: 13, backgroundColor: colors.bgHover, borderRadius: 4, paddingHorizontal: 4 }}>
            {node.content}
          </Text>
        );
      },
    }),
    [colors],
  );

  const memoizedMarkdownStyles = useMemo(() => markdownStyles(colors), [colors]);

  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    if (item.role === 'visualization' && item.artifact && activeSessionName) {
      return (
        <VisualizationCard
          artifact={item.artifact}
          sessionName={activeSessionName}
          onInteractionChange={onVisualizationInteractionChange}
        />
      );
    }

    const isUser = item.role === 'user';
    const isAssistant = item.role === 'assistant';

    // Truncate very long non-streaming messages to avoid OOM from Markdown
    // AST parsing + native view creation on large tool outputs / code dumps.
    // The full text remains available via the copy button.
    const rawText = item.text;
    const truncated = !item.streaming && rawText.length > MAX_MESSAGE_CHARS;
    const displayText = truncated
      ? rawText.slice(0, MAX_MESSAGE_CHARS) + '\n\n_… (truncated — tap copy for full text)_'
      : rawText;

    return (
      <View style={[
        styles.msgRow,
        isUser ? { justifyContent: 'flex-end' } : { justifyContent: 'flex-start' },
      ]}>
        <View
          style={[
            styles.msgBubble,
            isUser
              ? { backgroundColor: colors.bgHover, maxWidth: '84%' }
              : { backgroundColor: 'transparent', maxWidth: '100%', paddingHorizontal: 0 },
          ]}
        >
          {isUser ? (
            <Text style={{ color: colors.text }}>{displayText}</Text>
          ) : item.streaming ? (
            <Text style={{ color: colors.text, fontSize: 15, lineHeight: 23 }}>
              {displayText}
            </Text>
          ) : (
            <Markdown
              style={memoizedMarkdownStyles}
              rules={markdownRules}
            >
              {displayText}
            </Markdown>
          )}
          {isUser && item.attachments && item.attachments.length > 0 ? (
            <View style={styles.messageAttachments}>
              {item.attachments.map(attachment => (
                <View key={attachment.attachment_id} style={[styles.messageAttachment, { backgroundColor: colors.bgLift }]}>
                  <Ionicons name="document-outline" size={14} color={colors.textDim} />
                  <Text variant="xs" numberOfLines={1} style={{ color: colors.textSoft, maxWidth: 210 }}>{attachment.name}</Text>
                </View>
              ))}
            </View>
          ) : null}
          {isAssistant && rawText.length > 0 && (
            <TouchableOpacity
              onPress={() => copyMessage(rawText)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              style={styles.copyButton}
            >
              <Ionicons name="copy-outline" size={14} color={colors.textDim} />
            </TouchableOpacity>
          )}
        </View>
      </View>
    );
  }, [activeSessionName, colors, copyMessage, markdownRules, memoizedMarkdownStyles, onVisualizationInteractionChange]);

  return (
    <SafeAreaView edges={['bottom']} style={{ flex: 1, backgroundColor: colors.bg }}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={insets.bottom}
      >
        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={renderMessage}
          initialNumToRender={4}
          maxToRenderPerBatch={3}
          updateCellsBatchingPeriod={80}
          windowSize={5}
          removeClippedSubviews={removeClipped}
          scrollEnabled={!visualizationGestureActive}
          contentContainerStyle={[
            styles.messageList,
            messages.length === 0 ? styles.messageListEmpty : null,
          ]}
          keyboardShouldPersistTaps="always"
          keyboardDismissMode="none"
          onScroll={onChatScroll}
          scrollEventThrottle={100}
          onContentSizeChange={() => scrollToBottom(false)}
          onLayout={() => scrollToBottom(false)}
          ListHeaderComponent={
            error && messages.length > 0 ? (
              <View style={[styles.inlineError, { backgroundColor: colors.bgHover }]}>
                <Ionicons name="warning-outline" size={15} color={colors.error} />
                <Text variant="xs" style={{ color: colors.textSoft, flex: 1 }}>{error}</Text>
                <TouchableOpacity onPress={retry} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                  <Text variant="xs" style={{ color: colors.accent, fontWeight: '600' }}>Retry</Text>
                </TouchableOpacity>
              </View>
            ) : null
          }
          ListEmptyComponent={
            historyLoading ? (
              <View style={styles.historyLoading}>
                <Skeleton height={18} style={{ marginBottom: 10, width: '72%' }} />
                <Skeleton height={18} style={{ marginBottom: 10, width: '88%' }} />
                <Skeleton height={18} style={{ width: '56%' }} />
              </View>
            ) : error ? (
              <ErrorState message={error} onRetry={retry} />
            ) : (
              <EmptyState
                icon="sparkles-outline"
                title="What should we build?"
                message="Ask MuCLI to inspect, explain, debug, or change your workspace."
                actionLabel={activeMode ? `Mode: ${activeMode}` : 'Select Mode'}
                onAction={() => { loadModes(); setModeSheetOpen(true); }}
              />
            )
          }
          ListFooterComponent={
            <View>
              {streaming && waitingForFirstToken ? (
                <GeneratingIndicator label={sseConnected ? activityLabel : 'Reconnecting to session'} />
              ) : null}
            </View>
          }
        />
        <ArtifactStrip sessionName={activeSessionName} refreshKey={artifactRevision} />
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
          colors={colors}
          insets={insets}
          onModePress={() => { loadModes(); setModeSheetOpen(true); }}
          onSettingsPress={() => { loadVariables(); setSettingsSheetOpen(true); }}
          onAttachmentsPress={() => setAttachmentsOpen(true)}
          selectedAttachments={selectedAttachments}
          onRemoveAttachment={(attachmentId) => setSelectedAttachments(current => current.filter(item => item.attachment_id !== attachmentId))}
        />
        <AttachmentSheet
          visible={attachmentsOpen}
          sessionName={activeSessionName || ''}
          selected={selectedAttachments}
          onSelectedChange={setSelectedAttachments}
          onClose={() => setAttachmentsOpen(false)}
        />
        <ModeBottomSheet
          visible={modeSheetOpen}
          onClose={() => setModeSheetOpen(false)}
          modes={modes}
          activeMode={activeMode}
          onSelect={selectMode}
          colors={colors}
        />
        <SettingsBottomSheet
          visible={settingsSheetOpen}
          onClose={() => setSettingsSheetOpen(false)}
          varGroups={varGroups}
          varsLoading={varsLoading}
          connection={connection}
          onSetVariable={setVariable}
          colors={colors}
        />
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

interface ComposerProps {
  input: string;
  setInput: (s: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  colors: any;
  insets: { bottom: number };
  onModePress: () => void;
  onSettingsPress: () => void;
  onAttachmentsPress: () => void;
  selectedAttachments: AttachmentDescriptor[];
  onRemoveAttachment: (attachmentId: string) => void;
}

function Composer({ input, setInput, onSend, onStop, streaming, colors, insets, onModePress, onAttachmentsPress, selectedAttachments, onRemoveAttachment }: ComposerProps) {
  return (
    <View>
      {selectedAttachments.length > 0 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.selectedAttachments}>
          {selectedAttachments.map(item => (
            <View key={item.attachment_id} style={[styles.selectedAttachment, { backgroundColor: colors.bgHover }]}>
              <Text variant="xs" numberOfLines={1} style={{ maxWidth: 180 }}>{item.name}</Text>
              <TouchableOpacity onPress={() => onRemoveAttachment(item.attachment_id)}>
                <Ionicons name="close" size={15} color={colors.textDim} />
              </TouchableOpacity>
            </View>
          ))}
        </ScrollView>
      ) : null}
      <View
      style={[
        styles.composer,
        {
          backgroundColor: colors.bgLift,
          borderColor: colors.border,
          marginBottom: Math.max(insets.bottom, 8),
        },
      ]}
    >
      <TouchableOpacity
        onPress={onAttachmentsPress}
        hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        style={[styles.composerIconButton, { backgroundColor: selectedAttachments.length ? colors.accentSoft : colors.bgHover }]}
      >
        <Ionicons name="attach" size={19} color={selectedAttachments.length ? colors.accent : colors.textDim} />
      </TouchableOpacity>
      <TouchableOpacity
        onPress={onModePress}
        hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        style={[styles.composerIconButton, { backgroundColor: colors.bgHover }]}
      >
        <Ionicons name="options-outline" size={18} color={colors.textDim} />
      </TouchableOpacity>
      <TextInput
        value={input}
        onChangeText={setInput}
        placeholder="Message MuCLI"
        placeholderTextColor={colors.textDim}
        multiline
        style={[styles.input, { color: colors.text }]}
        // Keep focus and the draft keyboard open while a turn is running.
        // Sending is still gated by the composer action and hook busy state.
      />
      {streaming ? (
        <TouchableOpacity
          onPress={onStop}
          style={[styles.sendBtn, { backgroundColor: colors.bgHover }]}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="stop" size={17} color={colors.error} />
        </TouchableOpacity>
      ) : (
        <TouchableOpacity
          onPress={onSend}
          disabled={!input.trim() && selectedAttachments.length === 0}
          style={[styles.sendBtn, { backgroundColor: (input.trim() || selectedAttachments.length) ? colors.accent : colors.bgHover }]}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-up" size={19} color={(input.trim() || selectedAttachments.length) ? colors.accentText : colors.textDim} />
        </TouchableOpacity>
      )}
      </View>
    </View>
  );
}

interface ModeBottomSheetProps {
  visible: boolean;
  onClose: () => void;
  modes: ModeInfo[];
  activeMode: string | null;
  onSelect: (name: string) => void;
  colors: any;
}

function ModeBottomSheet({ visible, onClose, modes, activeMode, onSelect, colors }: ModeBottomSheetProps) {
  return (
    <BottomSheet visible={visible} onClose={onClose}>
      <View style={{ padding: spacing.sm }}>
        <Text variant="lg" style={{ marginBottom: spacing.base }}>Select Mode</Text>
        {modes.map(m => (
          <TouchableOpacity
            key={m.name}
            onPress={() => onSelect(m.name)}
            disabled={m.disabled}
            style={{ minHeight: 44, paddingVertical: 12 }}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <View style={{ flex: 1 }}>
                <Text variant="base" style={{ fontWeight: '500', color: m.disabled ? colors.textDim : colors.text }}>
                  {m.display_name}
                </Text>
                {m.description && (
                  <Text variant="xs" style={{ color: colors.textDim, marginTop: 2 }}>{m.description}</Text>
                )}
              </View>
              {activeMode === m.name && <Ionicons name="checkmark" size={20} color={colors.accent} />}
            </View>
          </TouchableOpacity>
        ))}
      </View>
    </BottomSheet>
  );
}

function markdownStyles(colors: any) {
  return {
    body: { color: colors.text, fontSize: 15, lineHeight: 23 },
    paragraph: { marginTop: 0, marginBottom: 9 },
    heading1: { fontSize: 21, fontWeight: '700' as const, marginTop: 12, marginBottom: 8 },
    heading2: { fontSize: 19, fontWeight: '700' as const, marginTop: 10, marginBottom: 6 },
    heading3: { fontSize: 17, fontWeight: '600' as const, marginTop: 8, marginBottom: 4 },
    code_inline: { backgroundColor: colors.bgHover, borderRadius: 4, paddingHorizontal: 4 },
    fence: { backgroundColor: colors.bgHover, borderRadius: 10, padding: 12, marginTop: 8, marginBottom: 8 },
    code_block: { backgroundColor: colors.bgHover, borderRadius: 10, padding: 12, marginTop: 8, marginBottom: 8 },
    link: { color: colors.accent, textDecorationLine: 'underline' as const },
    blockquote: { borderLeftWidth: 2, borderLeftColor: colors.borderStrong, paddingLeft: 12, marginLeft: 0, marginTop: 8, marginBottom: 8 },
    list_item: { marginTop: 3, marginBottom: 3 },
  };
}

const styles = StyleSheet.create({
  messageList: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 96 },
  messageListEmpty: { flexGrow: 1 },
  msgRow: { flexDirection: 'row', marginBottom: 14 },
  msgBubble: { borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10 },
  copyButton: { alignSelf: 'flex-start', minWidth: 28, minHeight: 28, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
  messageAttachments: { flexDirection: 'row', flexWrap: 'wrap', gap: 5, marginTop: 7 },
  messageAttachment: { flexDirection: 'row', alignItems: 'center', gap: 5, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 5 },
  selectedAttachments: { paddingHorizontal: 12, paddingTop: 6, gap: 6 },
  selectedAttachment: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 999, paddingHorizontal: 9, paddingVertical: 6 },
  codeBlock: { borderRadius: 10, padding: 12, marginVertical: 5 },
  codeBlockHeader: { flexDirection: 'row', justifyContent: 'flex-end', marginBottom: 3 },
  inlineError: { flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 9, marginBottom: 14 },
  historyLoading: { paddingTop: 22, paddingHorizontal: 4 },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 5,
    marginHorizontal: 10,
    marginTop: 6,
    padding: 5,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 24,
  },
  composerIconButton: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 2,
  },
  input: { flex: 1, borderWidth: 0, paddingHorizontal: 8, paddingVertical: 9, maxHeight: 120, minHeight: 40 },
  sendBtn: { width: 38, height: 38, borderRadius: 19, justifyContent: 'center', alignItems: 'center' },
  settingsRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 8, minHeight: 44 },
  settingsSection: { marginTop: 12, marginBottom: 4 },
  settingsLabel: { fontSize: 12, color: '#94a3b8', marginBottom: 4 },
  varValue: { fontSize: 12, color: '#94a3b8' },
});

// Session settings BottomSheet — provider/model info + variables
interface SettingsBottomSheetProps {
  visible: boolean;
  onClose: () => void;
  varGroups: InspectorVariableGroup[];
  varsLoading: boolean;
  connection: { activeProvider: string | null; activeModel: string | null; yolo: boolean };
  onSetVariable: (key: string, value: unknown) => void;
  colors: any;
}

function SettingsBottomSheet({ visible, onClose, varGroups, varsLoading, connection, colors }: SettingsBottomSheetProps) {
  return (
    <BottomSheet visible={visible} onClose={onClose} title="Session Settings">
      <View style={styles.settingsSection}>
        <Text variant="sm" style={{ fontWeight: '600', marginBottom: 8 }}>Current Session</Text>
        <View style={styles.settingsRow}>
          <Text variant="xs" style={{ color: colors.textDim }}>Provider</Text>
          <Text variant="sm">{connection.activeProvider || '—'}</Text>
        </View>
        <View style={styles.settingsRow}>
          <Text variant="xs" style={{ color: colors.textDim }}>Model</Text>
          <Text variant="sm">{connection.activeModel || '—'}</Text>
        </View>
        <View style={styles.settingsRow}>
          <Text variant="xs" style={{ color: colors.textDim }}>YOLO</Text>
          <Text variant="sm">{connection.yolo ? 'On' : 'Off'}</Text>
        </View>
      </View>

      <View style={styles.settingsSection}>
        <Text variant="sm" style={{ fontWeight: '600', marginBottom: 8 }}>Variables</Text>
        {varsLoading ? (
          <Text variant="xs" style={{ color: colors.textDim }}>Loading…</Text>
        ) : varGroups.length === 0 ? (
          <Text variant="xs" style={{ color: colors.textDim }}>No variables configured</Text>
        ) : (
          varGroups.map(group => (
            <View key={group.name} style={{ marginBottom: 12 }}>
              <Text variant="xs" style={{ fontWeight: '500', marginBottom: 4 }}>{group.name}</Text>
              {group.variables.map(v => (
                <View key={v.key} style={styles.settingsRow}>
                  <View style={{ flex: 1 }}>
                    <Text variant="xs" style={{ color: colors.text }}>{v.key}</Text>
                    <Text variant="xs" style={styles.varValue}>{v.help}</Text>
                  </View>
                  <Text variant="xs" style={{ color: v.is_default ? colors.textDim : colors.accent }}>
                    {String(v.value)}
                  </Text>
                </View>
              ))}
            </View>
          ))
        )}
      </View>
    </BottomSheet>
  );
}