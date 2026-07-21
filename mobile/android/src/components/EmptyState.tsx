import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Text } from './Text';
import { Button } from './Button';
import { useTheme } from '../theme/ThemeContext';

export type EmptyStateProps = {
  title: string;
  message?: string;
  icon?: string;
  actionLabel?: string;
  onAction?: () => void;
};

export function EmptyState({ title, message, actionLabel, onAction }: EmptyStateProps) {
  const { colors, spacing } = useTheme();
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.lg }}>
      <Text variant="lg" dim style={{ marginBottom: spacing.sm, textAlign: 'center' }}>
        {title}
      </Text>
      {message && (
        <Text variant="sm" dim style={{ textAlign: 'center' }}>
          {message}
        </Text>
      )}
      {actionLabel && onAction && (
        <View style={{ marginTop: spacing.base }}>
          <Button title={actionLabel} onPress={onAction} />
        </View>
      )}
    </View>
  );
}