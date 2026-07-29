import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Badge } from '../components';
import { useConnectionStore } from '../store/connection';
import { sessionsApi } from '../api/sessions';
import { spacing } from '../theme/tokens';

// Strip ANSI escape sequences for plain-text display.
const ANSI_RE = /\u001b\[[0-9;]*[a-zA-Z]|\u001b\][^\u0007]*\u0007|\u001b[()][AB012]|\x07/g;

function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, '');
}

export function ShellScreen() {
  const { colors } = useTheme();
  const { baseUrl, activeSessionName } = useConnectionStore();
  const [output, setOutput] = useState<string>('');
  const [input, setInput] = useState('');
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [containerName, setContainerName] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const outputBuf = useRef<string[]>([]);

  const appendOutput = useCallback((text: string) => {
    const clean = stripAnsi(text);
    outputBuf.current.push(clean);
    // Keep last 2000 lines to avoid memory blow-up.
    if (outputBuf.current.length > 2000) {
      outputBuf.current = outputBuf.current.slice(-2000);
    }
    setOutput(outputBuf.current.join(''));
    requestAnimationFrame(() => {
      scrollRef.current?.scrollToEnd({ animated: true });
    });
  }, []);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
      wsRef.current = null;
    }
    setConnected(false);
    setConnecting(false);
  }, []);

  const connect = useCallback(async () => {
    if (connecting || connected) return;
    setConnecting(true);
    setError(null);
    outputBuf.current = [];
    setOutput('');

    let container = containerName;
    if (!container && activeSessionName) {
      try {
        const info = await sessionsApi.getContainer(activeSessionName);
        const name = (info as Record<string, unknown>).name;
        if (typeof name === 'string') {
          container = name;
          setContainerName(name);
        }
      } catch {
        // fall through — no container attached
      }
    }

    if (!container) {
      setError('No container attached to the active session.');
      setConnecting(false);
      return;
    }

    // Build WS URL from base HTTP URL.
    const wsBase = baseUrl
      .replace(/^http:\/\//i, 'ws://')
      .replace(/^https:\/\//i, 'wss://');
    const wsUrl = `${wsBase}/api/containers/${encodeURIComponent(container)}/shell`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setConnecting(false);
        appendOutput(`Connected to ${container}\n`);
      };

      ws.onmessage = (event: WebSocketMessageEvent) => {
        const data = typeof event.data === 'string' ? event.data : '';
        if (data) appendOutput(data);
      };

      ws.onerror = () => {
        setError('WebSocket error — check the server is reachable.');
        setConnecting(false);
        setConnected(false);
      };

      ws.onclose = (event: CloseEvent) => {
        setConnected(false);
        setConnecting(false);
        if (event.code !== 1000) {
          appendOutput(`\n[Connection closed: ${event.code}${event.reason ? ' ' + event.reason : ''}]\n`);
        } else {
          appendOutput('\n[Connection closed]\n');
        }
      };
    } catch (e) {
      setError(String(e));
      setConnecting(false);
    }
  }, [connecting, connected, containerName, activeSessionName, baseUrl, appendOutput]);

  const send = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const line = input + '\n';
    ws.send(line);
    appendOutput(`$ ${input}\n`);
    setInput('');
  }, [input, appendOutput]);

  const clear = useCallback(() => {
    outputBuf.current = [];
    setOutput('');
  }, []);

  // Disconnect on screen blur / unmount.
  useFocusEffect(
    useCallback(() => {
      return () => disconnect();
    }, [disconnect]),
  );

  useEffect(() => {
    return () => {
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        wsRef.current = null;
      }
    };
  }, []);

  const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.bg },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: spacing.base,
      paddingVertical: spacing.sm,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: colors.border,
    },
    headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
    headerActions: { flexDirection: 'row', gap: 12 },
    actionBtn: { padding: 4 },
    outputWrap: { flex: 1, paddingHorizontal: spacing.sm },
    output: {
      fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
      fontSize: 12,
      lineHeight: 16,
      color: colors.text,
      includeFontPadding: false,
    },
    inputBar: {
      flexDirection: 'row',
      alignItems: 'center',
      paddingHorizontal: spacing.sm,
      paddingVertical: spacing.xs,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: colors.border,
      gap: 6,
    },
    prompt: {
      fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
      fontSize: 13,
      color: colors.accent,
    },
    input: {
      flex: 1,
      fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
      fontSize: 13,
      color: colors.text,
      paddingVertical: 6,
      paddingHorizontal: 8,
      backgroundColor: colors.card,
      borderRadius: 6,
    },
    errorBar: {
      paddingHorizontal: spacing.base,
      paddingVertical: spacing.xs,
      backgroundColor: colors.errorBg || colors.card,
    },
    emptyWrap: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.base },
  });

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Ionicons name="terminal-outline" size={18} color={colors.accent} />
          <Text variant="base" style={{ fontWeight: '600' }}>Shell</Text>
          <Badge
            label={connected ? 'Connected' : connecting ? 'Connecting…' : 'Disconnected'}
            variant={connected ? 'accent' : 'neutral'}
          />
        </View>
        <View style={styles.headerActions}>
          <TouchableOpacity onPress={clear} style={styles.actionBtn} accessibilityLabel="Clear output">
            <Ionicons name="trash-outline" size={20} color={colors.textDim} />
          </TouchableOpacity>
          {connected ? (
            <TouchableOpacity onPress={disconnect} style={styles.actionBtn} accessibilityLabel="Disconnect">
              <Ionicons name="close-circle-outline" size={20} color={colors.error || colors.textDim} />
            </TouchableOpacity>
          ) : (
            <TouchableOpacity
              onPress={connect}
              disabled={connecting}
              style={styles.actionBtn}
              accessibilityLabel="Connect"
            >
              <Ionicons name="play-circle-outline" size={20} color={colors.accent} />
            </TouchableOpacity>
          )}
        </View>
      </View>

      {error ? (
        <View style={styles.errorBar}>
          <Text variant="xs" style={{ color: colors.error || colors.textDim }}>{error}</Text>
        </View>
      ) : null}

      {connected || output ? (
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          keyboardVerticalOffset={90}
        >
          <ScrollView
            ref={scrollRef}
            style={styles.outputWrap}
            contentContainerStyle={{ paddingVertical: spacing.sm }}
          >
            <Text style={styles.output}>{output}</Text>
          </ScrollView>

          <View style={styles.inputBar}>
            <Text style={styles.prompt}>$</Text>
            <TextInput
              style={styles.input}
              value={input}
              onChangeText={setInput}
              placeholder="Type a command…"
              placeholderTextColor={colors.textDim}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="send"
              onSubmitEditing={send}
              editable={connected}
            />
          </View>
        </KeyboardAvoidingView>
      ) : (
        <View style={styles.emptyWrap}>
          <Ionicons name="terminal-outline" size={48} color={colors.textDim} />
          <Text variant="sm" style={{ color: colors.textDim, marginTop: spacing.sm, textAlign: 'center' }}>
            {'No shell connected.\nTap the connect button to open an interactive terminal into the session container.'}
          </Text>
        </View>
      )}
    </SafeAreaView>
  );
}