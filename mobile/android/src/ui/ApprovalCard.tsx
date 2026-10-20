// Approval card and prompt frame (G§9.9, G§9.10) — safety-critical.
//
// A state card in `attention` on glass: an apricot-wash header strip (the
// wash mixed into the glass) and the "◆ Needs you" status chip, with no side
// rule (G§3.4). High-risk tools escalate the header tint and risk chip to
// `danger`. Approve is `primary`, Deny is `secondary` (danger
// only when the action itself is destructive), Edit is `ghost`. Approve is
// never auto-focused: when the card appears, screen-reader focus moves to the
// heading. Questions (choice, quiz, input, confirm) share the frame in
// `accent` unless they block work.

import React, { useEffect, useRef } from 'react';
import { AccessibilityInfo, ScrollView, View, findNodeHandle, type Text as RNText } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, toneOf, type Tone } from '../theme/tokens';
import { vocabulary } from '../theme/wash.generated';
import { Text } from './Text';
import { Chip } from './Chip';
import { Button } from './Button';
import { CheckRow, ChoiceRow } from './Form';
import { DiffView, looksLikeDiff } from './DiffView';
import { InkDrop } from './Progress';
import { useGlass } from './Glass';

export type PromptQueue = { index: number; total: number; onPrev?: () => void; onNext?: () => void };

export type PromptFrameProps = {
  tone?: Tone;
  /** Status chip text, e.g. "Needs you" (glyph from the vocabulary). */
  chip?: string;
  title: string;
  queue?: PromptQueue;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  testID?: string;
};

/** Move screen-reader focus to the heading (never to an action). */
function useHeadingFocus(ref: React.RefObject<RNText>, key: string) {
  useEffect(() => {
    const t = setTimeout(() => {
      const node = ref.current ? findNodeHandle(ref.current) : null;
      if (node) AccessibilityInfo.setAccessibilityFocus(node);
    }, 250);
    return () => clearTimeout(t);
  }, [key, ref]);
}

export function PromptFrame({ tone = 'attention', chip, title, queue, children, actions, testID }: PromptFrameProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const t = toneOf(colors, tone);
  const heading = useRef<RNText>(null);
  useHeadingFocus(heading, title);
  const waiting = vocabulary.status.waiting;
  return (
    <View testID={testID} style={[glass.style(1), { borderRadius: radii.lg, overflow: 'hidden' }]}>
      <View testID={testID ? `${testID}-header` : undefined} style={{ backgroundColor: glass.tint(tone), paddingHorizontal: spacing.base, paddingVertical: spacing.md, gap: spacing.xs }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
          {chip ? <Chip testID={testID ? `${testID}-chip` : undefined} tone={tone} glyph={tone === 'attention' || tone === 'danger' ? waiting.glyph : undefined} style={{ backgroundColor: glass.solid ? colors.surface : glass.style(2, { flat: true }).backgroundColor }}>{chip}</Chip> : null}
          <View style={{ flex: 1 }} />
          {queue && queue.total > 1 ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
              {queue.onPrev ? <Button variant="icon" icon="chevron-left" label="Previous request" onPress={queue.onPrev} disabled={queue.index <= 1} /> : null}
              <Text variant="caption" numeric style={{ color: t.ink }}>{queue.index} of {queue.total}</Text>
              {queue.onNext ? <Button variant="icon" icon="chevron-right" label="Next request" onPress={queue.onNext} disabled={queue.index >= queue.total} /> : null}
            </View>
          ) : null}
        </View>
        <Text ref={heading} variant="title3" accessibilityRole="header" style={{ color: colors.ink }}>{title}</Text>
      </View>
      <View style={{ padding: spacing.base, gap: spacing.md }}>
        {children}
        {actions}
      </View>
    </View>
  );
}

