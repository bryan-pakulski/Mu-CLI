// Form controls (G§9.5): label above, hint below, inline error with ✗.
// Text fields are 44 on touch: a `glass-subtle` inset with a control-border; the switch is accent
// when on; checkboxes are 18px with a 44px hit row; the slider is a sunken
// track with an accent fill and labelled stops.

import React, { useState } from 'react';
import { StyleSheet, Switch as RNSwitch, TextInput, View, type StyleProp, type TextInputProps, type TextStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { controlSize, fonts, radii, spacing, touchTarget, typeStyle, withStaticWeight } from '../theme/tokens';
import { Pressable } from './Pressable';
import { Text, scaleText } from './Text';
import { Icon } from './Icon';
import { Sheet } from './Sheet';
import { useGlass } from './Glass';

export function Field({ label, hint, error, optional, children, testID }: { label: string; hint?: string; error?: string; optional?: boolean; children: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ gap: spacing.xs, marginBottom: spacing.md }}>
      <Text variant="label" tone="ink2">{label}{optional ? <Text variant="caption" tone="ink3"> (optional)</Text> : null}</Text>
      {children}
      {hint ? <Text variant="caption" tone="ink3">{hint}</Text> : null}
      {error ? (
        <View accessibilityRole="alert" style={{ flexDirection: 'row', gap: spacing.xs }}>
          <Text variant="caption" importantForAccessibility="no" style={{ color: colors.danger.ink }}>✗</Text>
          <Text variant="caption" style={{ color: colors.danger.ink, flexShrink: 1 }}>{error}</Text>
        </View>
      ) : null}
    </View>
  );
}

export type TextFieldProps = TextInputProps & { mono?: boolean; invalid?: boolean };

export function TextField({ style, multiline, mono, invalid, ...rest }: TextFieldProps) {
  const { colors, textScale } = useTheme();
  const glass = useGlass();
  const [focused, setFocused] = useState(false);
  return (
    <TextInput
      placeholderTextColor={colors.ink3}
      multiline={multiline}
      maxFontSizeMultiplier={2}
      {...rest}
      onFocus={e => { setFocused(true); rest.onFocus?.(e); }}
      onBlur={e => { setFocused(false); rest.onBlur?.(e); }}
      style={withStaticWeight(StyleSheet.flatten([
        scaleText(typeStyle(mono ? 'mono' : 'body'), textScale),
        {
          minHeight: touchTarget,
          borderRadius: radii.sm,
          paddingHorizontal: spacing.md,
          paddingVertical: spacing.sm,
          color: colors.ink,
          backgroundColor: glass.solid ? colors.surface : glass.style('subtle').backgroundColor,
          borderWidth: focused ? 2 : 1,
          borderColor: invalid ? colors.danger.ink : focused ? colors.accent.ink : colors.controlBorder,
        },
        multiline ? { minHeight: 96, textAlignVertical: 'top' } : null,
        mono ? { fontFamily: fonts.mono } : null,
        style as StyleProp<TextStyle>,
      ]))}
    />
  );
}

/** Looks like a text field with a trailing chevron; opens a picker sheet. */
export function Select<T extends string>({ label, value, options, onChange, placeholder = 'Choose…', disabled, testID, accessibilityLabel }: {
  label: string; value: T | null | undefined; options: Array<{ value: T; label: string; description?: string }>; onChange: (v: T) => void;
  placeholder?: string; disabled?: boolean; testID?: string; accessibilityLabel?: string;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  const [open, setOpen] = useState(false);
  const current = options.find(o => o.value === value);
  return (
    <>
      <Pressable testID={testID} accessibilityLabel={accessibilityLabel || `${label}: ${current?.label || placeholder}`} accessibilityHint="Opens a list of options"
        disabled={disabled} scale={false} onPress={() => setOpen(true)}
        style={{ minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderRadius: radii.sm, borderWidth: 1, borderColor: colors.controlBorder, backgroundColor: glass.solid ? colors.surface : glass.style('subtle').backgroundColor, paddingHorizontal: spacing.md }}>
        <Text variant="body" numberOfLines={1} style={{ flex: 1, color: current ? colors.ink : colors.ink3 }}>{current?.label || placeholder}</Text>
        <Icon name="chevron-down" size="sm" color={colors.ink3} />
      </Pressable>
      <Sheet visible={open} onClose={() => setOpen(false)} title={label}>
        <View style={{ gap: spacing.xs }}>
          {options.map(o => (
            <ChoiceRow key={o.value} label={o.label} description={o.description} selected={o.value === value} onPress={() => { onChange(o.value); setOpen(false); }} />
          ))}
        </View>
      </Sheet>
    </>
  );
}

/** Full-width selectable row (G§9.10): sunken press, accent wash + ✓ when selected. */
export function ChoiceRow({ label, description, selected, onPress, multi, disabled, index, testID }: {
  label: string; description?: string; selected: boolean; onPress: () => void; multi?: boolean; disabled?: boolean; index?: number; testID?: string;
}) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole={multi ? 'checkbox' : 'radio'} accessibilityLabel={label} accessibilityState={{ checked: selected, selected }}
      disabled={disabled} scale={false} onPress={onPress}
      style={({ pressed }) => ({
        minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.md, borderRadius: radii.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
        backgroundColor: selected ? colors.accent.wash : pressed ? colors.sunken : 'transparent',
      })}>
      {index != null ? <Text variant="monoSm" tone="ink3" numeric>{index}</Text> : null}
      <View style={{ flex: 1 }}>
        <Text variant="body" style={{ color: selected ? colors.accent.ink : colors.ink, fontWeight: selected ? '600' : '400' }}>{label}</Text>
        {description ? <Text variant="caption" tone="ink3">{description}</Text> : null}
      </View>
      {selected ? <Icon name="check" size="sm" color={colors.accent.ink} /> : null}
    </Pressable>
  );
}

