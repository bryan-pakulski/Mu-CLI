import React from 'react';
import { View as RNView, ViewProps as RNViewProps } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type ViewProps = RNViewProps;

export function View({ style, ...rest }: ViewProps) {
  return <RNView style={style} {...rest} />;
}