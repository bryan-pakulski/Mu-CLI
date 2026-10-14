import React from 'react';
import { View, Pressable, StyleSheet, ScrollView } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import type { CompletionItem } from '../hooks/useCommandCompletion';
import { spacing, fonts, radii, fontWeights, touchTarget, elevation } from '../theme/tokens';

interface CommandSuggestionBarProps {
  visible: boolean;
  items: CompletionItem[];
  selectedIdx: number;
  onSelect: (item: CompletionItem) => void;
}

/**
 * Suggestion dropdown that appears above the chat composer when the user
 * types a slash command. Mirrors the web GUI's cmdComplete dropdown.
 *
 * Shows matching commands + subcommands with help text. Tapping a
 * suggestion inserts it into the input. The hook (useCommandCompletion)
 * handles the completion logic; this component is purely presentational.
 */
export function CommandSuggestionBar({ visible, items, selectedIdx, onSelect }: CommandSuggestionBarProps) {
  const { colors, isDark } = useTheme();

  if (!visible || items.length === 0) return null;

  return (
    <View style={[styles.container, elevation(1, colors, isDark)]}>
      <ScrollView
        style={{ maxHeight: 220 }}
        keyboardShouldPersistTaps="always"
        nestedScrollEnabled
      >
        {items.map((item, idx) => (
          <Pressable
            key={item.value + idx}
            onPress={() => onSelect(item)}
            style={({ pressed }) => [
              styles.item,
              idx === selectedIdx ? { backgroundColor: colors.accentSubtle } : pressed ? { backgroundColor: colors.bgHover } : null,
            ]}
          >
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text
                variant="sm"
                style={{
                  color: idx === selectedIdx ? colors.accentFg : colors.text,
                  fontFamily: fonts.mono,
                  fontWeight: idx === selectedIdx ? fontWeights.semibold : fontWeights.regular,
                }}
                numberOfLines={1}
              >
                {item.label}
              </Text>
              {item.desc ? (
                <Text
                  variant="xs"
                  style={{ color: colors.textDim, marginTop: 2 }}
                  numberOfLines={1}
                >
                  {item.desc}
                </Text>
              ) : null}
            </View>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    borderRadius: radii.lg,
    marginHorizontal: spacing.sm,
    marginBottom: 2,
    overflow: 'hidden',
  },
  item: {
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    minHeight: touchTarget,
    justifyContent: 'center',
  },
});