import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
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

export type ModernBottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

export function ModernBottomSheet({ visible, onClose, children, title }: ModernBottomSheetProps) {
  const { colors, spacing, radii } = useTheme();
  const { height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const { reducedMotion } = useMotionPreference();
  const maxHeight = Math.max(0, height - insets.top - insets.bottom - spacing.base);

  if (!visible) return null;

  return (
    <SafeAreaModal visible={visible} transparent animationType={reducedMotion ? 'none' : 'fade'} onRequestClose={onClose} statusBarTranslucent edges={['top', 'bottom', 'left', 'right']}>
      <View style={styles.root}>
        <TouchableWithoutFeedback onPress={onClose} accessible={false}>
          <View style={[StyleSheet.absoluteFillObject, styles.backdrop]} />
        </TouchableWithoutFeedback>
        <KeyboardAvoidingView pointerEvents="box-none" style={styles.keyboardArea} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <View
            style={[
              styles.sheet,
              {
                backgroundColor: colors.glassStrong,
                borderColor: colors.hairline,
                borderTopLeftRadius: radii.lg + 3,
                borderTopRightRadius: radii.lg + 3,
                paddingHorizontal: spacing.base,
                paddingBottom: spacing.base,
                maxHeight,
              },
            ]}
          >
            <View style={[styles.handle, { backgroundColor: colors.borderStrong }]} />
            <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
                <Text variant="lg" accessibilityRole={title ? 'header' : undefined} numberOfLines={2} style={styles.title}>{title || ''}</Text>
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel="Close"
                  onPress={onClose}
                  style={styles.closeButton}
                >
                  <Ionicons name="close" size={20} color={colors.textDim} />
                </TouchableOpacity>
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
  backdrop: { backgroundColor: 'rgba(5,10,16,0.50)' },
  sheet: { flexShrink: 1, minHeight: 0, width: '100%', maxWidth: 640, alignSelf: 'center', paddingTop: 9, borderTopWidth: StyleSheet.hairlineWidth },
  scroll: { flexShrink: 1, minHeight: 0 },
  scrollContent: { paddingBottom: 4 },
  handle: { width: 34, height: 3, borderRadius: 2, alignSelf: 'center', marginBottom: 9, opacity: 0.7 },
  header: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { flex: 1, flexShrink: 1, paddingRight: 12, paddingVertical: 8, fontWeight: '600', letterSpacing: -0.2 },
  closeButton: { width: 44, height: 44, flexShrink: 0, alignItems: 'center', justifyContent: 'center' },
});
