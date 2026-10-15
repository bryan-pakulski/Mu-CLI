// The one pressable for the Wash kit (spec §5.2 interaction rules).
//
// - pressed scale 0.98 (G§5 "Press"); nothing else moves
// - a 44dp minimum touch target: when the measured visual is smaller, the
//   missing distance is added as hitSlop (G§10.6)
// - accessibilityRole defaults to "button"; disabled is always 0.4 opacity
//   plus accessibilityState.disabled
// - optional light haptics on press-in (primary and approval actions)
//
// It accepts the TouchableOpacity props the old call sites used
// (`activeOpacity` is accepted and ignored) so migrations are mechanical.

import React, { useCallback, useState } from 'react';
import {
  Pressable as RNPressable,
  type GestureResponderEvent,
  type Insets,
  type LayoutChangeEvent,
  type PressableProps as RNPressableProps,
  type PressableStateCallbackType,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { touchTarget } from '../theme/tokens';

export type Haptic = 'light' | 'medium' | false;

export type PressableProps = Omit<RNPressableProps, 'style' | 'hitSlop'> & {
  style?: StyleProp<ViewStyle> | ((state: PressableStateCallbackType) => StyleProp<ViewStyle>);
  /** Extra hit slop; the wrapper always adds enough to reach 44dp. `null` disables slop. */
  hitSlop?: number | Insets | null;
  /** Light/medium impact on press-in. Off by default. */
  haptic?: Haptic;
  /** Pressed scale feedback (default on). */
  scale?: boolean;
  /** Accepted for TouchableOpacity compatibility; ignored. */
  activeOpacity?: number;
};

type HapticsModule = { impactAsync: (style: unknown) => Promise<void>; ImpactFeedbackStyle: { Light: unknown; Medium: unknown } };
let hapticsModule: HapticsModule | null | undefined;

function haptics(): HapticsModule | null {
  if (hapticsModule !== undefined) return hapticsModule;
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    hapticsModule = require('expo-haptics') as HapticsModule;
  } catch {
    hapticsModule = null;
  }
  return hapticsModule;
}

export function fireHaptic(kind: Haptic) {
  if (!kind) return;
  const mod = haptics();
  if (!mod?.impactAsync) return;
  const style = kind === 'medium' ? mod.ImpactFeedbackStyle?.Medium : mod.ImpactFeedbackStyle?.Light;
  mod.impactAsync(style).catch(() => undefined);
}

function slopFor(size: { width: number; height: number } | null): Insets | undefined {
  if (!size) return undefined;
  const x = Math.max(0, Math.ceil((touchTarget - size.width) / 2));
  const y = Math.max(0, Math.ceil((touchTarget - size.height) / 2));
  return x || y ? { top: y, bottom: y, left: x, right: x } : undefined;
}

function mergeSlop(explicit: number | Insets | undefined, needed: Insets | undefined): number | Insets | undefined {
  if (explicit == null) return needed;
  if (!needed) return explicit;
  const e: Insets = typeof explicit === 'number' ? { top: explicit, bottom: explicit, left: explicit, right: explicit } : explicit;
  return {
    top: Math.max(e.top ?? 0, needed.top ?? 0),
    bottom: Math.max(e.bottom ?? 0, needed.bottom ?? 0),
    left: Math.max(e.left ?? 0, needed.left ?? 0),
    right: Math.max(e.right ?? 0, needed.right ?? 0),
  };
}

export function Pressable({
  style,
  hitSlop,
  haptic = false,
  scale = true,
  disabled,
  accessibilityRole,
  accessibilityState,
  onPressIn,
  onLayout,
  activeOpacity: _activeOpacity,
  ...rest
}: PressableProps) {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  const handleLayout = useCallback((event: LayoutChangeEvent) => {
    const { width, height } = event.nativeEvent.layout;
    setSize(prev => (prev && prev.width === width && prev.height === height ? prev : { width, height }));
    onLayout?.(event);
  }, [onLayout]);

  const handlePressIn = useCallback((event: GestureResponderEvent) => {
    if (haptic && !disabled) fireHaptic(haptic);
    onPressIn?.(event);
  }, [disabled, haptic, onPressIn]);

  // An explicit slop never shrinks the target below 44dp: each side takes the
  // larger of the requested and the measured-shortfall slop.
  const resolvedSlop = hitSlop === null ? undefined : mergeSlop(hitSlop, slopFor(size));
  const state = disabled ? { ...accessibilityState, disabled: true } : accessibilityState;

  return (
    <RNPressable
      {...rest}
      disabled={disabled}
      accessibilityRole={accessibilityRole ?? 'button'}
      accessibilityState={state}
      hitSlop={resolvedSlop}
      onLayout={handleLayout}
      onPressIn={handlePressIn}
      style={(pressState) => {
        const base = typeof style === 'function' ? style(pressState) : style;
        return [
          base,
          disabled ? { opacity: 0.4 } : null,
          scale && pressState.pressed && !disabled ? { transform: [{ scale: 0.98 }] } : null,
        ];
      }}
    />
  );
}