export type ApprovalCardProps = {
  toolName: string;
  /** The heading ("Permission to edit routes.py", "Run `pytest -q`"); default "Permission to run <tool>". */
  title?: string;
  /** A shell command shown as a code block (shell approvals). */
  command?: string;
  /** A unified diff to review (edit approvals); arguments follow under a disclosure. */
  diff?: string;
  /** Human sentence, e.g. "Allow write_file to run?" */
  description: string;
  /** Primary target (path, command) in mono. */
  target?: string;
  /** Arguments text or a unified diff preview. */
  preview?: string;
  risk?: string;
  /** Shell with network, delete, push: escalates to danger. */
  highRisk?: boolean;
  remember: boolean;
  onRememberChange: (v: boolean) => void;
  onApprove: () => void;
  onDeny: () => void;
  onEdit?: () => void;
  submitting?: boolean;
  queue?: PromptQueue;
  approveTestID?: string;
  denyTestID?: string;
  rememberTestID?: string;
  /** Deny label; "Cancel" keeps the existing prompt-cancel semantics. */
  denyLabel?: string;
  denyIsDestructive?: boolean;
};

export function ApprovalCard({
  toolName, title, command, diff, description, target, preview, risk, highRisk, remember, onRememberChange, onApprove, onDeny, onEdit,
  submitting, queue, approveTestID, denyTestID, rememberTestID, denyLabel = 'Deny', denyIsDestructive,
}: ApprovalCardProps) {
  const { colors } = useTheme();
  const tone: Tone = highRisk ? 'danger' : 'attention';
  const [argsOpen, setArgsOpen] = React.useState(false);
  return (
    <PromptFrame tone={tone} chip="Needs you" title={title || `Permission to run ${toolName}`} queue={queue} testID="approval-card">
      {description ? <Text variant="body" tone="ink2">{description}</Text> : null}
      {command ? (
        <View testID="approval-command" style={{ borderRadius: radii.sm, backgroundColor: colors.code, padding: spacing.md }}>
          <Text variant="mono" selectable>{command}</Text>
        </View>
      ) : target ? <Text variant="mono" numberOfLines={2} ellipsizeMode="middle" selectable>{target}</Text> : null}
      {risk ? <Chip tone={tone} glyph="◆">{`Risk: ${risk}`}</Chip> : null}
      {diff ? <DiffView patch={diff} maxLines={16} /> : null}
      {diff && preview ? (
        <Button variant="ghost" label={argsOpen ? 'Hide the arguments' : 'Show the arguments'} onPress={() => setArgsOpen(o => !o)} testID="approval-args-toggle" />
      ) : null}
      {preview && (!diff || argsOpen) ? (
        looksLikeDiff(preview) ? <DiffView patch={preview} maxLines={12} /> : (
          <View style={{ borderRadius: radii.sm, backgroundColor: colors.code, padding: spacing.md }}>
            <Text variant="caption" tone="ink3" style={{ marginBottom: spacing.xs }}>Arguments</Text>
            <ScrollView style={{ maxHeight: 190 }} nestedScrollEnabled>
              <Text variant="monoSm" selectable>{preview}</Text>
            </ScrollView>
          </View>
        )
      ) : null}
      <CheckRow label="Always allow this tool" checked={remember} onChange={onRememberChange} testID={rememberTestID} />
      {submitting ? (
        <View style={{ minHeight: 44, alignItems: 'center', justifyContent: 'center' }}><InkDrop label="Submitting…" /></View>
      ) : (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end', gap: spacing.sm }}>
          {onEdit ? <Button variant="ghost" label="Edit" onPress={onEdit} /> : null}
          <Button variant={denyIsDestructive ? 'danger' : 'secondary'} label={denyLabel} onPress={onDeny} testID={denyTestID} />
          {/* One control, one meaning: the checkbox decides whether the approval is remembered. */}
          <Button variant="primary" icon="check" label={remember ? 'Always allow' : 'Allow once'} onPress={onApprove} testID={approveTestID} haptic="medium" />
        </View>
      )}
    </PromptFrame>
  );
}

export type ChoiceOption = { value: string; label: string; description?: string };

/** Full-width selectable rows with number cues (G§9.10). */
export function ChoicePrompt({ options, selected, onToggle, multi }: { options: ChoiceOption[]; selected: string[]; onToggle: (value: string) => void; multi?: boolean }) {
  return (
    <View style={{ gap: spacing.xs }}>
      {options.map((o, i) => (
        <ChoiceRow key={`${o.value}-${i}`} index={i < 9 ? i + 1 : undefined} label={o.label} description={o.description} multi={multi}
          selected={selected.includes(o.value)} onPress={() => onToggle(o.value)} />
      ))}
    </View>
  );
}
