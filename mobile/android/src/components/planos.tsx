// planos UI primitives for React Native — ports of the planos web components
// (Panel, Pill/Badge, Status, Stepper, Tabs, bars, Sparkline, Toasts, form
// fields) built from plain Views: no SVG or other native dependency.
//
// Colours come only from theme tokens; tints use the semantic wash steps. Status always pairs a glyph with a label. Touch targets are >= 44dp.

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  TextInput,
  View,
  type StyleProp,
  type TextInputProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import {
  fonts,
  fontWeights,
  radii,
  spacing,
  toneInk as toneColor,
  washOf,
  touchTarget,
  type Tone as WashTone,
} from '../theme/tokens';
import { STATUS, asStatus, duration, money, type Status } from '../theme/status';

export type Tone = WashTone;

/* ------------------------------------------------------------ Panel */

export type PanelProps = {
  title?: React.ReactNode;
  /** Count/label shown as a badge next to the title. */
  badge?: React.ReactNode;
  badgeTone?: Tone;
  /** Glyph before the title (e.g. ◆ in warn). */
  glyph?: string;
  glyphTone?: Tone;
  right?: React.ReactNode;
  children?: React.ReactNode;
  /** No body padding (tables / lists that draw their own rows). */
  flush?: boolean;
  style?: StyleProp<ViewStyle>;
  bodyStyle?: StyleProp<ViewStyle>;
  testID?: string;
  accessibilityLabel?: string;
};

/** planos panel: surface + 1px border + radius 10 with a header row. */
export function Panel({ title, badge, badgeTone = '', glyph, glyphTone = '', right, children, flush, style, bodyStyle, testID, accessibilityLabel }: PanelProps) {
  const { colors } = useTheme();
  return (
    <View testID={testID} accessibilityLabel={accessibilityLabel} style={[styles.panel, { backgroundColor: colors.surface, borderColor: colors.hairline }, style]}>
      {title != null || right != null ? (
        <View style={[styles.panelHead, { borderBottomColor: colors.hairline }]}>
          {glyph ? <Text variant="sm" accessibilityElementsHidden importantForAccessibility="no" style={{ color: toneColor(colors, glyphTone || 'muted') }}>{glyph}</Text> : null}
          {typeof title === 'string' ? <Text variant="sm" accessibilityRole="header" style={styles.panelTitle} numberOfLines={1}>{title}</Text> : title}
          {badge != null && badge !== '' ? <CountBadge tone={badgeTone}>{badge}</CountBadge> : null}
          <View style={{ flex: 1 }} />
          {right}
        </View>
      ) : null}
      {children != null ? <View style={[flush ? null : styles.panelBody, bodyStyle]}>{children}</View> : null}
    </View>
  );
}

/* ------------------------------------------------------------ Pill / badge */

export function Pill({ tone = 'muted', children, glyph, style, accessibilityLabel, testID }: { tone?: Tone; children: React.ReactNode; glyph?: string; style?: StyleProp<ViewStyle>; accessibilityLabel?: string; testID?: string }) {
  const { colors } = useTheme();
  const c = toneColor(colors, tone || 'muted');
  return (
    <View testID={testID} accessibilityLabel={accessibilityLabel} style={[styles.pill, { backgroundColor: washOf(colors, tone || 'muted'), borderColor: washOf(colors, tone || 'muted') }, style]}>
      {glyph ? <Text variant="xs" importantForAccessibility="no" style={{ color: c, fontWeight: fontWeights.semibold }}>{glyph}</Text> : null}
      <Text variant="xs" style={{ color: c, fontWeight: fontWeights.medium }} numberOfLines={1}>{children}</Text>
    </View>
  );
}

export function CountBadge({ tone = '', children }: { tone?: Tone; children: React.ReactNode }) {
  const { colors } = useTheme();
  const c = tone ? toneColor(colors, tone) : colors.ink3;
  return (
    <View style={[styles.countBadge, { backgroundColor: tone ? washOf(colors, tone) : colors.sunken }]}>
      <Text variant="xs" style={{ color: tone ? c : colors.ink, fontWeight: fontWeights.semibold, fontVariant: ['tabular-nums'] }}>{children}</Text>
    </View>
  );
}

