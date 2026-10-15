// Glass restructure (G§3.3, G§3.4, G§6.2; spec §13): glass levels respect the
// opacity floor, no file sets a coloured side accent, and the approval card
// shows its state with a tinted header and chip instead of a side rule.
import fs from 'fs';
import path from 'path';
import React from 'react';
import { StyleSheet } from 'react-native';
import { act, render } from '@testing-library/react-native';
import { ThemeProvider } from '../src/theme/ThemeContext';
import { duskColors, paperColors } from '../src/theme/tokens';
import { glass as glassTokens, glassEffect } from '../src/theme/wash.generated';
import { ApprovalCard, Card, markdownStyles } from '../src/ui';
import {
  AmbientBackground,
  GLASS_MIN_ALPHA,
  TEXT_BEARING_LEVELS,
  glassAlpha,
  glassStyle,
  parseColor,
  routeAmbient,
  stateTint,
} from '../src/ui/Glass';

const SRC = path.join(__dirname, '..', 'src');

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.(tsx?|jsx?)$/.test(entry.name) ? [full] : [];
  });
}

describe('no coloured side accents (G§3.4)', () => {
  const files = sourceFiles(SRC);
  // Width > 1 on a side edge is a stripe, whatever its colour.
  const WIDTH = /border(Left|Start|Right|End)Width:\s*([^,}\n]+)/g;
  // Side edge colours may only be neutral structure lines.
  const COLOR = /border(Left|Start|Right|End)Color:\s*([^,}\n]+)/g;
  const NEUTRAL = /^(colors\.hairline|colors\.hairlineStrong|'transparent'|"transparent")$/;

  it('scans the whole source tree', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it('never sets a side border wider than 1px', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf8');
      for (const m of text.matchAll(WIDTH)) {
        const value = m[2].trim();
        // Allowed: 0, 1, StyleSheet.hairlineWidth, or a conditional choosing only among them.
        const branches = value.includes('?') ? value.slice(value.indexOf('?') + 1).split(':').map(b => b.trim()) : [value];
        const ok = branches.every(b => b === '0' || b === '1' || b === 'StyleSheet.hairlineWidth');
        if (!ok) offenders.push(`${path.relative(SRC, file)}: ${m[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('only colours side edges with hairline or transparent (data marks excepted)', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const lines = fs.readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        for (const m of line.matchAll(COLOR)) {
          if (NEUTRAL.test(m[2].trim())) continue;
          // A 1px neutral data mark inside a meter (G§9.14), explicitly tagged.
          if (line.includes('wash-data-mark') && m[2].trim() === 'colors.ink') continue;
          offenders.push(`${path.relative(SRC, file)}:${i + 1}: ${m[0]}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  it('overrides the Markdown renderer default grey side rule on blockquotes (G§9.7)', () => {
    for (const colors of [paperColors, duskColors]) {
      for (const solid of [false, true]) {
        const quote = StyleSheet.flatten(markdownStyles(colors, 'chat', { solid }).blockquote);
        expect(quote.borderLeftWidth).toBe(0);
        expect(quote.backgroundColor).toBe(solid ? colors.sunken : glassTokens[colors.name].subtle);
      }
    }
  });
});

describe('glassStyle (G§3.3)', () => {
  const alphaOf = (style: { backgroundColor?: unknown }) => parseColor(String(style.backgroundColor)).a;

  it('keeps every text-bearing level at or above the opacity floor in both themes', () => {
    expect(GLASS_MIN_ALPHA).toBe(glassEffect.min_alpha);
    for (const colors of [paperColors, duskColors]) {
      for (const level of TEXT_BEARING_LEVELS) {
        expect(alphaOf(glassStyle(level, colors))).toBeGreaterThanOrEqual(glassEffect.min_alpha);
        expect(alphaOf(glassStyle(level, colors, { flat: true }))).toBeGreaterThanOrEqual(glassEffect.min_alpha);
        expect(glassAlpha(level, colors.name)).toBeGreaterThanOrEqual(glassEffect.min_alpha);
      }
    }
  });

  it('uses the generated glass tokens: surface at level 1, strong at 2 and 3, subtle for insets', () => {
    for (const colors of [paperColors, duskColors]) {
      const g = glassTokens[colors.name];
      expect(glassStyle(1, colors).backgroundColor).toBe(g.surface);
      expect(glassStyle(2, colors).backgroundColor).toBe(g.strong);
      expect(glassStyle(3, colors).backgroundColor).toBe(g.strong);
      expect(glassStyle('subtle', colors).backgroundColor).toBe(g.subtle);
      expect(glassStyle(1, colors).borderColor).toBe(g.edge);
      expect(glassStyle(1, colors).borderTopColor).toBe(g.border);
    }
  });

  it('only uses the subtle inset nested on glass, where the compounded opacity clears the floor', () => {
    for (const colors of [paperColors, duskColors]) {
      const parent = glassAlpha(1, colors.name);
      const inset = glassAlpha('subtle', colors.name);
      expect(1 - (1 - parent) * (1 - inset)).toBeGreaterThanOrEqual(glassEffect.min_alpha);
    }
  });

  it('falls back to solid surfaces with Reduce decoration', () => {
    for (const colors of [paperColors, duskColors]) {
      expect(glassStyle(1, colors, { solid: true }).backgroundColor).toBe(colors.surface);
      expect(glassStyle(2, colors, { solid: true }).backgroundColor).toBe(colors.raised);
      expect(glassStyle(3, colors, { solid: true }).backgroundColor).toBe(colors.raised);
      expect(glassStyle('subtle', colors, { solid: true }).backgroundColor).toBe(colors.sunken);
      expect(stateTint(colors, 'attention', true)).toBe(colors.attention.wash);
    }
  });

  it('never outlines glass in a pigment', () => {
    const pigments = new Set(
      [paperColors, duskColors].flatMap(c => [c.attention, c.danger, c.ok, c.plan, c.accent, c.running].flatMap(t => [t.pigment, t.ink])),
    );
    for (const colors of [paperColors, duskColors]) {
      for (const level of [1, 2, 3] as const) {
        const s = glassStyle(level, colors);
        expect(pigments.has(String(s.borderColor))).toBe(false);
        expect(pigments.has(String(s.borderTopColor))).toBe(false);
      }
    }
  });

  it('tints state strips with the semantic wash mixed into opaque-enough glass', () => {
    for (const colors of [paperColors, duskColors]) {
      const tint = parseColor(stateTint(colors, 'attention'));
      expect(tint.a).toBeGreaterThanOrEqual(glassEffect.min_alpha);
      expect(stateTint(colors, 'attention')).not.toBe(stateTint(colors, 'danger'));
    }
  });
});

describe('ApprovalCard on glass (G§9.9)', () => {
  const renderCard = () => render(
    <ThemeProvider statusBar={false}>
      <ApprovalCard toolName="write_file" description="Edit 2 files" target="src/app.py" remember={false}
        onRememberChange={() => undefined} onApprove={() => undefined} onDeny={() => undefined} />
    </ThemeProvider>,
  );

  it('shows its state with a tinted header strip and the Needs you chip, with no side border', async () => {
    const ui = renderCard();
    const card = StyleSheet.flatten(ui.getByTestId('approval-card').props.style);
    const header = StyleSheet.flatten(ui.getByTestId('approval-card-header').props.style);
    const chip = ui.getByTestId('approval-card-chip');
    expect(header.backgroundColor).toBe(stateTint(paperColors, 'attention'));
    expect(ui.getByText('Needs you')).toBeTruthy();
    expect(chip).toBeTruthy();
    for (const key of ['borderLeftWidth', 'borderStartWidth', 'borderRightWidth', 'borderEndWidth', 'borderLeftColor', 'borderStartColor'] as const) {
      expect(card[key]).toBeUndefined();
      expect(header[key]).toBeUndefined();
    }
    expect(card.backgroundColor).toBe(glassTokens.paper.surface);
    await act(async () => undefined);
  });

  it('escalates the header tint to danger for high-risk tools', async () => {
    const ui = render(
      <ThemeProvider statusBar={false}>
        <ApprovalCard toolName="shell" description="Run a networked command" highRisk remember={false}
          onRememberChange={() => undefined} onApprove={() => undefined} onDeny={() => undefined} />
      </ThemeProvider>,
    );
    const header = StyleSheet.flatten(ui.getByTestId('approval-card-header').props.style);
    expect(header.backgroundColor).toBe(stateTint(paperColors, 'danger'));
    await act(async () => undefined);
  });

  it('state cards share the tinted header and have no side rule', async () => {
    const ui = render(
      <ThemeProvider statusBar={false}>
        <Card variant="state" tone="danger" title="Build failed" glyph="✗" testID="state">body</Card>
      </ThemeProvider>,
    );
    const frame = StyleSheet.flatten(ui.getByTestId('state').props.style);
    const header = StyleSheet.flatten(ui.getByTestId('state-header').props.style);
    expect(frame.borderLeftWidth).toBeUndefined();
    expect(frame.borderLeftColor).toBeUndefined();
    expect(header.backgroundColor).toBe(stateTint(paperColors, 'danger'));
    await act(async () => undefined);
  });
});

describe('ambient wash (G§6.2)', () => {
  it('maps screens to their wash', () => {
    expect(routeAmbient('Chat')).toBe('dawn');
    expect(routeAmbient('Work')).toBe('lagoon');
    expect(routeAmbient('JobDetail')).toBe('lagoon');
    expect(routeAmbient('Traces')).toBe('lagoon');
    expect(routeAmbient('Containers')).toBe('meadow');
    expect(routeAmbient('Settings')).toBe('dawn');
    expect(routeAmbient(undefined)).toBe('dawn');
  });

  it('renders one fixed ambient image behind the app', async () => {
    const ui = render(
      <ThemeProvider statusBar={false}>
        <AmbientBackground><></></AmbientBackground>
      </ThemeProvider>,
    );
    expect(ui.getAllByTestId('ambient-wash', { includeHiddenElements: true })).toHaveLength(1);
    await act(async () => undefined);
  });
});
