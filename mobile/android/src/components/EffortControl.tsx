import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { effortApi, effortHint, effortLabel, effortOptions } from '../api/effort';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, spacing } from '../theme/tokens';
import { ModernBottomSheet } from './ModernBottomSheet';
import { Text } from './Text';

const HIT = { top: 6, bottom: 6, left: 4, right: 4 };

/**
 * Reasoning effort for agent turns (session variable `reasoning_effort`). `chip` sits in the
 * composer next to the mode picker; `row` is the settings-menu entry. Both open the same picker.
 */
export function EffortControl({ variant = 'chip' }: { variant?: 'chip' | 'row' }) {
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const activeProvider = useConnectionStore(state => state.activeProvider);
  const activeModel = useConnectionStore(state => state.activeModel);
  const isConnected = useConnectionStore(state => state.isConnected);
  const [effort, setEffort] = useState('');
  const [levels, setLevels] = useState<string[]>(effortOptions());
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    if (!isConnected || !activeSessionName) return;
    try {
      const current = await effortApi.get();
      setEffort(current.effort);
      setLevels(current.levels);
    } catch {
      // Keep the last known value during a transient disconnect.
    }
  }, [activeSessionName, isConnected]);

  // Refresh on session or provider/model switch (effort is per session).
  useEffect(() => {
    void refresh();
  }, [refresh, activeProvider, activeModel]);

  const select = useCallback(async (level: string) => {
    if (!activeSessionName || saving) return;
    const previous = effort;
    setEffort(level);
    setSaving(true);
    try {
      setEffort(await effortApi.set(level, activeSessionName));
      setOpen(false);
    } catch (error) {
      setEffort(previous);
      Alert.alert('Could not update effort', String(error));
    } finally {
      setSaving(false);
    }
  }, [activeSessionName, effort, saving]);

  const disabled = !isConnected || !activeSessionName;
  return (
    <>
      {variant === 'chip' ? (
        <TouchableOpacity
          onPress={() => setOpen(true)}
          disabled={disabled}
          style={[styles.chip, disabled && styles.disabled]}
          hitSlop={HIT}
          accessibilityRole="button"
          accessibilityLabel={`Effort: ${effortLabel(effort)}`}
        >
          <Ionicons name="speedometer-outline" size={13} color={effort ? colors.accent.ink : colors.ink3} />
          <Text variant="xs" style={{ color: effort ? colors.ink2 : colors.ink3 }}>{effortLabel(effort)}</Text>
          <Ionicons name="chevron-down" size={13} color={colors.ink3} />
        </TouchableOpacity>
      ) : (
        <TouchableOpacity
          onPress={() => setOpen(true)}
          disabled={disabled}
          style={[styles.row, disabled && styles.disabled]}
          accessibilityRole="button"
          accessibilityLabel={`Effort: ${effortLabel(effort)}`}
        >
          <Ionicons name="speedometer-outline" size={20} color={colors.ink3} />
          <View style={styles.rowCopy}>
            <Text variant="base" style={{ color: colors.ink, fontWeight: fontWeights.semibold }}>Effort</Text>
            <Text variant="xs" style={{ color: colors.ink3 }}>
              {disabled ? 'Load a session to set effort' : `${effortLabel(effort)} · ${effortHint(effort)}`}
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={17} color={colors.ink3} />
        </TouchableOpacity>
      )}

      <ModernBottomSheet visible={open} onClose={() => setOpen(false)} title="Effort">
        <Text variant="xs" dim style={styles.intro}>
          How much the model reasons per turn. Lower is faster; each provider maps the level to what its API supports.
        </Text>
        <View style={styles.options}>
          {levels.map(level => {
            const active = level === effort;
            return (
              <TouchableOpacity
                key={level || 'default'}
                onPress={() => void select(level)}
                disabled={saving}
                style={[styles.option, {
                  borderColor: active ? colors.accent.ink : colors.hairline,
                  backgroundColor: active ? colors.accent.wash : colors.raised,
                }]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`Effort ${effortLabel(level)}`}
              >
                <View style={styles.optionCopy}>
                  <Text variant="sm" style={{ color: colors.ink, fontWeight: fontWeights.semibold }}>{effortLabel(level)}</Text>
                  <Text variant="xs" dim>{effortHint(level)}</Text>
                </View>
                {active ? <Ionicons name="checkmark" size={18} color={colors.accent.ink} /> : null}
              </TouchableOpacity>
            );
          })}
        </View>
      </ModernBottomSheet>
    </>
  );
}

const styles = StyleSheet.create({
  chip: { minHeight: 32, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm },
  row: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm },
  rowCopy: { flex: 1 },
  disabled: { opacity: 0.4 },
  intro: { marginBottom: spacing.md },
  options: { gap: spacing.xs },
  option: { minHeight: 52, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm, paddingHorizontal: spacing.md, borderWidth: 1, borderRadius: radii.control },
  optionCopy: { flex: 1, paddingRight: spacing.md },
});
