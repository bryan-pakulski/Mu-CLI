import React from 'react';
import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
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

export function EmptyState({ title, message, icon, actionLabel, onAction }: EmptyStateProps) {
  const { colors, spacing } = useTheme();
  const iconName = (icon || 'sparkles-outline') as keyof typeof Ionicons.glyphMap;

  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.xl }}>
      <View
        style={{
          width: 58,
          height: 58,
          borderRadius: 20,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: colors.accentSoft,
          marginBottom: spacing.base,
        }}
      >
        <Ionicons name={iconName} size={27} color={colors.accent} />
      </View>
      <Text variant="xl" style={{ marginBottom: spacing.sm, textAlign: 'center', fontWeight: '700', letterSpacing: -0.4 }}>
        {title}
      </Text>
      {message && (
        <Text variant="sm" dim style={{ textAlign: 'center', maxWidth: 310 }}>
          {message}
        </Text>
      )}
      {actionLabel && onAction && (
        <View style={{ marginTop: spacing.lg, minWidth: 180 }}>
          <Button title={actionLabel} onPress={onAction} variant="secondary" />
        </View>
      )}
    </View>
  );
}