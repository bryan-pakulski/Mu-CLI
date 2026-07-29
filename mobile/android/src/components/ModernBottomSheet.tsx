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
                backgroundColor: colors.bgLift,
                borderTopLeftRadius: radii.lg + 8,
                borderTopRightRadius: radii.lg + 8,
                paddingHorizontal: spacing.base,
                paddingBottom: spacing.base,
              },
            ]}
          >
            <View style={[styles.handle, { backgroundColor: colors.borderStrong }]} />
            {title && (
              <View style={styles.header}>
                <Text variant="lg" style={styles.title}>{title}</Text>
                <TouchableOpacity
                  accessibilityRole="button"
                  accessibilityLabel="Close"
                  onPress={onClose}
                  style={[styles.closeButton, { backgroundColor: colors.bgHover }]}
                >
                  <Ionicons name="close" size={20} color={colors.text} />
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
  backdrop: { backgroundColor: 'rgba(0,0,0,0.42)' },
  sheet: { maxHeight: '84%', paddingTop: 10 },
  handle: { width: 38, height: 4, borderRadius: 2, alignSelf: 'center', marginBottom: 10 },
  header: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 },
  title: { fontWeight: '700', letterSpacing: -0.3 },
  closeButton: { width: 38, height: 38, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
});
