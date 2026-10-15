import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { tracesApi, TraceRun, TraceSummary } from '../api/traces';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { spacing, fontSizes, fontWeights, radii, tracking, typography, touchTarget } from '../theme/tokens';
import { Card, EmptyState, ErrorState, Pressable, Skeleton, Text } from '../ui';

type NumericPoint = Record<string, unknown>;

type TraceDashboardData = {
  run_id: string;
  n_runs?: number;
  summary: TraceSummary & { efficiency?: Record<string, unknown> };
  series: {
    context?: NumericPoint[];
    context_attribution?: NumericPoint[];
    tokens?: NumericPoint[];
    latency?: NumericPoint[];
    tool_histogram?: NumericPoint[];
    efficiency?: NumericPoint[];
    compaction_timeline?: NumericPoint[];
    nudge_timeline?: NumericPoint[];
    redundant_reads?: NumericPoint[];
    subagent_timeline?: NumericPoint[];
    memory_series?: NumericPoint[];
    top_context_spikes?: NumericPoint[];
  };
};

type ChartSeries = { key: string; label: string; color: string };
type BarMode = 'grouped' | 'stacked' | 'single';

const PLOT_HEIGHT = 112;
const AXIS_HEIGHT = 20;
// MUCLI_MOBILE_TRACE_Y_AXIS_V1: reserve a fixed lane so Y values remain visible while the plot scrolls.
const Y_AXIS_WIDTH = 56;
const Y_AXIS_TARGET_TICKS = 4;
const MAX_VISIBLE_POINTS = 72;
// Round-44 F12: caps for the non-virtualized chip/pill rails. The server now
// bounds trace payloads (500 iters / 2000 events per category), but the
// scope-chip run rail and the compaction/nudge pill rails still mounted
// every row they received. Newest-window slices keep the rails light while
// preserving the visual design; totals remain visible via the MetricGrid.
const MAX_SCOPE_CHIPS = 40;
const MAX_EVENT_PILLS = 60;

