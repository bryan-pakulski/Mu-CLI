// Glass surfaces and the ambient wash (G§3.3, G§6.2; Mu revision 2, mobile_parity.md §4).
//
// Mu revision 2: surfaces are flat and the watercolour is the destination's accent. Each
// destination has an ambient scene (tokens.json destination) that cross-fades when the tab
// changes. With the Wash theme a glass *strength* (0–100, default 60, Settings → Appearance) lets
// a little of it through, using the web's no-blur formula: a fill of the theme's own surface at
// `1 − 0.12 × strength` (never below `glassEffect.min_alpha`). Palettes, "Reduce decoration" and
// strength 0 are solid, and palettes and reduced decoration show no watercolour (as on the web).
//
// | level    | fill (strength s)          | use                                   |
// |----------|----------------------------|---------------------------------------|
// | 1        | surface at 1 − 0.12·s      | stage, panels, composer               |
// | 'subtle' | sunken at 1 − 0.12·s       | inputs, tool rows, chips on a surface |
// | 2        | raised at 1 − 0.06·s       | menus, toasts, active nav pill        |
// | 3        | raised at 1 − 0.06·s       | sheets, dialogs                       |
//
// Edges are light hairlines, never pigment (G§3.4). Only chrome (header, tab bar, sheets) uses a
// native BlurView, and only on iOS (Android has no live blur; see BLUR_AVAILABLE).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Image, Platform, StyleSheet, View, type ImageSourcePropType, type StyleProp, type ViewStyle } from 'react-native';
import { BlurView } from 'expo-blur';
import { useTheme, type ThemeName } from '../theme/ThemeContext';
import { destinationAmbient, easings, elevation, toneOf, type DestinationName, type Tone, type WashColors } from '../theme/tokens';
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
  /** Solid fallback (a palette, "Reduce decoration", strength 0). */
  solid?: boolean;
  /** No edges (a full-bleed stage or a strip inside a card). */
  flat?: boolean;
  /** Glass strength 0–1 (the Settings slider / 100). */
  strength?: number;
};

export const GLASS_CLEAR = 0.12;

/** Fill alpha of a level at a strength (0–1), floor-clamped for text-bearing levels. */
export function glassFillAlpha(level: GlassLevel, strength: number): number {
  const s = Math.max(0, Math.min(1, strength));
  const clear = level === 2 || level === 3 ? GLASS_CLEAR / 2 : GLASS_CLEAR;
  const alpha = 1 - clear * s;
  return level === 'subtle' ? alpha : Math.max(GLASS_MIN_ALPHA, alpha);
}

function withAlpha(color: string, alpha: number): string {
  return toCss({ ...parseColor(color), a: alpha });
}

/**
 * Fill, edges and depth for one level. Never sets a coloured edge. On Android the elevation
 * shadow is omitted for translucent fills (Android draws a shadow through a translucent surface
 * as a dark smudge); depth comes from the hairline instead.
 */
export function glassStyle(level: GlassLevel, colors: WashColors, options: GlassOptions = {}): ViewStyle {
  const { solid, flat, strength = 0.6 } = options;
  if (solid || strength <= 0) {
    if (level === 'subtle') return { backgroundColor: colors.sunken };
    if (flat) return { backgroundColor: level === 1 ? colors.surface : colors.raised };
    return level === 1 ? elevation(1, colors) : elevation(level, colors);
  }
  const alpha = glassFillAlpha(level, strength);
  if (level === 'subtle') return { backgroundColor: withAlpha(colors.sunken, alpha) };
  const fill = withAlpha(level === 1 ? colors.surface : colors.raised, alpha);
  if (flat) return { backgroundColor: fill };
  const edges: ViewStyle = { backgroundColor: fill, borderWidth: 1, borderColor: colors.hairline };
  if (Platform.OS === 'android' || level === 1) return edges;
  return { ...edges, ...{ shadowColor: colors.shadow, shadowOpacity: level === 2 ? 0.08 : 0.16,
    shadowRadius: level === 2 ? 12 : 32, shadowOffset: { width: 0, height: level === 2 ? 8 : 24 } } };
}

/** Neutral edge colour (for dividers drawn on a surface). */
export function glassEdge(colors: WashColors, _solid?: boolean): string {
  return colors.hairline;
}

/**
 * A state header strip (G§3.4, G§9.3): about 70% of the semantic wash mixed
 * into the glass. This replaces the coloured side rule. Solid fallback is the
 * plain semantic wash.
 */
export function stateTint(colors: WashColors, tone: Tone, solid?: boolean): string {
  const wash = toneOf(colors, tone).wash;
  if (solid) return wash;
  return mixColor(wash, colors.raised, 0.7, 1);
}

/** Active rows (G§9.4, G§9.20): a glass-strong pill with a light edge, never a side bar. */
export function activePillStyle(colors: WashColors, _solid?: boolean): ViewStyle {
  return { backgroundColor: colors.raised, borderWidth: 1, borderColor: colors.hairline };
}

/* ------------------------------------------------------------ hook */

/**
 * Live backdrop blur is iOS-only. On Android expo-blur's only real blur is the experimental
 * Dimezis method, which re-captures the screen behind each BlurView every frame; with several
 * chrome surfaces mounted (header, tab bar, drawers, sheets) and the chat's animated "thinking"
 * indicator running, it starved the render thread and froze the UI on device (taps still reached
 * JS, the screen stopped updating). Android chrome therefore uses the translucent glass fill —
 * the design's documented no-blur fallback (G§3.3) — which keeps the ambient wash visible.
 */
