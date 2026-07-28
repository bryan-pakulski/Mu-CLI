import React, { useState, useRef, useCallback } from 'react';
import {
  View,
  FlatList,
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
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
import { useChatSession, type ChatMessage } from '../hooks/useChatSession';
import { spacing } from '../theme/tokens';

export function ChatScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { activeSessionName } = useConnectionStore();
  const {
    messages,
    streaming,
    waitingForFirstToken,
    historyLoading,
    activityLabel,
    sseConnected,
    error,
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
  const connection = useConnectionStore();
  const flatListRef = useRef<FlatList<ChatMessage>>(null);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');
    await sendMessage(text);
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

  const copyMessage = (text: string) => {
    Clipboard.setStringAsync(text);
  };

  const renderMessage = ({ item }: { item: ChatMessage }) => {
    const isUser = item.role === 'user';
    const isAssistant = item.role === 'assistant';

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
            <Text style={{ color: colors.text }}>{item.text}</Text>
          ) : (
            <Markdown
              style={markdownStyles(colors)}
              rules={{
                fence: (node) => {
                  const code = node.content;
                  return (
                    <View key={node.key} style={[styles.codeBlock, { backgroundColor: colors.bgHover }]}>
                      <View style={styles.codeBlockHeader}>
                        <TouchableOpacity onPress={() => copyMessage(code)} hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}>
                          <Ionicons name="copy-outline" size={15} color={colors.textDim} />
                        </TouchableOpacity>
                      </View>
                      <Text variant="sm" style={{ color: colors.textSoft, fontFamily: 'monospace' }}>
                        {code}
                      </Text>
                    </View>
                  );
                },
                code_block: (node) => {
                  const code = node.content;
                  return (
                    <View key={node.key} style={[styles.codeBlock, { backgroundColor: colors.bgHover }]}>
                      <View style={styles.codeBlockHeader}>
                        <TouchableOpacity onPress={() => copyMessage(code)} hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}>
                          <Ionicons name="copy-outline" size={15} color={colors.textDim} />
                        </TouchableOpacity>
                      </View>
                      <Text variant="sm" style={{ color: colors.textSoft, fontFamily: 'monospace' }}>
                        {code}
                      </Text>
                    </View>
                  );
                },
                code_inline: (node) => {
                  return (
                    <Text key={node.key} style={{ color: colors.accent, fontFamily: 'monospace', fontSize: 13 }}>
                      {node.content}
                    </Text>
                  );
                },
              }}
            >
              {item.text}
            </Markdown>
          )}
          {isAssistant && item.text.length > 0 && (
            <TouchableOpacity
              onPress={() => copyMessage(item.text)}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              style={styles.copyButton}
            >
              <Ionicons name="copy-outline" size={14} color={colors.textDim} />
            </TouchableOpacity>
          )}
        </View>
      </View>
    );
  };

  if (error && messages.length === 0 && !historyLoading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={retry} />
      </SafeAreaView>
    );
  }

  if (messages.length === 0 && !waitingForFirstToken && !historyLoading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState
          icon="sparkles-outline"
          title="What should we build?"
          message="Ask MuCLI to inspect, explain, debug, or change your workspace."
          actionLabel={activeMode ? `Mode: ${activeMode}` : 'Select Mode'}
          onAction={() => { loadModes(); setModeSheetOpen(true); }}
        />
        <ArtifactStrip sessionName={activeSessionName} />
        <Composer
          input={input}
          setInput={setInput}
          onSend={send}
          onStop={stop}
          streaming={streaming}
          colors={colors}
          insets={insets}
          onModePress={() => { loadModes(); setModeSheetOpen(true); }}
          onSettingsPress={() => { loadVariables(); setSettingsSheetOpen(true); }}
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
      </SafeAreaView>
    );
  }

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
          contentContainerStyle={styles.messageList}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: messages.length > 0 })}
          onLayout={() => flatListRef.current?.scrollToEnd({ animated: false })}
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
            ) : null
          }
          ListFooterComponent={
            <View>
              <ArtifactStrip sessionName={activeSessionName} />
              {streaming && waitingForFirstToken ? (
                <GeneratingIndicator label={sseConnected ? activityLabel : 'Reconnecting to session'} />
              ) : null}
            </View>
          }
        />
        <Composer
          input={input}
          setInput={setInput}
          onSend={send}
          onStop={stop}
          streaming={streaming}
          colors={colors}
          insets={insets}
          onModePress={() => { loadModes(); setModeSheetOpen(true); }}
          onSettingsPress={() => { loadVariables(); setSettingsSheetOpen(true); }}
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
}

function Composer({ input, setInput, onSend, onStop, streaming, colors, insets, onModePress }: ComposerProps) {
  return (
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
        editable={!streaming}
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
          disabled={!input.trim()}
          style={[styles.sendBtn, { backgroundColor: input.trim() ? colors.accent : colors.bgHover }]}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
        >
          <Ionicons name="arrow-up" size={19} color={input.trim() ? colors.accentText : colors.textDim} />
        </TouchableOpacity>
      )}
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
  msgRow: { flexDirection: 'row', marginBottom: 14 },
  msgBubble: { borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10 },
  copyButton: { alignSelf: 'flex-start', minWidth: 28, minHeight: 28, alignItems: 'center', justifyContent: 'center', marginTop: 1 },
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