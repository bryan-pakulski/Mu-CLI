// The one sheet/modal implementation (G§8.3, G§9.17; spec §5.2 replaces
// ModernBottomSheet and SafeAreaModal).
//
// React Native modals render in their own native window, so every modal
// surface owns a modal-local SafeAreaProvider seeded with
// initialWindowMetrics and keeps content above the Android navigation bar
// (navigationBarTranslucent = false). Three presentations:
//
// - `Sheet` (default): bottom sheet with radius-xl top corners, a grab
//   handle, drag-to-dismiss and a 44dp close button. `dismissable={false}`
//   removes every implicit way out (scrim tap, back button, drag, close) so
//   the user must make an explicit choice — used for approvals.
// - `Sheet full`: a full-screen flow (editors, container manager) on canvas.
// - `Dialog`: a centred level-3 card for small confirmations.

import React, { useMemo, useRef } from 'react';
import {
  Animated,
  KeyboardAvoidingView,
  Modal,
  PanResponder,
  Platform,
  ScrollView,
  StyleSheet,
  View,
  useWindowDimensions,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import {
  SafeAreaProvider,
  SafeAreaView,
  initialWindowMetrics,
  useSafeAreaInsets,
  type Edge,
} from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { elevation, radii, spacing, touchTarget } from '../theme/tokens';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Icon } from './Icon';

/** Modal-local insets: the navigation bar must not bleed under modal content. */
const navigationBarTranslucent = false;

export type ModalFrameProps = {
  visible: boolean;
  onRequestClose: () => void;
  transparent?: boolean;
  edges?: Edge[];
  animationType?: 'none' | 'fade' | 'slide';
  containerStyle?: StyleProp<ViewStyle>;
  children: React.ReactNode;
  testID?: string;
};

/** The safe-area-aware native modal window every kit overlay renders into. */
export function ModalFrame({ visible, onRequestClose, transparent = true, edges = ['top', 'bottom'], animationType = 'fade', containerStyle, children, testID }: ModalFrameProps) {
  const { colors } = useTheme();
  return (
    <Modal
      testID={testID}
      visible={visible}
      transparent={transparent}
      animationType={animationType}
      onRequestClose={onRequestClose}
      statusBarTranslucent
      // eslint-disable-next-line @typescript-eslint/ban-ts-comment
      // @ts-ignore navigationBarTranslucent exists on newer RN; false keeps content above the nav bar.
      navigationBarTranslucent={navigationBarTranslucent}
    >
      <SafeAreaProvider initialMetrics={initialWindowMetrics} style={styles.flex}>
        <SafeAreaView edges={edges} style={[styles.flex, transparent ? null : { backgroundColor: colors.canvas }, containerStyle]}>
          {children}
        </SafeAreaView>
      </SafeAreaProvider>
    </Modal>
  );
}

export type SheetProps = {
  visible: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  /** false: no scrim tap, back button, drag or close button. */
  dismissable?: boolean;
  /** Full-screen flow instead of a bottom sheet. */
  full?: boolean;
  /** Sticky footer (actions, primary last). */
  footer?: React.ReactNode;
  /** Wrap children in a ScrollView (default true). */
  scroll?: boolean;
  /** Header accessory (left of the close button). */
  headerRight?: React.ReactNode;
  testID?: string;
  closeLabel?: string;
};

export function Sheet({ visible, onClose, title, children, dismissable = true, full, footer, scroll = true, headerRight, testID, closeLabel = 'Close' }: SheetProps) {
  const { colors, isDark } = useTheme();
  const { reducedMotion } = useMotionPreference();
  const drag = useRef(new Animated.Value(0)).current;
  const responder = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_e, g) => dismissable && g.dy > 6 && Math.abs(g.dy) > Math.abs(g.dx),
    onPanResponderMove: (_e, g) => { if (g.dy > 0) drag.setValue(g.dy); },
    onPanResponderRelease: (_e, g) => {
      if (g.dy > 120 || g.vy > 1.2) { drag.setValue(0); onClose(); return; }
      Animated.timing(drag, { toValue: 0, duration: 200, useNativeDriver: true }).start();
    },
  }), [dismissable, drag, onClose]);

  if (!visible) return null;
  const requestClose = dismissable ? onClose : () => undefined;

  const header = (
    <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
      <Text variant="title3" accessibilityRole={title ? 'header' : undefined} numberOfLines={2} style={styles.title}>{title || ''}</Text>
      {headerRight}
      {dismissable ? (
        <Pressable accessibilityLabel={closeLabel} onPress={onClose}
          style={({ pressed }) => [styles.close, { backgroundColor: pressed ? colors.sunken : 'transparent' }]}>
          <Icon name="x" color={colors.ink3} />
        </Pressable>
      ) : null}
    </View>
  );
  const body = scroll ? (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
      {children}
    </ScrollView>
  ) : <View style={styles.scroll}>{children}</View>;
  const foot = footer ? <View style={[styles.footer, { borderTopColor: colors.hairline }]}>{footer}</View> : null;

  if (full) {
    return (
      <ModalFrame testID={testID} visible transparent={false} onRequestClose={requestClose} animationType={reducedMotion ? 'none' : 'slide'}>
        <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={[styles.fullInner]}>
            {header}
            {body}
            {foot}
          </View>
        </KeyboardAvoidingView>
      </ModalFrame>
    );
  }

  return (
    <ModalFrame testID={testID} visible onRequestClose={requestClose} animationType={reducedMotion ? 'none' : 'fade'} edges={['top', 'bottom', 'left', 'right']}>
      <SheetBody
        colors={colors}
        isDark={isDark}
        dismissable={dismissable}
        onScrim={requestClose}
        drag={drag}
        panHandlers={responder.panHandlers}
      >
        <View {...(dismissable ? responder.panHandlers : null)}>
          <View style={[styles.handle, { backgroundColor: colors.hairlineStrong }]} />
          {header}
        </View>
        {body}
        {foot}
      </SheetBody>
    </ModalFrame>
  );
}

