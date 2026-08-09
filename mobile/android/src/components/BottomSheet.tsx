import React from 'react';
import {
  View,
  StyleSheet,
  TouchableWithoutFeedback,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { SafeAreaModal } from './SafeAreaModal';

export type BottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

export function BottomSheet({ visible, onClose, children }: BottomSheetProps) {
  const { colors, spacing, radii } = useTheme();
  return (
    <SafeAreaModal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent edges={['bottom']}>
      <TouchableWithoutFeedback onPress={onClose}>
        <View style={styles.backdrop} />
      </TouchableWithoutFeedback>
      <View
        style={[
          styles.sheet,
          {
            backgroundColor: colors.glassStrong,
            borderColor: colors.hairline,
            borderTopLeftRadius: radii.lg + 2,
            borderTopRightRadius: radii.lg + 2,
            padding: spacing.base,
          },
        ]}
      >
        <View style={[styles.handle, { backgroundColor: colors.borderStrong }]} />
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
        </KeyboardAvoidingView>
      </View>
    </SafeAreaModal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(5,10,16,0.48)' },
  sheet: {
    maxHeight: '80%',
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  handle: {
    width: 34,
    height: 3,
    borderRadius: 2,
    alignSelf: 'center',
    marginBottom: 14,
    opacity: 0.7,
  },
});
