// An environment's terminal (web /containers shell; WS /api/containers/{name}/shell). The server
// gives a shell to this computer or a paired device only (mobile_parity.md §7a): the socket is
// signed with the device token, and a refusal (1008: not paired, or signed out) says so and offers
// pairing. Tab completion, history (↑/↓) and follow-the-output carry over from the old Shell screen.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, NativeScrollEvent, NativeSyntheticEvent, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect, useNavigation, useRoute } from '@react-navigation/native';
import { authHeaders } from '../../api/auth';
import { sessionsApi } from '../../api/sessions';
import { useConnectionStore } from '../../store/connection';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { Banner, Button, EmptyState, IconButton, PageHeader, Text, TextField } from '../../ui';

const SHELL_HISTORY = new Map<string, string[]>();
const MAX_LINES = 2000;

// ANSI escape sequences are stripped for plain-text display.
const ANSI_RE = /\u001b\[[0-9;]*[a-zA-Z]|\u001b\][^\u0007]*\u0007|\u001b[()][AB012]|\x07/g;
export const stripAnsi = (s: string) => s.replace(ANSI_RE, '');

type CompletionMessage = { type: 'shell_completion'; source?: string; start?: number; end?: number; replacement?: string; candidates?: string[] };

export type CloseInfo = { text: string; pairing: boolean };

/** What a closed socket means, in words (1008 = the server refused this phone). */
export function describeClose(code: number, reason?: string): CloseInfo {
  if (code === 1008) {
    const signedOut = /signed out/i.test(reason || '');
    return {
      text: signedOut ? 'This phone was signed out of mucli, so its terminal closed. Pair it again to reconnect.'
        : 'The terminal needs a paired phone. Pair this phone from Settings → Devices on the computer.',
      pairing: true,
    };
  }
  if (code === 1000) return { text: 'The terminal closed.', pairing: false };
  if (code === 1011) return { text: `The environment couldn't open a shell${reason ? `: ${reason}` : '.'}`, pairing: false };
  return { text: `The connection closed (${code}${reason ? ` ${reason}` : ''}).`, pairing: false };
}

/** Keep the last MAX_LINES lines. */
export function trimOutput(text: string): string {
  const lines = text.split('\n');
  return lines.length > MAX_LINES ? lines.slice(-MAX_LINES).join('\n') : text;
}

