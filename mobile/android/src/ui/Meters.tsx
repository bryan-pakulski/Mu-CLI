// Context meters (G§9.14). These only *display* data the server already
// sends (memory snapshots, trace summaries); they never compute budgets.
//
// - ContextRing: a 20px ring of the context fill in ink-2, caution at >=65%
//   and danger at >=85% (the web `.warm`/`.hot` thresholds).
// - LayerBar: one stacked bar split into L0-L5 segments in their layer
//   pigments, direct labels above segments of 6% or more, a legend for the
//   rest, and an optional dashed budget tick.

import React from 'react';
import { View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, type LayerName, type Tone3 } from '../theme/tokens';
import { layerNames } from '../theme/wash.generated';
import { Pressable } from './Pressable';
import { Text } from './Text';

export const CONTEXT_CAUTION = 0.65;
export const CONTEXT_DANGER = 0.85;

export function contextTone(fill: number): 'neutral' | 'caution' | 'danger' {
  if (fill >= CONTEXT_DANGER) return 'danger';
  if (fill >= CONTEXT_CAUTION) return 'caution';
  return 'neutral';
}

export function ContextRing({ fill, size = 20, showLabel = true, onPress, testID }: { fill: number; size?: number; showLabel?: boolean; onPress?: () => void; testID?: string }) {
  const { colors } = useTheme();
  const pct = Math.max(0, Math.min(1, Number.isFinite(fill) ? fill : 0));
  const tone = contextTone(pct);
  const stroke = tone === 'danger' ? colors.danger.ink : tone === 'caution' ? colors.caution.ink : colors.ink2;
  const r = (size - 4) / 2;
  const circ = 2 * Math.PI * r;
  const label = `Context ${Math.round(pct * 100)}% full`;
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
      <Svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors.sunken} strokeWidth={3} fill="none" />
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={stroke} strokeWidth={3} fill="none" strokeLinecap="round"
          strokeDasharray={`${circ * pct} ${circ}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </Svg>
      {showLabel ? <Text variant="caption" numeric style={{ color: tone === 'neutral' ? colors.ink2 : stroke }}>{Math.round(pct * 100)}%</Text> : null}
    </View>
  );
  if (onPress) {
    return <Pressable testID={testID} accessibilityLabel={label} accessibilityHint="Opens the context breakdown" onPress={onPress} style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.xs }}>{body}</Pressable>;
  }
  return <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={label}>{body}</View>;
}

export type LayerSegment = { id: string; tokens: number; name?: string };

export function layerTone(colors: ReturnType<typeof useTheme>['colors'], id: string, index: number): Tone3 {
  return (colors.layer as Record<string, Tone3>)[id] || colors.series[index % colors.series.length];
}

export function LayerBar({ layers, limit, budget, testID }: { layers: LayerSegment[]; limit: number; budget?: number; testID?: string }) {
  const { colors } = useTheme();
  const total = Math.max(1, limit || layers.reduce((s, l) => s + Math.max(0, l.tokens), 0));
  const shown = layers.filter(l => l.tokens > 0);
  const description = shown.map(l => `${l.id} ${Math.round((l.tokens / total) * 100)}%`).join(', ');
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={`Context layers: ${description || 'empty'}`} style={{ gap: spacing.xs }}>
      <View style={{ flexDirection: 'row', height: 14 }}>
        {shown.map(l => {
          const share = l.tokens / total;
          return (
            <View key={l.id} style={{ width: `${share * 100}%`, overflow: 'hidden' }}>
              {share >= 0.06 ? <Text variant="monoSm" tone="ink2" numberOfLines={1}>{l.id}</Text> : null}
            </View>
          );
        })}
      </View>
      <View style={{ height: 10, borderRadius: radii.pill, backgroundColor: colors.sunken, flexDirection: 'row', overflow: 'hidden' }}>
        {shown.map((l, i) => (
          <View key={l.id} style={{ width: `${(l.tokens / total) * 100}%`, backgroundColor: layerTone(colors, l.id, i).pigment }} />
        ))}
        {budget && limit ? (
          // Budget tick: a neutral 1px data mark inside the meter (wash-data-mark), not a side accent.
          <View style={{ position: 'absolute', left: `${Math.min(1, budget / total) * 100}%`, top: 0, bottom: 0, borderLeftWidth: 1, borderStyle: 'dashed', borderLeftColor: colors.ink }} /* wash-data-mark */ />
        ) : null}
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: spacing.md, rowGap: 2 }}>
        {shown.map((l, i) => (
          <View key={l.id} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: layerTone(colors, l.id, i).pigment }} />
            <Text variant="caption" tone="ink3">{l.id} {l.name || (layerNames as Record<string, string>)[l.id as LayerName] || ''}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}
