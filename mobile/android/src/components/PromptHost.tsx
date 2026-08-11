import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Button } from './Button';
import { Text } from './Text';
import {
  promptsApi,
  type PendingPrompt,
  type QuizQuestion,
} from '../api/prompts';
import { subscribeToEvents, type SSESubscription } from '../api/sse';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { SafeAreaModal } from './SafeAreaModal';

// ---------- helpers ------------------------------------------------------

function promptMatchesSession(prompt: PendingPrompt, activeSessionName: string | null): boolean {
  if (!activeSessionName) return false;
  return !prompt.session_name || prompt.session_name === activeSessionName;
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

function optLabel(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o && typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (typeof rec.label === 'string') return rec.label;
    if (typeof rec.name === 'string') return rec.name;
  }
  try { return JSON.stringify(o); } catch { return String(o); }
}

function optValue(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o && typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (rec.value !== undefined) return String(rec.value);
    if (typeof rec.id === 'string') return rec.id;
    if (typeof rec.label === 'string') return rec.label;
  }
  return String(o);
}

function quizOptionValue(o: unknown): string {
  if (typeof o === 'string') return o;
  if (o == null) return '';
  if (typeof o === 'object') {
    const rec = o as Record<string, unknown>;
    if (rec.value !== undefined) return String(rec.value);
    if (rec.label !== undefined) return String(rec.label);
  }
  return String(o);
}

// ---------- main component ----------------------------------------------

