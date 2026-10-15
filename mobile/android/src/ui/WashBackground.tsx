// Watercolour atmosphere (G§6): pre-rendered Wash art behind hero areas
// (Connect, Session start, the Work dashboard), dissolving into canvas at
// the bottom edge. Dusk has its own paintings (never a darkened Paper wash).
// "Reduce decoration" replaces the art with a flat wash band. Art never sits
// behind body text: content placed over it must sit where the fade has
// reached paper, or on a solid surface.

import React from 'react';
import { Image, StyleSheet, View, type ImageSourcePropType, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Defs, LinearGradient, Rect, Stop } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeName } from '../theme/ThemeContext';

export type WashArt = 'dawn' | 'lagoon' | 'meadow';
export type SpotPigment = 'apricot' | 'lagoon' | 'lilac' | 'meadow' | 'rose' | 'sky';

const ART: Record<WashArt, Record<ThemeName, ImageSourcePropType>> = {
  dawn: { paper: require('../../assets/wash/dawn-paper.webp'), dusk: require('../../assets/wash/dawn-dusk.webp') },
  lagoon: { paper: require('../../assets/wash/lagoon-paper.webp'), dusk: require('../../assets/wash/lagoon-dusk.webp') },
  meadow: { paper: require('../../assets/wash/meadow-paper.webp'), dusk: require('../../assets/wash/meadow-dusk.webp') },
};

const SPOTS: Record<SpotPigment, Record<ThemeName, ImageSourcePropType>> = {
  apricot: { paper: require('../../assets/wash/spot-apricot-paper.webp'), dusk: require('../../assets/wash/spot-apricot-dusk.webp') },
  lagoon: { paper: require('../../assets/wash/spot-lagoon-paper.webp'), dusk: require('../../assets/wash/spot-lagoon-dusk.webp') },
  lilac: { paper: require('../../assets/wash/spot-lilac-paper.webp'), dusk: require('../../assets/wash/spot-lilac-dusk.webp') },
  meadow: { paper: require('../../assets/wash/spot-meadow-paper.webp'), dusk: require('../../assets/wash/spot-meadow-dusk.webp') },
  rose: { paper: require('../../assets/wash/spot-rose-paper.webp'), dusk: require('../../assets/wash/spot-rose-dusk.webp') },
  sky: { paper: require('../../assets/wash/spot-sky-paper.webp'), dusk: require('../../assets/wash/spot-sky-dusk.webp') },
};

export function washArt(art: WashArt, theme: ThemeName): ImageSourcePropType {
  return ART[art][theme];
}

export function spotWash(pigment: SpotPigment, theme: ThemeName): ImageSourcePropType {
  return SPOTS[pigment][theme];
}

const FLAT: Record<WashArt, 'apricot' | 'lagoon' | 'meadow'> = { dawn: 'apricot', lagoon: 'lagoon', meadow: 'meadow' };

export type WashBackgroundProps = {
  art?: WashArt;
  /** Height of the art band; the content below it sits on canvas. */
  height?: number;
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  testID?: string;
};

/**
 * A canvas-coloured container with a hero band of watercolour art at the top
 * that fades to canvas. Children render on top, full size.
 */
export function WashBackground({ art = 'dawn', height = 280, children, style, testID }: WashBackgroundProps) {
  const { colors, name, reduceDecoration } = useTheme();
  return (
    <View testID={testID} style={[styles.root, { backgroundColor: colors.canvas }, style]}>
      <View pointerEvents="none" importantForAccessibility="no-hide-descendants" style={[styles.band, { height }]}>
        {reduceDecoration ? (
          <View testID="wash-flat" style={[StyleSheet.absoluteFill, { backgroundColor: colors.pigment[FLAT[art]].wash }]} />
        ) : (
          <Image testID="wash-art" source={washArt(art, name)} resizeMode="cover" accessibilityIgnoresInvertColors style={StyleSheet.absoluteFill} />
        )}
        <Svg style={StyleSheet.absoluteFill} width="100%" height="100%" preserveAspectRatio="none">
          <Defs>
            <LinearGradient id="wash-fade" x1="0" y1="0" x2="0" y2="1">
              <Stop offset="0" stopColor={colors.canvas} stopOpacity={0} />
              <Stop offset="0.4" stopColor={colors.canvas} stopOpacity={0.3} />
              <Stop offset="0.75" stopColor={colors.canvas} stopOpacity={0.85} />
              <Stop offset="1" stopColor={colors.canvas} stopOpacity={1} />
            </LinearGradient>
          </Defs>
          <Rect x="0" y="0" width="100%" height="100%" fill="url(#wash-fade)" />
        </Svg>
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, overflow: 'hidden' },
  band: { position: 'absolute', left: 0, right: 0, top: 0 },
  content: { flex: 1 },
});
