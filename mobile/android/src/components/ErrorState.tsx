import React from 'react';
import { View } from 'react-native';
import { Text } from './Text';
import { Button } from './Button';
import { useTheme } from '../theme/ThemeContext';
import { radii } from '../theme/tokens';

export type ErrorStateProps = {
  message: string;
  onRetry?: () => void;
};

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  const { colors, spacing } = useTheme();
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.lg }}>
      <View
        accessibilityRole="alert"
        style={{
          flexDirection: 'row',
          gap: spacing.sm,
          alignItems: 'flex-start',
          backgroundColor: colors.errorSubtle,
          borderWidth: 1,
          borderLeftWidth: 3,
          borderColor: colors.danger,
          borderRadius: radii.control,
          padding: spacing.md,
          marginBottom: spacing.md,
          maxWidth: 420,
        }}
      >
        <Text variant="sm" importantForAccessibility="no" style={{ color: colors.danger }}>✗</Text>
        <Text variant="sm" style={{ color: colors.text, flexShrink: 1 }}>
          {message}
        </Text>
      </View>
      {onRetry && <Button title="Retry" variant="secondary" onPress={onRetry} />}
    </View>
  );
}