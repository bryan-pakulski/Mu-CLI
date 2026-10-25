// Approvals and questions inline in the transcript (web .w-approval / .w-prompt, G§9.9, G§9.10),
// plus the resolved breadcrumbs (.w-resolved, "✓ Approved · write_file routes.py · 14:02").
//
// Transport-agnostic: a body gets the prompt and answers through `submit(payload)` / `cancel()`,
// so chat and the fleet chat share it. The heading, risk, command and diff previews and the
// payloads come from src/ui/approval.ts (ported from the web). Approve is never auto-focused.

import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { PendingPrompt, QuizQuestion } from '../../api/prompts';
import { useTheme } from '../../theme/ThemeContext';
import { spacing } from '../../theme/tokens';
import { ApprovalCard, ChoicePrompt, PromptFrame, type PromptQueue } from '../../ui/ApprovalCard';
import { Button } from '../../ui/Button';
import { Chip } from '../../ui/Chip';
import { Banner } from '../../ui/Feedback';
import { TextField } from '../../ui/Form';
import { InkDrop } from '../../ui/Progress';
import { Text } from '../../ui/Text';
import {
  answerPayload, commandPreview, formatArgs, headline, isApproval, isRisky, optLabel, optValue, previewDiff, promptTarget,
  promptTone, quizOptionValue, resolvedSubject, resolvedVerb, riskText, type ResolvedPrompt,
} from '../../ui/approval';

export type PromptBodyProps = {
  prompt: PendingPrompt;
  submitting: boolean;
  submit: (payload: Record<string, unknown>) => void;
  cancel: () => void;
  queueDepth: number;
  /** Queue navigation (n of m); index is 1-based. */
  queue?: PromptQueue;
};

function queueOf(props: PromptBodyProps): PromptQueue | undefined {
  if (props.queue) return props.queue.total > 1 ? props.queue : undefined;
  return props.queueDepth > 1 ? { index: 1, total: props.queueDepth } : undefined;
}

function Actions({ children }: { children: React.ReactNode }) {
  return <View style={styles.actions}>{children}</View>;
}

function Submitting() {
  return <View style={styles.loadingRow}><InkDrop label="Submitting…" /></View>;
}

function CancelButton({ onPress }: { onPress: () => void }) {
  return <Button variant="ghost" label="Cancel" onPress={onPress} accessibilityHint="Dismisses the request without answering" testID="prompt-cancel" />;
}

