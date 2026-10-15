import React from 'react';
import { View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Text } from './Text';
import { Button } from './Button';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, radii, tracking } from '../theme/tokens';

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
          width: 56,
          height: 56,
          borderRadius: radii.panel,
          borderWidth: 1,
          borderColor: colors.hairline,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: colors.surface,
          marginBottom: spacing.base,
        }}
      >
        <Ionicons name={iconName} size={24} color={colors.ink3} />
      </View>
      <Text variant="md" style={{ marginBottom: spacing.sm, textAlign: 'center', fontWeight: fontWeights.semibold, letterSpacing: tracking.tight }}>
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