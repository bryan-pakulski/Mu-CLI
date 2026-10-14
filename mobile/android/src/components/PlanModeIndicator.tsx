import React, { useCallback, useState } from 'react';
import { Alert, StyleSheet, TouchableOpacity, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { PLAN_MODE_TOOLTIP, setPlanModeRemote } from '../api/planMode';
import { Text } from './Text';
import { fontWeights, radii, spacing } from '../theme/tokens';

export const PLAN_MODE_PLACEHOLDER = 'Plan mode: read-only — writing is blocked';

/** Shared plan-mode state + toggle used by the header badge and composer strip. */
export function usePlanMode() {
  const planMode = useConnectionStore(state => state.planMode);
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const isConnected = useConnectionStore(state => state.isConnected);
  const [busy, setBusy] = useState(false);

  const setPlanMode = useCallback(async (next: boolean) => {
    if (!activeSessionName || busy) return;
    setBusy(true);
    try {
      await setPlanModeRemote(next, activeSessionName);
    } catch (error) {
      Alert.alert('Could not update plan mode', String(error));
    } finally {
      setBusy(false);
    }
  }, [activeSessionName, busy]);

  const confirmTurnOff = useCallback(() => {
    Alert.alert(
      'Turn off plan mode?',
      'The agent will be able to edit files and run write tools again.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Turn off', style: 'destructive', onPress: () => { void setPlanMode(false); } },
      ],
    );
  }, [setPlanMode]);

  return {
    planMode,
    busy,
    canToggle: Boolean(isConnected && activeSessionName) && !busy,
    setPlanMode,
    turnOff: () => setPlanMode(false),
    confirmTurnOff,
  };
}

/** Header badge — rendered only while plan mode is on. Press to turn it off. */
export function PlanModeBadge() {
  const { colors } = useTheme();
  const { planMode, busy, confirmTurnOff } = usePlanMode();
  if (!planMode) return null;
  return (
    <TouchableOpacity
      testID="plan-mode-badge"
      accessibilityRole="button"
      accessibilityLabel="Plan mode on: read-only, writing is blocked. Tap to turn off."
      accessibilityHint={PLAN_MODE_TOOLTIP}
      disabled={busy}
      onPress={confirmTurnOff}
      style={[styles.badge, { backgroundColor: colors.planSubtle, borderColor: colors.plan }]}
    >
      <Text variant="xs" style={[styles.badgeText, { color: colors.plan }]}>🔒 Plan</Text>
    </TouchableOpacity>
  );
}

/** Persistent strip above the composer — rendered only while plan mode is on. */
export function PlanModeStrip() {
  const { colors } = useTheme();
  const { planMode, busy, turnOff } = usePlanMode();
  if (!planMode) return null;
  return (
    <View
      testID="plan-mode-strip"
      accessibilityRole="summary"
      accessibilityLabel="Plan mode, read-only. Writing is blocked."
      style={[styles.strip, { backgroundColor: colors.planSubtle, borderColor: colors.plan }]}
    >
      <Text variant="xs" style={[styles.stripText, { color: colors.text }]}>
        <Text variant="xs" style={{ color: colors.plan, fontWeight: fontWeights.semibold }}>🔒 Plan mode</Text>
        {' — read-only. Writing is blocked.'}
      </Text>
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Turn off plan mode"
        disabled={busy}
        onPress={() => { void turnOff(); }}
        style={[styles.stripButton, { borderColor: colors.plan }, busy && styles.disabled]}
      >
        <Text variant="xs" style={{ color: colors.plan, fontWeight: fontWeights.semibold }}>
          {busy ? 'Turning off…' : 'Turn off'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radii.pill,
    borderWidth: 1,
  },
  badgeText: { fontWeight: fontWeights.semibold },
  strip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderLeftWidth: 3,
    borderRadius: radii.sm,
  },
  stripText: { flex: 1 },
  stripButton: {
    paddingHorizontal: spacing.md,
    paddingVertical: 4,
    borderWidth: 1,
    borderRadius: radii.pill,
  },
  disabled: { opacity: 0.5 },
});
