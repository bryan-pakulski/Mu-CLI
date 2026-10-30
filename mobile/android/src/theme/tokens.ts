// Wash design tokens for the Android client (wash_design_guide.md §1-§5,
// wash_implementation_spec.md §5.1).
//
// This file is a thin adapter over `wash.generated.ts`, which is generated
// from `mu/design/tokens.json` by `scripts/design/build_tokens.py` and is the
// only colour source. Nothing here declares a colour literal: every value is
// read from the generated Paper or Dusk palette.
//
// Components consume the semantic shape (`colors.canvas`, `colors.ink3`,
// `colors.attention.wash`, ...) through `useTheme()`. Pigments are only used
// directly for categorical graphics (modes, layers, series).

import { Easing, Platform, type TextStyle, type ViewStyle } from 'react-native';
import {
  destination as destinationMap,
  dusk,
  layer as layerPigments,
  palettes as palettePalettes,
  mode as modePigments,
  motion as washMotion,
  paper,
  radius as washRadius,
  scenery as sceneryMap,
  semantic as semanticPigments,
  series as seriesPigments,
  shadowTint,
  size as washSize,
  space as washSpace,
  syntax as syntaxSlots,
  type as washType,
  type WashPalette,
  type WashThemeName,
} from './wash.generated';

/* ------------------------------------------------------------ colour shape */

/** One pigment or semantic in its three steps (G§1.2). `pigment` is never text. */
export type Tone3 = { wash: string; pigment: string; ink: string };

export type Semantic = 'accent' | 'running' | 'ok' | 'attention' | 'caution' | 'danger' | 'plan' | 'info' | 'neutral';
export type PigmentName = 'lagoon' | 'meadow' | 'sun' | 'apricot' | 'rose' | 'lilac' | 'sky' | 'plum';
export type ModeName = keyof typeof modePigments;
/** The shell's destinations (tokens.json destination): Chat, Work, Orchestration, Environments, Insights, Settings. */
export type DestinationName = keyof typeof destinationMap;
export const DESTINATIONS = Object.keys(destinationMap) as DestinationName[];
/** Alternative palettes (tokens.json palettes); null = the Wash theme. */
export type PaletteName = keyof typeof palettePalettes;
export const PALETTES = Object.keys(palettePalettes) as PaletteName[];
export function paletteLabel(name: PaletteName | null): string {
  return name ? palettePalettes[name].label : 'Wash';
}
export function paletteNote(name: PaletteName | null): string {
  return name ? palettePalettes[name].note : 'Watercolour paper, the default.';
}
/** Every watercolour scene (tokens.json glass.ambient): the classic three and the scenery packs. */
export type SceneName = 'dawn' | 'lagoon' | 'meadow' | 'desert' | 'forest' | 'ocean' | 'space';
/** A destination's watercolour scene (assets/wash/ambient-<scene>-<theme>). */
export function destinationAmbient(name: DestinationName): SceneName {
  return destinationMap[name].ambient as SceneName;
}
/** Watercolour scenery packs (tokens.json scenery): null = Classic (each destination keeps its own). */
export type SceneryName = Exclude<keyof typeof sceneryMap, 'classic' | '_doc'>;
export const SCENERY = (Object.keys(sceneryMap) as string[])
  .filter(name => !name.startsWith('_') && Boolean((sceneryMap as Record<string, { accent?: string }>)[name].accent)) as SceneryName[];
type ScenerySpec = { label: string; note: string; accent?: string };
export function sceneryLabel(name: SceneryName | null): string {
  return (sceneryMap as Record<string, ScenerySpec>)[name ?? 'classic'].label;
}
export function sceneryNote(name: SceneryName | null): string {
  return (sceneryMap as Record<string, ScenerySpec>)[name ?? 'classic'].note;
}
/** The pigment a scenery pack recolours every destination's accent to. */
export function sceneryAccent(name: SceneryName): PigmentName {
  return (sceneryMap as Record<string, ScenerySpec>)[name].accent as PigmentName;
}
export type LayerName = keyof typeof layerPigments;

