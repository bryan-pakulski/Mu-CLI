// Text size (Settings → Appearance; tokens.json density.text_scale): text and its line height
// scale with the setting, never below the readable floor, and full sheets give their content the
// whole height (the fleet chat's transcript, viewers).
import React, { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import { act, render } from '@testing-library/react-native';
import { ThemeProvider, parseTextScale, TEXT_SCALE_RANGE, useTheme } from '../../src/theme/ThemeContext';
import { typeStyle } from '../../src/theme/tokens';
import { MIN_SCALED_TEXT, Sheet, Text, scaleText } from '../../src/ui';

describe('scaleText', () => {
  it('multiplies size and line height, keeps the floor, and is a no-op at 100%', () => {
    const body = typeStyle('body');
    expect(scaleText(body, 100)).toBe(body);
    const big = scaleText(body, 120);
    expect(big.fontSize).toBeCloseTo((body.fontSize as number) * 1.2, 0);
    expect(big.lineHeight).toBeGreaterThan(body.lineHeight as number);
    const small = scaleText(typeStyle('caption'), TEXT_SCALE_RANGE.min);
    expect(small.fontSize).toBeGreaterThanOrEqual(MIN_SCALED_TEXT);
    expect(small.lineHeight as number).toBeGreaterThanOrEqual((small.fontSize as number) * 1.2);
  });

  it('parses stored sizes into the range and step', () => {
    expect(parseTextScale(null)).toBe(TEXT_SCALE_RANGE.default);
    expect(parseTextScale('87')).toBe(85);
    expect(parseTextScale(400)).toBe(TEXT_SCALE_RANGE.max);
    expect(parseTextScale('nope')).toBe(TEXT_SCALE_RANGE.default);
  });
});

function AtScale({ percent, children }: { percent: number; children: React.ReactNode }) {
  const { setTextScale } = useTheme();
  useEffect(() => { setTextScale(percent); }, [percent, setTextScale]);
  return <>{children}</>;
}

it('Text renders at the chosen size, including caller-set sizes', async () => {
  const ui = render(
    <ThemeProvider statusBar={false}>
      <AtScale percent={90}>
        <Text testID="t">Body</Text>
        <Text testID="custom" style={{ fontSize: 20, lineHeight: 28 }}>Custom</Text>
      </AtScale>
    </ThemeProvider>,
  );
  await act(async () => undefined);
  const body = StyleSheet.flatten(ui.getByTestId('t').props.style);
  expect(body.fontSize).toBeCloseTo((typeStyle('body').fontSize as number) * 0.9, 0);
  expect(StyleSheet.flatten(ui.getByTestId('custom').props.style).fontSize).toBe(18);
});

it('a full sheet that does not scroll gives its content the whole height', async () => {
  const ui = render(
    <ThemeProvider statusBar={false}>
      <Sheet visible full scroll={false} onClose={() => undefined} title="Ask about the fleet">
        <View testID="content" style={{ flex: 1 }} />
      </Sheet>
    </ThemeProvider>,
  );
  await act(async () => undefined);
  const parent = ui.getByTestId('content').parent;
  // The wrapper the Sheet puts around its children must flex, or a flex:1 transcript is 0 tall.
  let node: any = parent;
  while (node && !(node.props && node.props.style)) node = node.parent;
  expect(StyleSheet.flatten(node.props.style)).toMatchObject({ flex: 1 });
});
