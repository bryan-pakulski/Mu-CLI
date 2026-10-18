// Wash theme contract (spec §8.1): the palette equals the generated tokens,
// contrast floors hold, the theme preference persists, and the approval
// checkbox sends `remember` (B6).
import React from 'react';
import { Text } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import {
  duskColors,
  paperColors,
  PIGMENTS,
  SEMANTICS,
  toSemantic,
  washOf,
  type WashColors,
} from '../src/theme/tokens';
import { dusk, layer, mode, paper, series, vocabulary } from '../src/theme/wash.generated';
import {
  DECORATION_STORAGE_KEY,
  THEME_STORAGE_KEY,
  ThemeProvider,
  parseThemePreference,
  resolveThemeName,
  useTheme,
} from '../src/theme/ThemeContext';
import { STATUS, stateLabel, stateStatus, WAITING_STATES } from '../src/theme/status';
import { NEEDS_YOU_STATES } from '../src/api/jobNotifications';
import { ToolApprovalBody } from '../src/components/PromptHost';
import type { PendingPrompt } from '../src/api/prompts';

const getItem = AsyncStorage.getItem as jest.Mock;
const setItem = AsyncStorage.setItem as jest.Mock;

function lum(hex: string) {
  const n = parseInt(hex.slice(1), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe('palette', () => {
  it.each([['paper', paperColors, paper], ['dusk', duskColors, dusk]] as const)('%s equals the generated tokens', (_n, colors: WashColors, source) => {
    expect(colors.canvas).toBe(source.canvas);
    expect(colors.ink3).toBe(source['ink-3']);
    expect(colors.scrim).toBe(source.scrim);
    for (const s of SEMANTICS) expect(colors[s].ink).toBe(source[`${s}-ink`]);
    for (const p of PIGMENTS) expect(colors.pigment[p]).toEqual({ wash: source[`${p}-wash`], pigment: source[`${p}-pigment`], ink: source[`${p}-ink`] });
    for (const [name, pigment] of Object.entries(mode)) expect(colors.mode[name as keyof typeof mode].ink).toBe(source[`${pigment}-ink`]);
    for (const [name, pigment] of Object.entries(layer)) expect(colors.layer[name as keyof typeof layer].pigment).toBe(source[`${pigment}-pigment`]);
    expect(colors.series.map(t => t.pigment)).toEqual(series.map(p => source[`${p}-pigment`]));
  });

  it.each([['paper', paperColors], ['dusk', duskColors]] as const)('%s clears the contrast floors', (_n, colors: WashColors) => {
    for (const fg of [colors.ink, colors.ink2, colors.ink3, colors.plum, ...SEMANTICS.map(s => colors[s].ink)]) {
      for (const bg of [colors.canvas, colors.surface, colors.sunken, colors.raised]) expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
    }
    for (const s of SEMANTICS) expect(ratio(colors[s].ink, colors[s].wash)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(colors.onInk, colors.ink)).toBeGreaterThanOrEqual(4.5);
    for (const bg of [colors.surface, colors.sunken]) expect(ratio(colors.controlBorder, bg)).toBeGreaterThanOrEqual(3);
  });

  it('keeps attention, danger, plan and accent apart', () => {
    for (const c of [paperColors, duskColors]) {
      const inks = new Set([c.attention.ink, c.danger.ink, c.plan.ink, c.accent.ink]);
      expect(inks.size).toBe(4);
    }
  });

  it('maps legacy tones onto semantics and replaces tint() with wash tokens', () => {
    expect(toSemantic('warn')).toBe('attention');
    expect(toSemantic('muted')).toBe('neutral');
    expect(toSemantic('')).toBe('neutral');
    expect(washOf(paperColors, 'danger')).toBe(paper['danger-wash']);
  });
});

describe('native chrome', () => {
  it('paints the splash and adaptive icon with the μ mark ground (light and dark)', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const app = require('../app.json').expo;
    expect(paper['mark-ground']).toBe(dusk['mark-ground']);
    expect(app.splash.backgroundColor).toBe(paper['mark-ground']);
    expect(app.android.adaptiveIcon.backgroundColor).toBe(paper['mark-ground']);
    expect(app.android.splash.backgroundColor).toBe(paper['mark-ground']);
    expect(app.android.splash.dark.backgroundColor).toBe(dusk['mark-ground']);
  });
});

describe('vocabulary', () => {
  it('reads glyphs, labels and needs-you states from wash.generated.ts', () => {
    expect(STATUS.waiting.icon).toBe(vocabulary.status.waiting.glyph);
    expect(STATUS.waiting.tone).toBe('attention');
    expect(stateLabel('provisioning')).toBe('preparing');
    expect(stateStatus('plan_review')).toBe('waiting');
    const waiting = Object.entries(vocabulary.job_state).filter(([, row]) => row.status === 'waiting').map(([k]) => k);
    expect([...NEEDS_YOU_STATES].sort()).toEqual(waiting.sort());
    expect(WAITING_STATES.sort()).toEqual(waiting.sort());
  });
});

describe('theme preference', () => {
  beforeEach(() => { getItem.mockReset(); setItem.mockReset(); setItem.mockResolvedValue(undefined); });

  it('migrates legacy names and follows the OS for system', () => {
    expect(parseThemePreference('light')).toBe('paper');
    expect(parseThemePreference('dark')).toBe('dusk');
    expect(parseThemePreference(null)).toBe('system');
    expect(resolveThemeName('system', 'dark')).toBe('dusk');
    expect(resolveThemeName('system', 'light')).toBe('paper');
    expect(resolveThemeName('paper', 'dark')).toBe('paper');
  });

  function Probe() {
    const theme = useTheme();
    return (
      <>
        <Text testID="name">{theme.name}</Text>
        <Text testID="pref">{theme.preference}</Text>
        <Text testID="deco">{String(theme.reduceDecoration)}</Text>
        <Text testID="set-dusk" onPress={() => theme.setPreference('dusk')}>dusk</Text>
        <Text testID="deco-on" onPress={() => theme.setReduceDecoration(true)}>deco</Text>
      </>
    );
  }

  it('restores the stored preference and persists changes', async () => {
    getItem.mockImplementation((key: string) => Promise.resolve(key === THEME_STORAGE_KEY ? 'dark' : null));
    const ui = render(<ThemeProvider statusBar={false}><Probe /></ThemeProvider>);
    await waitFor(() => expect(ui.getByTestId('pref').props.children).toBe('dusk'));
    expect(ui.getByTestId('name').props.children).toBe('dusk');
    await act(async () => { fireEvent.press(ui.getByTestId('set-dusk')); });
    expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, 'dusk');
    await act(async () => { fireEvent.press(ui.getByTestId('deco-on')); });
    expect(ui.getByTestId('deco').props.children).toBe('true');
    expect(setItem).toHaveBeenCalledWith(DECORATION_STORAGE_KEY, '1');
  });
});

describe('approval remember (B6)', () => {
  it('sends remember: true only when "Always allow" is checked', () => {
    const submit = jest.fn();
    const prompt = { id: 'p1', shape: 'tool_approval', tool_name: 'write_file', tool_args: { path: 'a.py' } } as unknown as PendingPrompt;
    const ui = render(<ThemeProvider statusBar={false}><ToolApprovalBody prompt={prompt} submitting={false} submit={submit} cancel={jest.fn()} queueDepth={1} /></ThemeProvider>);
    fireEvent.press(ui.getByTestId('tool-approval-remember'));
    fireEvent.press(ui.getByTestId('tool-approval-allow'));
    expect(submit).toHaveBeenCalledWith({ approved: true, remember: true });
  });
});
