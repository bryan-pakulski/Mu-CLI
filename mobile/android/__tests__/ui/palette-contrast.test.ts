// Every theme the phone offers (Wash and each palette, light and dark) keeps text readable on
// every surface, semantic ink on its own wash, and controls visible (WCAG AA; the same floors as
// scripts/design/check_contrast.py, computed on the colours the app actually renders).
import { PALETTES, SEMANTICS, washColors, type PaletteName } from '../../src/theme/tokens';
import { parseColor } from '../../src/ui/Glass';

function luminance(css: string): number {
  const { r, g, b } = parseColor(css);
  const lin = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const THEMES = (['paper', 'dusk'] as const).flatMap(side => ([null, ...PALETTES] as Array<PaletteName | null>).map(p => ({ side, p })));

describe.each(THEMES)('$p / $side', ({ side, p }) => {
  const c = washColors(side, p);
  const surfaces = { canvas: c.canvas, surface: c.surface, sunken: c.sunken, raised: c.raised };

  it('text clears 4.5:1 on every surface', () => {
    const texts = { ink: c.ink, ink2: c.ink2, ink3: c.ink3, plum: c.plum, ...Object.fromEntries(SEMANTICS.map(s => [`${s}.ink`, c[s].ink])) };
    const failures: string[] = [];
    for (const [fg, fgv] of Object.entries(texts)) {
      for (const [bg, bgv] of Object.entries(surfaces)) {
        const r = ratio(fgv, bgv);
        if (r < 4.5) failures.push(`${fg} on ${bg}: ${r.toFixed(2)}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it('semantic ink clears 4.5:1 on its wash and on-ink on ink', () => {
    for (const s of SEMANTICS) expect(ratio(c[s].ink, c[s].wash)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(c.onInk, c.ink)).toBeGreaterThanOrEqual(4.5);
  });

  it('control borders and the focus ring clear 3:1', () => {
    expect(ratio(c.controlBorder, c.surface)).toBeGreaterThanOrEqual(3);
    expect(ratio(c.controlBorder, c.sunken)).toBeGreaterThanOrEqual(3);
    expect(ratio(c.accent.ink, c.surface)).toBeGreaterThanOrEqual(3);
  });
});