/* ------------------------------------------------------------ Status */

/** Glyph + (visible or accessible) label from the shared status vocabulary. */
export function StatusIcon({ status, label, showLabel = false, size = 'sm' }: { status: Status | string; label?: string; showLabel?: boolean; size?: 'xs' | 'sm' | 'base' }) {
  const { colors } = useTheme();
  const s = STATUS[asStatus(status)];
  const text = label || s.label;
  const c = toneColor(colors, s.tone);
  return (
    <View accessible accessibilityLabel={text} style={styles.statusIcon}>
      <Text variant={size} style={{ color: c, fontWeight: fontWeights.semibold }}>{s.icon}</Text>
      {showLabel ? <Text variant={size} style={{ color: c }}>{text}</Text> : null}
    </View>
  );
}

export function StatusPill({ status, label }: { status: Status | string; label?: string }) {
  const s = STATUS[asStatus(status)];
  return <Pill tone={s.tone} glyph={s.icon} accessibilityLabel={label || s.label}>{label || s.label}</Pill>;
}

/* ------------------------------------------------------------ Buttons */

export type PlanosButtonProps = {
  label: string;
  onPress: () => void;
  /** primary = accent fill (or solid tone fill when a tone is set). */
  primary?: boolean;
  tone?: Tone;
  /** ghost = text only (toolbars, "N unavailable" expanders). */
  ghost?: boolean;
  disabled?: boolean;
  /** Visually disabled but still pressable (shows the reason instead). */
  ariaDisabled?: boolean;
  loading?: boolean;
  small?: boolean;
  icon?: React.ReactNode;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  testID?: string;
  selected?: boolean;
  /** Icon-only button: `label` becomes the accessibility label only. */
  iconOnly?: boolean;
  style?: StyleProp<ViewStyle>;
};

/** planos button: raised + border; primary = accent fill; tone variants (solid when primary). */
export function PlanosButton({ label, onPress, primary, tone = '', ghost, disabled, ariaDisabled, loading, small, icon, accessibilityLabel, accessibilityHint, testID, selected, iconOnly, style }: PlanosButtonProps) {
  const { colors } = useTheme();
  const toneC = tone ? toneColor(colors, tone) : null;
  const solid = !!primary && !disabled && !ariaDisabled;
  const bg = ghost ? 'transparent' : solid ? (toneC || colors.accent.ink) : selected ? colors.accent.wash : colors.raised;
  const border = ghost ? 'transparent' : solid ? (toneC || colors.accent.ink) : toneC ? toneC : selected ? colors.accent.ink : colors.hairline;
  const fg = solid ? colors.onInk : toneC || (selected ? colors.accent.ink : colors.ink);
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!(disabled || ariaDisabled), busy: !!loading, selected: !!selected }}
      disabled={disabled || loading}
      onPress={onPress}
      hitSlop={small ? 6 : 0}
      style={({ pressed }) => [
        styles.btn,
        small && styles.btnSmall,
        iconOnly && styles.btnIcon,
        { backgroundColor: pressed && !solid ? colors.sunken : bg, borderColor: border, opacity: disabled || ariaDisabled ? 0.5 : pressed && solid ? 0.86 : 1 },
        style,
      ]}
    >
      {loading ? <ActivityIndicator size="small" color={fg} /> : icon}
      {iconOnly ? null : <Text variant={small ? 'xs' : 'sm'} style={{ color: fg, fontWeight: fontWeights.medium }} numberOfLines={1}>{label}</Text>}
    </Pressable>
  );
}

/* ------------------------------------------------------------ Tabs */

export type TabItem = { id: string; label: string; badge?: React.ReactNode; testID?: string };