function SheetBody({ colors, isDark, onScrim, drag, children }: {
  colors: ReturnType<typeof useTheme>['colors']; isDark: boolean; dismissable: boolean; onScrim: () => void; drag: Animated.Value;
  panHandlers: object; children: React.ReactNode;
}) {
  const { height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const maxHeight = Math.max(0, height - insets.top - insets.bottom - spacing.base);
  return (
    <View style={styles.root}>
      <Pressable accessible={false} accessibilityRole="none" scale={false} hitSlop={null} onPress={onScrim}
        style={[StyleSheet.absoluteFillObject, { backgroundColor: colors.scrim }]} />
      <KeyboardAvoidingView pointerEvents="box-none" style={styles.keyboardArea} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <Animated.View
          accessibilityViewIsModal
          style={[
            styles.sheet,
            elevation(3, colors, isDark),
            { borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, borderBottomWidth: 0, maxHeight, transform: [{ translateY: drag }] },
          ]}
        >
          {children}
        </Animated.View>
      </KeyboardAvoidingView>
    </View>
  );
}

export type DialogProps = {
  visible: boolean;
  onClose: () => void;
  title: string;
  children?: React.ReactNode;
  /** Actions, primary last. */
  footer?: React.ReactNode;
  dismissable?: boolean;
  testID?: string;
};

/** Centred level-3 dialog: title-2 header, scrolling body, right-aligned actions. */
export function Dialog({ visible, onClose, title, children, footer, dismissable = true, testID }: DialogProps) {
  const { colors, isDark } = useTheme();
  if (!visible) return null;
  const requestClose = dismissable ? onClose : () => undefined;
  return (
    <ModalFrame testID={testID} visible onRequestClose={requestClose}>
      <View style={[styles.dialogRoot, { backgroundColor: colors.scrim }]}>
        <View accessibilityViewIsModal style={[styles.dialog, elevation(3, colors, isDark)]}>
          <Text variant="title2" accessibilityRole="header" style={{ marginBottom: spacing.md }}>{title}</Text>
          <ScrollView style={{ flexGrow: 0 }} keyboardShouldPersistTaps="handled">{children}</ScrollView>
          {footer ? <View style={styles.dialogFooter}>{footer}</View> : null}
        </View>
      </View>
    </ModalFrame>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  root: { flex: 1, justifyContent: 'flex-end' },
  keyboardArea: { flex: 1, justifyContent: 'flex-end', minHeight: 0 },
  sheet: { flexShrink: 1, minHeight: 0, width: '100%', maxWidth: 640, alignSelf: 'center', paddingTop: spacing.sm, paddingHorizontal: spacing.base, paddingBottom: spacing.base, borderTopWidth: 1 },
  fullInner: { flex: 1, paddingHorizontal: spacing.base, paddingBottom: spacing.base },
  scroll: { flexShrink: 1, minHeight: 0 },
  scrollContent: { paddingBottom: spacing.xs },
  handle: { width: 36, height: 4, borderRadius: radii.pill, alignSelf: 'center', marginBottom: spacing.sm },
  header: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.sm, borderBottomWidth: 1 },
  title: { flex: 1, flexShrink: 1, paddingVertical: spacing.sm },
  close: { width: touchTarget, height: touchTarget, flexShrink: 0, alignItems: 'center', justifyContent: 'center', borderRadius: radii.pill },
  footer: { flexDirection: 'row', justifyContent: 'flex-end', flexWrap: 'wrap', gap: spacing.sm, paddingTop: spacing.md, borderTopWidth: 1 },
  dialogRoot: { flex: 1, justifyContent: 'center', paddingHorizontal: spacing.lg },
  dialog: { width: '100%', maxWidth: 560, maxHeight: '88%', alignSelf: 'center', borderRadius: radii.xl, padding: spacing.lg },
  dialogFooter: { flexDirection: 'row', justifyContent: 'flex-end', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.base },
});
