import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Button } from './Button';
import { Text } from './Text';
import { promptsApi, type PendingPrompt } from '../api/prompts';
import { subscribeToEvents, type SSESubscription } from '../api/sse';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { SafeAreaModal } from './SafeAreaModal';

const RECOVERY_POLL_MS = 2000;

function isToolApproval(prompt: PendingPrompt): boolean {
  return prompt.shape === 'tool_approval';
}

function promptMatchesSession(prompt: PendingPrompt, activeSessionName: string | null): boolean {
  return !prompt.session_name || !activeSessionName || prompt.session_name === activeSessionName;
}

function asPendingPrompt(event: { [key: string]: unknown }): PendingPrompt | null {
  const id = typeof event.id === 'string' ? event.id : null;
  const raw = event.prompt;
  if (!id || !raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const prompt = raw as Record<string, unknown>;
  return {
    ...prompt,
    id,
    shape: typeof prompt.shape === 'string' ? prompt.shape : '',
  } as PendingPrompt;
}

function formatArgs(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function PromptHost() {
  const { colors } = useTheme();
  const isConnected = useConnectionStore(state => state.isConnected);
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const [approvals, setApprovals] = useState<PendingPrompt[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const subscriptionRef = useRef<SSESubscription | null>(null);

  const mergeApprovals = useCallback((incoming: PendingPrompt[]) => {
    const relevant = incoming.filter(prompt => isToolApproval(prompt) && promptMatchesSession(prompt, activeSessionName));
    if (relevant.length === 0) return;
    setApprovals(current => {
      const byId = new Map(current.map(prompt => [prompt.id, prompt]));
      relevant.forEach(prompt => byId.set(prompt.id, prompt));
      return Array.from(byId.values());
    });
  }, [activeSessionName]);

  const removeApproval = useCallback((id: string) => {
    setApprovals(current => current.filter(prompt => prompt.id !== id));
  }, []);

  const recoverPending = useCallback(async () => {
    if (!isConnected) return;
    try {
      const response = await promptsApi.listPending();
      const relevant = (response.pending || []).filter(
        prompt => isToolApproval(prompt) && promptMatchesSession(prompt, activeSessionName),
      );
      setApprovals(relevant);
    } catch {
      // Preserve the current queue during transient network failures. SSE or
      // the next recovery poll will reconcile it.
    }
  }, [activeSessionName, isConnected]);

  useEffect(() => {
    if (!isConnected) {
      subscriptionRef.current?.close();
      subscriptionRef.current = null;
      setApprovals([]);
      return;
    }

    setApprovals([]);
    recoverPending();
    subscriptionRef.current?.close();
    subscriptionRef.current = subscribeToEvents({
      onMessage: event => {
        if (event.kind === 'prompt') {
          const prompt = asPendingPrompt(event);
          if (prompt) mergeApprovals([prompt]);
          return;
        }
        if ((event.kind === 'prompt_resolved' || event.kind === 'prompt_cancelled') && typeof event.id === 'string') {
          removeApproval(event.id);
        }
      },
      onOpen: recoverPending,
    });

    const poll = setInterval(recoverPending, RECOVERY_POLL_MS);
    return () => {
      clearInterval(poll);
      subscriptionRef.current?.close();
      subscriptionRef.current = null;
    };
  }, [isConnected, activeSessionName, mergeApprovals, recoverPending, removeApproval]);

  const activeApproval = approvals[0] || null;
  const argsText = useMemo(() => formatArgs(activeApproval?.tool_args), [activeApproval]);

  const answer = async (approved: boolean, remember: boolean) => {
    if (!activeApproval || submitting) return;
    setSubmitting(true);
    try {
      await promptsApi.answer(activeApproval.id, { approved, remember });
      removeApproval(activeApproval.id);
    } catch (error) {
      Alert.alert('Could not submit approval', String(error));
      recoverPending();
    } finally {
      setSubmitting(false);
    }
  };

  if (!activeApproval) return null;

  const risk = typeof activeApproval.risk === 'string' ? activeApproval.risk : '';
  const description =
    (typeof activeApproval.description === 'string' && activeApproval.description.trim()) ||
    `Allow ${activeApproval.tool_name || 'this tool'} to run?`;

  return (
    <SafeAreaModal
      visible
      transparent
      animationType="fade"
      onRequestClose={() => answer(false, false)}
    >
      <View style={styles.backdrop}>
        <View style={[styles.dialog, { backgroundColor: colors.bg, borderColor: colors.border }]}>
          <View style={styles.headingRow}>
            <View style={[styles.icon, { backgroundColor: colors.accentSoft }]}>
              <Ionicons name="shield-checkmark-outline" size={23} color={colors.accent} />
            </View>
            <View style={styles.headingCopy}>
              <Text variant="xs" style={{ color: colors.textDim, fontWeight: '700', letterSpacing: 0.7 }}>
                TOOL APPROVAL
              </Text>
              <Text variant="lg" style={{ color: colors.text, fontWeight: '700' }} numberOfLines={2}>
                {activeApproval.tool_name || 'Tool request'}
              </Text>
            </View>
            {approvals.length > 1 && (
              <View style={[styles.queueBadge, { backgroundColor: colors.bgHover }]}>
                <Text variant="xs" style={{ color: colors.textDim }}>1 of {approvals.length}</Text>
              </View>
            )}
          </View>

          <Text variant="sm" style={[styles.description, { color: colors.textSoft }]}>
            {description}
          </Text>

          {!!risk && (
            <View style={[styles.riskRow, { backgroundColor: colors.bgLift }]}>
              <Ionicons name="warning-outline" size={17} color={colors.warning} />
              <Text variant="xs" style={{ color: colors.textSoft, flex: 1 }}>
                Risk: {risk}
              </Text>
            </View>
          )}

          {!!argsText && (
            <View style={[styles.argsPanel, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
              <Text variant="xs" style={{ color: colors.textDim, fontWeight: '700', marginBottom: 7 }}>
                ARGUMENTS
              </Text>
              <ScrollView style={styles.argsScroll} nestedScrollEnabled>
                <Text variant="xs" style={{ color: colors.text, fontFamily: 'monospace' }} selectable>
                  {argsText}
                </Text>
              </ScrollView>
            </View>
          )}

          {submitting ? (
            <View style={styles.loadingRow}>
              <ActivityIndicator color={colors.accent} />
              <Text variant="sm" style={{ color: colors.textDim }}>Submitting decision…</Text>
            </View>
          ) : (
            <View style={styles.actions}>
              <Button title="Allow once" onPress={() => answer(true, false)} style={styles.primaryAction} />
              <Button
                title="Always allow this tool"
                variant="secondary"
                onPress={() => answer(true, true)}
                style={styles.secondaryAction}
              />
              <TouchableOpacity
                accessibilityRole="button"
                onPress={() => answer(false, false)}
                style={styles.denyAction}
              >
                <Text variant="sm" style={{ color: colors.error, fontWeight: '600' }}>Deny</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
    backgroundColor: 'rgba(0, 0, 0, 0.56)',
  },
  dialog: {
    width: '100%',
    maxWidth: 520,
    maxHeight: '88%',
    alignSelf: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 24,
    padding: 20,
  },
  headingRow: { flexDirection: 'row', alignItems: 'center' },
  icon: { width: 46, height: 46, borderRadius: 15, alignItems: 'center', justifyContent: 'center' },
  headingCopy: { flex: 1, marginLeft: 13 },
  queueBadge: { borderRadius: 999, paddingHorizontal: 9, paddingVertical: 5 },
  description: { marginTop: 18, lineHeight: 21 },
  riskRow: { flexDirection: 'row', alignItems: 'center', gap: 8, borderRadius: 13, padding: 11, marginTop: 14 },
  argsPanel: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 15, padding: 12, marginTop: 14 },
  argsScroll: { maxHeight: 190 },
  actions: { marginTop: 19, gap: 9 },
  primaryAction: { minHeight: 50, borderRadius: 15 },
  secondaryAction: { minHeight: 48, borderRadius: 15 },
  denyAction: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  loadingRow: { minHeight: 74, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
});
