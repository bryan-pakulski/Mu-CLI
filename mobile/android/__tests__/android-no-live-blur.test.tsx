/**
 * Android must never mount a live BlurView. expo-blur's Android blur (Dimezis) re-captures the
 * screen behind each view every frame; with the header, tab bar, drawers and sheets mounted and the
 * chat's animated thinking indicator running, it froze the UI on device (taps reached JS, the
 * screen stopped updating). Android chrome uses the translucent glass fill instead.
 */
import fs from 'fs';
import path from 'path';
import React from 'react';
import { Text } from 'react-native';
import { render } from '@testing-library/react-native';

jest.mock('react-native/Libraries/Utilities/Platform', () => {
  const actual = jest.requireActual('react-native/Libraries/Utilities/Platform');
  return { ...actual, OS: 'android', select: (o: Record<string, unknown>) => ('android' in o ? o.android : o.default) };
});

const mockMountedBlur = jest.fn();
jest.mock('expo-blur', () => ({ BlurView: () => { mockMountedBlur(); return null; } }));

import { BLUR_AVAILABLE, GlassChrome } from '../src/ui/Glass';
import { ThemeProvider } from '../src/theme/ThemeContext';

describe('Android chrome has no live blur', () => {
  it('reports no live blur on Android', () => {
    expect(BLUR_AVAILABLE).toBe(false);
  });

  it('renders GlassChrome without a BlurView, keeping a translucent fill', () => {
    const tree = render(<ThemeProvider><GlassChrome testID="chrome"><Text>child</Text></GlassChrome></ThemeProvider>);
    expect(mockMountedBlur).not.toHaveBeenCalled();
    const fill = [tree.getByTestId('chrome-backdrop').props.style].flat(Infinity)
      .find((s: any) => s && s.backgroundColor)?.backgroundColor;
    expect(String(fill)).toMatch(/^rgba\(/); // translucent glass, not an opaque surface
  });

  it('never uses the experimental Dimezis blur method anywhere in src', () => {
    const walk = (dir: string): string[] => fs.readdirSync(dir, { withFileTypes: true }).flatMap(d =>
      d.isDirectory() ? walk(path.join(dir, d.name)) : /\.(tsx?|jsx?)$/.test(d.name) ? [path.join(dir, d.name)] : []);
    const offenders = walk(path.join(__dirname, '..', 'src'))
      .filter(f => /experimentalBlurMethod|dimezisBlurView/.test(fs.readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });
});
