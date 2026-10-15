// Wash line icons (G§4): one Lucide-derived set at a 1.5 stroke on a 24
// grid, rendered from the generated path data with react-native-svg. Sizes
// come only from tokens: 16 (inline), 20 (buttons), 24 (navigation). Icons
// are decorative unless given an accessibilityLabel.

import React from 'react';
import Svg, { Circle, Line, Path, Polygon, Polyline, Rect } from 'react-native-svg';
import { iconData, type WashIconName } from '../components/icons/iconData.generated';
import { useTheme } from '../theme/ThemeContext';
import { iconSize } from '../theme/tokens';

export type { WashIconName as IconName };

const ELEMENTS: Record<string, React.ComponentType<Record<string, unknown>>> = {
  path: Path as unknown as React.ComponentType<Record<string, unknown>>,
  circle: Circle as unknown as React.ComponentType<Record<string, unknown>>,
  line: Line as unknown as React.ComponentType<Record<string, unknown>>,
  rect: Rect as unknown as React.ComponentType<Record<string, unknown>>,
  polyline: Polyline as unknown as React.ComponentType<Record<string, unknown>>,
  polygon: Polygon as unknown as React.ComponentType<Record<string, unknown>>,
};

export type IconProps = {
  name: WashIconName;
  size?: 'sm' | 'md' | 'lg';
  color?: string;
  /** Fill the shape (only for the `drop` mark). */
  filled?: boolean;
  accessibilityLabel?: string;
  testID?: string;
};

export function Icon({ name, size = 'md', color, filled, accessibilityLabel, testID }: IconProps) {
  const { colors } = useTheme();
  const px = iconSize[size];
  const stroke = color ?? colors.ink2;
  const parts = iconData[name] || [];
  return (
    <Svg
      testID={testID}
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill={filled ? stroke : 'none'}
      stroke={stroke}
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      accessible={!!accessibilityLabel}
      accessibilityLabel={accessibilityLabel}
      importantForAccessibility={accessibilityLabel ? 'yes' : 'no-hide-descendants'}
    >
      {parts.map(([tag, attrs], i) => {
        const El = ELEMENTS[tag];
        return El ? <El key={i} {...attrs} /> : null;
      })}
    </Svg>
  );
}
