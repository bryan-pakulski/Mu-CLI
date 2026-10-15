// Toasts and banners (G§9.16) plus empty and error states (G§9.18).
//
// Toasts: level 2, radius-md, above the tab bar, glyph in semantic ink. Info
// and success auto-dismiss after 5s; errors persist until dismissed. Polite
// live region (assertive for errors).
// Banners: a full-width state strip with the semantic wash, glyph, sentence,
// one tonal action and an optional dismiss.

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Image, View, type StyleProp, type ViewStyle } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useTheme } from '../theme/ThemeContext';
import { elevation, radii, spacing, toSemantic, toneOf, type Tone } from '../theme/tokens';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Icon, type IconName } from './Icon';
import { Button } from './Button';
import { spotWash } from './WashBackground';

const GLYPH: Record<string, string> = { ok: '✓', danger: '✗', attention: '◆', caution: '◆', running: '●', plan: '◇', info: 'ℹ', accent: 'ℹ', neutral: 'ℹ' };

type ToastItem = { id: number; text: string; tone: Tone };
const ToastContext = createContext<(text: string, tone?: Tone) => void>(() => {});

export function ToastHost({ children, bottomOffset = spacing.xl }: { children: React.ReactNode; bottomOffset?: number }) {
  const { colors, isDark } = useTheme();
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (t) clearTimeout(t);
    timers.current.delete(id);
    setItems(old => old.filter(x => x.id !== id));
  }, []);
  const push = useCallback((text: string, tone: Tone = 'accent') => {
    const id = ++seq.current;
    setItems(old => [...old.slice(-3), { id, text, tone }]);
    if (toSemantic(tone) !== 'danger') timers.current.set(id, setTimeout(() => dismiss(id), 5000));
    else AccessibilityInfo.announceForAccessibility?.(text);
  }, [dismiss]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); timers.current.clear(); }, []);
  return (
    <ToastContext.Provider value={push}>
      <View style={{ flex: 1 }}>
        {children}
        {items.length ? (
          <View pointerEvents="box-none" style={{ position: 'absolute', left: spacing.base, right: spacing.base, bottom: bottomOffset, gap: spacing.sm, alignItems: 'center' }}>
            {items.map(item => {
              const sem = toSemantic(item.tone || 'accent');
              const c = toneOf(colors, sem).ink;
              const isError = sem === 'danger';
              return (
                <View key={item.id} accessibilityRole={isError ? 'alert' : 'text'} accessibilityLiveRegion={isError ? 'assertive' : 'polite'}
                  style={[elevation(2, colors, isDark), { width: '100%', maxWidth: 360, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderRadius: radii.md, paddingLeft: spacing.md, minHeight: 44 }]}>
                  <Text variant="body" importantForAccessibility="no" style={{ color: c }}>{GLYPH[sem] || 'ℹ'}</Text>
                  <Text variant="body" style={{ flex: 1, paddingVertical: spacing.sm }}>{item.text}</Text>
                  <Pressable accessibilityLabel="Dismiss" onPress={() => dismiss(item.id)} style={{ minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' }}>
                    <Icon name="x" size="sm" color={colors.ink3} />
                  </Pressable>
                </View>
              );
            })}
          </View>
        ) : null}
      </View>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

export type BannerProps = {
  tone?: Tone;
  glyph?: string;
  title?: React.ReactNode;
  children?: React.ReactNode;
  right?: React.ReactNode;
  action?: { label: string; onPress: () => void; testID?: string };
  onDismiss?: () => void;
  role?: 'alert' | 'summary';
  dashed?: boolean;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function Banner({ tone = 'attention', glyph, title, children, right, action, onDismiss, role, dashed, testID, style }: BannerProps) {
  const { colors } = useTheme();
  const t = toneOf(colors, tone);
  return (
    <View testID={testID} accessibilityRole={role}
      style={[{ backgroundColor: t.wash, borderRadius: radii.md, padding: spacing.md, gap: spacing.sm, borderWidth: dashed ? 1 : 0, borderStyle: dashed ? 'dashed' : 'solid', borderColor: t.pigment }, style]}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        {glyph ? <Text variant="body" importantForAccessibility="no" style={{ color: t.ink, fontWeight: '600' }}>{glyph}</Text> : null}
        {typeof title === 'string' ? <Text variant="body" style={{ flex: 1, color: t.ink, fontWeight: '600' }}>{title}</Text> : title}
        {right}
        {action ? <Button variant="tonal" tone={tone} size="md" label={action.label} onPress={action.onPress} testID={action.testID} /> : null}
        {onDismiss ? (
          <Pressable accessibilityLabel="Dismiss" onPress={onDismiss} style={{ minWidth: 36, minHeight: 36, alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="x" size="sm" color={t.ink} />
          </Pressable>
        ) : null}
      </View>
      {children}
    </View>
  );
}

export type EmptyStateProps = {
  title: string;
  message?: string;
  /** Wash icon name. */
  icon?: IconName;
  actionLabel?: string;
  onAction?: () => void;
  /** Spot-wash pigment behind the icon. */
  pigment?: 'lagoon' | 'apricot' | 'lilac' | 'meadow' | 'rose' | 'sky';
  testID?: string;
};

function Spot({ pigment, icon, tint }: { pigment: EmptyStateProps['pigment']; icon: IconName; tint: string }) {
  const { name, reduceDecoration, colors } = useTheme();
  return (
    <View style={{ width: 96, height: 96, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.base }}>
      {reduceDecoration ? (
        <View style={{ position: 'absolute', width: 72, height: 72, borderRadius: 36, backgroundColor: colors.pigment[pigment || 'lagoon'].wash }} />
      ) : (
        <Image source={spotWash(pigment || 'lagoon', name)} accessibilityIgnoresInvertColors style={{ position: 'absolute', width: 96, height: 96 }} resizeMode="contain" />
      )}
      <Icon name={icon} size="lg" color={tint} />
    </View>
  );
}

/** Spot wash + line icon + title-3 + one sentence + one action (G§9.18). */
export function EmptyState({ title, message, icon = 'sparkles', actionLabel, onAction, pigment = 'lagoon', testID }: EmptyStateProps) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.xl, paddingVertical: spacing.lg }}>
      <Spot pigment={pigment} icon={icon} tint={colors.pigment[pigment].ink} />
      <Text variant="title3" style={{ marginBottom: spacing.sm, textAlign: 'center' }}>{title}</Text>
      {message ? <Text variant="body" tone="ink2" style={{ textAlign: 'center', maxWidth: 360 }}>{message}</Text> : null}
      {actionLabel && onAction ? (
        <View style={{ marginTop: spacing.lg }}>
          <Button variant="secondary" label={actionLabel} onPress={onAction} />
        </View>
      ) : null}
    </View>
  );
}

/** Rose spot wash, what happened, Retry and Copy details (G§9.18). */
export function ErrorState({ message, onRetry, title = "Something didn't load" }: { message: string; onRetry?: () => void; title?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.lg, paddingVertical: spacing.lg }}>
      <Spot pigment="rose" icon="alert-circle" tint={colors.danger.ink} />
      <Text variant="title3" style={{ marginBottom: spacing.sm, textAlign: 'center' }}>{title}</Text>
      <View accessibilityRole="alert" style={{ maxWidth: 420 }}>
        <Text variant="body" tone="ink2" style={{ textAlign: 'center' }}>{message}</Text>
      </View>
      <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.lg }}>
        {onRetry ? <Button variant="secondary" label="Retry" onPress={onRetry} /> : null}
        <Button variant="ghost" label="Copy details" onPress={() => { void Clipboard.setStringAsync(message); }} />
      </View>
    </View>
  );
}
