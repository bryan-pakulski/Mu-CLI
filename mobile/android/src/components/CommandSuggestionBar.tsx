import React from 'react';
import { View, TouchableOpacity, StyleSheet, ScrollView } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import type { CompletionItem } from '../hooks/useCommandCompletion';
import { spacing } from '../theme/tokens';

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
  const { colors } = useTheme();

  if (!visible || items.length === 0) return null;

  return (
    <View style={[styles.container, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
      <ScrollView
        style={{ maxHeight: 220 }}
        keyboardShouldPersistTaps="always"
        nestedScrollEnabled
      >
        {items.map((item, idx) => (
          <TouchableOpacity
            key={item.value + idx}
            onPress={() => onSelect(item)}
            style={[
              styles.item,
              idx === selectedIdx ? { backgroundColor: colors.bgHover } : null,
            ]}
          >
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text
                variant="sm"
                style={{
                  color: idx === selectedIdx ? colors.accent : colors.text,
                  fontFamily: 'monospace',
                  fontWeight: idx === selectedIdx ? '600' : '400',
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
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 12,
    marginHorizontal: 10,
    marginBottom: 2,
    overflow: 'hidden',
  },
  item: {
    paddingHorizontal: spacing.base,
    paddingVertical: 10,
    minHeight: 44,
    justifyContent: 'center',
  },
});