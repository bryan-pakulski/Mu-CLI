import React from 'react';
import { Modal, StyleSheet } from 'react-native';
import type { ModalProps, StyleProp, ViewStyle } from 'react-native';
import {
  SafeAreaProvider,
  SafeAreaView,
  initialWindowMetrics,
} from 'react-native-safe-area-context';
import type { Edge } from 'react-native-safe-area-context';

export type SafeAreaModalProps = Omit<ModalProps, 'children'> & {
  children: React.ReactNode;
  edges?: Edge[];
  containerStyle?: StyleProp<ViewStyle>;
};

/**
 * React Native modals are rendered in their own native window. On Android,
 * relying on the app-level SafeAreaProvider can therefore produce a zero or
 * stale navigation-bar inset. Give every modal window its own provider and
 * constrain its content to the requested system-bar edges.
 */
export function SafeAreaModal({
  children,
  edges = ['top', 'bottom'],
  containerStyle,
  statusBarTranslucent = false,
  navigationBarTranslucent = false,
  ...modalProps
}: SafeAreaModalProps) {
  return (
    <Modal
      {...modalProps}
      statusBarTranslucent={statusBarTranslucent}
      navigationBarTranslucent={navigationBarTranslucent}
    >
      <SafeAreaProvider initialMetrics={initialWindowMetrics} style={styles.provider}>
        <SafeAreaView edges={edges} style={[styles.container, containerStyle]}>
          {children}
        </SafeAreaView>
      </SafeAreaProvider>
    </Modal>
  );
}

const styles = StyleSheet.create({
  provider: { flex: 1 },
  container: { flex: 1 },
});
