import React, { useState, useRef, useCallback, useEffect } from 'react';
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
import { chatApi } from '../api/chat';
import { subscribeToEvents, SSESubscription } from '../api/sse';
import { modesApi, ModeInfo } from '../api/modes';
import { inspectorApi, InspectorVariableGroup } from '../api/inspector';
import { Text, Button, Card, Skeleton, EmptyState, ErrorState } from '../components';
import { BottomSheet } from '../components/BottomSheet';
import { spacing } from '../theme/tokens';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'thinking';
  text: string;
  turnId?: string;
  streaming?: boolean;
}

export function ChatScreen() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { activeSessionName } = useConnectionStore();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [waitingForFirstToken, setWaitingForFirstToken] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modeSheetOpen, setModeSheetOpen] = useState(false);
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [activeMode, setActiveMode] = useState<string | null>(null);
  const [settingsSheetOpen, setSettingsSheetOpen] = useState(false);
  const [varGroups, setVarGroups] = useState<InspectorVariableGroup[]>([]);
  const [varsLoading, setVarsLoading] = useState(false);
  const connection = useConnectionStore();
  const flatListRef = useRef<FlatList<ChatMessage>>(null);
  const sseSubRef = useRef<SSESubscription | null>(null);
  const msgIdRef = useRef(0);

  const nextId = () => `msg-${++msgIdRef.current}`;

  const appendAssistantDelta = useCallback((turnId: string, delta: string) => {
    setMessages(prev => {
      const idx = prev.findIndex(m => m.turnId === turnId && m.role === 'assistant');
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = { ...updated[idx], text: updated[idx].text + delta, streaming: true };
        return updated;
      }
      return [...prev, { id: nextId(), role: 'assistant', text: delta, turnId, streaming: true }];
    });
  }, []);

  const finalizeAssistant = useCallback((turnId: string) => {
    setMessages(prev => prev.map(m =>
      m.turnId === turnId && m.role === 'assistant' ? { ...m, streaming: false } : m,
    ));
    setStreaming(false);
    setWaitingForFirstToken(false);
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');
    setError(null);
    setStreaming(true);
    setWaitingForFirstToken(true);

    const userMsg: ChatMessage = { id: nextId(), role: 'user', text };
    setMessages(prev => [...prev, userMsg]);

    // Subscribe to SSE BEFORE sending so we don't miss early events
    if (sseSubRef.current) sseSubRef.current.close();
    sseSubRef.current = subscribeToEvents({
      onMessage: (event) => {
        const kind = event.kind as string;
        if (kind === 'assistant_delta') {
          setWaitingForFirstToken(false);
          appendAssistantDelta(String(event.turn_id), String(event.text));
        } else if (kind === 'assistant_end' || kind === 'turn_complete') {
          if (event.turn_id) finalizeAssistant(String(event.turn_id));
          else finalizeAssistant('__done__');
        } else if (kind === 'error') {
          setError(String(event.text || 'Stream error'));
          setStreaming(false);
          setWaitingForFirstToken(false);
        }
      },
      onError: (e) => {
        setError(e.message);
        setStreaming(false);
        setWaitingForFirstToken(false);
      },
    });

    try {
      await chatApi.send(text, activeSessionName || undefined);
    } catch (e) {
      setError(String(e));
      setStreaming(false);
      setWaitingForFirstToken(false);
      sseSubRef.current?.close();
    }
  };

  const stop = async () => {
    try {
      await chatApi.interrupt(activeSessionName || undefined);
    } catch { /* best effort */ }
    sseSubRef.current?.close();
    setStreaming(false);
    setWaitingForFirstToken(false);
    setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
  };

  const retry = () => {
    setError(null);
    // Re-subscribe and reload session history if available
    if (sseSubRef.current) sseSubRef.current.close();
    sseSubRef.current = subscribeToEvents({
      onMessage: (event) => {
        const kind = event.kind as string;
        if (kind === 'assistant_delta') {
          setWaitingForFirstToken(false);
          appendAssistantDelta(String(event.turn_id), String(event.text));
        } else if (kind === 'assistant_end' || kind === 'turn_complete') {
          if (event.turn_id) finalizeAssistant(String(event.turn_id));
        } else if (kind === 'error') {
          setError(String(event.text || 'Stream error'));
          setStreaming(false);
          setWaitingForFirstToken(false);
        }
      },
    });
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
    } catch (e) {
      setError(String(e));
    }
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

  useEffect(() => {
    return () => { sseSubRef.current?.close(); };
  }, []);

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
        <View style={[
          styles.msgBubble,
          {
            backgroundColor: isUser ? colors.accent : colors.bgLift,
            maxWidth: '85%',
          },
        ]}>
          {isAssistant && (
            <View style={styles.msgHeader}>
              <TouchableOpacity onPress={() => copyMessage(item.text)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <Ionicons name="copy-outline" size={16} color={colors.textDim} />
              </TouchableOpacity>
            </View>
          )}
          {isUser ? (
            <Text style={{ color: colors.accentText }}>{item.text}</Text>
          ) : (
            <Markdown
              style={markdownStyles(colors)}
              rules={{
                fence: (node) => {
                  const code = node.content;
                  return (
                    <View key={node.key} style={styles.codeBlock}>
                      <View style={styles.codeBlockHeader}>
                        <TouchableOpacity onPress={() => copyMessage(code)} hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}>
                          <Ionicons name="copy-outline" size={16} color={colors.textDim} />
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
                    <View key={node.key} style={styles.codeBlock}>
                      <View style={styles.codeBlockHeader}>
                        <TouchableOpacity onPress={() => copyMessage(code)} hitSlop={{ top: 4, bottom: 4, left: 4, right: 4 }}>
                          <Ionicons name="copy-outline" size={16} color={colors.textDim} />
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
        </View>
      </View>
    );
  };

  if (error && messages.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={retry} />
      </SafeAreaView>
    );
  }

  if (messages.length === 0 && !waitingForFirstToken) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState
          title="No messages yet"
          message="Send a message to start chatting"
          actionLabel={activeMode ? `Mode: ${activeMode}` : 'Select Mode'}
          onAction={() => { loadModes(); setModeSheetOpen(true); }}
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
          contentContainerStyle={{ padding: spacing.base, paddingBottom: 80 }}
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: false })}
          onLayout={() => flatListRef.current?.scrollToEnd({ animated: false })}
          ListFooterComponent={
            waitingForFirstToken ? (
              <View style={{ padding: spacing.sm }}>
                <Skeleton height={20} style={{ marginBottom: 4, width: '70%' }} />
              </View>
            ) : null
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

function Composer({ input, setInput, onSend, onStop, streaming, colors, insets, onModePress, onSettingsPress }: ComposerProps) {
  return (
    <View style={[styles.composer, { backgroundColor: colors.bgLift, paddingBottom: insets.bottom }]}>
      <TouchableOpacity onPress={onModePress} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
        <Ionicons name="options-outline" size={24} color={colors.textDim} style={{ paddingHorizontal: 8 }} />
      </TouchableOpacity>
      <TouchableOpacity onPress={onSettingsPress} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
        <Ionicons name="settings-outline" size={24} color={colors.textDim} style={{ paddingHorizontal: 8 }} />
      </TouchableOpacity>
      <TextInput
        value={input}
        onChangeText={setInput}
        placeholder="Message…"
        placeholderTextColor={colors.textDim}
        multiline
        style={[styles.input, { color: colors.text, borderColor: colors.border }]}
        editable={!streaming}
      />
      {streaming ? (
        <TouchableOpacity onPress={onStop} style={styles.sendBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Ionicons name="stop-circle" size={28} color={colors.error} />
        </TouchableOpacity>
      ) : (
        <TouchableOpacity onPress={onSend} disabled={!input.trim()} style={styles.sendBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Ionicons name="send" size={24} color={input.trim() ? colors.accent : colors.textDim} />
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
    body: { color: colors.text, fontSize: 15, lineHeight: 22 },
    paragraph: { marginTop: 0, marginBottom: 8 },
    heading1: { fontSize: 22, fontWeight: '700' as const, marginTop: 12, marginBottom: 8 },
    heading2: { fontSize: 20, fontWeight: '700' as const, marginTop: 10, marginBottom: 6 },
    heading3: { fontSize: 18, fontWeight: '600' as const, marginTop: 8, marginBottom: 4 },
    code_inline: { backgroundColor: colors.bgHover, borderRadius: 3, paddingHorizontal: 4 },
    fence: { backgroundColor: colors.bgHover, borderRadius: 6, padding: 12, marginTop: 8, marginBottom: 8 },
    code_block: { backgroundColor: colors.bgHover, borderRadius: 6, padding: 12, marginTop: 8, marginBottom: 8 },
    link: { color: colors.accent, textDecorationLine: 'underline' as const },
    blockquote: { borderLeftWidth: 3, borderLeftColor: colors.border, paddingLeft: 12, marginLeft: 0, marginTop: 8, marginBottom: 8 },
    list_item: { marginTop: 4, marginBottom: 4 },
  };
}

const styles = StyleSheet.create({
  msgRow: { flexDirection: 'row', marginBottom: spacing.sm },
  msgBubble: { borderRadius: 12, padding: spacing.sm },
  msgHeader: { flexDirection: 'row', justifyContent: 'flex-end', marginBottom: 4, minHeight: 20 },
  codeBlock: { borderRadius: 6, padding: 12, marginVertical: 4 },
  codeBlockHeader: { flexDirection: 'row', justifyContent: 'flex-end', marginBottom: 4 },
  composer: { flexDirection: 'row', alignItems: 'flex-end', paddingHorizontal: spacing.sm, paddingTop: spacing.sm },
  input: { flex: 1, borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, maxHeight: 120, minHeight: 44 },
  sendBtn: { padding: 8, minHeight: 44, minWidth: 44, justifyContent: 'center', alignItems: 'center' },
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