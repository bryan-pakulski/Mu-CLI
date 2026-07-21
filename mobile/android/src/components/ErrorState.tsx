import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Text } from './Text';
import { Button } from './Button';
import { useTheme } from '../theme/ThemeContext';

export type ErrorStateProps = {
  message: string;
  onRetry?: () => void;
};

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  const { colors, spacing } = useTheme();
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.lg }}>
      <Text variant="lg" style={{ color: colors.error, marginBottom: spacing.sm, textAlign: 'center' }}>
        {message}
      </Text>
      {onRetry && <Button title="Retry" variant="secondary" onPress={onRetry} />}
    </View>
  );
}