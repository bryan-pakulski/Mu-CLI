// Side panels open with a swipe in from the screen edge (conversations from the left, the
// workspace from the right) — never a tap, so the edge stays free for what sits there (the
// conversation-stage rail on the right). Once a drag from the edge is clearly sideways it is
// claimed and reported while the finger moves (`onDrag`), so the panel follows it, then on lift
// (`onRelease`). Vertical scrolling and taps underneath behave as usual.

import React, { useMemo, useRef } from 'react';
import { PanResponder, StyleSheet, useWindowDimensions, View } from 'react-native';

export type EdgeSide = 'left' | 'right';

export type EdgeSwipeViewProps = {
  children: React.ReactNode;
  /** The finger is `distance` px in from `side`'s edge. */
  onDrag: (side: EdgeSide, distance: number) => void;
  /** The finger lifted `distance` px in, moving inward at `velocity` px/ms. */
  onRelease: (side: EdgeSide, distance: number, velocity: number) => void;
  edgeWidth?: number;
};

const CLAIM = 12;

/** Which edge a drag came in from, once it has moved `distance` mostly sideways; else null. */
export function edgeSwipeSide(startX: number, dx: number, dy: number, width: number, edgeWidth: number, distance: number): EdgeSide | null {
  if (Math.abs(dx) < distance || Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  if (startX <= edgeWidth && dx > 0) return 'left';
  if (startX >= width - edgeWidth && dx < 0) return 'right';
  return null;
}

export function EdgeSwipeView({ children, onDrag, onRelease, edgeWidth = 32 }: EdgeSwipeViewProps) {
  const { width } = useWindowDimensions();
  const startX = useRef(0);
  const side = useRef<EdgeSide | null>(null);

  const responder = useMemo(() => {
    const inward = (dx: number) => Math.max(0, side.current === 'left' ? dx : -dx);
    const end = (dx: number, vx: number) => {
      if (side.current) onRelease(side.current, inward(dx), side.current === 'left' ? vx : -vx);
      side.current = null;
    };
    return PanResponder.create({
      // Capture phase: a horizontal edge drag wins even over a touchable child (the stage rail).
      onStartShouldSetPanResponderCapture: event => { startX.current = event.nativeEvent.pageX; return false; },
      onMoveShouldSetPanResponderCapture: (_event, g) => {
        side.current = edgeSwipeSide(startX.current, g.dx, g.dy, width, edgeWidth, CLAIM);
        return side.current !== null;
      },
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: (_event, g) => { if (side.current) onDrag(side.current, inward(g.dx)); },
      onPanResponderRelease: (_event, g) => end(g.dx, g.vx),
      onPanResponderTerminate: (_event, g) => end(g.dx, 0),
    });
  }, [edgeWidth, onDrag, onRelease, width]);

  return <View style={styles.root} {...responder.panHandlers}>{children}</View>;
}

const styles = StyleSheet.create({ root: { flex: 1 } });
