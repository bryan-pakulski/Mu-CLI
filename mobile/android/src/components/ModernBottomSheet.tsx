import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Pressable,
  TouchableWithoutFeedback,
  useWindowDimensions,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { SafeAreaModal } from './SafeAreaModal';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { elevation, fontWeights, radii, spacing, touchTarget, tracking } from '../theme/tokens';

export type ModernBottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

export function ModernBottomSheet({ visible, onClose, children, title }: ModernBottomSheetProps) {
  const { colors, spacing, radii, isDark } = useTheme();
  const { height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const { reducedMotion } = useMotionPreference();
  const maxHeight = Math.max(0, height - insets.top - insets.bottom - spacing.base);

  if (!visible) return null;

  return (
    <SafeAreaModal visible={visible} transparent animationType={reducedMotion ? 'none' : 'fade'} onRequestClose={onClose} statusBarTranslucent edges={['top', 'bottom', 'left', 'right']}>
      <View style={styles.root}>
        <TouchableWithoutFeedback onPress={onClose} accessible={false}>
          <View style={[StyleSheet.absoluteFillObject, { backgroundColor: colors.scrim }]} />
        </TouchableWithoutFeedback>
        <KeyboardAvoidingView pointerEvents="box-none" style={styles.keyboardArea} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <View
            style={[
              styles.sheet,
              elevation(2, colors, isDark),
              {
                backgroundColor: colors.raised,
                borderColor: colors.hairline,
                borderTopLeftRadius: radii.panel,
                borderTopRightRadius: radii.panel,
                borderBottomWidth: 0,
                paddingHorizontal: spacing.base,
                paddingBottom: spacing.base,
                maxHeight,
              },
            ]}
          >
            <View style={[styles.handle, { backgroundColor: colors.hairline }]} />
            <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
                <Text variant="md" accessibilityRole={title ? 'header' : undefined} numberOfLines={2} style={styles.title}>{title || ''}</Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Close"
                  onPress={onClose}
                  style={({ pressed }) => [styles.closeButton, { borderRadius: radii.sm }, pressed && { backgroundColor: colors.sunken }]}
                >
                  <Ionicons name="close" size={20} color={colors.ink3} />
                </Pressable>
            </View>
            <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
              {children}
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'flex-end' },
  keyboardArea: { flex: 1, justifyContent: 'flex-end', minHeight: 0 },
  sheet: { flexShrink: 1, minHeight: 0, width: '100%', maxWidth: 640, alignSelf: 'center', paddingTop: spacing.sm, borderTopWidth: 1 },
  scroll: { flexShrink: 1, minHeight: 0 },
  scrollContent: { paddingBottom: spacing.xs },
  handle: { width: 32, height: 4, borderRadius: radii.pill, alignSelf: 'center', marginBottom: spacing.sm },
  header: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
    borderBottomWidth: 1,
  },
  title: { flex: 1, flexShrink: 1, paddingRight: spacing.md, paddingVertical: spacing.sm, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  closeButton: { width: touchTarget, height: touchTarget, flexShrink: 0, alignItems: 'center', justifyContent: 'center' },
});
