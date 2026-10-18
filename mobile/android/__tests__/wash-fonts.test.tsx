// Wash fonts on Android (G§2, spec §5.1): Android cannot pick weights inside
// a custom family, so every weight is a static family registered in App.tsx
// and the type tokens select the family per weight instead of `fontWeight`.
import * as fs from 'fs';
import * as path from 'path';
import React from 'react';
import { Platform, StyleSheet } from 'react-native';
import { render } from '@testing-library/react-native';
import { fontFamilies, fontFamilyFor, fonts, typeStyle, withStaticWeight } from '../src/theme/tokens';
import { type as washType } from '../src/theme/wash.generated';
import { ThemeProvider } from '../src/theme/ThemeContext';
import { Text } from '../src/ui/Text';

const ROOT = path.join(__dirname, '..');

describe('Wash static font families', () => {
  afterEach(() => jest.restoreAllMocks());

  it('registers every weight family in useFonts from a bundled static TTF', () => {
    const app = fs.readFileSync(path.join(ROOT, 'App.tsx'), 'utf8');
    const families = Object.values(fontFamilies).flatMap(byWeight => Object.values(byWeight));
    expect(families).toEqual(['Inter', 'Inter-Medium', 'Inter-SemiBold', 'JetBrainsMono', 'JetBrainsMono-SemiBold']);
    for (const family of families) {
      const entry = app.match(new RegExp(`['"]?${family}['"]?: require\\('\\./assets/fonts/([^']+)'\\)`));
      expect(entry).not.toBeNull();
      expect(fs.existsSync(path.join(ROOT, 'assets/fonts', entry![1]))).toBe(true);
      expect(entry![1]).not.toMatch(/Variable/);
    }
  });

  it('maps weights to the nearest bundled cut', () => {
    expect(fontFamilyFor(fonts.ui, '400')).toBe('Inter');
    expect(fontFamilyFor(fonts.ui, '500')).toBe('Inter-Medium');
    expect(fontFamilyFor(fonts.ui, '600')).toBe('Inter-SemiBold');
    expect(fontFamilyFor(fonts.ui, 'bold')).toBe('Inter-SemiBold');
    expect(fontFamilyFor(fonts.ui, undefined)).toBe('Inter');
    expect(fontFamilyFor(fonts.mono, '400')).toBe('JetBrainsMono');
    expect(fontFamilyFor(fonts.mono, '600')).toBe('JetBrainsMono-SemiBold');
  });

  it('on Android the type tokens carry weight in the family name, not fontWeight', () => {
    jest.replaceProperty(Platform, 'OS', 'android');
    for (const name of Object.keys(washType) as Array<keyof typeof washType>) {
      const style = typeStyle(name);
      const base = name.startsWith('mono') ? fonts.mono : fonts.ui;
      expect(style.fontWeight).toBeUndefined();
      expect(style.fontFamily).toBe(fontFamilyFor(base, washType[name].weight));
    }
    expect(typeStyle('label').fontFamily).toBe('Inter-Medium');
    expect(typeStyle('title-3').fontFamily).toBe('Inter-SemiBold');
    expect(typeStyle('body').fontFamily).toBe('Inter');
    expect(withStaticWeight({ fontFamily: 'Inter-Medium', fontWeight: '600' })).toEqual({ fontFamily: 'Inter-SemiBold' });
    expect(withStaticWeight({ fontFamily: 'JetBrainsMono', fontWeight: '600' })).toEqual({ fontFamily: 'JetBrainsMono-SemiBold' });
    expect(withStaticWeight({ fontFamily: 'serif', fontWeight: '600' })).toEqual({ fontFamily: 'serif', fontWeight: '600' });
  });

  it('elsewhere the static family is paired with its numeric weight', () => {
    jest.replaceProperty(Platform, 'OS', 'ios');
    expect(typeStyle('title-3')).toMatchObject({ fontFamily: 'Inter-SemiBold', fontWeight: '600' });
    expect(withStaticWeight({ fontFamily: 'Inter', fontWeight: '600' })).toEqual({ fontFamily: 'Inter', fontWeight: '600' });
  });

  it('kit Text resolves a fontWeight override to the static family on Android', () => {
    jest.replaceProperty(Platform, 'OS', 'android');
    const screen = render(
      <ThemeProvider>
        <Text testID="semibold" variant="body" style={{ fontWeight: '600' }}>Semibold</Text>
      </ThemeProvider>,
    );
    const style = StyleSheet.flatten(screen.getByTestId('semibold').props.style);
    expect(style.fontFamily).toBe('Inter-SemiBold');
    expect(style.fontWeight).toBeUndefined();
  });
});
