// The composer (web .w-composer, Mu §7.5; mobile_parity.md §5): attachment chips, the slash menu,
// the textarea, then the foot — + attach · mode · model · effort · plan · auto-approve, the context
// ring, the mic and Send ↑ / Stop ■. While a turn runs, Send becomes Stop; a slash command can
// still be sent. Plan mode draws the dashed lilac outline; the mode pigment glows along the top.
//
// Transport-agnostic: chat and the fleet chat pass the value, the handlers and whichever foot
// facts they have. The mic records with expo-av and hands the file to `transcribe` (the chat uses
// POST /api/audio/stt); the text lands in the composer, as on the web.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { Audio } from 'expo-av';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { mode as modePigments } from '../theme/wash.generated';
import { useGlass } from './Glass';
import { Chip } from './Chip';
import { Icon } from './Icon';
import { IconButton } from './Button';
import { ContextRing } from './Meters';
import { InkDrop } from './Progress';
import { Pressable } from './Pressable';
import { Text, textStyle } from './Text';

export type ComposerSlashItem = { label: string; desc?: string; value: string };

export type ComposerFoot = {
  mode?: { name: string; label: string; onPress: () => void };
  model?: { label: string; onPress: () => void };
  /** The effort control (e.g. <EffortControl variant="chip" />). */
  effort?: React.ReactNode;
  /** Shown only while on; pressing asks to turn it off. */
  plan?: { on: boolean; onPress: () => void; busy?: boolean };
  /** Shown only while on, in caution ink; pressing turns approvals back on. */
  auto?: { on: boolean; onPress: () => void };
  context?: { fill: number | null; onPress?: () => void };
};

export type ComposerProps = {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  onStop?: () => void;
  busy?: boolean;
  placeholder?: string;
  attachments?: Array<{ id: string; name: string }>;
  onAttach?: () => void;
  onRemoveAttachment?: (id: string) => void;
  slash?: { visible: boolean; items: ComposerSlashItem[]; selectedIdx: number; onSelect: (item: ComposerSlashItem) => void };
  foot?: ComposerFoot;
  /** Speech to text: given the recording's file URI, resolve to its text. Omit to hide the mic. */
  transcribe?: (uri: string) => Promise<string>;
  /** A line under the composer ("◆ Waiting for your answer above · Review"). */
  hint?: React.ReactNode;
  disabled?: boolean;
  testID?: string;
};

export function isSlashCommand(text: string): boolean {
  return /^\/\S/.test(text.trim());
}

type MicState = 'idle' | 'recording' | 'transcribing';