export const SEMANTICS: readonly Semantic[] = ['accent', 'running', 'ok', 'attention', 'caution', 'danger', 'plan', 'info', 'neutral'];
export const PIGMENTS: readonly PigmentName[] = ['lagoon', 'meadow', 'sun', 'apricot', 'rose', 'lilac', 'sky', 'plum'];

export type SyntaxColors = {
  keyword: string; string: string; comment: string; number: string; func: string; type: string;
  operator: string; punctuation: string; plain: string; added: string; removed: string; diffHeader: string;
};

export type WashColors = {
  name: WashThemeName;
  canvas: string;
  surface: string;
  sunken: string;
  raised: string;
  hairline: string;
  hairlineStrong: string;
  controlBorder: string;
  ink: string;
  ink2: string;
  ink3: string;
  plum: string;
  onInk: string;
  scrim: string;
  code: string;
  terminal: string;
  /** Shadow colour (warm plum on Paper, near-black on Dusk); always used with a low opacity. */
  shadow: string;
  accent: Tone3;
  running: Tone3;
  ok: Tone3;
  attention: Tone3;
  caution: Tone3;
  danger: Tone3;
  plan: Tone3;
  info: Tone3;
  neutral: Tone3;
  pigment: Record<PigmentName, Tone3>;
  /** Each destination's accent (tokens.json destination). */
  destination: Record<DestinationName, Tone3>;
  mode: Record<ModeName, Tone3>;
  layer: Record<LayerName, Tone3>;
  series: readonly Tone3[];
  syntax: SyntaxColors;
};

/** Backwards-compatible name used by existing call sites. */
export type ThemeColors = WashColors;

function tone3(p: WashPalette, name: string): Tone3 {
  const key = (step: string) => `${name}-${step}` as keyof WashPalette;
  return { wash: p[key('wash')], pigment: p[key('pigment')], ink: p[key('ink')] };
}

function mapRecord<K extends string>(source: Record<K, string>, p: WashPalette): Record<K, Tone3> {
  const out = {} as Record<K, Tone3>;
  (Object.keys(source) as K[]).forEach(k => { out[k] = tone3(p, source[k]); });
  return out;
}

function syntaxFrom(p: WashPalette): SyntaxColors {
  const slot = (name: string) => (name.startsWith('ink') ? p[name as keyof WashPalette] : p[`${name}-ink` as keyof WashPalette]);
  return {
    keyword: slot(syntaxSlots.keyword),
    string: slot(syntaxSlots.string),
    number: slot(syntaxSlots.number),
    func: slot(syntaxSlots.function),
    type: slot(syntaxSlots.type),
    comment: slot(syntaxSlots.comment),
    punctuation: slot(syntaxSlots.punctuation),
    operator: slot(syntaxSlots.punctuation),
    plain: p.ink,
    added: p['ok-ink'],
    removed: p['danger-ink'],
    diffHeader: p['running-ink'],
  };
}

