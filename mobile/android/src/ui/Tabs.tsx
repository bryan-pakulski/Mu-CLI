// Tabs and segmented control (G§9.4). Tabs are text tabs with a 2px accent
// underline on the active tab; the segmented control is a `glass-subtle`
// pill track with a glass-strong thumb (solid sunken/surface with Reduce
// decoration).

import React from 'react';
import { ScrollView, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, touchTarget } from '../theme/tokens';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { useGlass } from './Glass';

export type TabItem = { id: string; label: string; badge?: React.ReactNode; testID?: string };

export function Tabs({ items, value, onChange, accessibilityLabel = 'Views' }: { items: TabItem[]; value: string; onChange: (id: string) => void; accessibilityLabel?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} accessibilityRole="tablist" accessibilityLabel={accessibilityLabel} contentContainerStyle={{ paddingHorizontal: spacing.sm }}>
        {items.map(item => {
          const on = item.id === value;
          return (
            <Pressable
              key={item.id}
              testID={item.testID}
              accessibilityRole="tab"
              accessibilityLabel={item.label}
              accessibilityState={{ selected: on }}
              scale={false}
              onPress={() => onChange(item.id)}
              style={({ pressed }) => ({
                minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.md,
                borderBottomWidth: 2, marginBottom: -1, borderBottomColor: on ? colors.accent.ink : 'transparent',
                backgroundColor: pressed ? colors.sunken : 'transparent',
              })}
            >
              <Text variant="label" style={{ color: on ? colors.ink : colors.ink3 }}>{item.label}</Text>
              {item.badge != null && item.badge !== '' ? item.badge : null}
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

export function Segmented<T extends string>({ options, value, onChange, accessibilityLabel, testID }: {
  options: Array<{ value: T; label: string; testID?: string }>; value: T; onChange: (v: T) => void; accessibilityLabel: string; testID?: string;
}) {
  const { colors } = useTheme();
  const glass = useGlass();
  return (
    <View testID={testID} accessibilityRole="radiogroup" accessibilityLabel={accessibilityLabel}
      style={[{ flexDirection: 'row', borderRadius: radii.pill, padding: 3, alignSelf: 'flex-start' }, glass.style('subtle')]}>
      {options.map(o => {
        const on = o.value === value;
        return (
          <Pressable key={o.value} testID={o.testID} accessibilityRole="radio" accessibilityLabel={o.label} accessibilityState={{ selected: on, checked: on }}
            scale={false} onPress={() => onChange(o.value)}
            style={{
              minHeight: 38, paddingHorizontal: spacing.base, alignItems: 'center', justifyContent: 'center', borderRadius: radii.pill,
              borderWidth: 1, borderColor: 'transparent',
              ...(on ? glass.activePill : null),
            }}>
            <Text variant="label" style={{ color: on ? colors.ink : colors.ink3 }}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}
