// Loop mode beside the chat (the web's loop panel next to the conversation): the mission's goal,
// its state sentence, the tightest budget's meter and what waits for you. Tapping opens the Loop
// panel. It reloads when the chat regains focus and when a turn ends.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { loopApi, type LoopState } from '../../api/loop';
import { useTheme } from '../../theme/ThemeContext';
import { radii, spacing } from '../../theme/tokens';
import { StateSentence } from '../../ui/Mu';
import { Meter } from '../../ui/Progress';
import { Pressable } from '../../ui/Pressable';
import { Text } from '../../ui/Text';
import { loopNow, loopSentence, meterText } from '../modes/modeModel';

export function LoopMissionStrip({ refreshKey, onOpen }: { refreshKey: unknown; onOpen: () => void }) {
  const { colors } = useTheme();
  const [state, setState] = useState<LoopState | null>(null);

  const load = useCallback(async () => {
    try { setState(await loopApi.getState()); }
    catch { /* the Loop panel shows errors; the strip just keeps its last state */ }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  const mission = state?.mission || null;
  const now = useMemo(() => loopNow(state), [state]);
  const meter = state?.budget_meter || null;

  return (
    <Pressable onPress={onOpen} testID="loop-strip" accessibilityLabel="Open the Loop panel"
      style={[styles.wrap, { backgroundColor: colors.raised, borderColor: colors.hairline }]}>
      {mission ? (
        <>
          <View style={styles.head}>
            <Text variant="xs" dim>Goal</Text>
            <StateSentence parts={loopSentence(mission)} testID="loop-strip-state" />
          </View>
          <Text variant="body" numberOfLines={2} testID="loop-strip-goal">{mission.goal}</Text>
          {meter ? (
            <View style={styles.meter}>
              <Meter value={meter.ratio} accessibilityLabel={meterText(meter)} />
              <Text variant="caption" tone="ink3" numeric>{meterText(meter)}</Text>
            </View>
          ) : null}
          {now.length ? (
            <Text variant="caption" style={{ color: colors.attention.ink }} numberOfLines={1} testID="loop-strip-now">
              {now[0].title}{now.length > 1 ? `  · ${now.length - 1} more` : ''}
            </Text>
          ) : null}
        </>
      ) : (
        <Text variant="caption" dim testID="loop-strip-empty">
          No mission yet. Describe a goal, what done looks like and a budget to start one.
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { marginHorizontal: spacing.base, marginTop: spacing.sm, padding: spacing.sm, gap: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth, borderRadius: radii.md },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  meter: { gap: spacing.xs },
});
