// Glass surfaces and the ambient wash (G§3.3, G§6.2, spec §13).
//
// Every screen sits on a fixed, full-bleed ambient watercolour. Surfaces are
// translucent glass over it; elevation comes from opacity, not from heavy
// shadows. One helper, `glassStyle(level)`, gives every kit surface its fill
// and edges:
//
// | level    | fill                 | edges                                   | use                              |
// |----------|----------------------|-----------------------------------------|----------------------------------|
// | 1        | `glass.surface` 62%  | 1px `glass.edge`, `glass.border` top    | cards, panels, stage, composer   |
// | 'subtle' | `glass.subtle`       | none (an inset on glass)                | inputs, tool rows, chips on glass|
// | 2        | `glass.strong` 82%+  | as level 1 + elevation-2 shadow         | menus, toasts, active nav pill   |
// | 3        | `glass.strong`       | as level 1 + elevation-3 shadow         | sheets, dialogs, approval sheet  |
//
// Rules this module enforces:
// - Text-bearing glass (1, 2, 3) is never below `glassEffect.min_alpha`.
//   `subtle` is only an inset on another glass surface, so its effective
//   opacity is compounded with its parent (always above the floor).
// - Edges are light, never pigment (G§3.4).
// - Fallback: with "Reduce decoration" on (or no blur), glass becomes the
//   solid `surface` / `raised` colour and the ambient wash becomes `canvas`.
// - Only chrome (header, tab bar, sheets, dialogs, toast) uses a native
//   BlurView (`GlassChrome`). Lists and cards use translucent fills only, so
//   scrolling never re-blurs per row.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Image, Platform, StyleSheet, View, type ImageSourcePropType, type StyleProp, type ViewStyle } from 'react-native';
import { BlurView } from 'expo-blur';
import { useTheme, type ThemeName } from '../theme/ThemeContext';
import { elevation, toneOf, type Tone, type WashColors } from '../theme/tokens';
import { glass as glassTokens, glassEffect } from '../theme/wash.generated';

/* ------------------------------------------------------------ colour maths */

export type Rgba = { r: number; g: number; b: number; a: number };

/** Parse `rgba(r, g, b, a)`, `rgb(r, g, b)` or `#rrggbb`. */
export function parseColor(css: string): Rgba {
  const hex = /^#([0-9a-f]{6})$/i.exec(css.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  const m = /rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)/i.exec(css);
  if (!m) throw new Error(`Unparseable colour: ${css}`);
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]), a: m[4] == null ? 1 : Number(m[4]) };
}

export function toCss({ r, g, b, a }: Rgba): string {
  const round = (v: number) => Math.round(v);
  return `rgba(${round(r)}, ${round(g)}, ${round(b)}, ${Number(a.toFixed(3))})`;
}

/** Mix `amount` of `top` into `base` (RGB), keeping the given alpha. */
export function mixColor(top: string, base: string, amount: number, alpha: number): string {
  const t = parseColor(top);
  const b = parseColor(base);
  return toCss({ r: t.r * amount + b.r * (1 - amount), g: t.g * amount + b.g * (1 - amount), b: t.b * amount + b.b * (1 - amount), a: alpha });
}

/** The first `rgba(...)` of a CSS shadow, plus its offset and blur. */
function parseShadow(css: string): { color: string; opacity: number; radius: number; offsetY: number } {
  // Use the larger (last) layer: it carries the soft, ambient depth.
  const layers = css.split(/,(?![^(]*\))/).map(s => s.trim());
  const last = layers[layers.length - 1];
  const nums = last.replace(/rgba?\([^)]*\)/, '').trim().split(/\s+/).map(v => parseFloat(v));
  const c = parseColor((/rgba?\([^)]*\)/.exec(last) || ['rgba(0, 0, 0, 0.1)'])[0]);
  return { color: toCss({ ...c, a: 1 }), opacity: c.a, offsetY: nums[1] || 0, radius: (nums[2] || 0) / 2 };
}

export type GlassTokens = {
  surface: string;
  strong: string;
  subtle: string;
  border: string;
  edge: string;
  shadow: { color: string; opacity: number; radius: number; offsetY: number };
};