/** Build the semantic colour shape from one generated palette (an alternative palette when given). */
export function washColors(name: WashThemeName, palette: PaletteName | null = null, scenery: SceneryName | null = null): WashColors {
  const base = name === 'dusk' ? dusk : paper;
  const p: WashPalette = palette && palettePalettes[palette] ? { ...base, ...(palettePalettes[palette][name] as WashPalette) } : base;
  const pigment = {} as Record<PigmentName, Tone3>;
  PIGMENTS.forEach(n => { pigment[n] = tone3(p, n); });
  return {
    name,
    canvas: p.canvas,
    surface: p.surface,
    sunken: p.sunken,
    raised: p.raised,
    hairline: p.hairline,
    hairlineStrong: p['hairline-strong'],
    controlBorder: p['control-border'],
    ink: p.ink,
    ink2: p['ink-2'],
    ink3: p['ink-3'],
    plum: p.plum,
    onInk: p['on-ink'],
    scrim: p.scrim,
    code: p.code,
    terminal: p.terminal,
    shadow: `rgb(${shadowTint[name]})`,
    accent: tone3(p, 'accent'),
    running: tone3(p, 'running'),
    ok: tone3(p, 'ok'),
    attention: tone3(p, 'attention'),
    caution: tone3(p, 'caution'),
    danger: tone3(p, 'danger'),
    plan: tone3(p, 'plan'),
    info: tone3(p, 'info'),
    neutral: tone3(p, 'neutral'),
    pigment,
    // A scenery pack (Wash only; flat palettes ignore it) recolours every destination to its pigment.
    destination: Object.fromEntries(DESTINATIONS.map(d => [d, tone3(p, scenery && !palette ? sceneryAccent(scenery) : destinationMap[d].accent)])) as Record<DestinationName, Tone3>,
    mode: mapRecord(modePigments as Record<ModeName, string>, p),
    layer: mapRecord(layerPigments as Record<LayerName, string>, p),
    series: seriesPigments.map(n => tone3(p, n)),
    syntax: syntaxFrom(p),
  };
}

export const paperColors: WashColors = washColors('paper');
export const duskColors: WashColors = washColors('dusk');

/** Sanity: the semantic → pigment mapping the generator emitted. */
export const semanticPigment = semanticPigments;

/* ------------------------------------------------------------ tones */

/**
 * Tone names accepted by kit components. Wash semantics, plus the two legacy
 * Wash names that older call sites and server payloads still send
 * (`warn` → attention, `muted` → neutral).
 */
export type Tone = Semantic | 'warn' | 'muted' | '';

export function toSemantic(tone: Tone | string | null | undefined): Semantic {
  switch (tone) {
    case 'warn': return 'attention';
    case 'muted': case '': case null: case undefined: return 'neutral';
    default: return (SEMANTICS as readonly string[]).includes(String(tone)) ? (tone as Semantic) : 'neutral';
  }
}

/** The three steps of a semantic tone. */
export function toneOf(colors: WashColors, tone: Tone | string | null | undefined): Tone3 {
  return colors[toSemantic(tone)];
}

/** Text-safe colour of a tone (`neutral` resolves to ink-3). */
export function toneInk(colors: WashColors, tone: Tone | string | null | undefined): string {
  return toneOf(colors, tone).ink;
}

/** Replacement for the old `tint(color, alpha)`: the semantic's wash fill. */
export function washOf(colors: WashColors, tone: Tone | string | null | undefined): string {
  return toneOf(colors, tone).wash;
}

/* ------------------------------------------------------------ type */

/**
 * Base families registered by `useFonts` in App.tsx (assets/fonts). Each
 * name is the regular (400) cut, so `fontFamily: fonts.mono` alone is always
 * a real face.
 */
export const fonts = {
  ui: 'Inter',
  mono: 'JetBrainsMono',
} as const;

export type FontBase = (typeof fonts)[keyof typeof fonts];

/**
 * Static weight families, one TTF per weight, instanced from the variable
 * fonts with fontTools (Inter at opsz 14). Android does not pick weights
 * inside a custom family loaded by expo-font (it fakes bold from the regular
 * face), so each weight is registered as its own family and selected by name.
 */
export const fontFamilies = {
  Inter: { 400: 'Inter', 500: 'Inter-Medium', 600: 'Inter-SemiBold' },
  JetBrainsMono: { 400: 'JetBrainsMono', 600: 'JetBrainsMono-SemiBold' },
} as const;

const BASE_OF: Record<string, FontBase> = Object.fromEntries(
  (Object.keys(fontFamilies) as FontBase[]).flatMap(base => Object.values(fontFamilies[base]).map(family => [family, base])),
);

function weightNumber(weight: TextStyle['fontWeight'] | number | undefined): number {
  if (weight == null || weight === 'normal') return 400;
  if (weight === 'bold') return 700;
  const n = Number(weight);
  return Number.isFinite(n) ? n : 400;
}

