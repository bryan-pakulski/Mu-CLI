// A side panel that slides in from a screen edge (conversations on the left, the workspace on the
// right). It follows the finger: an edge swipe (EdgeSwipeView) drives it through the `drag` /
// `release` handle while the finger moves, and dragging the open panel back toward its edge
// closes it the same way. A tap on the dimmed backdrop or Android back closes it too.

import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Animated, BackHandler, PanResponder, StyleSheet, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { GlassBackdrop } from '../ui/Glass';
import { Pressable } from '../ui/Pressable';

export type SlidePanelHandle = {
  /** The finger has travelled `distance` px in from the edge. */
  drag: (distance: number) => void;
  /** The finger lifted after `distance` px, moving at `velocity` px/ms toward the open position. */
  release: (distance: number, velocity: number) => void;
};

/** Whether a release at `fraction` of the panel width (moving at `velocity`) settles open. */
export function settlesOpen(fraction: number, velocity: number): boolean {
  if (velocity > 0.4) return true;
  if (velocity < -0.4) return false;
  return fraction > 0.35;
}

const clamp = (v: number) => Math.max(0, Math.min(1, v));

type Props = {
  side: 'left' | 'right';
  visible: boolean;
  onClose: () => void;
  /** Called when an edge swipe leaves the panel open. */
  onOpen?: () => void;
  label: string;
  testID?: string;
  children: React.ReactNode;
};

export const SlidePanel = forwardRef<SlidePanelHandle, Props>(function SlidePanel(
  { side, visible, onClose, onOpen, label, testID, children }, ref,
) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { width: screen } = useWindowDimensions();
  const { reducedMotion } = useMotionPreference();
  const width = Math.min(380, Math.round(screen * 0.88));
  const progress = useRef(new Animated.Value(visible ? 1 : 0)).current;
  const [shown, setShown] = useState(visible);
  const shownRef = useRef(visible);

  const reveal = useCallback(() => {
    if (!shownRef.current) { shownRef.current = true; setShown(true); }
  }, []);

  const settle = useCallback((to: 0 | 1) => {
    if (to === 0 && !shownRef.current) return;
    reveal();
    const done = () => { if (to === 0) { shownRef.current = false; setShown(false); } };
    if (reducedMotion) { progress.setValue(to); done(); return; }
    Animated.spring(progress, { toValue: to, speed: 24, bounciness: 0, overshootClamping: true, useNativeDriver: true })
      .start(({ finished }) => { if (finished) done(); });
  }, [progress, reducedMotion, reveal]);

  useEffect(() => { settle(visible ? 1 : 0); }, [settle, visible]);

  useImperativeHandle(ref, () => ({
    drag: distance => { reveal(); progress.setValue(clamp(distance / width)); },
    release: (distance, velocity) => {
      if (settlesOpen(distance / width, velocity)) {
        settle(1);
        if (!visible) onOpen?.();
      } else {
        settle(0);
      }
    },
  }), [onOpen, progress, reveal, settle, visible, width]);

  // Drag the open panel back toward its edge to close it.
  const toward = side === 'left' ? -1 : 1;
  const closer = useMemo(() => PanResponder.create({
    onMoveShouldSetPanResponder: (_e, g) => g.dx * toward > 8 && Math.abs(g.dx) > Math.abs(g.dy) * 1.2,
    onPanResponderMove: (_e, g) => progress.setValue(clamp(1 - Math.max(0, g.dx * toward) / width)),
    onPanResponderRelease: (_e, g) => {
      const travelled = Math.max(0, g.dx * toward);
      if (settlesOpen(1 - travelled / width, -g.vx * toward)) settle(1);
      else { settle(0); onClose(); }
    },
    onPanResponderTerminate: () => settle(visible ? 1 : 0),
  }), [onClose, progress, settle, toward, visible, width]);

  useEffect(() => {
    if (!visible) return undefined;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => { onClose(); return true; });
    return () => sub.remove();
  }, [onClose, visible]);

  if (!shown) return null;
  const translateX = progress.interpolate({ inputRange: [0, 1], outputRange: [side === 'left' ? -width : width, 0] });
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none" testID={testID}>
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: colors.scrim, opacity: progress }]}>
        <Pressable accessibilityLabel={`Close ${label.toLowerCase()}`} scale={false} style={StyleSheet.absoluteFill} onPress={onClose} testID={testID ? `${testID}-backdrop` : undefined} />
      </Animated.View>
      <Animated.View {...closer.panHandlers} accessibilityViewIsModal
        style={[styles.panel, side === 'left' ? styles.left : styles.right, {
          width, paddingBottom: insets.bottom, borderColor: colors.hairline, transform: [{ translateX }],
        }]}>
        <GlassBackdrop level={1} testID={testID ? `${testID}-glass` : undefined} />
        {children}
      </Animated.View>
    </View>
  );
});

const styles = StyleSheet.create({
  panel: { position: 'absolute', top: 0, bottom: 0, overflow: 'hidden' },
  left: { left: 0, borderRightWidth: StyleSheet.hairlineWidth },
  right: { right: 0, borderLeftWidth: StyleSheet.hairlineWidth },
});
