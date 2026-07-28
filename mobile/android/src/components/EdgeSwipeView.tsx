import React, { useMemo, useRef } from 'react';
import { PanResponder, StyleSheet, useWindowDimensions, View } from 'react-native';

export type EdgeSwipeViewProps = {
  children: React.ReactNode;
  onSwipeFromLeft: () => void;
  onSwipeFromRight: () => void;
  edgeWidth?: number;
};

export function EdgeSwipeView({
  children,
  onSwipeFromLeft,
  onSwipeFromRight,
  edgeWidth = 28,
}: EdgeSwipeViewProps) {
  const { width } = useWindowDimensions();
  const startX = useRef(0);

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: event => {
          startX.current = event.nativeEvent.pageX;
          return false;
        },
        onMoveShouldSetPanResponder: (_event, gesture) => {
          const horizontal = Math.abs(gesture.dx) > Math.abs(gesture.dy) * 1.25;
          const fromLeft = startX.current <= edgeWidth && gesture.dx > 10;
          const fromRight = startX.current >= width - edgeWidth && gesture.dx < -10;
          return horizontal && (fromLeft || fromRight);
        },
        onPanResponderRelease: (_event, gesture) => {
          if (startX.current <= edgeWidth && gesture.dx > 64) {
            onSwipeFromLeft();
          } else if (startX.current >= width - edgeWidth && gesture.dx < -64) {
            onSwipeFromRight();
          }
        },
      }),
    [edgeWidth, onSwipeFromLeft, onSwipeFromRight, width],
  );

  return (
    <View style={styles.root} {...responder.panHandlers}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