/** The static family for a base family at a weight (nearest bundled cut). */
export function fontFamilyFor(base: FontBase, weight?: TextStyle['fontWeight'] | number): string {
  const n = weightNumber(weight);
  if (base === fonts.mono) return n >= 500 ? fontFamilies.JetBrainsMono[600] : fontFamilies.JetBrainsMono[400];
  if (n >= 550) return fontFamilies.Inter[600];
  if (n >= 450) return fontFamilies.Inter[500];
  return fontFamilies.Inter[400];
}

/**
 * Family and weight for text. On Android the weight is carried by the family
 * name alone (no `fontWeight`, which would add synthetic bold); elsewhere
 * the numeric weight is kept alongside the matching static family.
 */
export function fontStyle(base: FontBase, weight?: TextStyle['fontWeight'] | number): TextStyle {
  const fontFamily = fontFamilyFor(base, weight);
  if (Platform.OS === 'android') return { fontFamily };
  return { fontFamily, fontWeight: String(weightNumber(weight)) as TextStyle['fontWeight'] };
}

/**
 * Resolve a flattened text style that pairs a Wash family with a
 * `fontWeight` override (`style={{ fontWeight: fontWeights.semibold }}`) to
 * the static family for that weight on Android. Other styles pass through.
 */
export function withStaticWeight<T extends TextStyle>(style: T): T {
  if (Platform.OS !== 'android' || style.fontWeight == null) return style;
  const base = BASE_OF[style.fontFamily ?? ''];
  if (!base) return style;
  const { fontWeight, ...rest } = style;
  return { ...rest, fontFamily: fontFamilyFor(base, fontWeight) } as T;
}

export type TypeName = keyof typeof washType;

/** A complete text style for one Wash type token (G§2.2). */
export function typeStyle(name: TypeName): TextStyle {
  const t = washType[name];
  return {
    ...fontStyle(name === 'mono' || name === 'mono-sm' ? fonts.mono : fonts.ui, t.weight),
    fontSize: t.size,
    lineHeight: t.line,
    letterSpacing: Number((t.tracking * t.size).toFixed(2)),
  };
}

/** Font sizes — the Wash scale under the legacy key names. */
export const fontSizes = {
  xs: washType.caption.size,
  sm: washType.body.size,
  base: washType.body.size,
  md: washType['body-lg'].size,
  lg: washType['title-3'].size,
  xl: washType['title-2'].size,
  '2xl': washType['title-1'].size,
} as const;

export const fontWeights = { regular: '400', medium: '500', semibold: '600', bold: '700' } as const;

export const lineHeights = { tight: 1.25, snug: 1.4, body: 1.5 } as const;

/** Display/title tracking (G§2.2), in dp at title-3 size. */
export const tracking = { tight: Number((washType['title-3'].tracking * washType['title-3'].size).toFixed(2)) } as const;

/** Legacy text variants mapped onto Wash type tokens. */
export const typography = {
  xs: { fontSize: washType.caption.size, lineHeight: washType.caption.line },
  sm: { fontSize: washType.body.size, lineHeight: washType.body.line },
  base: { fontSize: washType.body.size, lineHeight: washType.body.line },
  md: { fontSize: washType['body-lg'].size, lineHeight: washType['body-lg'].line },
  lg: { fontSize: washType['title-3'].size, lineHeight: washType['title-3'].line },
  xl: { fontSize: washType['title-2'].size, lineHeight: washType['title-2'].line },
  '2xl': { fontSize: washType['title-1'].size, lineHeight: washType['title-1'].line },
} as const;

/* ------------------------------------------------------------ space, shape, motion */

/** Spacing — the Wash 4px scale under named keys (space-1..10 = 4..96). */
export const spacing = {
  xs: washSpace[0],
  sm: washSpace[1],
  md: washSpace[2],
  base: washSpace[3],
  '20': washSpace[4],
  lg: washSpace[5],
  xl: washSpace[6],
  '2xl': washSpace[7],
  '3xl': washSpace[8],
  '4xl': washSpace[9],
} as const;

