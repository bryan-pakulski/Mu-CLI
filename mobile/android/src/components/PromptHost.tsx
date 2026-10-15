import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Alert, StyleSheet, TextInput, View } from 'react-native';
import {
  promptsApi,
  type PendingPrompt,
  type QuizQuestion,
} from '../api/prompts';
import { subscribeToEvents, type SSESubscription } from '../api/sse';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, touchTarget, typeStyle } from '../theme/tokens';
import { vocabulary } from '../theme/wash.generated';
import { ApprovalCard, Button, ChoicePrompt, Icon, InkDrop, Pressable, PromptFrame, Sheet, Text, primaryArg } from '../ui';
import { GlassBackdrop, useGlass } from '../ui/Glass';

// Approvals and questions (G§9.9, G§9.10). A pending prompt shows as the
// floating "Input required" banner; opening it presents the Wash approval
// card in a bottom sheet that cannot be swiped, tapped or back-buttoned away:
// the user either answers, cancels, or explicitly minimizes it. Approve is
// never auto-focused; a new prompt is announced through AccessibilityInfo.
// Payloads sent to /api/prompts are unchanged.

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
  const glass = useGlass();
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

  const announcedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activePrompt || announcedRef.current === activePrompt.id) return;
    announcedRef.current = activePrompt.id;
    AccessibilityInfo.announceForAccessibility(announcement(activePrompt));
  }, [activePrompt]);

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
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Review pending agent input"
        haptic="light"
        onPress={() => setReviewOpen(true)}
        // Attention state on level-2 glass chrome: blurred backdrop, the
        // attention wash mixed into the glass and the ◆ glyph; no side rule.
        style={[glass.style(2), styles.pendingBanner, { backgroundColor: 'transparent', overflow: 'hidden' }]}
      >
        <GlassBackdrop level={2} testID="pending-banner-glass" />
        <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: glass.tint('attention') }]} />
        <View style={[styles.pendingIcon, { backgroundColor: colors.attention.wash }]}>
          <Text variant="title3" importantForAccessibility="no" style={{ color: colors.attention.ink }}>{vocabulary.status.waiting.glyph}</Text>
        </View>
        <View style={styles.pendingCopy}>
          <Text variant="body" style={{ fontWeight: '600' }}>Input required</Text>
          <Text variant="caption" tone="ink3" numberOfLines={1}>Mucli is waiting · tap to review</Text>
        </View>
        {queue.length > 1 ? (
          <Text variant="caption" tone="ink3" numeric>{queue.length} pending</Text>
        ) : null}
        <Icon name="chevron-right" color={colors.ink3} />
      </Pressable>
    );
  }

  return (
    <Sheet
      visible
      dismissable={false}
      onClose={() => setReviewOpen(false)}
      title={activePrompt.shape === 'tool_approval' ? 'Approval needed' : 'Mucli has a question'}
      headerRight={(
        <Pressable
          accessibilityLabel="Minimize pending input"
          onPress={() => setReviewOpen(false)}
          style={({ pressed }) => [styles.minimizeButton, { backgroundColor: pressed ? colors.sunken : 'transparent' }]}
        >
          <Icon name="minimize-2" color={colors.ink3} />
        </Pressable>
      )}
    >
      <PromptBody
        prompt={activePrompt}
        submitting={submitting}
        submit={submit}
        cancel={cancel}
        queueDepth={queue.length}
      />
    </Sheet>
  );
}