export function SessionTraceScreenV2() {
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(state => state.activeSessionName);
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [dashboard, setDashboard] = useState<TraceDashboardData | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (runId?: string | null) => {
    try {
      setError(null);
      const listed = await tracesApi.list(activeSessionName || undefined);
      setRuns(listed);

      let next: TraceDashboardData | null = null;
      if (runId) {
        next = await tracesApi.getRun(runId, 96) as TraceDashboardData;
      } else if (activeSessionName) {
        try {
          next = await tracesApi.getSession(activeSessionName, 96) as TraceDashboardData;
        } catch {
          if (listed[0]) next = await tracesApi.getRun(listed[0].run_id, 96) as TraceDashboardData;
        }
      } else if (listed[0]) {
        next = await tracesApi.getRun(listed[0].run_id, 96) as TraceDashboardData;
      }

      setDashboard(next);
      setSelectedRunId(runId || null);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [activeSessionName]);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load(null);
    }, [load]),
  );

  if (loading) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
        <View style={styles.loadingWrap}>
          <Skeleton height={96} style={styles.loadingBlock} />
          <Skeleton height={190} style={styles.loadingBlock} />
          <Skeleton height={190} />
        </View>
      </SafeAreaView>
    );
  }

  if (error && !dashboard) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
        <ErrorState message={error} onRetry={() => load(selectedRunId)} />
      </SafeAreaView>
    );
  }

  if (!dashboard) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
        <EmptyState
          icon="activity"
          title="No session traces"
          message="Run an agent turn with tracing enabled to populate session analytics."
        />
      </SafeAreaView>
    );
  }

  const summary = dashboard.summary;
  const series = dashboard.series;
  const context = series.context || [];
  const attribution = series.context_attribution || [];
  const tokens = series.tokens || [];
  const latency = series.latency || [];
  const efficiency = series.efficiency || [];
  const tools = [...(series.tool_histogram || [])].sort((a, b) => numberValue(b.count) - numberValue(a.count));
  const compactions = series.compaction_timeline || [];
  const nudges = series.nudge_timeline || [];
  const redundantReads = series.redundant_reads || [];
  const memory = series.memory_series || [];
  const subagents = series.subagent_timeline || [];
  const spikes = series.top_context_spikes || [];

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.canvas }]}>
      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(selectedRunId); }} />}
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.pageHeader}>
          <View style={styles.pageHeaderCopy}>
            <Text variant="xl" style={styles.pageTitle}>Session trace</Text>
            <Text variant="sm" dim numberOfLines={2}>
              {selectedRunId || activeSessionName || summary.session} · {summary.provider} / {summary.model}
            </Text>
          </View>
          <View style={[styles.statusPill, { backgroundColor: summary.status === 'completed' ? colors.ok.wash : colors.attention.wash }]}>
            <View style={[styles.statusDot, { backgroundColor: summary.status === 'completed' ? colors.ok.ink : colors.attention.ink }]} />
            <Text variant="xs" style={{ color: summary.status === 'completed' ? colors.ok.ink : colors.attention.ink }}>{summary.status}</Text>
          </View>
        </View>

        {runs.length > 1 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scopeRow}>
            {activeSessionName ? (
              <ScopeChip
                label={`Session · ${runs.length} runs`}
                active={!selectedRunId}
                onPress={() => { setLoading(true); load(null); }}
              />
            ) : null}
            {/* Round-44 F12: newest-window slice — the rail is a flat .map
                (non-virtualized), so a session with hundreds of runs would
                mount every chip at once. runBounds/MetricGrid still surface
                the totals. */}
            {runs.slice(-MAX_SCOPE_CHIPS).map(run => (
              <ScopeChip
                key={run.run_id}
                label={`${run.mode} · ${run.iters} iters`}
                active={selectedRunId === run.run_id}
                onPress={() => { setLoading(true); load(run.run_id); }}
              />
            ))}
          </ScrollView>
        ) : null}

        <MetricGrid metrics={[
          { label: 'Iterations', value: formatInteger(summary.iters), detail: `${summary.tool_calls} tool calls` },
          { label: 'Context peak', value: formatTokens(Math.max(summary.peak_context, summary.peak_estimated)), detail: `${formatPercent(contextFill(summary))} of window` },
          { label: 'Input tokens', value: formatTokens(summary.total_in), detail: `${formatTokens(summary.total_out)} output` },
          { label: 'Wall time', value: formatDuration(summary.total_wall_ms), detail: `${formatDuration(summary.mean_wall_ms)} mean` },
          { label: 'Compactions', value: formatInteger(summary.compaction_count), detail: `${summary.mechanical_fallback_count} mechanical` },
          { label: 'Redundant reads', value: formatInteger(summary.redundant_reads), detail: `${summary.nudge_count} nudges` },
        ]} />

        <ChartCard title="Context growth" subtitle="Estimated and corrected provider context per iteration">
          <FixedBarChart
            points={context}
            mode="grouped"
            yAxisLabel="tokens"
            valueFormatter={formatTokens}
            series={[
              { key: 'total_est', label: 'Estimated', color: colors.ink3 },
              { key: 'real', label: 'Provider / corrected', color: colors.accent.ink },
            ]}
          />
        </ChartCard>

        <ChartCard title="Request context attribution" subtitle="What occupied each provider request">
          <FixedBarChart
            points={attribution}
            mode="stacked"
            yAxisLabel="tokens"
            valueFormatter={formatTokens}
            series={[
              { key: 'system', label: 'System', color: colors.ink3 },
              { key: 'user', label: 'User', color: colors.running.ink },
              { key: 'assistant', label: 'Assistant', color: colors.accent.ink },
              { key: 'tool_calls', label: 'Tool calls', color: colors.attention.ink },
              { key: 'tool_results', label: 'Tool results', color: colors.danger.ink },
              { key: 'tool_schemas', label: 'Schemas', color: colors.ok.ink },
            ]}
          />
        </ChartCard>

        <ChartCard title="Token breakdown" subtitle="Input, output, cached, and reasoning tokens">
          <FixedBarChart
            points={tokens}
            mode="stacked"
            yAxisLabel="tokens"
            valueFormatter={formatTokens}
            series={[
              { key: 'in', label: 'Input', color: colors.accent.ink },
              { key: 'out', label: 'Output', color: colors.running.ink },
              { key: 'cached', label: 'Cached', color: colors.ok.ink },
              { key: 'reasoning', label: 'Reasoning', color: colors.attention.ink },
            ]}
          />
        </ChartCard>

        <ChartCard title="Provider latency" subtitle="Wall time for each agent iteration">
          <ValueSummary points={latency} valueKey="wall_ms" formatter={formatDuration} />
          <FixedBarChart
            points={latency}
            mode="single"
            yAxisLabel="latency"
            valueFormatter={formatDuration}
            series={[{ key: 'wall_ms', label: 'Wall time', color: colors.accent.ink }]}
          />
        </ChartCard>

        <ChartCard title="Tool output efficiency" subtitle="Raw output compared with tokens injected into context">
          <FixedBarChart
            points={efficiency}
            mode="grouped"
            yAxisLabel="tokens"
            valueFormatter={formatTokens}
            series={[
              { key: 'raw_tokens', label: 'Raw', color: colors.ink3 },
              { key: 'injected_tokens', label: 'Injected', color: colors.ok.ink },
            ]}
          />
        </ChartCard>

        <ChartCard title="Tool activity" subtitle="Call volume and average latency by tool">
          <HorizontalBars
            items={tools.slice(0, 12)}
            valueKey="count"
            labelKey="name"
            detail={item => `${formatDuration(numberValue(item.avg_latency_ms))} avg · ${formatPercent(numberValue(item.cache_hit_rate))} cache`}
          />
        </ChartCard>

        <ChartCard title="Compactions and nudges" subtitle="Context recovery events across the session">
          <EventTimeline groups={[
            { label: 'Compaction', items: compactions, color: colors.attention.ink, kindKey: 'kind' },
            { label: 'Nudge', items: nudges, color: colors.running.ink, kindKey: 'kind' },
          ]} />
        </ChartCard>

        <ChartCard title="Memory and subagents" subtitle="State retained by the harness per iteration">
          <FixedBarChart
            points={mergeByIteration(memory, subagents)}
            mode="stacked"
            yAxisLabel="items"
            valueFormatter={formatInteger}
            axisMinStep={1}
            series={[
              { key: 'task_memory_count', label: 'Task memory', color: colors.accent.ink },
              { key: 'scratchpad_count', label: 'Scratchpad', color: colors.running.ink },
              { key: 'active', label: 'Subagents', color: colors.ok.ink },
              { key: 'stuck', label: 'Stuck', color: colors.danger.ink },
            ]}
          />
        </ChartCard>

        <ChartCard title="Largest context spikes" subtitle="Requests with the greatest iteration-to-iteration growth">
          <RankedList
            items={spikes.slice(0, 8)}
            title={item => `Iteration ${formatInteger(numberValue(item.iter))} · ${String(item.growth_source || 'other')}`}
            value={item => `+${formatTokens(numberValue(item.delta))}`}
            detail={item => {
              const largest = item.largest_item as Record<string, unknown> | undefined;
              return largest?.label ? `${String(largest.label)} · ${formatTokens(numberValue(largest.tokens))}` : 'No individual item metadata';
            }}
          />
        </ChartCard>

        <ChartCard title="Redundant reads" subtitle="Files read again without an intervening write">
          {redundantReads.length === 0 ? (
            <Text variant="sm" dim style={styles.emptyChart}>No redundant reads detected.</Text>
          ) : (
            <RankedList
              items={redundantReads.slice(0, 12)}
              title={item => String(item.path || 'Unknown path')}
              value={item => `iter ${formatInteger(numberValue(item.iter))}`}
              detail={item => `${String(item.tool || 'read')} · ${formatInteger(numberValue(item.gap))} iteration gap`}
            />
          )}
        </ChartCard>
      </ScrollView>
    </SafeAreaView>
  );
}

