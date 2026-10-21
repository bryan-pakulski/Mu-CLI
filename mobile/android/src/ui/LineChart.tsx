// A small time-series chart (node history, mu/orchestration/metrics.py): one series as a line over
// a soft area, or two as lines (network in/out, disk read/write). Points sit at their time inside
// the chart's range, so a stretch with no samples (the node was off) shows as a gap instead of a
// slope. Percentages use a fixed 0–100 scale; other units scale to the largest value. Dragging
// across the chart reads out the nearest point; screen readers get one summary sentence
// ("CPU over the last 6 hours: now 12%, average 9%, peak 40%").

import React, { useMemo, useRef, useState } from 'react';
import { PanResponder, StyleSheet, View, type LayoutChangeEvent } from 'react-native';
import Svg, { Line, Path } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';
import { spacing } from '../theme/tokens';
import { Text } from './Text';

export type ChartUnit = 'percent' | 'number' | 'bytes_per_s';
export type ChartPoint = { t: number } & Record<string, number | undefined>;
export type ChartSeries = { key: string; label: string };

/** A value in words for its unit: 42%, 1.5 MB/s, 0.73. */
export function formatMetric(value: number | null | undefined, unit: ChartUnit): string {
  if (value == null || !Number.isFinite(value)) return '–';
  if (unit === 'percent') return `${Math.round(value)}%`;
  if (unit === 'bytes_per_s') {
    for (const [size, suffix] of [[2 ** 30, 'GB/s'], [2 ** 20, 'MB/s'], [2 ** 10, 'kB/s']] as const) {
      if (value >= size) return `${(value / size).toFixed(1)} ${suffix}`;
    }
    return `${Math.round(value)} B/s`;
  }
  return value >= 10 ? value.toFixed(1) : value.toFixed(2);
}

/** The top of the scale: 100 for percentages, else the largest value (never 0). */
export function chartMax(values: Array<number | undefined>, unit: ChartUnit): number {
  if (unit === 'percent') return 100;
  const real = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const top = real.length ? Math.max(...real) : 0;
  return top > 0 ? top * 1.1 : 1;
}

/**
 * SVG paths for one series: a line per unbroken run of points (a missing value, or a jump of more
 * than 1.5 buckets, breaks it). x is the point's time within [since, until].
 */
export function chartPaths(points: ChartPoint[], key: string, opts: { since: number; until: number; bucketS: number; width: number; height: number; max: number }): string[] {
  const { since, until, bucketS, width, height, max } = opts;
  const span = Math.max(1, until - since);
  const runs: string[] = [];
  let current: string[] = [];
  let lastT: number | null = null;
  for (const p of points) {
    const v = p[key];
    const broken = v == null || !Number.isFinite(v) || (lastT != null && p.t - lastT > 1.5 * Math.max(1, bucketS));
    if (broken && current.length) { runs.push(current.join(' ')); current = []; }
    if (v == null || !Number.isFinite(v)) { lastT = null; continue; }
    const x = Math.max(0, Math.min(width, ((p.t - since) / span) * width));
    const y = height - Math.max(0, Math.min(1, v / max)) * height;
    current.push(`${current.length ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`);
    lastT = p.t;
  }
  if (current.length) runs.push(current.join(' '));
  return runs;
}

/** A line path closed down to the baseline (the soft area under a single series). */
export function areaPath(line: string, height: number): string {
  const xs = line.split(' ').map(step => Number(step.slice(1).split(',')[0]));
  return `${line} L${xs[xs.length - 1]},${height} L${xs[0]},${height} Z`;
}