function announcement(prompt: PendingPrompt): string {
  if (prompt.shape === 'tool_approval') {
    return `Mucli needs your approval: ${prompt.tool_name || 'a tool'}`;
  }
  return `Mucli has a question: ${prompt.question || prompt.message || 'input required'}`;
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

// ---------- shared pieces -----------------------------------------------

function CancelButton({ onPress }: { onPress: () => void }) {
  return <Button variant="ghost" label="Cancel" onPress={onPress} accessibilityHint="Dismisses the request without answering" />;
}

function LoadingRow() {
  return (
    <View style={styles.loadingRow}>
      <InkDrop label="Submitting…" />
    </View>
  );
}

function Actions({ children }: { children: React.ReactNode }) {
  return <View style={styles.actions}>{children}</View>;
}

function AnswerField(props: React.ComponentProps<typeof TextInput>) {
  const { colors } = useTheme();
  return (
    <TextInput
      placeholderTextColor={colors.ink3}
      maxFontSizeMultiplier={2}
      {...props}
      style={[styles.textInput, typeStyle('body-lg'), { backgroundColor: colors.surface, borderColor: colors.controlBorder, color: colors.ink }]}
    />
  );
}

// ---------- tool_approval -----------------------------------------------

export function ToolApprovalBody({
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
  const [remember, setRemember] = useState(false);

  const argsText = formatArgs(prompt.tool_args);
  const risk = typeof prompt.risk === 'string' ? prompt.risk : '';
  const description =
    (typeof prompt.description === 'string' && prompt.description.trim()) ||
    `Allow ${prompt.tool_name || 'this tool'} to run?`;

  return (
    <ApprovalCard
      toolName={prompt.tool_name || 'this tool'}
      description={description}
      target={primaryArg(prompt.tool_args)}
      preview={argsText}
      risk={risk}
      highRisk={/high|critical|destructive/i.test(risk)}
      remember={remember}
      onRememberChange={setRemember}
      rememberTestID="tool-approval-remember"
      approveTestID="tool-approval-allow"
      submitting={submitting}
      queue={queueDepth > 1 ? { index: 1, total: queueDepth } : undefined}
      // One control, one meaning: the checkbox decides whether the approval is remembered.
      onApprove={() => submit({ approved: true, remember })}
      denyLabel="Cancel"
      onDeny={cancel}
    />
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

  const choiceOptions = options.map(opt => ({ value: optValue(opt), label: optLabel(opt) }));
  return (
    <PromptFrame tone="accent" chip="Question" title={prompt.question || prompt.message || 'Choose an option'}
      queue={queueDepth > 1 ? { index: 1, total: queueDepth } : undefined}
      actions={submitting ? <LoadingRow /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={handleSubmit} disabled={!canSubmit} />
        </Actions>
      )}>
      {!!prompt.description && <Text variant="body" tone="ink2">{prompt.description}</Text>}
      <ChoicePrompt options={choiceOptions} selected={selected} onToggle={toggle} multi={multi} />
      {allowOther && (
        <ChoicePrompt
          options={[{ value: '__other__', label: 'Other…' }]}
          selected={useOther ? ['__other__'] : []}
          multi={multi}
          onToggle={() => { setUseOther(true); if (!multi) setSelected([]); }}
        />
      )}
      {allowOther && useOther && (
        <AnswerField value={otherText} onChangeText={setOtherText} placeholder="Type your answer" accessibilityLabel="Your answer" />
      )}
    </PromptFrame>
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
    <PromptFrame tone="accent" chip="Quiz" title="Answer the questions"
      queue={queueDepth > 1 ? { index: 1, total: queueDepth } : undefined}
      actions={submitting ? <LoadingRow /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={handleSubmit} disabled={!canSubmit} />
        </Actions>
      )}>
      {questions.map((q, qi) => (
        <View key={q.qid || `q-${qi}`} style={styles.quizQuestionBlock}>
          <Text variant="body" style={{ fontWeight: '600', marginBottom: spacing.sm }}>
            {qi + 1}. {q.prompt}
          </Text>
          {q.kind === 'fill_blank' ? (
            <AnswerField value={answers[q.qid] || ''} onChangeText={text => setAnswer(q.qid, text)} placeholder="Type your answer" accessibilityLabel={`Answer to question ${qi + 1}`} />
          ) : (
            <ChoicePrompt
              options={(q.options || []).map(opt => ({ value: quizOptionValue(opt), label: optLabel(opt) }))}
              selected={answers[q.qid] !== undefined ? [answers[q.qid]] : []}
              onToggle={value => setAnswer(q.qid, value)}
            />
          )}
        </View>
      ))}
    </PromptFrame>
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
  const message = prompt.message || prompt.description || 'Confirm?';

  return (
    <PromptFrame tone="accent" chip="Confirm" title={message}
      queue={queueDepth > 1 ? { index: 1, total: queueDepth } : undefined}
      actions={submitting ? <LoadingRow /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="secondary" label="No" onPress={() => submit({ value: false })} />
          <Button variant="primary" label="Yes" onPress={() => submit({ value: true })} />
        </Actions>
      )}
    />
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
  const defaultValue = typeof prompt.default === 'string' ? prompt.default : '';
  const [text, setText] = useState(defaultValue);
  const message = prompt.message || prompt.description || 'Input';

  const handleSubmit = () => {
    submit({ value: text });
  };

  return (
    <PromptFrame tone="accent" chip="Input" title={message}
      queue={queueDepth > 1 ? { index: 1, total: queueDepth } : undefined}
      actions={submitting ? <LoadingRow /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={handleSubmit} disabled={text.trim().length === 0} />
        </Actions>
      )}>
      <AnswerField value={text} onChangeText={setText} placeholder={defaultValue || 'Type your answer'} accessibilityLabel="Your answer" multiline />
    </PromptFrame>
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
    borderRadius: radii.lg,
    paddingHorizontal: spacing.base,
    borderWidth: 1,
  },
  pendingIcon: { width: 40, height: 40, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center' },
  pendingCopy: { flex: 1, marginHorizontal: spacing.md },
  minimizeButton: { width: touchTarget, height: touchTarget, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center' },
  textInput: { borderWidth: 1, borderRadius: radii.sm, padding: spacing.md, marginTop: spacing.sm, minHeight: 48, textAlignVertical: 'top' },
  quizQuestionBlock: { marginBottom: spacing.base },
  actions: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: spacing.sm },
  loadingRow: { minHeight: 72, alignItems: 'center', justifyContent: 'center' },
});