/** tool_approval and diff: the approval card. */
export function ToolApprovalBody(props: PromptBodyProps) {
  const { prompt, submitting, submit, cancel } = props;
  const [remember, setRemember] = useState(false);
  const diff = previewDiff(prompt);
  const command = commandPreview(prompt);
  const args = prompt.shape === 'diff' ? '' : formatArgs(prompt.tool_args);
  if (prompt.requires_desktop) {
    return <PromptFrame tone={promptTone(prompt)} chip="Review" title="Review this PoC on desktop" queue={queueOf(props)}
      actions={<CancelButton onPress={cancel} />}>
      <Text variant="body" tone="ink2">Open the browser or terminal to review the command, runner and PoC scripts before approving execution.</Text>
    </PromptFrame>;
  }
  const description = typeof prompt.description === 'string' && prompt.description.trim() ? prompt.description.trim() : '';
  return (
    <ApprovalCard
      toolName={prompt.tool_name || (prompt.shape === 'diff' ? 'change' : 'this tool')}
      title={headline(prompt).replace(/`/g, '')}
      description={description}
      target={command ? undefined : promptTarget(prompt)}
      command={command || undefined}
      diff={diff || undefined}
      preview={args && args !== command ? args : undefined}
      risk={riskText(prompt) || undefined}
      highRisk={isRisky(prompt)}
      remember={remember}
      onRememberChange={setRemember}
      rememberTestID="tool-approval-remember"
      approveTestID="tool-approval-allow"
      submitting={submitting}
      queue={queueOf(props)}
      // One control, one meaning: the checkbox decides whether the approval is remembered.
      onApprove={() => submit(prompt.shape === 'diff' ? { approved: true } : answerPayload(prompt, { remember }))}
      denyLabel="Cancel"
      onDeny={cancel}
    />
  );
}

function ChoiceBody(props: PromptBodyProps) {
  const { prompt, submitting, submit, cancel } = props;
  const single = prompt.shape === 'choices' || !prompt.multi_select;
  const options = Array.isArray(prompt.options) ? prompt.options : Array.isArray(prompt.choices) ? prompt.choices : [];
  const [selected, setSelected] = useState<string[]>([]);
  const [otherText, setOtherText] = useState('');
  const [useOther, setUseOther] = useState(false);
  const toggle = (value: string) => {
    if (single) { setSelected([value]); setUseOther(false); return; }
    setSelected(cur => (cur.includes(value) ? cur.filter(v => v !== value) : [...cur, value]));
  };
  const canSubmit = selected.length > 0 || (useOther && otherText.trim().length > 0);
  return (
    <PromptFrame tone={promptTone(prompt)} chip="Question" title={headline(prompt)} queue={queueOf(props)} testID="prompt-card"
      actions={submitting ? <Submitting /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={() => submit(answerPayload(prompt, { selected, otherText, useOther }))} disabled={!canSubmit} testID="prompt-submit" />
        </Actions>
      )}>
      {prompt.description ? <Text variant="body" tone="ink2">{String(prompt.description)}</Text> : null}
      <ChoicePrompt options={options.map(o => ({ value: optValue(o), label: optLabel(o) }))} selected={selected} onToggle={toggle} multi={!single} />
      {prompt.allow_other ? (
        <ChoicePrompt options={[{ value: '__other__', label: 'Other…' }]} selected={useOther ? ['__other__'] : []} multi={!single}
          onToggle={() => { setUseOther(u => (single ? true : !u)); if (single) setSelected([]); }} />
      ) : null}
      {prompt.allow_other && useOther ? (
        <TextField value={otherText} onChangeText={setOtherText} placeholder="Type your answer" accessibilityLabel="Your answer" testID="prompt-other" />
      ) : null}
    </PromptFrame>
  );
}

function QuizBody(props: PromptBodyProps) {
  const { prompt, submitting, submit, cancel } = props;
  const questions: QuizQuestion[] = Array.isArray(prompt.questions) ? prompt.questions : [];
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const answered = questions.filter(q => answers[q.qid] !== undefined && answers[q.qid] !== '').length;
  return (
    <PromptFrame tone="accent" chip="Quiz" title="Answer the questions" queue={queueOf(props)} testID="prompt-card"
      actions={submitting ? <Submitting /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={() => submit(answerPayload(prompt, { answers }))} disabled={!questions.length || answered < questions.length} testID="prompt-submit" />
        </Actions>
      )}>
      {questions.map((q, qi) => (
        <View key={q.qid || `q-${qi}`} style={styles.quizQuestion}>
          <Text variant="label">{qi + 1}. {q.prompt}</Text>
          {q.kind === 'fill_blank' ? (
            <TextField value={answers[q.qid] || ''} onChangeText={text => setAnswers(cur => ({ ...cur, [q.qid]: text }))} placeholder="Type your answer" accessibilityLabel={`Answer to question ${qi + 1}`} />
          ) : (
            <ChoicePrompt options={(q.options || []).map(o => ({ value: quizOptionValue(o), label: optLabel(o) }))}
              selected={answers[q.qid] !== undefined ? [answers[q.qid]] : []} onToggle={value => setAnswers(cur => ({ ...cur, [q.qid]: value }))} />
          )}
        </View>
      ))}
    </PromptFrame>
  );
}

function ConfirmBody(props: PromptBodyProps) {
  const { prompt, submitting, submit, cancel } = props;
  return (
    <PromptFrame tone={promptTone(prompt)} chip="Confirm" title={headline(prompt)} queue={queueOf(props)} testID="prompt-card"
      actions={submitting ? <Submitting /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="secondary" label="No" onPress={() => submit(answerPayload(prompt, { confirm: false }))} />
          <Button variant="primary" label="Yes" onPress={() => submit(answerPayload(prompt, { confirm: true }))} testID="prompt-submit" />
        </Actions>
      )} />
  );
}

function InputBody(props: PromptBodyProps) {
  const { prompt, submitting, submit, cancel } = props;
  const initial = typeof prompt.default === 'string' ? prompt.default : '';
  const [text, setText] = useState(initial);
  return (
    <PromptFrame tone={promptTone(prompt)} chip="Input" title={headline(prompt)} queue={queueOf(props)} testID="prompt-card"
      actions={submitting ? <Submitting /> : (
        <Actions>
          <CancelButton onPress={cancel} />
          <Button variant="primary" label="Submit" onPress={() => submit(answerPayload(prompt, { text }))} disabled={!text.trim()} testID="prompt-submit" />
        </Actions>
      )}>
      <TextField value={text} onChangeText={setText} placeholder={initial || 'Type your answer'} accessibilityLabel="Your answer" multiline testID="prompt-input" />
    </PromptFrame>
  );
}

/** The card for any prompt shape. */
export function PromptBody(props: PromptBodyProps) {
  switch (props.prompt.shape) {
    case 'tool_approval':
    case 'diff': return <ToolApprovalBody {...props} />;
    case 'choice':
    case 'choices': return <ChoiceBody key={props.prompt.id} {...props} />;
    case 'quiz': return <QuizBody key={props.prompt.id} {...props} />;
    case 'input': return <InputBody key={props.prompt.id} {...props} />;
    default: return <ConfirmBody {...props} />;
  }
}

/** The inline card: the shown prompt, its position in the queue, and a failed answer's error. */
export function InlinePrompt({ prompt, index, total, submitting, error, onAnswer, onCancel, onIndex }: {
  prompt: PendingPrompt;
  /** 0-based. */
  index: number;
  total: number;
  submitting: boolean;
  error?: string | null;
  onAnswer: (prompt: PendingPrompt, payload: Record<string, unknown>) => void;
  onCancel: (prompt: PendingPrompt) => void;
  onIndex?: (index: number) => void;
}) {
  const queue: PromptQueue = { index: index + 1, total, onPrev: onIndex ? () => onIndex(index - 1) : undefined, onNext: onIndex ? () => onIndex(index + 1) : undefined };
  return (
    <View testID="inline-prompt" style={styles.inline}>
      <PromptBody key={prompt.id} prompt={prompt} submitting={submitting} queueDepth={total} queue={queue}
        submit={payload => onAnswer(prompt, payload)} cancel={() => onCancel(prompt)} />
      {error ? <Banner tone="danger" title="The answer didn't go through" testID="inline-prompt-error">{error}</Banner> : null}
    </View>
  );
}

function clock(ms: number): string {
  if (!ms) return '';
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** "✓ Approved · write_file routes.py · 14:02"; auto-approvals carry the caution "auto" tag. */
export function ResolvedLine({ record }: { record: ResolvedPrompt }) {
  const { colors } = useTheme();
  const verb = resolvedVerb(record);
  const subject = resolvedSubject(record);
  const bad = record.cancelled;
  return (
    <View testID={`resolved-${record.id}`} accessible accessibilityLabel={[verb, subject, clock(record.at)].filter(Boolean).join(', ')} style={styles.resolved}>
      <Text variant="label" importantForAccessibility="no" style={{ color: bad ? colors.ink3 : colors.ok.ink }}>{bad ? '✗' : '✓'}</Text>
      <Text variant="label" tone="ink2">{verb}</Text>
      {subject ? <Text variant="monoSm" tone="ink3" numberOfLines={1} style={styles.resolvedSubject}>{subject}</Text> : null}
      {record.auto ? <Chip tone="caution">auto</Chip> : null}
      <Text variant="caption" tone="ink3" numeric>{clock(record.at)}</Text>
    </View>
  );
}

export { isApproval };

const styles = StyleSheet.create({
  inline: { gap: spacing.sm, marginBottom: spacing.base },
  actions: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: spacing.sm },
  loadingRow: { minHeight: 72, alignItems: 'center', justifyContent: 'center' },
  quizQuestion: { gap: spacing.sm, marginBottom: spacing.sm },
  resolved: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.sm },
  resolvedSubject: { flexShrink: 1 },
});