/** The chart in one sentence for screen readers. */
export function chartSummary(title: string, rangeLabel: string, points: ChartPoint[], series: ChartSeries[], unit: ChartUnit): string {
  const parts = series.map(s => {
    const real = points.map(p => p[s.key]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    if (!real.length) return `${series.length > 1 ? `${s.label}: ` : ''}no data`;
    const avg = real.reduce((a, b) => a + b, 0) / real.length;
    return `${series.length > 1 ? `${s.label} ` : ''}now ${formatMetric(real[real.length - 1], unit)}, average ${formatMetric(avg, unit)}, peak ${formatMetric(Math.max(...real), unit)}`;
  });
  return `${title} over the last ${rangeLabel}: ${parts.join('; ')}`;
}

function timeLabel(t: number, spanS: number): string {
  const d = new Date(t * 1000);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return spanS > 24 * 3600 ? `${d.getDate()}/${d.getMonth() + 1} ${hm}` : hm;
}

export type LineChartProps = {
  title: string;
  points: ChartPoint[];
  series: ChartSeries[];
  unit: ChartUnit;
  since: number;
  until: number;
  bucketS: number;
  /** "6 hours" — for the summary sentence. */
  rangeLabel: string;
  height?: number;
  testID?: string;
};

export function LineChart({ title, points, series, unit, since, until, bucketS, rangeLabel, height = 72, testID }: LineChartProps) {
  const { colors } = useTheme();
  const [width, setWidth] = useState(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const widthRef = useRef(0);
  const values = useMemo(() => series.flatMap(s => points.map(p => p[s.key])), [points, series]);
  const max = chartMax(values, unit);
  const tones = [colors.series[0] || colors.accent, colors.series[1] || colors.plan];
  const latest = series.map(s => {
    for (let i = points.length - 1; i >= 0; i -= 1) { const v = points[i][s.key]; if (typeof v === 'number') return v; }
    return undefined;
  });
  const hasData = values.some(v => typeof v === 'number');
  const summary = chartSummary(title, rangeLabel, points, series, unit);

  const nearest = (x: number) => {
    const w = widthRef.current;
    if (!w || !points.length) return null;
    const t = since + (Math.max(0, Math.min(w, x)) / w) * (until - since);
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(p.t - t) < Math.abs(points[best].t - t)) best = i; });
    return best;
  };
  const responder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: (_e, g) => Math.abs(g.dx) > Math.abs(g.dy),
    onPanResponderGrant: e => setCursor(nearest(e.nativeEvent.locationX)),
    onPanResponderMove: e => setCursor(nearest(e.nativeEvent.locationX)),
    onPanResponderRelease: () => setCursor(null),
    onPanResponderTerminate: () => setCursor(null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [points, since, until]);

  const onLayout = (e: LayoutChangeEvent) => { widthRef.current = e.nativeEvent.layout.width; setWidth(e.nativeEvent.layout.width); };
  const at = cursor != null ? points[cursor] : null;
  const readout = at
    ? `${timeLabel(at.t, until - since)} · ${series.map(s => `${series.length > 1 ? `${s.label} ` : ''}${formatMetric(at[s.key], unit)}`).join(' · ')}`
    : series.map((s, i) => `${series.length > 1 ? `${s.label} ` : ''}${formatMetric(latest[i], unit)}`).join(' · ');
  const cursorX = at && width ? ((at.t - since) / Math.max(1, until - since)) * width : null;

  return (
    <View testID={testID} style={styles.wrap} accessible accessibilityRole="image" accessibilityLabel={summary}>
      <View style={styles.head}>
        <Text variant="label" style={{ flex: 1 }}>{title}</Text>
        <Text variant="caption" numeric tone={at ? 'ink' : 'ink3'} testID={testID ? `${testID}-readout` : undefined}>{hasData ? readout : 'no data'}</Text>
      </View>
      <View style={[styles.plot, { height, borderColor: colors.hairline }]} onLayout={onLayout} {...responder.panHandlers}>
        {width > 0 && hasData ? (
          <Svg width={width} height={height}>
            <Line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke={colors.hairline} strokeWidth={StyleSheet.hairlineWidth} />
            {series.map((s, i) => chartPaths(points, s.key, { since, until, bucketS, width, height, max }).map((d, j) => (
              <React.Fragment key={`${s.key}-${j}`}>
                {series.length === 1 ? <Path d={areaPath(d, height)} fill={tones[i].wash} /> : null}
                <Path d={d} stroke={tones[i].pigment} strokeWidth={1.5} fill="none" strokeLinejoin="round" />
              </React.Fragment>
            )))}
            {cursorX != null ? <Line x1={cursorX} x2={cursorX} y1={0} y2={height} stroke={colors.ink3} strokeWidth={1} /> : null}
          </Svg>
        ) : null}
      </View>
      <View style={styles.foot}>
        <Text variant="caption" tone="ink3" numeric>{unit === 'percent' ? '100%' : `max ${formatMetric(max / 1.1, unit)}`}</Text>
        {series.length > 1 ? (
          <View style={styles.legend}>
            {series.map((s, i) => (
              <View key={s.key} style={styles.legendItem}>
                <View style={[styles.swatch, { backgroundColor: tones[i].pigment }]} />
                <Text variant="caption" tone="ink3">{s.label}</Text>
              </View>
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}

export type MiniChartProps = {
  label: string;
  points: ChartPoint[];
  valueKey: string;
  unit: ChartUnit;
  since: number;
  until: number;
  bucketS: number;
  /** The scale's top (100 for percentages; pass a shared max so rows compare). */
  max?: number;
  /** The latest value's tone (high use → attention or danger ink). */
  tone?: 'attention' | 'danger' | null;
  testID?: string;
};

/**
 * A row-sized chart (the node dashboard's health rows): the label and the latest value on one line,
 * a thin line under it. No readout or legend — the node page has the full chart.
 */
export function MiniChart({ label, points, valueKey, unit, since, until, bucketS, max, tone, testID }: MiniChartProps) {
  const { colors } = useTheme();
  const [width, setWidth] = useState(0);
  const values = points.map(p => p[valueKey]);
  const top = max && max > 0 ? max : chartMax(values, unit);
  let latest: number | undefined;
  for (let i = points.length - 1; i >= 0; i -= 1) { const v = points[i][valueKey]; if (typeof v === 'number') { latest = v; break; } }
  const ink = tone === 'danger' ? colors.danger : tone === 'attention' ? colors.attention : (colors.series[0] || colors.accent);
  const height = MINI_HEIGHT;
  return (
    <View testID={testID} style={styles.mini}>
      <Text variant="caption" numeric tone={tone ? (tone === 'danger' ? 'danger' : 'attention') : 'ink2'} numberOfLines={1}
        testID={testID ? `${testID}-value` : undefined}>{label} {formatMetric(latest, unit)}</Text>
      <View style={{ height }} onLayout={e => setWidth(e.nativeEvent.layout.width)}>
        {width > 0 && latest !== undefined ? (
          <Svg width={width} height={height}>
            {chartPaths(points, valueKey, { since, until, bucketS, width, height, max: top }).map((d, j) => (
              <Path key={j} d={d} stroke={ink.pigment} strokeWidth={1.25} fill="none" strokeLinejoin="round" />
            ))}
          </Svg>
        ) : null}
      </View>
    </View>
  );
}

const MINI_HEIGHT = 18;

const styles = StyleSheet.create({
  mini: { flex: 1, minWidth: 0, gap: 2 },
  wrap: { gap: spacing.xs },
  head: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm },
  plot: { borderBottomWidth: StyleSheet.hairlineWidth, overflow: 'hidden' },
  foot: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  legend: { flexDirection: 'row', gap: spacing.md },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  swatch: { width: 10, height: 2 },
});