const CACHE: Partial<Record<ThemeName, GlassTokens>> = {};

/** Glass tokens for a theme (parsed once). */
export function glassTokensFor(theme: ThemeName | WashColors): GlassTokens {
  const name: ThemeName = typeof theme === 'string' ? theme : theme.name;
  const hit = CACHE[name];
  if (hit) return hit;
  const g = glassTokens[name];
  const tokens: GlassTokens = { surface: g.surface, strong: g.strong, subtle: g.subtle, border: g.border, edge: g.edge, shadow: parseShadow(g.shadow) };
  CACHE[name] = tokens;
  return tokens;
}

export type GlassLevel = 1 | 'subtle' | 2 | 3;

/** Fill alpha of a glass level, from the generated tokens. */
export function glassAlpha(level: GlassLevel, theme: ThemeName): number {
  const g = glassTokensFor(theme);
  const fill = level === 'subtle' ? g.subtle : level === 1 ? g.surface : g.strong;
  return parseColor(fill).a;
}

/** Levels that carry text directly over the ambient wash. */
export const TEXT_BEARING_LEVELS: readonly GlassLevel[] = [1, 2, 3];

/** The opacity floor for text-bearing glass (G§3.3). */
export const GLASS_MIN_ALPHA = glassEffect.min_alpha;

/* ------------------------------------------------------------ glassStyle */

export type GlassOptions = {
  /** Solid fallback ("Reduce decoration", no blur). */
  solid?: boolean;
  /** No edges (a full-bleed stage or a strip inside a card). */
  flat?: boolean;
};

/**
 * Fill, edges and depth for one glass level. Never sets a coloured edge. On
 * Android the elevation shadow is omitted for translucent fills (Android
 * draws the shadow through a translucent surface as a dark smudge); depth
 * comes from opacity and the light edge instead.
 */
export function glassStyle(level: GlassLevel, colors: WashColors, options: GlassOptions = {}): ViewStyle {
  const { solid, flat } = options;
  if (solid) {
    if (level === 'subtle') return { backgroundColor: colors.sunken };
    if (flat) return { backgroundColor: level === 1 ? colors.surface : colors.raised };
    return level === 1 ? elevation(1, colors) : elevation(level, colors);
  }
  const g = glassTokensFor(colors);
  if (level === 'subtle') return { backgroundColor: g.subtle };
  const fill = level === 1 ? g.surface : g.strong;
  if (flat) return { backgroundColor: fill };
  const edges: ViewStyle = { backgroundColor: fill, borderWidth: 1, borderColor: g.edge, borderTopColor: g.border };
  if (Platform.OS === 'android') return edges;
  const depth = level === 1 ? 1 : level === 2 ? 1.5 : 2.5;
  return {
    ...edges,
    shadowColor: g.shadow.color,
    shadowOpacity: Math.min(0.4, g.shadow.opacity * depth),
    shadowRadius: g.shadow.radius * (level === 3 ? 1.6 : 1),
    shadowOffset: { width: 0, height: g.shadow.offsetY * (level === 3 ? 1.6 : 1) },
  };
}

/** Neutral glass edge colour (for dividers drawn on glass). */
export function glassEdge(colors: WashColors, solid?: boolean): string {
  return solid ? colors.hairline : glassTokensFor(colors).edge;
}

/**
 * A state header strip (G§3.4, G§9.3): about 70% of the semantic wash mixed
 * into the glass. This replaces the coloured side rule. Solid fallback is the
 * plain semantic wash.
 */
export function stateTint(colors: WashColors, tone: Tone, solid?: boolean): string {
  const wash = toneOf(colors, tone).wash;
  if (solid) return wash;
  const g = glassTokensFor(colors);
  return mixColor(wash, g.strong, 0.7, Math.max(parseColor(g.surface).a, GLASS_MIN_ALPHA));
}

/** Active rows (G§9.4, G§9.20): a glass-strong pill with a light edge, never a side bar. */
export function activePillStyle(colors: WashColors, solid?: boolean): ViewStyle {
  if (solid) return { backgroundColor: colors.raised, borderWidth: 1, borderColor: colors.hairline };
  const g = glassTokensFor(colors);
  return { backgroundColor: g.strong, borderWidth: 1, borderColor: g.edge, borderTopColor: g.border };
}

