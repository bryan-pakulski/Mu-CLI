import { useEffect, useState } from 'react';
import { AccessibilityInfo, AppState } from 'react-native';

/** Decorative animation should yield to accessibility and background work. */
export function useMotionPreference() {
  const [reducedMotion, setReducedMotion] = useState(true);
  const [active, setActive] = useState(AppState.currentState === 'active');

  useEffect(() => {
    let mounted = true;
    let preferenceChanged = false;
    Promise.resolve(AccessibilityInfo.isReduceMotionEnabled()).then(value => {
      if (mounted && !preferenceChanged && typeof value === 'boolean') setReducedMotion(value);
    }).catch(() => {});
    const motion = AccessibilityInfo.addEventListener('reduceMotionChanged', value => {
      preferenceChanged = true;
      setReducedMotion(value);
    });
    const state = AppState.addEventListener('change', value => setActive(value === 'active'));
    return () => {
      mounted = false;
      motion?.remove();
      state?.remove();
    };
  }, []);

  return { reducedMotion, animate: active && !reducedMotion };
}