/** Record → stop → transcribe (expo-av). */
export function useMic(transcribe: ComposerProps['transcribe'], onText: (text: string) => void) {
  const [state, setState] = useState<MicState>('idle');
  const [error, setError] = useState<string | null>(null);
  const recording = useRef<Audio.Recording | null>(null);
  useEffect(() => () => { void recording.current?.stopAndUnloadAsync().catch(() => undefined); }, []);

  const start = useCallback(async () => {
    setError(null);
    try {
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) { setError('Microphone access is off. Allow it in Android settings to dictate.'); return; }
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const rec = new Audio.Recording();
      await rec.prepareToRecordAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
      await rec.startAsync();
      recording.current = rec;
      setState('recording');
    } catch (cause) {
      setError(`Couldn't start recording: ${cause instanceof Error ? cause.message : String(cause)}`);
      setState('idle');
    }
  }, []);

  const stop = useCallback(async () => {
    const rec = recording.current;
    recording.current = null;
    if (!rec || !transcribe) { setState('idle'); return; }
    setState('transcribing');
    try {
      await rec.stopAndUnloadAsync();
      const uri = rec.getURI();
      if (!uri) throw new Error('nothing was recorded');
      const text = (await transcribe(uri)).trim();
      if (text) onText(text);
    } catch (cause) {
      setError(`Couldn't transcribe: ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally {
      setState('idle');
    }
  }, [onText, transcribe]);

  return { state, error, toggle: state === 'recording' ? stop : state === 'idle' ? start : () => undefined };
}

export function Composer({
  value, onChangeText, onSend, onStop, busy, placeholder = 'Ask μcli anything…', attachments = [], onAttach, onRemoveAttachment,
  slash, foot, transcribe, hint, disabled, testID = 'composer',
}: ComposerProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const valueRef = useRef(value);
  valueRef.current = value;
  const mic = useMic(transcribe, text => onChangeText(valueRef.current.trim() ? `${valueRef.current.trimEnd()} ${text}` : text));
  const hasContent = value.trim().length > 0 || attachments.length > 0;
  const showStop = !!busy && !isSlashCommand(value);
  const canSend = hasContent && !disabled && (!busy || isSlashCommand(value));
  const planOn = !!foot?.plan?.on;
  const modeName = foot?.mode?.name || 'default';
  const modeTone = colors.mode[(modeName in modePigments ? modeName : 'default') as keyof typeof modePigments];

  return (
    <View testID={testID} style={styles.area}>
      {slash?.visible && slash.items.length ? (
        <View testID={`${testID}-slash`} accessibilityRole="menu" style={[glass.style(2), styles.slash]}>
          <ScrollView style={{ maxHeight: 220 }} keyboardShouldPersistTaps="always" nestedScrollEnabled>
            {slash.items.map((item, i) => (
              <Pressable key={`${item.value}-${i}`} accessibilityRole="menuitem" accessibilityState={{ selected: i === slash.selectedIdx }} scale={false}
                onPress={() => slash.onSelect(item)} testID={`${testID}-slash-${i}`}
                style={({ pressed }) => [styles.slashItem, i === slash.selectedIdx ? { backgroundColor: colors.accent.wash } : pressed ? { backgroundColor: colors.sunken } : null]}>
                <Text variant="mono" numberOfLines={1} style={{ color: i === slash.selectedIdx ? colors.accent.ink : colors.ink }}>{item.label}</Text>
                {item.desc ? <Text variant="caption" tone="ink3" numberOfLines={1}>{item.desc}</Text> : null}
              </Pressable>
            ))}
          </ScrollView>
        </View>
      ) : null}

      {attachments.length ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.attachments}>
          {attachments.map(item => (
            <Chip key={item.id} variant="meta" label={item.name} onRemove={onRemoveAttachment ? () => onRemoveAttachment(item.id) : undefined} removeLabel={`Remove ${item.name} from the next message`} />
          ))}
        </ScrollView>
      ) : null}

      <View style={[glass.style(1), styles.form, {
        borderColor: planOn ? colors.plan.pigment : colors.hairline,
        borderStyle: planOn ? 'dashed' : 'solid',
        borderTopColor: planOn ? colors.plan.pigment : modeTone.pigment,
      }]}>
        <TextInput
          testID={`${testID}-input`}
          value={value}
          onChangeText={onChangeText}
          placeholder={planOn ? 'Plan mode — read-only. Ask for a plan…' : placeholder}
          placeholderTextColor={colors.ink3}
          multiline
          editable={!disabled}
          maxFontSizeMultiplier={2}
          accessibilityLabel="Message μcli"
          style={[styles.input, textStyle('bodyLg'), { color: colors.ink }]}
        />
        <View style={styles.foot}>
          {onAttach ? <IconButton icon="plus" label="Attach a file" onPress={onAttach} selected={attachments.length > 0} testID={`${testID}-attach`} /> : null}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="always" contentContainerStyle={styles.facts} style={{ flex: 1 }}>
            {foot?.mode ? (
              <Pressable onPress={foot.mode.onPress} accessibilityRole="button" accessibilityLabel={`Mode: ${foot.mode.label}`} accessibilityHint="Choose the agent mode" testID={`${testID}-mode`}
                style={[styles.chip, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
                <View style={[styles.modeDot, { backgroundColor: modeTone.pigment }]} />
                <Text variant="caption" tone="ink2">{foot.mode.label}</Text>
                <Text variant="caption" tone="ink3" importantForAccessibility="no">▾</Text>
              </Pressable>
            ) : null}
            {foot?.model ? (
              <Pressable onPress={foot.model.onPress} accessibilityRole="button" accessibilityLabel={`Session settings: ${foot.model.label}`} testID={`${testID}-model`} style={styles.item}>
                <Text variant="caption" tone="ink2" numberOfLines={1} style={{ maxWidth: 160 }}>{foot.model.label}</Text>
                <Text variant="caption" tone="ink3" importantForAccessibility="no">▾</Text>
              </Pressable>
            ) : null}
            {foot?.effort}
            {foot?.plan?.on ? (
              <Pressable onPress={foot.plan.onPress} disabled={foot.plan.busy} accessibilityRole="button" testID={`${testID}-plan`}
                accessibilityLabel="Plan mode on: read-only, writing is blocked. Tap to turn off."
                style={[styles.chip, { backgroundColor: colors.plan.wash, borderColor: colors.plan.pigment }]}>
                <Icon name="lock" size="sm" color={colors.plan.ink} />
                <Text variant="caption" style={{ color: colors.plan.ink }}>Plan mode</Text>
              </Pressable>
            ) : null}
            {foot?.auto?.on ? (
              <Pressable onPress={foot.auto.onPress} accessibilityRole="button" testID={`${testID}-auto`}
                accessibilityLabel="Auto-approve on. Tap to require approvals." style={styles.item}>
                <Text variant="caption" style={{ color: colors.caution.ink }}>◆ Auto-approve</Text>
              </Pressable>
            ) : null}
          </ScrollView>
          {foot?.context && foot.context.fill != null ? (
            <ContextRing fill={foot.context.fill} size={18} showLabel={false} onPress={foot.context.onPress} testID={`${testID}-ring`} />
          ) : null}
          {transcribe ? (
            mic.state === 'transcribing' ? (
              <View style={styles.round} accessible accessibilityLabel="Transcribing"><InkDrop tone="running" size={8} /></View>
            ) : (
              <IconButton icon={mic.state === 'recording' ? 'square' : 'mic'} label={mic.state === 'recording' ? 'Stop recording' : 'Voice input'}
                selected={mic.state === 'recording'} onPress={() => { void mic.toggle(); }} testID={`${testID}-mic`} tone={mic.state === 'recording' ? 'danger' : undefined} />
            )
          ) : null}
          {showStop ? (
            <Pressable onPress={onStop} accessibilityRole="button" accessibilityLabel="Stop generating" haptic="light" testID={`${testID}-stop`}
              style={[styles.round, { backgroundColor: colors.surface, borderColor: colors.hairlineStrong, borderWidth: 1 }]}>
              <View style={[styles.stopGlyph, { backgroundColor: colors.danger.ink }]} />
            </Pressable>
          ) : (
            <Pressable onPress={onSend} disabled={!canSend} accessibilityRole="button" accessibilityLabel="Send" accessibilityState={{ disabled: !canSend }} haptic="light" testID={`${testID}-send`}
              style={[styles.round, { backgroundColor: canSend ? colors.ink : colors.sunken }]}>
              <Icon name="arrow-up" color={canSend ? colors.onInk : colors.ink3} />
            </Pressable>
          )}
        </View>
      </View>
      {mic.error ? <Text variant="caption" style={{ color: colors.danger.ink }} testID={`${testID}-mic-error`}>{mic.error}</Text> : null}
      {hint ? <View style={styles.hint}>{hint}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  area: { paddingHorizontal: spacing.md, paddingTop: spacing.xs, paddingBottom: spacing.sm, gap: spacing.xs },
  slash: { borderRadius: radii.lg, overflow: 'hidden' },
  slashItem: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.base, paddingVertical: spacing.sm },
  attachments: { gap: spacing.xs, paddingHorizontal: 2 },
  form: { borderWidth: 1, borderTopWidth: 3, borderRadius: radii.lg, padding: spacing.xs },
  input: { paddingHorizontal: spacing.sm, paddingTop: 10, paddingBottom: spacing.xs, maxHeight: 220, minHeight: 44, textAlignVertical: 'top' },
  foot: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  facts: { alignItems: 'center', gap: spacing.xs },
  chip: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.pill },
  item: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: 2, paddingHorizontal: spacing.xs },
  modeDot: { width: 8, height: 8, borderRadius: 4 },
  round: { width: 44, height: 44, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center' },
  stopGlyph: { width: 14, height: 14, borderRadius: 3 },
  hint: { paddingHorizontal: spacing.xs },
});
