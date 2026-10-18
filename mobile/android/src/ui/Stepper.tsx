// Pipeline stepper (G§9.13): status glyph + label per step, joined by
// hairlines that turn ok-pigment once a step has passed; the current step is
// a running wash pill; waiting steps show ◆ on the attention wash. Labels
// come from the shared vocabulary. MiniStepper is the compact dots form.

import React from 'react';
import { ScrollView, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, toneOf } from '../theme/tokens';
import { STATUS, asStatus, duration, money } from '../theme/status';
import { Pressable } from './Pressable';
import { Text } from './Text';

export type StepLike = { id: string; label: string; status: string; elapsed?: number; cost_usd?: number; runs?: number; detail?: string };

export function describeStep(s: StepLike): string {
  const st = STATUS[asStatus(s.status)];
  return [`${s.label}: ${st.label}`, (s.elapsed || 0) >= 1 ? duration((s.elapsed || 0) * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', (s.runs || 0) > 1 ? `${s.runs} runs` : ''].filter(Boolean).join(', ');
}

export function MiniStepper({ steps, testID }: { steps: StepLike[]; testID?: string }) {
  const { colors } = useTheme();
  if (!steps.length) return null;
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={`Pipeline: ${steps.map(describeStep).join('; ')}`} style={{ flexDirection: 'row', alignItems: 'center' }}>
      {steps.map((s, i) => {
        const st = STATUS[asStatus(s.status)];
        const t = toneOf(colors, st.tone);
        const hollow = s.status === 'pending';
        return (
          <React.Fragment key={s.id || i}>
            {i > 0 ? <View style={{ width: 6, height: 1, backgroundColor: colors.hairlineStrong }} /> : null}
            <View style={{ width: 8, height: 8, borderRadius: 4, borderWidth: 1.5, borderColor: hollow ? colors.hairlineStrong : t.pigment, backgroundColor: hollow ? 'transparent' : t.pigment }} />
          </React.Fragment>
        );
      })}
    </View>
  );
}

export function PipelineStepper({ steps, onStep }: { steps: StepLike[]; onStep?: (id: string) => void }) {
  const { colors } = useTheme();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingVertical: spacing.sm, paddingHorizontal: spacing.xs, alignItems: 'flex-start' }} accessibilityLabel="Pipeline">
      {steps.map((s, i) => {
        const st = STATUS[asStatus(s.status)];
        const t = toneOf(colors, st.tone);
        const pending = s.status === 'pending';
        const current = s.status === 'active' || s.status === 'waiting';
        const meta = [(s.elapsed || 0) >= 1 ? duration((s.elapsed || 0) * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', (s.runs || 0) > 1 ? `×${s.runs}` : ''].filter(Boolean).join(' · ');
        const passed = (x: StepLike) => x.status === 'done' || x.status === 'skipped';
        return (
          <React.Fragment key={s.id}>
            {i > 0 ? <View style={{ width: 18, height: 1, marginTop: 22, backgroundColor: passed(steps[i - 1]) ? colors.ok.pigment : colors.hairline }} /> : null}
            <Pressable
              testID={`pipeline-step-${s.id}`}
              accessibilityLabel={describeStep(s) + (s.detail ? `. ${s.detail}` : '')}
              onPress={onStep ? () => onStep(s.id) : undefined}
              scale={false}
              style={({ pressed }) => ({ minWidth: 72, minHeight: 44, alignItems: 'center', gap: 2, paddingHorizontal: spacing.xs, borderRadius: radii.md, backgroundColor: pressed ? colors.sunken : 'transparent' })}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm, minHeight: 28, borderRadius: radii.pill, backgroundColor: current ? t.wash : 'transparent' }}>
                <Text variant="label" style={{ color: pending ? colors.ink3 : t.ink }}>{st.icon}</Text>
                <Text variant="caption" style={{ color: pending ? colors.ink3 : current ? t.ink : colors.ink2, fontWeight: current ? '600' : '500' }}>{s.label}</Text>
              </View>
              <Text variant="caption" numeric tone="ink3" numberOfLines={1}>{meta || ' '}</Text>
            </Pressable>
          </React.Fragment>
        );
      })}
    </ScrollView>
  );
}