function ScopeChip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      style={[
        styles.scopeChip,
        { backgroundColor: active ? colors.accent.wash : 'transparent', borderColor: active ? colors.accent.wash : colors.hairline },
      ]}
    >
      <Text variant="xs" style={{ color: active ? colors.accent.ink : colors.ink2, fontWeight: active ? fontWeights.semibold : fontWeights.regular }}>{label}</Text>
    </Pressable>
  );
}

function MetricGrid({ metrics }: { metrics: Array<{ label: string; value: string; detail: string }> }) {
  const { colors } = useTheme();
  return (
    <View style={styles.metricGrid}>
      {metrics.map(metric => (
        <Card key={metric.label} style={styles.metricCard}>
          <Text variant="xs" dim>{metric.label}</Text>
          <Text style={[styles.metricValue, { color: colors.ink }]}>{metric.value}</Text>
          <Text variant="xs" dim style={styles.num}>{metric.detail}</Text>
        </Card>
      ))}
    </View>
  );
}

function ChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <Card style={styles.chartCard}>
      <Text variant="base" style={styles.chartTitle}>{title}</Text>
      <Text variant="xs" dim style={styles.chartSubtitle}>{subtitle}</Text>
      {children}
    </Card>
  );
}

function Legend({ series }: { series: ChartSeries[] }) {
  return (
    <View style={styles.legend}>
      {series.map(item => (
        <View key={item.key} style={styles.legendItem}>
          <View style={[styles.legendDot, { backgroundColor: item.color }]} />
          <Text variant="xs" dim>{item.label}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * The Y-axis is outside the horizontal ScrollView, so its values remain
 * visible while the user pans through long traces. Grid lines scroll with the
 * columns and use the same scale as the fixed labels.
 */
function FixedBarChart({
  points,
  series,
  mode,
  yAxisLabel,
  valueFormatter = formatInteger,
  axisMinStep = 0,
}: {
  points: NumericPoint[];
  series: ChartSeries[];
  mode: BarMode;
  yAxisLabel: string;
  valueFormatter?: (value: number) => string;
  axisMinStep?: number;
}) {
  const { colors } = useTheme();
  const visible = points.slice(-MAX_VISIBLE_POINTS);
  if (!visible.length) return <Text variant="sm" dim style={styles.emptyChart}>No data recorded.</Text>;

  const totals = visible.map(point => (
    mode === 'stacked'
      ? series.reduce((sum, item) => sum + numberValue(point[item.key]), 0)
      : Math.max(...series.map(item => numberValue(point[item.key])), 0)
  ));
  const scale = buildAxisScale(Math.max(0, ...totals), axisMinStep);
  const labelEvery = Math.max(1, Math.ceil(visible.length / 9));
  const columnWidth = mode === 'grouped' ? Math.max(18, series.length * 8 + 6) : 14;

  return (
    <View>
      <Legend series={series} />
      <View style={styles.chartFrame}>
        <View style={[styles.yAxis, { width: Y_AXIS_WIDTH }]}>
          <View style={styles.yAxisTicks}>
            {scale.ticks.map(value => (
              <Text
                key={`axis-${value}`}
                style={[styles.yAxisTick, { color: colors.ink3 }]}
                numberOfLines={1}
              >
                {valueFormatter(value)}
              </Text>
            ))}
          </View>
          <View style={styles.yAxisUnitLane}>
            <Text style={[styles.yAxisUnit, { color: colors.ink3 }]} numberOfLines={1}>
              {yAxisLabel}
            </Text>
          </View>
        </View>

        <ScrollView
          horizontal
          style={styles.chartViewport}
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chartScroll}
        >
          <View pointerEvents="none" style={styles.gridOverlay}>
            {scale.ticks.map(value => (
              <View
                key={`grid-${value}`}
                style={[
                  styles.gridLine,
                  {
                    top: Math.min(
                      PLOT_HEIGHT - 1,
                      Math.max(0, (1 - value / scale.max) * PLOT_HEIGHT),
                    ),
                    borderTopColor: colors.hairline,
                  },
                ]}
              />
            ))}
          </View>

          {visible.map((point, index) => {
            const showLabel = index % labelEvery === 0 || index === visible.length - 1;
            return (
              <View key={`${String(point.iter)}-${index}`} style={[styles.chartColumn, { width: columnWidth }]}>
                <View style={[styles.plotLane, { borderBottomColor: colors.hairline }]}>
                  {mode === 'stacked' ? (
                    <View style={styles.stackedBar}>
                      {series.map(item => {
                        const height = (numberValue(point[item.key]) / scale.max) * PLOT_HEIGHT;
                        return height > 0 ? (
                          <View key={item.key} style={{ width: 9, height: Math.max(1, height), backgroundColor: item.color }} />
                        ) : null;
                      })}
                    </View>
                  ) : (
                    <View style={styles.groupedBars}>
                      {series.map(item => (
                        <View
                          key={item.key}
                          style={[
                            styles.verticalBar,
                            {
                              width: mode === 'single' ? 8 : 6,
                              backgroundColor: item.color,
                              height: Math.max(2, (numberValue(point[item.key]) / scale.max) * PLOT_HEIGHT),
                            },
                          ]}
                        />
                      ))}
                    </View>
                  )}
                </View>
                <View style={styles.axisLane}>
                  {showLabel ? <Text style={[styles.axisLabel, { color: colors.ink3 }]}>{formatInteger(numberValue(point.iter))}</Text> : null}
                </View>
              </View>
            );
          })}
        </ScrollView>
      </View>
    </View>
  );
}

function ValueSummary({ points, valueKey, formatter }: { points: NumericPoint[]; valueKey: string; formatter: (value: number) => string }) {
  const visible = points.slice(-MAX_VISIBLE_POINTS);
  const values = visible.map(point => numberValue(point[valueKey]));
  const latest = values.length ? values[values.length - 1] : 0;
  const peak = values.length ? Math.max(...values) : 0;
  return <Text variant="xs" dim style={styles.latestValue}>Latest {formatter(latest)} · Peak {formatter(peak)}</Text>;
}

function HorizontalBars({
  items,
  valueKey,
  labelKey,
  detail,
}: {
  items: NumericPoint[];
  valueKey: string;
  labelKey: string;
  detail: (item: NumericPoint) => string;
}) {
  const { colors } = useTheme();
  const max = Math.max(1, ...items.map(item => numberValue(item[valueKey])));
  if (!items.length) return <Text variant="sm" dim style={styles.emptyChart}>No tool calls recorded.</Text>;
  return (
    <View style={styles.horizontalList}>
      {items.map((item, index) => (
        <View key={`${String(item[labelKey])}-${index}`} style={styles.horizontalItem}>
          <View style={styles.horizontalHeader}>
            <Text variant="sm" style={styles.horizontalLabel} numberOfLines={1}>{String(item[labelKey])}</Text>
            <Text variant="xs" dim>{formatInteger(numberValue(item[valueKey]))}</Text>
          </View>
          <View style={[styles.horizontalTrack, { backgroundColor: colors.sunken }]}>
            <View style={[styles.horizontalFill, { backgroundColor: colors.accent.ink, width: `${Math.max(3, (numberValue(item[valueKey]) / max) * 100)}%` }]} />
          </View>
          <Text variant="xs" dim>{detail(item)}</Text>
        </View>
      ))}
    </View>
  );
}

function EventTimeline({ groups }: { groups: Array<{ label: string; items: NumericPoint[]; color: string; kindKey: string }> }) {
  const { colors } = useTheme();
  if (!groups.some(group => group.items.length > 0)) {
    return <Text variant="sm" dim style={styles.emptyChart}>No compactions or nudges recorded.</Text>;
  }
  return (
    <View style={styles.timeline}>
      {groups.map(group => (
        <View key={group.label} style={styles.timelineGroup}>
          <Text variant="xs" dim style={styles.timelineLabel}>{group.label}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.timelineItems}>
            {/* Round-44 F12: newest-window slice — the pill rail is a flat
                .map inside a ScrollView (renders all children); long sessions
                with thousands of compactions/nudges would mount them all. */}
            {group.items.slice(-MAX_EVENT_PILLS).map((item, index) => (
              <View key={`${group.label}-${index}`} style={[styles.eventPill, { backgroundColor: colors.sunken }]}>
                <View style={[styles.eventDot, { backgroundColor: group.color }]} />
                <Text variant="xs" style={styles.num}>{String(item[group.kindKey] || group.label)} · {formatInteger(numberValue(item.iter))}</Text>
              </View>
            ))}
          </ScrollView>
        </View>
      ))}
    </View>
  );
}

function RankedList({
  items,
  title,
  value,
  detail,
}: {
  items: NumericPoint[];
  title: (item: NumericPoint) => string;
  value: (item: NumericPoint) => string;
  detail: (item: NumericPoint) => string;
}) {
  const { colors } = useTheme();
  if (!items.length) return <Text variant="sm" dim style={styles.emptyChart}>No events recorded.</Text>;
  return (
    <View>
      {items.map((item, index) => (
        <View key={`${title(item)}-${index}`} style={styles.rankRow}>
          <View style={styles.rankCopy}>
            <Text variant="sm" style={styles.rankTitle} numberOfLines={1}>{title(item)}</Text>
            <Text variant="xs" dim numberOfLines={2}>{detail(item)}</Text>
          </View>
          <Text variant="xs" style={{ color: colors.ink2, fontVariant: ['tabular-nums'] }}>{value(item)}</Text>
        </View>
      ))}
    </View>
  );
}

function mergeByIteration(primary: NumericPoint[], secondary: NumericPoint[]): NumericPoint[] {
  const merged = new Map<number, NumericPoint>();
  [...primary, ...secondary].forEach(point => {
    const iter = numberValue(point.iter);
    merged.set(iter, { ...(merged.get(iter) || { iter }), ...point });
  });
  return [...merged.values()].sort((a, b) => numberValue(a.iter) - numberValue(b.iter));
}

type AxisScale = { max: number; ticks: number[] };

function buildAxisScale(maxValue: number, minimumStep = 0): AxisScale {
  const safeMax = Math.max(0, Number.isFinite(maxValue) ? maxValue : 0);
  if (safeMax === 0) {
    const fallback = Math.max(1, minimumStep);
    return { max: fallback, ticks: [fallback, 0] };
  }

  const rawStep = safeMax / Y_AXIS_TARGET_TICKS;
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(rawStep, Number.EPSILON)));
  const normalized = rawStep / magnitude;
  const niceFraction = normalized <= 1
    ? 1
    : normalized <= 2
      ? 2
      : normalized <= 2.5
        ? 2.5
        : normalized <= 5
          ? 5
          : 10;
  const step = Math.max(minimumStep, niceFraction * magnitude);
  const axisMax = Math.max(step, Math.ceil(safeMax / step) * step);
  const tickCount = Math.max(1, Math.round(axisMax / step));
  const ticks = Array.from({ length: tickCount + 1 }, (_, index) => {
    const value = axisMax - index * step;
    return Math.abs(value) < step / 1000 ? 0 : value;
  });

  return { max: axisMax, ticks };
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function contextFill(summary: TraceSummary): number {
  if (!summary.context_limit) return 0;
  return Math.max(summary.peak_context, summary.peak_estimated) / summary.context_limit;
}

function formatInteger(value: number): string {
  return Math.round(value).toLocaleString('en-US');
}

function formatTokens(value: number): string {
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return formatInteger(value);
}

function formatDuration(milliseconds: number): string {
  if (milliseconds >= 60_000) return `${(milliseconds / 60_000).toFixed(1)}m`;
  if (milliseconds >= 1_000) return `${(milliseconds / 1_000).toFixed(1)}s`;
  return `${Math.round(milliseconds)}ms`;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  content: { padding: spacing.base, paddingBottom: spacing['2xl'] },
  loadingWrap: { padding: spacing.base },
  loadingBlock: { marginBottom: spacing.sm },
  pageHeader: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: spacing.base },
  pageHeaderCopy: { flex: 1, paddingRight: spacing.md },
  pageTitle: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight },
  statusPill: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, borderRadius: radii.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  statusDot: { width: 8, height: 8, borderRadius: radii.pill },
  num: { fontVariant: ['tabular-nums'] },
  scopeRow: { gap: spacing.sm, paddingBottom: spacing.base },
  scopeChip: { borderWidth: 1, borderRadius: radii.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, minHeight: touchTarget, justifyContent: 'center' },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.xs },
  metricCard: { width: '48%', minHeight: 104 },
  metricValue: { fontSize: fontSizes.lg, lineHeight: typography.lg.lineHeight, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight, marginTop: spacing.xs, marginBottom: 2, fontVariant: ['tabular-nums'] },
  chartCard: { marginTop: spacing.md, paddingVertical: spacing.base },
  chartTitle: { fontWeight: fontWeights.semibold },
  chartSubtitle: { marginTop: 2, marginBottom: spacing.md },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, marginBottom: spacing.sm },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  legendDot: { width: 8, height: 8, borderRadius: radii.pill },
  chartFrame: { width: '100%', flexDirection: 'row', alignItems: 'flex-start' },
  yAxis: { height: PLOT_HEIGHT + AXIS_HEIGHT, paddingRight: spacing.sm },
  yAxisTicks: { height: PLOT_HEIGHT, justifyContent: 'space-between', alignItems: 'flex-end' },
  yAxisTick: { maxWidth: Y_AXIS_WIDTH - 7, fontSize: fontSizes.xs, lineHeight: typography.xs.lineHeight, fontVariant: ['tabular-nums'], textAlign: 'right' },
  yAxisUnitLane: { height: AXIS_HEIGHT, alignItems: 'flex-end', justifyContent: 'flex-start', paddingTop: spacing.xs },
  yAxisUnit: { fontSize: fontSizes.xs, lineHeight: typography.xs.lineHeight },
  chartViewport: { flex: 1 },
  chartScroll: { minWidth: '100%', alignItems: 'flex-start', gap: spacing.xs, position: 'relative' },
  gridOverlay: { position: 'absolute', left: 0, right: 0, top: 0, height: PLOT_HEIGHT },
  gridLine: { position: 'absolute', left: 0, right: 0, borderTopWidth: 1 },
  chartColumn: { height: PLOT_HEIGHT + AXIS_HEIGHT },
  plotLane: { height: PLOT_HEIGHT, justifyContent: 'flex-end', alignItems: 'center', borderBottomWidth: 1 },
  axisLane: { height: AXIS_HEIGHT, alignItems: 'center', justifyContent: 'flex-start', paddingTop: spacing.xs },
  axisLabel: { fontSize: fontSizes.xs, lineHeight: typography.xs.lineHeight, fontVariant: ['tabular-nums'] },
  groupedBars: { height: PLOT_HEIGHT, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'center', gap: 2 },
  verticalBar: {},
  stackedBar: { height: PLOT_HEIGHT, width: 9, justifyContent: 'flex-end', overflow: 'hidden' },
  latestValue: { marginBottom: spacing.xs, fontVariant: ['tabular-nums'] },
  horizontalList: { gap: spacing.md },
  horizontalItem: { minHeight: 48 },
  horizontalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.xs },
  horizontalLabel: { flex: 1, fontWeight: fontWeights.semibold, marginRight: spacing.md },
  horizontalTrack: { height: 8, borderRadius: radii.pill, overflow: 'hidden', marginBottom: spacing.xs },
  horizontalFill: { height: 8, borderRadius: radii.pill },
  timeline: { gap: spacing.md },
  timelineGroup: { gap: spacing.xs },
  timelineLabel: { fontWeight: fontWeights.semibold },
  timelineItems: { gap: spacing.sm },
  eventPill: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, borderRadius: radii.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  eventDot: { width: 8, height: 8, borderRadius: radii.pill },
  rankRow: { minHeight: 64, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.sm },
  rankCopy: { flex: 1, paddingRight: spacing.md },
  rankTitle: { fontWeight: fontWeights.semibold },
  emptyChart: { paddingVertical: spacing.base, textAlign: 'center' },
});