export const BLUR_AVAILABLE = Platform.OS === 'ios';

export type GlassApi = {
  /** Surfaces are solid (a palette, Reduce decoration or strength 0). */
  solid: boolean;
  /** Glass strength 0–1. */
  strength: number;
  style: (level: GlassLevel, options?: Omit<GlassOptions, 'solid'>) => ViewStyle;
  tint: (tone: Tone) => string;
  edge: string;
  activePill: ViewStyle;
};

export function useGlass(): GlassApi {
  const { colors, flat, glass } = useTheme();
  return useMemo(() => {
    const strength = flat ? 0 : glass / 100;
    return {
      solid: flat,
      strength,
      style: (level, options) => glassStyle(level, colors, { ...options, solid: flat, strength }),
      tint: tone => stateTint(colors, tone, flat),
      edge: glassEdge(colors, flat),
      activePill: activePillStyle(colors, flat),
    };
  }, [colors, flat, glass]);
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
  const { colors, isDark, flat, glass } = useTheme();
  // Flat → solid surface. No live blur (Android) → the translucent fill alone.
  const solid = flat;
  const fill = glassStyle(level, colors, { solid, flat: true, strength: glass / 100 }).backgroundColor;
  if (solid || !BLUR_AVAILABLE) return <View testID={testID} pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: fill }]} />;
  const blur = level === 1 ? glassEffect.blur : glassEffect.blur_strong;
  return (
    <View testID={testID} pointerEvents="none" style={StyleSheet.absoluteFill}>
      <BlurView
        tint={isDark ? 'dark' : 'light'}
        intensity={Math.min(100, blur * 4)}
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
  const { colors, flat, glass } = useTheme();
  const surface = glassStyle(level, colors, { solid: flat, flat: !edges, strength: glass / 100 });
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

/** The ambient scene of a destination (tokens.json destination). */
export function destinationArt(destination: DestinationName): AmbientArt {
  return destinationAmbient(destination);
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
export function AmbientLayer({ art, testID = 'ambient-wash', opacity }: { art: AmbientArt; testID?: string; opacity?: Animated.Value }) {
  const { colors, name, reduceDecoration, palette } = useTheme();
  if (reduceDecoration || palette) {
    return <View testID={`${testID}-flat`} pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: colors.canvas }]} />;
  }
  return (
    <Animated.View pointerEvents="none" importantForAccessibility="no-hide-descendants" style={[StyleSheet.absoluteFill, { backgroundColor: colors.canvas }, opacity ? { opacity } : null]}>
      <Image testID={testID} source={ambientArt(art, name)} resizeMode="cover" fadeDuration={0} accessibilityIgnoresInvertColors style={[StyleSheet.absoluteFill, styles.fill]} />
    </Animated.View>
  );
}

let overrideSeq = 0;

/** The destination fade (Mu §11b): 1.4 s; instant when the system asks for reduced motion. */
export const AMBIENT_FADE_MS = 1400;

/**
 * The app-wide ambient backdrop (G§6.2): a single fixed image behind the navigator, chosen by the
 * destination on screen (useTheme().destination) and cross-faded when it changes. Overrides (a
 * full-screen flow) win while mounted. Children (the navigator) must be transparent to show it.
 */
export function AmbientBackground({ children }: { children: React.ReactNode; initialArt?: AmbientArt }) {
  const { destination } = useTheme();
  const [overrides, setOverrides] = useState<Array<{ id: number; art: AmbientArt }>>([]);
  const pushOverride = useCallback((art: AmbientArt) => {
    overrideSeq += 1;
    const id = overrideSeq;
    setOverrides(list => [...list, { id, art }]);
    return () => setOverrides(list => list.filter(o => o.id !== id));
  }, []);
  const art = overrides.length ? overrides[overrides.length - 1].art : destinationArt(destination);
  const [shown, setShown] = useState<{ current: AmbientArt; previous: AmbientArt | null }>({ current: art, previous: null });
  const fade = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (art === shown.current) return;
    let cancelled = false;
    void AccessibilityInfo.isReduceMotionEnabled().catch(() => false).then(reduce => {
      if (cancelled) return;
      if (reduce) { setShown({ current: art, previous: null }); return; }
      setShown(prev => ({ current: art, previous: prev.current }));
      fade.setValue(0);
      Animated.timing(fade, { toValue: 1, duration: AMBIENT_FADE_MS, easing: easings.bleed, useNativeDriver: true }).start(({ finished }) => {
        if (finished && !cancelled) setShown(prev => ({ current: prev.current, previous: null }));
      });
    });
    return () => { cancelled = true; };
  }, [art, fade, shown.current]);
  const setRouteArt = useCallback(() => undefined, []);
  const value = useMemo(() => ({ art, setRouteArt, pushOverride }), [art, setRouteArt, pushOverride]);
  return (
    <AmbientContext.Provider value={value}>
      <View style={styles.root}>
        {shown.previous ? <AmbientLayer art={shown.previous} testID="ambient-wash-previous" /> : null}
        <AmbientLayer art={shown.current} opacity={shown.previous ? fade : undefined} />
        {children}
      </View>
    </AmbientContext.Provider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  // Explicit size: some Image backends otherwise keep the asset's intrinsic size.
  fill: { width: '100%', height: '100%' },
});