/* ------------------------------------------------------------ hook */

/** Blur is available on the native platforms; elsewhere chrome is solid. */
export const BLUR_AVAILABLE = Platform.OS === 'ios' || Platform.OS === 'android';

export type GlassApi = {
  /** Solid fallback is active (Reduce decoration). */
  solid: boolean;
  style: (level: GlassLevel, options?: Omit<GlassOptions, 'solid'>) => ViewStyle;
  tint: (tone: Tone) => string;
  edge: string;
  activePill: ViewStyle;
};

export function useGlass(): GlassApi {
  const { colors, reduceDecoration } = useTheme();
  return useMemo(() => ({
    solid: reduceDecoration,
    style: (level, options) => glassStyle(level, colors, { ...options, solid: reduceDecoration }),
    tint: tone => stateTint(colors, tone, reduceDecoration),
    edge: glassEdge(colors, reduceDecoration),
    activePill: activePillStyle(colors, reduceDecoration),
  }), [colors, reduceDecoration]);
}

/* ------------------------------------------------------------ chrome */

export type GlassChromeProps = {
  level?: 1 | 2 | 3;
  style?: StyleProp<ViewStyle>;
  children?: React.ReactNode;
  testID?: string;
  /** Draw edges (false for bars that draw their own hairline). */
  edges?: boolean;
  pointerEvents?: 'auto' | 'none' | 'box-none' | 'box-only';
  accessibilityViewIsModal?: boolean;
};

/**
 * The blurred backdrop of a chrome surface, as an absolute-fill layer: a
 * native backdrop blur (24px at level 1, 32px above) under the glass fill.
 * Put it first inside a container that owns its own shape and edges.
 * Renders the solid surface when decoration is reduced or blur is missing.
 */
export function GlassBackdrop({ level = 1, testID }: { level?: 1 | 2 | 3; testID?: string }) {
  const { colors, isDark, reduceDecoration } = useTheme();
  const solid = reduceDecoration || !BLUR_AVAILABLE;
  const fill = glassStyle(level, colors, { solid, flat: true }).backgroundColor;
  if (solid) return <View testID={testID} pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: fill }]} />;
  const blur = level === 1 ? glassEffect.blur : glassEffect.blur_strong;
  return (
    <View testID={testID} pointerEvents="none" style={StyleSheet.absoluteFill}>
      <BlurView
        tint={isDark ? 'dark' : 'light'}
        // Android: radius = intensity / blurReductionFactor (Dimezis caps at 25).
        intensity={Math.min(100, blur * 4)}
        blurReductionFactor={4}
        experimentalBlurMethod="dimezisBlurView"
        style={StyleSheet.absoluteFill}
      />
      <View style={[StyleSheet.absoluteFill, { backgroundColor: fill }]} />
    </View>
  );
}

/**
 * Chrome glass (header, tab bar, sheets, dialogs, toasts): glass edges and a
 * blurred backdrop. Lists and cards never use this; they use `glassStyle`.
 */
export function GlassChrome({ level = 1, style, children, testID, edges = true, pointerEvents, accessibilityViewIsModal }: GlassChromeProps) {
  const { colors, reduceDecoration } = useTheme();
  const solid = reduceDecoration || !BLUR_AVAILABLE;
  const surface = glassStyle(level, colors, { solid, flat: !edges });
  return (
    <View testID={testID} pointerEvents={pointerEvents} accessibilityViewIsModal={accessibilityViewIsModal}
      style={[surface, { backgroundColor: 'transparent', overflow: 'hidden' }, style]}>
      <GlassBackdrop level={level} testID={testID ? `${testID}-backdrop` : undefined} />
      {children}
    </View>
  );
}

/** A full-bleed stage (G§9.3 "Panel (stage)"): level-1 glass with no edges. */
export function Stage({ children, style, testID }: { children?: React.ReactNode; style?: StyleProp<ViewStyle>; testID?: string }) {
  const glass = useGlass();
  return <View testID={testID} style={[{ flex: 1 }, glass.style(1, { flat: true }), style]}>{children}</View>;
}

/* ------------------------------------------------------------ ambient wash */