/** Switch row: label + hint on the left, switch on the right. */
export function SwitchRow({ label, hint, value, onValueChange, disabled, testID }: { label: string; hint?: string; value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean; testID?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.xs }}>
      <View style={{ flex: 1 }}>
        <Text variant="body" style={{ fontWeight: '500' }}>{label}</Text>
        {hint ? <Text variant="caption" tone="ink3">{hint}</Text> : null}
      </View>
      <Switch testID={testID} label={label} value={value} disabled={disabled} onValueChange={onValueChange} />
    </View>
  );
}

export function Switch({ value, onValueChange, disabled, label, testID }: { value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean; label: string; testID?: string }) {
  const { colors } = useTheme();
  return (
    <RNSwitch
      testID={testID}
      accessibilityLabel={label}
      value={value}
      disabled={disabled}
      onValueChange={onValueChange}
      trackColor={{ false: colors.controlBorder, true: colors.accent.ink }}
      thumbColor={colors.surface}
      ios_backgroundColor={colors.controlBorder}
      style={disabled ? { opacity: 0.4 } : undefined}
    />
  );
}

/** Checkbox row (18px box, 44px hit row). */
export function CheckRow({ label, checked, onChange, disabled, accessibilityLabel, testID }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; accessibilityLabel?: string; testID?: string }) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="checkbox" accessibilityLabel={accessibilityLabel || label} accessibilityState={{ checked }} disabled={disabled}
      scale={false} onPress={() => onChange(!checked)}
      style={{ minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
      <View style={{ width: 18, height: 18, borderRadius: radii.xs, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center', borderColor: checked ? colors.accent.ink : colors.controlBorder, backgroundColor: checked ? colors.accent.ink : colors.surface }}>
        {checked ? <Icon name="check" size="sm" color={colors.onInk} /> : null}
      </View>
      <Text variant="body" style={{ color: colors.ink, flexShrink: 1 }}>{label}</Text>
    </Pressable>
  );
}

export const Checkbox = CheckRow;

/** Discrete slider (effort, budgets): sunken track, accent fill, labelled stops. */
export function Slider<T extends string>({ stops, value, onChange, accessibilityLabel, disabled }: {
  stops: Array<{ value: T; label: string }>; value: T; onChange: (v: T) => void; accessibilityLabel: string; disabled?: boolean;
}) {
  const { colors } = useTheme();
  const index = Math.max(0, stops.findIndex(s => s.value === value));
  const pct = stops.length > 1 ? index / (stops.length - 1) : 1;
  return (
    <View accessible accessibilityRole="adjustable" accessibilityLabel={accessibilityLabel}
      accessibilityValue={{ text: stops[index]?.label }}
      accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
      onAccessibilityAction={e => {
        const next = e.nativeEvent.actionName === 'increment' ? index + 1 : index - 1;
        if (!disabled && next >= 0 && next < stops.length) onChange(stops[next].value);
      }}
      style={{ gap: spacing.xs, opacity: disabled ? 0.4 : 1 }}>
      <View style={{ height: 6, borderRadius: radii.pill, backgroundColor: colors.sunken, marginHorizontal: 10, marginTop: 10 }}>
        <View style={{ width: `${pct * 100}%`, height: 6, borderRadius: radii.pill, backgroundColor: colors.accent.ink }} />
        <View style={{ position: 'absolute', left: `${pct * 100}%`, top: -7, marginLeft: -10, width: 20, height: 20, borderRadius: 10, backgroundColor: colors.surface, borderWidth: 2, borderColor: colors.accent.ink }} />
      </View>
      <View style={{ flexDirection: 'row' }}>
        {stops.map(s => (
          <Pressable key={s.value} accessibilityLabel={s.label} accessibilityState={{ selected: s.value === value }} disabled={disabled} scale={false}
            onPress={() => onChange(s.value)} style={{ flex: 1, minHeight: controlSize.lg, alignItems: 'center', justifyContent: 'center' }}>
            <Text variant="caption" style={{ color: s.value === value ? colors.ink : colors.ink3 }}>{s.label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
