import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  TouchableWithoutFeedback,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { SafeAreaModal } from './SafeAreaModal';

export type ModernBottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

export function ModernBottomSheet({ visible, onClose, children, title }: ModernBottomSheetProps) {
  const { colors, spacing, radii } = useTheme();

  return (
    <SafeAreaModal visible={visible} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent edges={['bottom']}>
      <View style={styles.root}>
        <TouchableWithoutFeedback onPress={onClose}>
          <View style={[StyleSheet.absoluteFillObject, styles.backdrop]} />
        </TouchableWithoutFeedback>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
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
              },
            ]}
          >
            <View style={[styles.handle, { backgroundColor: colors.borderStrong }]} />
            {title && (
              <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
                <Text variant="lg" style={styles.title}>{title}</Text>
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel="Close"
                  onPress={onClose}
                  style={styles.closeButton}
                >
                  <Ionicons name="close" size={20} color={colors.textDim} />
                </TouchableOpacity>
              </View>
            )}
            <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
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
  backdrop: { backgroundColor: 'rgba(5,10,16,0.50)' },
  sheet: { maxHeight: '84%', paddingTop: 9, borderTopWidth: StyleSheet.hairlineWidth },
  handle: { width: 34, height: 3, borderRadius: 2, alignSelf: 'center', marginBottom: 9, opacity: 0.7 },
  header: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { fontWeight: '600', letterSpacing: -0.2 },
  closeButton: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
});