export function PromptHost() {
  const { colors } = useTheme();
  const isConnected = useConnectionStore(state => state.isConnected);
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const yolo = useConnectionStore(state => state.yolo);
  const [queue, setQueue] = useState<PendingPrompt[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const subscriptionRef = useRef<SSESubscription | null>(null);
  const autoApprovingRef = useRef(new Set<string>());

  const mergeQueue = useCallback((incoming: PendingPrompt[]) => {
    const relevant = incoming.filter(prompt => promptMatchesSession(prompt, activeSessionName));
    if (relevant.length === 0) return;
    setQueue(current => {
      const byId = new Map(current.map(prompt => [prompt.id, prompt]));
      relevant.forEach(prompt => byId.set(prompt.id, prompt));
      return Array.from(byId.values());
    });
  }, [activeSessionName]);

  const removePrompt = useCallback((id: string) => {
    setQueue(current => current.filter(prompt => prompt.id !== id));
  }, []);

  const recoverPending = useCallback(async () => {
    if (!isConnected || !activeSessionName) return;
    try {
      const response = await promptsApi.listPending();
      const relevant = (response.pending || []).filter(
        prompt => promptMatchesSession(prompt, activeSessionName),
      );
      setQueue(relevant);
    } catch {
      // Preserve the current queue during transient network failures.
    }
  }, [activeSessionName, isConnected]);

  // MUCLI_MOBILE_RECONNECT_YOLO_V1: approve queued tool prompts when
  // YOLO is enabled. This mirrors the web GUI and also releases a prompt that
  // was already waiting when the user toggled YOLO on.
  useEffect(() => {
    if (!yolo || !isConnected || !activeSessionName) return;
    const approvals = queue.filter(prompt =>
      prompt.shape === 'tool_approval'
      && promptMatchesSession(prompt, activeSessionName)
      && !autoApprovingRef.current.has(prompt.id),
    );
    for (const prompt of approvals) {
      autoApprovingRef.current.add(prompt.id);
      void promptsApi.answer(prompt.id, { approved: true, remember: false })
        .then(() => removePrompt(prompt.id))
        .catch(() => recoverPending())
        .finally(() => autoApprovingRef.current.delete(prompt.id));
    }
  }, [activeSessionName, isConnected, queue, recoverPending, removePrompt, yolo]);

  useEffect(() => {
    if (!isConnected || !activeSessionName) {
      subscriptionRef.current?.close();
      subscriptionRef.current = null;
      setQueue([]);
      setReviewOpen(false);
      return;
    }

    setQueue([]);
    setReviewOpen(false);
    recoverPending();
    subscriptionRef.current?.close();
    subscriptionRef.current = subscribeToEvents({
      onMessage: event => {
        if (event.kind === 'prompt') {
          const prompt = asPendingPrompt(event);
          if (prompt) mergeQueue([prompt]);
          return;
        }
        if ((event.kind === 'prompt_resolved' || event.kind === 'prompt_cancelled') && typeof event.id === 'string') {
          removePrompt(event.id);
        }
      },
      onOpen: recoverPending,
    }, { sessionName: activeSessionName });

    return () => {
      subscriptionRef.current?.close();
      subscriptionRef.current = null;
    };
  }, [isConnected, activeSessionName, mergeQueue, recoverPending, removePrompt]);

  const activePrompt = queue[0] || null;

  useEffect(() => {
    setReviewOpen(false);
  }, [activePrompt?.id]);

  const submit = useCallback(async (payload: Record<string, unknown>) => {
    if (!activePrompt || submitting) return;
    setSubmitting(true);
    try {
      await promptsApi.answer(activePrompt.id, payload);
      removePrompt(activePrompt.id);
      setReviewOpen(false);
    } catch (error) {
      Alert.alert('Could not submit answer', String(error));
      recoverPending();
    } finally {
      setSubmitting(false);
    }
  }, [activePrompt, recoverPending, removePrompt, submitting]);

  const cancel = useCallback(async () => {
    if (!activePrompt || submitting) return;
    setSubmitting(true);
    try {
      await promptsApi.cancel(activePrompt.id);
      removePrompt(activePrompt.id);
      setReviewOpen(false);
    } catch (error) {
      Alert.alert('Could not cancel prompt', String(error));
      recoverPending();
    } finally {
      setSubmitting(false);
    }
  }, [activePrompt, recoverPending, removePrompt, submitting]);

  if (!activePrompt) return null;

  if (!reviewOpen) {
    return (
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Review pending agent input"
        activeOpacity={0.86}
        onPress={() => setReviewOpen(true)}
        style={[styles.pendingBanner, { backgroundColor: colors.glassStrong, borderColor: colors.accent }]}
      >
        <View style={[styles.pendingIcon, { backgroundColor: colors.accentSoft }]}>
          <Ionicons name="help-circle-outline" size={20} color={colors.accent} />
        </View>
        <View style={styles.pendingCopy}>
          <Text variant="sm" style={{ color: colors.text, fontWeight: '700' }}>Input required</Text>
          <Text variant="xs" dim numberOfLines={1}>Agent is waiting · tap to review</Text>
        </View>
        {queue.length > 1 ? (
          <Text variant="xs" dim>{queue.length} pending</Text>
        ) : null}
        <Ionicons name="chevron-forward" size={18} color={colors.textDim} />
      </TouchableOpacity>
    );
  }

  return (
    <SafeAreaModal
      visible
      transparent
      animationType="fade"
      onRequestClose={() => setReviewOpen(false)}
    >
      <View style={styles.backdrop}>
        <View style={[styles.dialog, { backgroundColor: colors.bg, borderColor: colors.border }]}>
          <TouchableOpacity
            accessibilityRole="button"
            accessibilityLabel="Minimize pending input"
            onPress={() => setReviewOpen(false)}
            style={[styles.minimizeButton, { backgroundColor: colors.bgHover }]}
          >
            <Ionicons name="remove-outline" size={20} color={colors.textDim} />
          </TouchableOpacity>
          <PromptBody
            prompt={activePrompt}
            submitting={submitting}
            submit={submit}
            cancel={cancel}
            queueDepth={queue.length}
          />
        </View>
      </View>
    </SafeAreaModal>
  );
}

// ---------- per-shape body ----------------------------------------------

interface PromptBodyProps {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}

function PromptBody({ prompt, submitting, submit, cancel, queueDepth }: PromptBodyProps) {
  const shape = prompt.shape;

  switch (shape) {
    case 'tool_approval':
      return <ToolApprovalBody prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
    case 'choice':
    case 'choices':
      return <ChoiceBody key={prompt.id} prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
    case 'quiz':
      return <QuizBody key={prompt.id} prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
    case 'confirm':
      return <ConfirmBody prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
    case 'input':
      return <InputBody key={prompt.id} prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
    default:
      return <ConfirmBody prompt={prompt} submitting={submitting} submit={submit} cancel={cancel} queueDepth={queueDepth} />;
  }
}

// ---------- shared header -----------------------------------------------

function PromptHeader({
  icon,
  label,
  title,
  queueDepth,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  title: string;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  return (
    <View style={styles.headingRow}>
      <View style={[styles.icon, { backgroundColor: colors.accentSoft }]}>
        <Ionicons name={icon} size={23} color={colors.accent} />
      </View>
      <View style={styles.headingCopy}>
        <Text variant="xs" style={{ color: colors.textDim, fontWeight: '700', letterSpacing: 0.7 }}>
          {label.toUpperCase()}
        </Text>
        <Text variant="lg" style={{ color: colors.text, fontWeight: '700' }} numberOfLines={2}>
          {title}
        </Text>
      </View>
      {queueDepth > 1 && (
        <View style={[styles.queueBadge, { backgroundColor: colors.bgHover }]}>
          <Text variant="xs" style={{ color: colors.textDim }}>1 of {queueDepth}</Text>
        </View>
      )}
    </View>
  );
}

function CancelButton({ onPress }: { onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity accessibilityRole="button" onPress={onPress} style={styles.denyAction}>
      <Text variant="sm" style={{ color: colors.error, fontWeight: '600' }}>Cancel</Text>
    </TouchableOpacity>
  );
}

function LoadingRow() {
  const { colors } = useTheme();
  return (
    <View style={styles.loadingRow}>
      <ActivityIndicator color={colors.accent} />
      <Text variant="sm" style={{ color: colors.textDim }}>Submitting…</Text>
    </View>
  );
}

// ---------- tool_approval -----------------------------------------------

function ToolApprovalBody({
  prompt,
  submitting,
  submit,
  cancel,
  queueDepth,
}: {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  const [remember, setRemember] = useState(false);

  const argsText = useMemo(() => formatArgs(prompt.tool_args), [prompt.tool_args]);
  const risk = typeof prompt.risk === 'string' ? prompt.risk : '';
  const description =
    (typeof prompt.description === 'string' && prompt.description.trim()) ||
    `Allow ${prompt.tool_name || 'this tool'} to run?`;

  return (
    <>
      <PromptHeader
        icon="shield-checkmark-outline"
        label="Tool approval"
        title={prompt.tool_name || 'Tool request'}
        queueDepth={queueDepth}
      />
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
      <TouchableOpacity
        accessibilityRole="checkbox"
        onPress={() => setRemember(v => !v)}
        style={styles.checkboxRow}
      >
        <Ionicons name={remember ? 'checkbox' : 'square-outline'} size={22} color={colors.accent} />
        <Text variant="sm" style={{ color: colors.textSoft, marginLeft: 9 }}>
          Always allow this tool
        </Text>
      </TouchableOpacity>
      {submitting ? (
        <LoadingRow />
      ) : (
        <View style={styles.actions}>
          <Button title="Allow once" onPress={() => submit({ approved: true, remember: false })} style={styles.primaryAction} />
          <Button
            title="Always allow this tool"
            variant="secondary"
            onPress={() => submit({ approved: true, remember: true })}
            style={styles.secondaryAction}
          />
          <CancelButton onPress={cancel} />
        </View>
      )}
    </>
  );
}

// ---------- choice / choices --------------------------------------------

function ChoiceBody({
  prompt,
  submitting,
  submit,
  cancel,
  queueDepth,
}: {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  const isChoices = prompt.shape === 'choices';
  const multi = !!prompt.multi_select && !isChoices;
  const options = Array.isArray(prompt.options) ? prompt.options
    : Array.isArray(prompt.choices) ? prompt.choices
    : [];
  const allowOther = !!prompt.allow_other;
  const [selected, setSelected] = useState<string[]>(multi ? [] : []);
  const [otherText, setOtherText] = useState('');
  const [useOther, setUseOther] = useState(false);

  const toggle = (value: string) => {
    if (isChoices || !multi) {
      setSelected([value]);
      setUseOther(false);
      return;
    }
    setSelected(cur => cur.includes(value) ? cur.filter(v => v !== value) : [...cur, value]);
  };

  const handleSubmit = () => {
    if (isChoices) {
      const real = selected.filter(v => v !== '__other__');
      const value = real[0] || (useOther ? otherText : '');
      submit({ value });
      return;
    }
    const real = selected.filter(v => v !== '__other__');
    submit({
      selected: real,
      other_text: useOther ? otherText : '',
    });
  };

  const canSubmit = selected.length > 0 || (useOther && otherText.trim().length > 0);

  return (
    <>
      <PromptHeader
        icon="list-outline"
        label="Choose"
        title={prompt.question || prompt.message || 'Choose an option'}
        queueDepth={queueDepth}
      />
      {!!prompt.description && (
        <Text variant="sm" style={[styles.description, { color: colors.textSoft }]}>
          {prompt.description}
        </Text>
      )}
      <ScrollView style={styles.choiceScroll} nestedScrollEnabled>
        {options.map((opt, idx) => {
          const value = optValue(opt);
          const label = optLabel(opt);
          const isSelected = selected.includes(value);
          return (
            <TouchableOpacity
              key={`opt-${idx}`}
              accessibilityRole={multi ? 'checkbox' : 'radio'}
              onPress={() => toggle(value)}
              style={[
                styles.optionRow,
                {
                  backgroundColor: isSelected ? colors.accentSoft : colors.bgLift,
                  borderColor: isSelected ? colors.accent : colors.border,
                },
              ]}
            >
              <Ionicons
                name={multi
                  ? (isSelected ? 'checkbox' : 'square-outline')
                  : (isSelected ? 'radio-button-on' : 'radio-button-off')}
                size={22}
                color={isSelected ? colors.accent : colors.textDim}
              />
              <Text variant="sm" style={{ color: colors.text, flex: 1, marginLeft: 11 }}>
                {label}
              </Text>
            </TouchableOpacity>
          );
        })}
        {allowOther && (
          <TouchableOpacity
            accessibilityRole="radio"
            onPress={() => { setUseOther(true); if (!multi) setSelected([]); }}
            style={[
              styles.optionRow,
              {
                backgroundColor: useOther ? colors.accentSoft : colors.bgLift,
                borderColor: useOther ? colors.accent : colors.border,
              },
            ]}
          >
            <Ionicons
              name={multi ? (useOther ? 'checkbox' : 'square-outline') : (useOther ? 'radio-button-on' : 'radio-button-off')}
              size={22}
              color={useOther ? colors.accent : colors.textDim}
            />
            <Text variant="sm" style={{ color: colors.text, marginLeft: 11 }}>Other…</Text>
          </TouchableOpacity>
        )}
        {allowOther && useOther && (
          <TextInput
            value={otherText}
            onChangeText={setOtherText}
            placeholder="Type your answer"
            placeholderTextColor={colors.textDim}
            style={[
              styles.textInput,
              { backgroundColor: colors.bgLift, borderColor: colors.border, color: colors.text },
            ]}
            autoFocus
          />
        )}
      </ScrollView>
      {submitting ? (
        <LoadingRow />
      ) : (
        <View style={styles.actions}>
          <Button title="Submit" onPress={handleSubmit} disabled={!canSubmit} style={styles.primaryAction} />
          <CancelButton onPress={cancel} />
        </View>
      )}
    </>
  );
}

// ---------- quiz ---------------------------------------------------------

function QuizBody({
  prompt,
  submitting,
  submit,
  cancel,
  queueDepth,
}: {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  const questions: QuizQuestion[] = Array.isArray(prompt.questions) ? prompt.questions : [];
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const setAnswer = (qid: string, value: string) => {
    setAnswers(cur => ({ ...cur, [qid]: value }));
  };

  const handleSubmit = () => {
    submit({ answers });
  };

  const answeredCount = questions.filter(q => {
    const a = answers[q.qid];
    return a !== undefined && a !== '';
  }).length;
  const canSubmit = questions.length > 0 && answeredCount === questions.length;

  return (
    <>
      <PromptHeader
        icon="school-outline"
        label="Quiz"
        title="Answer the questions"
        queueDepth={queueDepth}
      />
      <ScrollView style={styles.choiceScroll} nestedScrollEnabled>
        {questions.map((q, qi) => (
          <View key={q.qid || `q-${qi}`} style={styles.quizQuestionBlock}>
            <Text variant="sm" style={{ color: colors.text, fontWeight: '600', marginBottom: 8 }}>
              {qi + 1}. {q.prompt}
            </Text>
            {q.kind === 'fill_blank' ? (
              <TextInput
                value={answers[q.qid] || ''}
                onChangeText={text => setAnswer(q.qid, text)}
                placeholder="Type your answer"
                placeholderTextColor={colors.textDim}
                style={[
                  styles.textInput,
                  { backgroundColor: colors.bgLift, borderColor: colors.border, color: colors.text },
                ]}
              />
            ) : (
              (q.options || []).map((opt, oi) => {
                const optVal = quizOptionValue(opt);
                const optLbl = optLabel(opt);
                const isSelected = answers[q.qid] === optVal;
                return (
                  <TouchableOpacity
                    key={`q${qi}-opt${oi}`}
                    accessibilityRole="radio"
                    onPress={() => setAnswer(q.qid, optVal)}
                    style={[
                      styles.optionRow,
                      {
                        backgroundColor: isSelected ? colors.accentSoft : colors.bgLift,
                        borderColor: isSelected ? colors.accent : colors.border,
                      },
                    ]}
                  >
                    <Ionicons
                      name={isSelected ? 'radio-button-on' : 'radio-button-off'}
                      size={22}
                      color={isSelected ? colors.accent : colors.textDim}
                    />
                    <Text variant="sm" style={{ color: colors.text, flex: 1, marginLeft: 11 }}>
                      {optLbl}
                    </Text>
                  </TouchableOpacity>
                );
              })
            )}
          </View>
        ))}
      </ScrollView>
      {submitting ? (
        <LoadingRow />
      ) : (
        <View style={styles.actions}>
          <Button title="Submit" onPress={handleSubmit} disabled={!canSubmit} style={styles.primaryAction} />
          <CancelButton onPress={cancel} />
        </View>
      )}
    </>
  );
}

// ---------- confirm ------------------------------------------------------

function ConfirmBody({
  prompt,
  submitting,
  submit,
  cancel,
  queueDepth,
}: {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  const message = prompt.message || prompt.description || 'Confirm?';

  return (
    <>
      <PromptHeader
        icon="help-circle-outline"
        label="Confirm"
        title={message}
        queueDepth={queueDepth}
      />
      {submitting ? (
        <LoadingRow />
      ) : (
        <View style={styles.actions}>
          <Button title="Yes" onPress={() => submit({ value: true })} style={styles.primaryAction} />
          <Button title="No" variant="secondary" onPress={() => submit({ value: false })} style={styles.secondaryAction} />
          <CancelButton onPress={cancel} />
        </View>
      )}
    </>
  );
}

// ---------- input --------------------------------------------------------

function InputBody({
  prompt,
  submitting,
  submit,
  cancel,
  queueDepth,
}: {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
}) {
  const { colors } = useTheme();
  const defaultValue = typeof prompt.default === 'string' ? prompt.default : '';
  const [text, setText] = useState(defaultValue);
  const message = prompt.message || prompt.description || 'Input';

  const handleSubmit = () => {
    submit({ value: text });
  };

  return (
    <>
      <PromptHeader
        icon="create-outline"
        label="Input"
        title={message}
        queueDepth={queueDepth}
      />
      <TextInput
        value={text}
        onChangeText={setText}
        placeholder={defaultValue || 'Type your answer'}
        placeholderTextColor={colors.textDim}
        style={[
          styles.textInput,
          { backgroundColor: colors.bgLift, borderColor: colors.border, color: colors.text },
        ]}
        autoFocus
        multiline
      />
      {submitting ? (
        <LoadingRow />
      ) : (
        <View style={styles.actions}>
          <Button title="Submit" onPress={handleSubmit} disabled={text.trim().length === 0} style={styles.primaryAction} />
          <CancelButton onPress={cancel} />
        </View>
      )}
    </>
  );
}

// ---------- styles -------------------------------------------------------

const styles = StyleSheet.create({
  pendingBanner: {
    position: 'absolute',
    top: '50%',
    transform: [{ translateY: -38 }],
    width: '88%',
    maxWidth: 520,
    alignSelf: 'center',
    zIndex: 1000,
    elevation: 18,
    minHeight: 76,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 20,
    paddingHorizontal: 14,
    borderWidth: 1,
    shadowColor: '#000',
    shadowOpacity: 0.24,
    shadowRadius: 22,
    shadowOffset: { width: 0, height: 9 },
  },
  pendingIcon: { width: 38, height: 38, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  pendingCopy: { flex: 1, marginHorizontal: 11 },
  minimizeButton: {
    position: 'absolute',
    top: 12,
    right: 12,
    zIndex: 2,
    width: 36,
    height: 36,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
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
  checkboxRow: { flexDirection: 'row', alignItems: 'center', marginTop: 14 },
  choiceScroll: { maxHeight: 320, marginTop: 14 },
  optionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 13,
    padding: 13,
    marginBottom: 8,
  },
  textInput: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 13,
    padding: 13,
    marginTop: 8,
    fontSize: 16,
    minHeight: 48,
    textAlignVertical: 'top',
  },
  quizQuestionBlock: { marginBottom: 16 },
  actions: { marginTop: 19, gap: 9 },
  primaryAction: { minHeight: 50, borderRadius: 15 },
  secondaryAction: { minHeight: 48, borderRadius: 15 },
  denyAction: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  loadingRow: { minHeight: 74, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
});
