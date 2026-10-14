import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, spacing, tint, typography } from '../theme/tokens';

/** Minimal shape shared by every session list surface. */
export type SessionStateSource = {
  name?: string;
  is_current?: boolean;
  is_loaded?: boolean;
  is_busy?: boolean;
};

export type SessionState = 'active' | 'working' | 'loaded' | null;

/**
 * Resolve the single most relevant state for a session row.
 * `active` is the client-side focus (activeSessionName) and wins over the
 * server-reported flags so the row the user is actually chatting in is the
 * one that stands out.
 */
export function sessionState(session: SessionStateSource, active?: boolean): SessionState {
  if (active || session.is_current) return 'active';
  if (session.is_busy) return 'working';
  if (session.is_loaded) return 'loaded';
  return null;
}

export const SESSION_STATE_LABEL: Record<Exclude<SessionState, null>, string> = {
  active: 'Active',
  working: 'Working',
  loaded: 'Loaded',
};

function rank(session: SessionStateSource, activeName?: string | null): number {
  const state = sessionState(session, !!activeName && session.name === activeName);
  return state === 'active' ? 0 : state === 'working' ? 1 : state === 'loaded' ? 2 : 3;
}

/** Stable sort: active → working → loaded → saved, preserving server order within a rank. */
export function sortSessions<T extends SessionStateSource>(sessions: readonly T[], activeName?: string | null): T[] {
  return sessions
    .map((s, i) => [s, i] as const)
    .sort((a, b) => rank(a[0], activeName) - rank(b[0], activeName) || a[1] - b[1])
    .map(([s]) => s);
}

export type SessionStatusBadgeProps = {
  session: SessionStateSource;
  /** True when this row is the client's focused session (activeSessionName). */
  active?: boolean;
  size?: 'sm' | 'md';
};

/**
 * Pill + dot that makes active / working / loaded sessions stand out.
 * Renders nothing for plain saved sessions so the list stays quiet.
 * Active = ● accent (selection); Working = ● running; Loaded = ○ muted.
 */
export function SessionStatusBadge({ session, active, size = 'md' }: SessionStatusBadgeProps) {
  const { colors, radii } = useTheme();
  const state = sessionState(session, active);
  if (!state) return null;
  const label = SESSION_STATE_LABEL[state];
  // planos pill: tone text + tint of the same tone; status glyph + label.
  const tone = state === 'active' ? colors.accent : state === 'working' ? colors.running : colors.muted;
  const glyph = state === 'loaded' ? '○' : '●';
  const palette = { bg: tint(tone, 0.14), border: tint(tone, 0.4), text: state === 'loaded' ? colors.text : tone, dot: tone };
  const sm = size === 'sm';
  return (
    <View
      accessibilityRole="text"
      accessibilityLabel={`Session ${label.toLowerCase()}`}
      testID={`session-state-${state}`}
      style={[
        styles.pill,
        sm && styles.pillSm,
        { backgroundColor: palette.bg, borderColor: palette.border, borderRadius: radii.pill },
      ]}
    >
      <Text variant="xs" importantForAccessibility="no" style={[styles.label, { color: palette.dot }]}>{glyph}</Text>
      <Text variant="xs" style={[styles.label, sm && styles.labelSm, { color: palette.text }]}>
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    borderWidth: 1,
    alignSelf: 'flex-start',
  },
  pillSm: { paddingHorizontal: spacing.sm, paddingVertical: 1, gap: spacing.xs },
  label: { fontSize: fontSizes.xs, lineHeight: typography.xs.lineHeight, fontWeight: fontWeights.medium },
  labelSm: { fontSize: fontSizes.xs },
});
