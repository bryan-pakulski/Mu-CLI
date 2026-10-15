// Shared layout helpers for the Wash kit.

import { useEffect, useRef } from 'react';
import { StyleSheet } from 'react-native';
import { fonts, spacing } from '../theme/tokens';

export const layout = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  col: { gap: spacing.sm },
  num: { fontVariant: ['tabular-nums'] },
  mono: { fontFamily: fonts.mono },
});

export function useInterval(fn: () => void, ms: number, enabled = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return undefined;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms, enabled]);
}