/** Underlined tabs: active tab gets a 2px accent underline. Scrolls horizontally. */
export function Tabs({ items, value, onChange, accessibilityLabel = 'Views' }: { items: TabItem[]; value: string; onChange: (id: string) => void; accessibilityLabel?: string }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.tabsWrap, { borderBottomColor: colors.hairline }]}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} accessibilityRole="tablist" accessibilityLabel={accessibilityLabel} contentContainerStyle={styles.tabsRow}>
        {items.map(item => {
          const on = item.id === value;
          return (
            <Pressable
              key={item.id}
              testID={item.testID}
              accessibilityRole="tab"
              accessibilityLabel={item.label}
              accessibilityState={{ selected: on }}
              onPress={() => onChange(item.id)}
              style={({ pressed }) => [styles.tab, { borderBottomColor: on ? colors.accent.ink : 'transparent', backgroundColor: pressed ? colors.sunken : 'transparent' }]}
            >
              <Text variant="sm" style={{ color: on ? colors.ink : colors.ink3, fontWeight: on ? fontWeights.semibold : fontWeights.medium }}>{item.label}</Text>
              {item.badge != null && item.badge !== '' ? item.badge : null}
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

/** Segmented control (planos `.work-segmented`). */
export function Segmented<T extends string>({ options, value, onChange, accessibilityLabel }: { options: Array<{ value: T; label: string }>; value: T; onChange: (v: T) => void; accessibilityLabel: string }) {
  const { colors } = useTheme();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={accessibilityLabel} style={[styles.segmented, { borderColor: colors.hairline, backgroundColor: colors.canvas }]}>
      {options.map(o => {
        const on = o.value === value;
        return (
          <Pressable key={o.value} accessibilityRole="radio" accessibilityLabel={o.label} accessibilityState={{ selected: on, checked: on }} onPress={() => onChange(o.value)}
            style={[styles.segment, { backgroundColor: on ? colors.raised : 'transparent', borderColor: on ? colors.hairline : 'transparent' }]}>
            <Text variant="xs" style={{ color: on ? colors.ink : colors.ink3, fontWeight: on ? fontWeights.semibold : fontWeights.medium }}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/* ------------------------------------------------------------ Stepper */

export type StepLike = { id: string; label: string; status: string; elapsed?: number; cost_usd?: number; runs?: number; detail?: string };

export function describeStep(s: StepLike): string {
  const st = STATUS[asStatus(s.status)];
  return [`${s.label}: ${st.label}`, (s.elapsed || 0) >= 1 ? duration((s.elapsed || 0) * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', (s.runs || 0) > 1 ? `${s.runs} runs` : ''].filter(Boolean).join(', ');
}

/** Mini pipeline ✓─●─○ built from Views. */
export function MiniStepper({ steps, testID }: { steps: StepLike[]; testID?: string }) {
  const { colors } = useTheme();
  if (!steps.length) return null;
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={`Pipeline: ${steps.map(describeStep).join('; ')}`} style={styles.mini}>
      {steps.map((s, i) => {
        const st = STATUS[asStatus(s.status)];
        const c = toneColor(colors, st.tone);
        const hollow = s.status === 'pending';
        return (
          <React.Fragment key={s.id || i}>
            {i > 0 ? <View style={[styles.miniLink, { backgroundColor: colors.hairline }]} /> : null}
            <View style={[styles.miniNode, { borderColor: c, backgroundColor: hollow ? 'transparent' : c }]} />
          </React.Fragment>
        );
      })}
    </View>
  );
}

/** Full pipeline stepper: node with glyph, label, duration · cost · runs. */
export function PipelineStepper({ steps, onStep }: { steps: StepLike[]; onStep?: (id: string) => void }) {
  const { colors } = useTheme();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.stepper} accessibilityLabel="Pipeline">
      {steps.map((s, i) => {
        const st = STATUS[asStatus(s.status)];
        const c = toneColor(colors, st.tone);
        const pending = s.status === 'pending';
        const meta = [(s.elapsed || 0) >= 1 ? duration((s.elapsed || 0) * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', (s.runs || 0) > 1 ? `×${s.runs}` : ''].filter(Boolean).join(' · ');
        return (
          <React.Fragment key={s.id}>
            {i > 0 ? <View style={[styles.stepLink, { backgroundColor: steps[i - 1].status === 'done' || steps[i - 1].status === 'skipped' ? colors.ok.pigment : colors.hairline }]} /> : null}
            <Pressable
              testID={`pipeline-step-${s.id}`}
              accessibilityRole="button"
              accessibilityLabel={describeStep(s) + (s.detail ? `. ${s.detail}` : '')}
              onPress={onStep ? () => onStep(s.id) : undefined}
              style={({ pressed }) => [styles.step, pressed && { backgroundColor: colors.sunken }]}
            >
              <View style={[styles.stepNode, { borderColor: pending ? colors.hairline : c, backgroundColor: pending ? 'transparent' : washOf(colors, st.tone) }]}>
                <Text variant="sm" style={{ color: pending ? colors.ink3 : c, fontWeight: fontWeights.semibold }}>{st.icon}</Text>
              </View>
              <Text variant="xs" style={{ color: pending ? colors.ink3 : colors.ink, fontWeight: fontWeights.medium }}>{s.label}</Text>
              <Text variant="xs" style={[styles.num, { color: colors.ink3 }]} numberOfLines={1}>{meta || ' '}</Text>
            </Pressable>
          </React.Fragment>
        );
      })}
    </ScrollView>
  );
}

/* ------------------------------------------------------------ Bars / charts */

/** Stacked pass/fail/skip bar. */
export function ResultBar({ passed, failed, skipped = 0, large = false, testID }: { passed: number; failed: number; skipped?: number; large?: boolean; testID?: string }) {
  const { colors } = useTheme();
  const sum = Math.max(1, passed + failed + skipped);
  const text = `${passed} passed, ${failed} failed${skipped ? `, ${skipped} skipped` : ''}`;
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={text} style={[styles.bar, large && styles.barLarge, { backgroundColor: colors.sunken }]}>
      {passed ? <View style={{ flex: passed / sum, backgroundColor: colors.ok.ink }} /> : null}
      {failed ? <View style={{ flex: failed / sum, backgroundColor: colors.danger.ink }} /> : null}
      {skipped ? <View style={{ flex: skipped / sum, backgroundColor: colors.ink3 }} /> : null}
    </View>
  );
}

/** Single-value horizontal meter (cost per stage, readiness). */
export function Meter({ value, max = 1, tone = 'accent', accessibilityLabel }: { value: number; max?: number; tone?: Tone; accessibilityLabel?: string }) {
  const { colors } = useTheme();
  const pct = Math.max(0, Math.min(1, max > 0 ? value / max : 0));
  return (
    <View accessible={!!accessibilityLabel} accessibilityLabel={accessibilityLabel} style={[styles.bar, { backgroundColor: colors.sunken, flex: 1 }]}>
      <View style={{ width: `${pct * 100}%`, backgroundColor: toneColor(colors, tone || 'accent') }} />
    </View>
  );
}

/** Vertical bar sparkline from Views (CPU/mem, duration history). */
export function Sparkline({ values, max, tone = 'running', height = 24, colorsFor, accessibilityLabel, testID }: { values: Array<number | null>; max?: number; tone?: Tone; height?: number; colorsFor?: (i: number) => string | undefined; accessibilityLabel: string; testID?: string }) {
  const { colors } = useTheme();
  const top = max ?? Math.max(1e-9, ...values.map(v => Number(v || 0)));
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={accessibilityLabel} style={[styles.spark, { height, borderBottomColor: colors.hairline }]}>
      {values.map((v, i) => (
        <View key={i} style={{ flex: 1, height: `${Math.max(v == null ? 0 : 4, (Number(v || 0) / top) * 100)}%`, backgroundColor: v == null ? 'transparent' : colorsFor?.(i) || toneColor(colors, tone || 'running'), marginHorizontal: 0.5, borderTopLeftRadius: 1, borderTopRightRadius: 1 }} />
      ))}
    </View>
  );
}

/** Stat tile (dashboard): glyph + bold value + label. */
export function StatTile({ value, label, tone, glyph, onPress, testID }: { value: number | string; label: string; tone: Tone; glyph: string; onPress?: () => void; testID?: string }) {
  const { colors } = useTheme();
  const c = toneColor(colors, tone || 'muted');
  return (
    <Pressable testID={testID} accessibilityRole={onPress ? 'button' : undefined} accessibilityLabel={`${value} ${label}`} onPress={onPress}
      style={({ pressed }) => [styles.stat, { backgroundColor: pressed ? colors.sunken : colors.surface, borderColor: colors.hairline }]}>
      <Text style={[styles.statValue, { color: c }]}><Text style={{ color: c }} importantForAccessibility="no">{glyph}</Text> {value}</Text>
      <Text variant="xs" style={{ color: colors.ink3 }} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------ Notes / banners */

/** planos note/error box: tone tint fill + tone left border. */
export function Banner({ tone = 'warn', glyph, title, children, right, role, testID }: { tone?: Tone; glyph?: string; title?: React.ReactNode; children?: React.ReactNode; right?: React.ReactNode; role?: 'alert' | 'summary'; testID?: string }) {
  const { colors } = useTheme();
  const c = toneColor(colors, tone || 'warn');
  return (
    <View testID={testID} accessibilityRole={role} style={[styles.banner, { backgroundColor: washOf(colors, tone || 'warn'), borderColor: washOf(colors, tone || 'warn'), borderLeftColor: c }]}>
      <View style={styles.row}>
        {glyph ? <Text variant="sm" importantForAccessibility="no" style={{ color: c, fontWeight: fontWeights.semibold }}>{glyph}</Text> : null}
        {typeof title === 'string' ? <Text variant="sm" style={{ flex: 1, color: colors.ink, fontWeight: fontWeights.semibold }}>{title}</Text> : title}
        {right}
      </View>
      {children}
    </View>
  );
}

/* ------------------------------------------------------------ Form fields */

/** Label above field, optional hint and error — the planos Settings look. */
export function Field({ label, hint, error, optional, children, testID }: { label: string; hint?: string; error?: string; optional?: boolean; children: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={styles.field}>
      <Text variant="xs" style={{ color: colors.ink, fontWeight: fontWeights.medium }}>{label}{optional ? <Text variant="xs" style={{ color: colors.ink3 }}> (optional)</Text> : null}</Text>
      {children}
      {hint ? <Text variant="xs" style={{ color: colors.ink3 }}>{hint}</Text> : null}
      {error ? <Text variant="xs" accessibilityRole="alert" style={{ color: colors.danger.ink }}>{error}</Text> : null}
    </View>
  );
}

export function TextField({ style, multiline, mono, invalid, ...rest }: TextInputProps & { mono?: boolean; invalid?: boolean }) {
  const { colors } = useTheme();
  const [focused, setFocused] = useState(false);
  return (
    <TextInput
      placeholderTextColor={colors.ink3}
      multiline={multiline}
      {...rest}
      onFocus={e => { setFocused(true); rest.onFocus?.(e); }}
      onBlur={e => { setFocused(false); rest.onBlur?.(e); }}
      style={[
        styles.input,
        multiline && styles.textarea,
        { color: colors.ink, backgroundColor: colors.canvas, borderColor: invalid ? colors.danger.ink : focused ? colors.accent.ink : colors.hairline, borderWidth: focused ? 2 : 1 },
        mono && { fontFamily: fonts.mono },
        style as StyleProp<TextStyle>,
      ]}
    />
  );
}

/** Switch row: label + hint on the left, switch on the right. */
export function SwitchRow({ label, hint, value, onValueChange, disabled, testID }: { label: string; hint?: string; value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean; testID?: string }) {
  const { colors } = useTheme();
  return (
    <View style={styles.switchRow}>
      <View style={{ flex: 1 }}>
        <Text variant="sm" style={{ fontWeight: fontWeights.medium }}>{label}</Text>
        {hint ? <Text variant="xs" style={{ color: colors.ink3 }}>{hint}</Text> : null}
      </View>
      <Switch
        testID={testID}
        accessibilityLabel={label}
        value={value}
        disabled={disabled}
        onValueChange={onValueChange}
        trackColor={{ false: colors.hairline, true: colors.accent.ink }}
        thumbColor={value ? colors.onInk : colors.raised}
      />
    </View>
  );
}

/** Checkbox row (planos `.check`). */
export function CheckRow({ label, checked, onChange, disabled, accessibilityLabel, testID }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; accessibilityLabel?: string; testID?: string }) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="checkbox" accessibilityLabel={accessibilityLabel || label} accessibilityState={{ checked, disabled: !!disabled }} disabled={disabled}
      onPress={() => onChange(!checked)} style={[styles.check, { opacity: disabled ? 0.5 : 1 }]}>
      <View style={[styles.checkBox, { borderColor: checked ? colors.accent.ink : colors.hairline, backgroundColor: checked ? colors.accent.ink : colors.canvas }]}>
        {checked ? <Text variant="xs" style={{ color: colors.onInk, fontWeight: fontWeights.bold, lineHeight: 14 }}>✓</Text> : null}
      </View>
      <Text variant="xs" style={{ color: colors.ink }}>{label}</Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------ Empty / loading */

export function Empty({ children, status }: { children: React.ReactNode; status?: Status }) {
  const { colors } = useTheme();
  return (
    <View style={styles.empty}>
      {status ? <StatusIcon status={status} /> : null}
      <Text variant="sm" style={{ color: colors.ink3, flex: 1 }}>{children}</Text>
    </View>
  );
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  const { colors } = useTheme();
  return (
    <View accessibilityRole="progressbar" accessibilityLabel={label} style={styles.empty}>
      <ActivityIndicator size="small" color={colors.running.ink} />
      <Text variant="sm" style={{ color: colors.ink3 }}>{label}</Text>
    </View>
  );
}

/** Mono pre block (logs, args, diffs). */
export function Pre({ children, testID, style, numberOfLines }: { children: React.ReactNode; testID?: string; style?: StyleProp<TextStyle>; numberOfLines?: number }) {
  const { colors } = useTheme();
  return (
    <Text testID={testID} selectable variant="xs" numberOfLines={numberOfLines} style={[styles.pre, { backgroundColor: colors.canvas, borderColor: colors.hairline, color: colors.ink }, style]}>{children}</Text>
  );
}

/* ------------------------------------------------------------ Toasts */

type ToastItem = { id: number; text: string; tone: Tone };
const ToastContext = createContext<(text: string, tone?: Tone) => void>(() => {});
const TOAST_GLYPH: Record<string, string> = { ok: '✓', danger: '✗', warn: '◆', running: '●' };

/** Hosts planos toasts (tone left border) and exposes `useToast()`. */
export function ToastHost({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
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
    timers.current.set(id, setTimeout(() => dismiss(id), tone === 'danger' ? 9000 : tone === 'warn' ? 7000 : 4500));
  }, [dismiss]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); timers.current.clear(); }, []);
  return (
    <ToastContext.Provider value={push}>
      <View style={{ flex: 1 }}>
        {children}
        {items.length ? (
          <View pointerEvents="box-none" style={styles.toastStack}>
            {items.map(item => {
              const c = toneColor(colors, item.tone || 'accent');
              return (
                <View key={item.id} accessibilityRole={item.tone === 'danger' ? 'alert' : 'text'} accessibilityLiveRegion="polite"
                  style={[styles.toast, { backgroundColor: colors.raised, borderColor: colors.hairline, borderLeftColor: c }]}>
                  <Text variant="sm" importantForAccessibility="no" style={{ color: c }}>{TOAST_GLYPH[item.tone] || 'ℹ'}</Text>
                  <Text variant="sm" style={{ flex: 1, color: colors.ink }}>{item.text}</Text>
                  <Pressable accessibilityRole="button" accessibilityLabel="Dismiss" onPress={() => dismiss(item.id)} hitSlop={10} style={styles.toastClose}>
                    <Text variant="sm" style={{ color: colors.ink3 }}>✕</Text>
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

/* ------------------------------------------------------------ Table-ish rows */

/** Key/value row used in detail panels. */
export function KV({ label, children }: { label: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <View style={styles.kv}>
      <Text variant="xs" style={{ color: colors.ink3, minWidth: 96 }}>{label}</Text>
      <View style={{ flex: 1 }}>{typeof children === 'string' || typeof children === 'number' ? <Text variant="xs">{children}</Text> : children}</View>
    </View>
  );
}

export function useInterval(fn: () => void, ms: number, enabled = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return undefined;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, enabled]);
}

export const planosStyles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  col: { gap: spacing.sm },
  num: { fontVariant: ['tabular-nums'] },
  mono: { fontFamily: fonts.mono },
});

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  num: { fontVariant: ['tabular-nums'] },
  panel: { borderWidth: 1, borderRadius: radii.panel, overflow: 'hidden' },
  panelHead: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.base, paddingVertical: spacing.sm, borderBottomWidth: 1 },
  panelTitle: { fontWeight: fontWeights.semibold, flexShrink: 1 },
  panelBody: { padding: spacing.base, gap: spacing.sm },
  pill: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, alignSelf: 'flex-start', borderWidth: 1, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 1 },
  countBadge: { minWidth: 20, alignItems: 'center', borderRadius: radii.pill, paddingHorizontal: 6, paddingVertical: 0 },
  statusIcon: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  btn: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.control },
  btnSmall: { minHeight: 36, paddingHorizontal: spacing.sm },
  btnIcon: { minWidth: touchTarget, paddingHorizontal: 0 },
  tabsWrap: { borderBottomWidth: 1 },
  tabsRow: { paddingHorizontal: spacing.sm },
  tab: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md, borderBottomWidth: 2, marginBottom: -1 },
  segmented: { flexDirection: 'row', borderWidth: 1, borderRadius: radii.control, padding: 2, alignSelf: 'flex-start' },
  segment: { minHeight: 36, paddingHorizontal: spacing.md, alignItems: 'center', justifyContent: 'center', borderRadius: radii.control - 2, borderWidth: 1 },
  mini: { flexDirection: 'row', alignItems: 'center' },
  miniNode: { width: 8, height: 8, borderRadius: 4, borderWidth: 1.5 },
  miniLink: { width: 6, height: 1 },
  stepper: { paddingVertical: spacing.sm, paddingHorizontal: spacing.xs, alignItems: 'flex-start' },
  step: { minWidth: 72, minHeight: touchTarget, alignItems: 'center', gap: 2, paddingHorizontal: spacing.xs, borderRadius: radii.control },
  stepNode: { width: 30, height: 30, borderRadius: 15, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  stepLink: { width: 18, height: 2, marginTop: 15 },
  bar: { height: 6, borderRadius: 3, overflow: 'hidden', flexDirection: 'row', minWidth: 40 },
  barLarge: { height: 10, borderRadius: 5 },
  spark: { flexDirection: 'row', alignItems: 'flex-end', borderBottomWidth: 1, alignSelf: 'stretch', minWidth: 60 },
  stat: { flexGrow: 1, flexBasis: '30%', minWidth: 96, minHeight: touchTarget, borderWidth: 1, borderRadius: radii.panel, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, gap: 2 },
  statValue: { fontSize: 20, lineHeight: 26, fontWeight: fontWeights.bold, fontVariant: ['tabular-nums'] },
  banner: { borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, padding: spacing.md, gap: spacing.sm },
  field: { gap: spacing.xs, marginBottom: spacing.md },
  input: { minHeight: touchTarget, borderRadius: radii.control, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, fontSize: 14 },
  textarea: { minHeight: 96, textAlignVertical: 'top' },
  switchRow: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.xs },
  check: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  checkBox: { width: 20, height: 20, borderRadius: 4, borderWidth: 1.5, alignItems: 'center', justifyContent: 'center' },
  empty: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.md },
  pre: { fontFamily: fonts.mono, borderWidth: 1, borderRadius: radii.control, padding: spacing.sm },
  toastStack: { position: 'absolute', left: spacing.base, right: spacing.base, bottom: spacing.xl, gap: spacing.sm },
  toast: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderWidth: 1, borderLeftWidth: 3, borderRadius: radii.control, paddingLeft: spacing.md, minHeight: touchTarget },
  toastClose: { minWidth: touchTarget, minHeight: touchTarget, alignItems: 'center', justifyContent: 'center' },
  kv: { flexDirection: 'row', gap: spacing.sm, paddingVertical: 2 },
});

