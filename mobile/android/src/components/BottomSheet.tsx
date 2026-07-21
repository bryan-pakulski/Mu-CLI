import React from 'react';
import {
  Modal,
  View,
  StyleSheet,
  TouchableWithoutFeedback,
  ScrollView,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type BottomSheetProps = {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
};

export function BottomSheet({ visible, onClose, children }: BottomSheetProps) {
  const { colors, spacing, radii } = useTheme();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <TouchableWithoutFeedback onPress={onClose}>
        <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.4)' }} />
      </TouchableWithoutFeedback>
      <View
        style={{
          backgroundColor: colors.bg,
          borderTopLeftRadius: radii.lg,
          borderTopRightRadius: radii.lg,
          padding: spacing.base,
          maxHeight: '80%',
        }}
      >
        <View
          style={{
            width: 40,
            height: 4,
            borderRadius: 2,
            backgroundColor: colors.borderStrong,
            alignSelf: 'center',
            marginBottom: spacing.base,
          }}
        />
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView>{children}</ScrollView>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}