/** Wash radius (G§3.2) plus the two role names older call sites use. */
export const radii = {
  xs: washRadius.xs,
  sm: washRadius.sm,
  md: washRadius.md,
  lg: washRadius.lg,
  xl: washRadius.xl,
  pill: washRadius.pill,
  control: washRadius.sm,
  panel: washRadius.lg,
} as const;

/** Motion durations (ms). */
export const motion = {
  micro: washMotion.micro.duration,
  small: washMotion.small.duration,
  medium: washMotion.medium.duration,
  large: washMotion.medium.duration,
  bleed: washMotion.bleed.duration,
} as const;

function bezier(css: string) {
  const [a, b, c, d] = (css.match(/[\d.]+/g) || ['0', '0', '1', '1']).map(Number);
  return Easing.bezier(a, b, c, d);
}

export const easings = {
  micro: bezier(washMotion.micro.easing),
  out: bezier(washMotion.small.easing),
  bleed: bezier(washMotion.bleed.easing),
  in: Easing.bezier(0.4, 0, 1, 1),
  standard: bezier(washMotion.micro.easing),
} as const;

/** Icon sizes (G§4): 16 inline, 20 buttons, 24 navigation. */
export const iconSize = { sm: washSize['icon-sm'], md: washSize.icon, lg: washSize['icon-lg'] } as const;

/** Minimum touch target (dp). */
export const touchTarget = washSize.touch;

/** Control heights (G§9.1). */
export const controlSize = { sm: washSize['control-sm'], md: washSize.control, lg: washSize['control-lg'], field: washSize.field } as const;

/* ------------------------------------------------------------ elevation */

/**
 * Elevation (G§3.3): paper resting on paper. Level 1 = surface + hairline;
 * level 2 = raised + hairline + faint warm shadow (menus, toasts); level 3 =
 * raised + deeper shadow (sheets, modals). Dusk adds a hairline-strong top
 * edge at level 2+ because shadows barely show on dark surfaces.
 */
export function elevation(level: 1 | 2 | 3, colors: WashColors, isDark = colors.name === 'dusk'): ViewStyle {
  if (level === 1) return { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline };
  const base: ViewStyle = {
    backgroundColor: colors.raised,
    borderWidth: 1,
    borderColor: colors.hairline,
    shadowColor: colors.shadow,
    shadowOpacity: level === 2 ? 0.08 : 0.16,
    shadowRadius: level === 2 ? 12 : 32,
    shadowOffset: { width: 0, height: level === 2 ? 8 : 24 },
    elevation: level === 2 ? 4 : 12,
  };
  return isDark ? { ...base, borderTopColor: colors.hairlineStrong } : base;
}

/* ------------------------------------------------------------ theme */

export type Theme = {
  colors: WashColors;
  spacing: typeof spacing;
  typography: typeof typography;
  fontSizes: typeof fontSizes;
  fontWeights: typeof fontWeights;
  radii: typeof radii;
  motion: typeof motion;
};

export const paperTheme: Theme = { colors: paperColors, spacing, typography, fontSizes, fontWeights, radii, motion };
export const duskTheme: Theme = { colors: duskColors, spacing, typography, fontSizes, fontWeights, radii, motion };

/** A theme for any light/dark side and palette. */
export function themeFor(name: WashThemeName, palette: PaletteName | null = null, scenery: SceneryName | null = null): Theme {
  if (!palette && !scenery) return name === 'dusk' ? duskTheme : paperTheme;
  return { colors: washColors(name, palette, palette ? null : scenery), spacing, typography, fontSizes, fontWeights, radii, motion };
}

/** Platform default UI family when the bundled fonts have not loaded. */
export const fallbackUiFont = Platform.select({ android: 'sans-serif', default: undefined }) as string | undefined;