export type AmbientArt = 'dawn' | 'lagoon' | 'meadow';

const AMBIENT: Record<AmbientArt, Record<ThemeName, ImageSourcePropType>> = {
  dawn: { paper: require('../../assets/wash/ambient-dawn-paper.webp'), dusk: require('../../assets/wash/ambient-dawn-dusk.webp') },
  lagoon: { paper: require('../../assets/wash/ambient-lagoon-paper.webp'), dusk: require('../../assets/wash/ambient-lagoon-dusk.webp') },
  meadow: { paper: require('../../assets/wash/ambient-meadow-paper.webp'), dusk: require('../../assets/wash/ambient-meadow-dusk.webp') },
};

export function ambientArt(art: AmbientArt, theme: ThemeName): ImageSourcePropType {
  return AMBIENT[art][theme];
}

/** G§6.2: Dawn for chat, Lagoon for Work and Trace, Meadow for environments; Dawn elsewhere. */
export function routeAmbient(routeName: string | null | undefined): AmbientArt {
  if (!routeName) return 'dawn';
  if (routeName === 'Work' || routeName === 'Traces' || routeName.startsWith('Job')) return 'lagoon';
  if (routeName === 'Containers' || routeName === 'Environments') return 'meadow';
  return 'dawn';
}

type AmbientContextValue = { art: AmbientArt; setRouteArt: (art: AmbientArt) => void; pushOverride: (art: AmbientArt) => () => void };

const AmbientContext = createContext<AmbientContextValue>({ art: 'dawn', setRouteArt: () => undefined, pushOverride: () => () => undefined });

/** Ambient art for the current screen; overrides (e.g. the environment manager) win while mounted. */
export function useAmbientArt(): AmbientArt {
  return useContext(AmbientContext).art;
}

export function useAmbientController() {
  return useContext(AmbientContext);
}

/** While `art` is set, the ambient backdrop shows it (for full-screen flows). */
export function useAmbientOverride(art: AmbientArt | null) {
  const { pushOverride } = useContext(AmbientContext);
  useEffect(() => (art ? pushOverride(art) : undefined), [art, pushOverride]);
}

/** One fixed, full-bleed image (or flat canvas). Never per row. */
export function AmbientLayer({ art, testID = 'ambient-wash' }: { art: AmbientArt; testID?: string }) {
  const { colors, name, reduceDecoration } = useTheme();
  if (reduceDecoration) {
    return <View testID={`${testID}-flat`} pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: colors.canvas }]} />;
  }
  return (
    <View pointerEvents="none" importantForAccessibility="no-hide-descendants" style={[StyleSheet.absoluteFill, { backgroundColor: colors.canvas }]}>
      <Image testID={testID} source={ambientArt(art, name)} resizeMode="cover" fadeDuration={0} accessibilityIgnoresInvertColors style={StyleSheet.absoluteFill} />
    </View>
  );
}

let overrideSeq = 0;

/**
 * The app-wide ambient backdrop (G§6.2): a single fixed image behind the
 * navigator. Children (the navigator) must be transparent to show it.
 */
export function AmbientBackground({ children, initialArt = 'dawn' }: { children: React.ReactNode; initialArt?: AmbientArt }) {
  const [routeArt, setRouteArtState] = useState<AmbientArt>(initialArt);
  const [overrides, setOverrides] = useState<Array<{ id: number; art: AmbientArt }>>([]);
  const setRouteArt = useCallback((art: AmbientArt) => setRouteArtState(prev => (prev === art ? prev : art)), []);
  const pushOverride = useCallback((art: AmbientArt) => {
    overrideSeq += 1;
    const id = overrideSeq;
    setOverrides(list => [...list, { id, art }]);
    return () => setOverrides(list => list.filter(o => o.id !== id));
  }, []);
  const art = overrides.length ? overrides[overrides.length - 1].art : routeArt;
  const value = useMemo(() => ({ art, setRouteArt, pushOverride }), [art, setRouteArt, pushOverride]);
  return (
    <AmbientContext.Provider value={value}>
      <View style={styles.root}>
        <AmbientLayer art={art} />
        {children}
      </View>
    </AmbientContext.Provider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