export function TerminalScreen() {
  const navigation = useNavigation<any>();
  const route = useRoute<any>();
  const { colors } = useTheme();
  const { baseUrl, activeSessionName } = useConnectionStore();
  const [container, setContainer] = useState<string | null>(route.params?.container || null);
  const [output, setOutput] = useState('');
  const [input, setInputState] = useState('');
  const [state, setState] = useState<'idle' | 'connecting' | 'open'>('idle');
  const [closed, setClosed] = useState<CloseInfo | null>(null);
  const [following, setFollowing] = useState(true);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const outputRef = useRef('');
  const followRef = useRef(true);
  const inputValue = useRef('');
  const history = useRef<string[]>([]);
  const historyIndex = useRef(0);
  const historyDraft = useRef('');
  const completionSeq = useRef(0);

  const setInput = useCallback((value: string) => { inputValue.current = value; setInputState(value); }, []);
  const setFollow = useCallback((value: boolean) => { followRef.current = value; setFollowing(value); }, []);

  const append = useCallback((text: string, force = false) => {
    const clean = stripAnsi(text);
    if (!clean) return;
    outputRef.current = trimOutput(outputRef.current + clean);
    setOutput(outputRef.current);
    if (force || followRef.current) requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: false }));
  }, []);

  const onCompletion = useCallback((message: CompletionMessage) => {
    const current = inputValue.current;
    if (message.source !== current) return;
    const start = Math.max(0, Math.min(Number(message.start) || 0, current.length));
    const end = Math.max(start, Math.min(Number(message.end) || current.length, current.length));
    if (message.replacement) { setInput(current.slice(0, start) + message.replacement + current.slice(end)); return; }
    const candidates = (message.candidates || []).map(String);
    if (candidates.length > 1) append(`\n${candidates.join('  ')}\n`);
  }, [append, setInput]);

  const disconnect = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws) { try { ws.close(); } catch { /* already closed */ } }
    setState('idle');
  }, []);

  const connect = useCallback(async () => {
    if (state !== 'idle') return;
    setState('connecting');
    setClosed(null);
    outputRef.current = '';
    setOutput('');
    setFollow(true);
    let name = container;
    if (!name && activeSessionName) {
      try {
        const info = await sessionsApi.getContainer(activeSessionName) as Record<string, unknown>;
        if (typeof info.name === 'string') { name = info.name; setContainer(info.name); }
      } catch { /* no environment attached */ }
    }
    if (!name) { setState('idle'); setClosed({ text: 'Choose an environment first: open one under Environments and tap Terminal.', pairing: false }); return; }
    history.current = [...(SHELL_HISTORY.get(name) || [])];
    historyIndex.current = history.current.length;
    const wsUrl = `${baseUrl.replace(/^http:\/\//i, 'ws://').replace(/^https:\/\//i, 'wss://')}/api/containers/${encodeURIComponent(name)}/shell`;
    try {
      // React Native's WebSocket takes headers as a third argument: paired devices sign the socket.
      const WS = WebSocket as unknown as new (url: string, protocols?: string | string[] | null, options?: { headers: Record<string, string> }) => WebSocket;
      const ws = new WS(wsUrl, null, { headers: authHeaders(baseUrl) });
      wsRef.current = ws;
      ws.onopen = () => { if (wsRef.current !== ws) return; setState('open'); append(`Connected to ${name}\n`, true); };
      ws.onmessage = (event: WebSocketMessageEvent) => {
        const data = typeof event.data === 'string' ? event.data : '';
        if (!data) return;
        if (data.startsWith('{')) {
          try {
            const message = JSON.parse(data) as CompletionMessage;
            if (message.type === 'shell_completion') { onCompletion(message); return; }
          } catch { /* ordinary output that starts with "{" */ }
        }
        append(data);
      };
      ws.onerror = () => { if (wsRef.current === ws) setClosed({ text: "Couldn't reach the terminal. Check the computer is reachable.", pairing: false }); };
      ws.onclose = (event: CloseEvent) => {
        if (wsRef.current !== ws) return;
        wsRef.current = null;
        setState('idle');
        const info = describeClose(event.code, event.reason);
        if (info.pairing) useConnectionStore.getState().setPairingRequired(true);
        setClosed(info);
      };
    } catch (err) {
      setState('idle');
      setClosed({ text: String(err), pairing: false });
    }
  }, [activeSessionName, append, baseUrl, container, onCompletion, setFollow, state]);

  const send = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const command = inputValue.current;
    const value = command.trim();
    if (value) {
      history.current = [...history.current.filter(item => item !== value), value].slice(-200);
      historyIndex.current = history.current.length;
      historyDraft.current = '';
      if (container) SHELL_HISTORY.set(container, history.current);
    }
    setFollow(true);
    append(`$ ${command}\n`, true);
    ws.send(`${command}\n`);
    setInput('');
  }, [append, container, setFollow, setInput]);

  const cycle = useCallback((direction: -1 | 1) => {
    const list = history.current;
    if (!list.length) return;
    if (historyIndex.current === list.length) historyDraft.current = inputValue.current;
    const next = Math.max(0, Math.min(list.length, historyIndex.current + direction));
    historyIndex.current = next;
    setInput(next === list.length ? historyDraft.current : list[next]);
  }, [setInput]);

  const complete = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const line = inputValue.current;
    ws.send(JSON.stringify({ type: 'shell_complete', request_id: String(++completionSeq.current), line, cursor: line.length }));
  }, []);

  const onScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    setFollow(contentSize.height - layoutMeasurement.height - contentOffset.y < 72);
  }, [setFollow]);

  useFocusEffect(useCallback(() => () => disconnect(), [disconnect]));
  useEffect(() => () => { const ws = wsRef.current; wsRef.current = null; if (ws) { try { ws.close(); } catch { /* closed */ } } }, []);

  const open = state === 'open';
  return (
    <SafeAreaView style={styles.page} edges={['bottom']} testID="terminal-screen">
      <PageHeader title={container ? `Terminal · ${container}` : 'Terminal'} back={{ label: 'Back', onPress: () => navigation.goBack() }}
        state={[{ text: open ? 'Connected' : state === 'connecting' ? 'Connecting…' : 'Not connected', tone: open ? 'ok' : 'quiet' }]}
        right={open ? <IconButton icon="circle-x" label="Disconnect" onPress={disconnect} testID="terminal-disconnect" /> : null}
        menu={[{ key: 'clear', label: 'Clear output', icon: 'trash-2', onPress: () => { outputRef.current = ''; setOutput(''); setFollow(true); } }]} testID="terminal-header" />
      {closed ? (
        <View style={styles.banner}>
          <Banner tone={closed.pairing ? 'attention' : 'danger'} title={closed.pairing ? 'Pair this phone' : 'Terminal closed'} testID="terminal-closed"
            action={closed.pairing ? { label: 'Pair', onPress: () => navigation.navigate('Pair'), testID: 'terminal-pair' } : { label: 'Reconnect', onPress: () => void connect(), testID: 'terminal-reconnect' }}>
            {closed.text}
          </Banner>
        </View>
      ) : null}
      {open || output ? (
        <KeyboardAvoidingView style={styles.grow} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
          <ScrollView ref={scrollRef} style={[styles.output, { backgroundColor: colors.terminal }]} contentContainerStyle={styles.outputContent}
            onScroll={onScroll} scrollEventThrottle={80}
            onContentSizeChange={() => { if (followRef.current) scrollRef.current?.scrollToEnd({ animated: false }); }}>
            <Text variant="mono" selectable testID="terminal-output">{output}</Text>
          </ScrollView>
          {!following && output ? <View style={styles.jump}><Button label="Latest" icon="arrow-down" variant="secondary" size="md" onPress={() => { setFollow(true); scrollRef.current?.scrollToEnd({ animated: true }); }} /></View> : null}
          <View style={[styles.inputBar, { borderTopColor: colors.hairline }]}>
            <View style={styles.grow}>
              <TextField value={input} onChangeText={setInput} placeholder="Type a command…" mono autoCapitalize="none" autoCorrect={false}
                returnKeyType="send" blurOnSubmit={false} onSubmitEditing={send} editable={open} accessibilityLabel="Command" testID="terminal-input"
                onKeyPress={event => {
                  const key = event.nativeEvent.key;
                  if (key === 'Tab') complete();
                  else if (key === 'ArrowUp') cycle(-1);
                  else if (key === 'ArrowDown') cycle(1);
                }} />
            </View>
            <IconButton icon="corner-down-left" label="Complete" onPress={complete} disabled={!open} />
            <IconButton icon="chevron-up" label="Previous command" onPress={() => cycle(-1)} disabled={!open} />
            <IconButton icon="chevron-down" label="Next command" onPress={() => cycle(1)} disabled={!open} />
          </View>
        </KeyboardAvoidingView>
      ) : (
        <View style={styles.grow}>
          <EmptyState icon="terminal" title={container ? `A shell in ${container}` : 'No environment chosen'} pigment="meadow"
            message="Open an interactive shell inside the environment. Commands run as the environment's user."
            actionLabel={state === 'connecting' ? 'Connecting…' : 'Connect'} onAction={() => void connect()} testID="terminal-empty" />
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1 },
  grow: { flex: 1 },
  banner: { paddingHorizontal: spacing.base, paddingTop: spacing.sm },
  output: { flex: 1, paddingHorizontal: spacing.sm },
  outputContent: { paddingVertical: spacing.sm },
  jump: { position: 'absolute', right: spacing.base, bottom: 72 },
  inputBar: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, borderTopWidth: 1 },
});
