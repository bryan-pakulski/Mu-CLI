import React, { useCallback, useState } from 'react';
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { tracesApi, TraceRun, TraceSummary } from '../api/traces';
import { useConnectionStore } from '../store/connection';
import { useTheme } from '../theme/ThemeContext';
import { Card, EmptyState, ErrorState, Skeleton, Text } from '../components';
import { spacing } from '../theme/tokens';

type NumericPoint = Record<string, unknown>;

type TraceDashboardData = {
  run_id: string;
  n_runs?: number;
  summary: TraceSummary & {
    efficiency?: Record<string, unknown>;
  };
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

type ChartSeries = {
  key: string;
  label: string;
  color: string;
};

export function SessionTraceScreen() {
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
    } catch (loadError) {
      setError(String(loadError));
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
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
        <View style={styles.loadingWrap}>
          <Skeleton height={96} style={styles.loadingBlock} />
          <Skeleton height={180} style={styles.loadingBlock} />
          <Skeleton height={180} />
        </View>
      </SafeAreaView>
    );
  }

  if (error && !dashboard) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
        <ErrorState message={error} onRetry={() => load(selectedRunId)} />
      </SafeAreaView>
    );
  }

  if (!dashboard) {
    return (
      <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
        <EmptyState
          icon="analytics-outline"
          title="No session traces"
          message="Run an agent turn with tracing enabled to populate session analytics."
        />
      </SafeAreaView>
    );
  }

  const summary = dashboard.summary;
  const series = dashboard.series;
  const context = series.context || [];
  const contextAttribution = series.context_attribution || [];
  const tokens = series.tokens || [];
  const latency = series.latency || [];
  const tools = [...(series.tool_histogram || [])].sort((a, b) => numberValue(b.count) - numberValue(a.count));
  const efficiency = series.efficiency || [];
  const compactions = series.compaction_timeline || [];
  const nudges = series.nudge_timeline || [];
  const redundantReads = series.redundant_reads || [];
  const memory = series.memory_series || [];
  const subagents = series.subagent_timeline || [];
  const spikes = series.top_context_spikes || [];

  return (
    <SafeAreaView edges={['bottom']} style={[styles.safeArea, { backgroundColor: colors.bg }]}>
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
          <View style={[styles.statusPill, { backgroundColor: summary.status === 'completed' ? colors.accentSoft : colors.bgHover }]}>
            <View style={[styles.statusDot, { backgroundColor: summary.status === 'completed' ? colors.success : colors.warning }]} />
            <Text variant="xs">{summary.status}</Text>
          </View>
        </View>

        {runs.length > 1 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scopeRow}>
            {activeSessionName && (
              <ScopeChip
                label={`Session · ${runs.length} runs`}
                active={!selectedRunId}
                onPress={() => { setLoading(true); load(null); }}
              />
            )}
            {runs.map(run => (
              <ScopeChip
                key={run.run_id}
                label={`${run.mode} · ${run.iters} iters`}
                active={selectedRunId === run.run_id}
                onPress={() => { setLoading(true); load(run.run_id); }}
              />
            ))}
          </ScrollView>
        )}

        <MetricGrid
          metrics={[
            { label: 'Iterations', value: formatInteger(summary.iters), detail: `${summary.tool_calls} tool calls` },
            { label: 'Context peak', value: formatTokens(Math.max(summary.peak_context, summary.peak_estimated)), detail: `${formatPercent(contextFill(summary))} of window` },
            { label: 'Input tokens', value: formatTokens(summary.total_in), detail: `${formatTokens(summary.total_out)} output` },
            { label: 'Wall time', value: formatDuration(summary.total_wall_ms), detail: `${formatDuration(summary.mean_wall_ms)} mean` },
            { label: 'Compactions', value: formatInteger(summary.compaction_count), detail: `${summary.mechanical_fallback_count} mechanical` },
            { label: 'Redundant reads', value: formatInteger(summary.redundant_reads), detail: `${summary.nudge_count} nudges` },
          ]}
        />

        <ChartCard title="Context growth" subtitle="Estimated and drift-corrected provider context per iteration">
          <GroupedBarChart
            points={context}
            series={[
              { key: 'total_est', label: 'Estimated', color: colors.textDim },
              { key: 'real', label: 'Provider / corrected', color: colors.accent },
            ]}
          />
        </ChartCard>

        <ChartCard title="Request context attribution" subtitle="What occupied each provider request">
          <StackedBarChart
            points={contextAttribution}
            series={[
              { key: 'system', label: 'System', color: colors.textDim },
              { key: 'user', label: 'User', color: colors.info },
              { key: 'assistant', label: 'Assistant', color: colors.accent },
              { key: 'tool_calls', label: 'Tool calls', color: colors.warning },
              { key: 'tool_results', label: 'Tool results', color: colors.error },
              { key: 'tool_schemas', label: 'Schemas', color: colors.success },
            ]}
          />
        </ChartCard>

        <ChartCard title="Token breakdown" subtitle="Input, output, cached, and reasoning tokens">
          <StackedBarChart
            points={tokens}
            series={[
              { key: 'in', label: 'Input', color: colors.accent },
              { key: 'out', label: 'Output', color: colors.info },
              { key: 'cached', label: 'Cached', color: colors.success },
              { key: 'reasoning', label: 'Reasoning', color: colors.warning },
            ]}
          />
        </ChartCard>

        <ChartCard title="Provider latency" subtitle="Wall time for each agent iteration">
          <SingleBarChart points={latency} valueKey="wall_ms" color={colors.accent} valueFormatter={formatDuration} />
        </ChartCard>

        <ChartCard title="Tool output efficiency" subtitle="Raw tool output compared with tokens injected into context">
          <GroupedBarChart
            points={efficiency}
            series={[
              { key: 'raw_tokens', label: 'Raw', color: colors.textDim },
              { key: 'injected_tokens', label: 'Injected', color: colors.success },
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
          <EventTimeline
            groups={[
              { label: 'Compaction', items: compactions, color: colors.warning, kindKey: 'kind' },
              { label: 'Nudge', items: nudges, color: colors.info, kindKey: 'kind' },
            ]}
          />
        </ChartCard>

        <ChartCard title="Memory and subagents" subtitle="State retained by the harness per iteration">
          <StackedBarChart
            points={mergeByIteration(memory, subagents)}
            series={[
              { key: 'task_memory_count', label: 'Task memory', color: colors.accent },
              { key: 'scratchpad_count', label: 'Scratchpad', color: colors.info },
              { key: 'active', label: 'Subagents', color: colors.success },
              { key: 'stuck', label: 'Stuck', color: colors.error },
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
    <TouchableOpacity
      onPress={onPress}
      style={[
        styles.scopeChip,
        { backgroundColor: active ? colors.accent : colors.bgLift, borderColor: active ? colors.accent : colors.border },
      ]}
    >
      <Text variant="xs" style={{ color: active ? colors.accentText : colors.text }}>{label}</Text>
    </TouchableOpacity>
  );
}

function MetricGrid({ metrics }: { metrics: { label: string; value: string; detail: string }[] }) {
  const { colors } = useTheme();
  return (
    <View style={styles.metricGrid}>
      {metrics.map(metric => (
        <Card key={metric.label} style={styles.metricCard}>
          <Text variant="xs" dim>{metric.label}</Text>
          <Text style={[styles.metricValue, { color: colors.text }]}>{metric.value}</Text>
          <Text variant="xs" dim>{metric.detail}</Text>
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

function GroupedBarChart({ points, series }: { points: NumericPoint[]; series: ChartSeries[] }) {
  const visible = points.slice(-72);
  const max = Math.max(1, ...visible.flatMap(point => series.map(item => numberValue(point[item.key]))));
  return (
    <View>
      <Legend series={series} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chartScroll}>
        {visible.map((point, index) => (
          <View key={`${String(point.iter)}-${index}`} style={styles.groupColumn}>
            <View style={styles.groupBars}>
              {series.map(item => (
                <View
                  key={item.key}
                  style={[
                    styles.groupBar,
                    { backgroundColor: item.color, height: Math.max(2, (numberValue(point[item.key]) / max) * 108) },
                  ]}
                />
              ))}
            </View>
            {index % 8 === 0 && <Text variant="xs" dim>{formatInteger(numberValue(point.iter))}</Text>}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function StackedBarChart({ points, series }: { points: NumericPoint[]; series: ChartSeries[] }) {
  const visible = points.slice(-72);
  const totals = visible.map(point => series.reduce((sum, item) => sum + numberValue(point[item.key]), 0));
  const max = Math.max(1, ...totals);
  return (
    <View>
      <Legend series={series} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chartScroll}>
        {visible.map((point, index) => (
          <View key={`${String(point.iter)}-${index}`} style={styles.stackColumn}>
            <View style={styles.stackBarArea}>
              {series.map(item => {
                const height = (numberValue(point[item.key]) / max) * 112;
                return height > 0 ? <View key={item.key} style={{ width: 9, height: Math.max(1, height), backgroundColor: item.color }} /> : null;
              })}
            </View>
            {index % 8 === 0 && <Text variant="xs" dim>{formatInteger(numberValue(point.iter))}</Text>}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function SingleBarChart({
  points,
  valueKey,
  color,
  valueFormatter,
}: {
  points: NumericPoint[];
  valueKey: string;
  color: string;
  valueFormatter: (value: number) => string;
}) {
  const visible = points.slice(-72);
  const max = Math.max(1, ...visible.map(point => numberValue(point[valueKey])));
  const latest = visible.length ? numberValue(visible[visible.length - 1][valueKey]) : 0;
  return (
    <View>
      <Text variant="xs" dim style={styles.latestValue}>Latest {valueFormatter(latest)} · Peak {valueFormatter(max)}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chartScroll}>
        {visible.map((point, index) => (
          <View key={`${String(point.iter)}-${index}`} style={styles.singleColumn}>
            <View style={[styles.singleBar, { backgroundColor: color, height: Math.max(2, (numberValue(point[valueKey]) / max) * 108) }]} />
            {index % 8 === 0 && <Text variant="xs" dim>{formatInteger(numberValue(point.iter))}</Text>}
          </View>
        ))}
      </ScrollView>
    </View>
  );
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
          <View style={[styles.horizontalTrack, { backgroundColor: colors.bgHover }]}>
            <View style={[styles.horizontalFill, { backgroundColor: colors.accent, width: `${Math.max(3, (numberValue(item[valueKey]) / max) * 100)}%` }]} />
          </View>
          <Text variant="xs" dim>{detail(item)}</Text>
        </View>
      ))}
    </View>
  );
}

function EventTimeline({ groups }: { groups: { label: string; items: NumericPoint[]; color: string; kindKey: string }[] }) {
  const { colors } = useTheme();
  const hasEvents = groups.some(group => group.items.length > 0);
  if (!hasEvents) return <Text variant="sm" dim style={styles.emptyChart}>No compactions or nudges recorded.</Text>;
  return (
    <View style={styles.timeline}>
      {groups.map(group => (
        <View key={group.label} style={styles.timelineGroup}>
          <Text variant="xs" dim style={styles.timelineLabel}>{group.label}</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.timelineItems}>
            {group.items.map((item, index) => (
              <View key={`${group.label}-${index}`} style={[styles.eventPill, { backgroundColor: colors.bgHover }]}>
                <View style={[styles.eventDot, { backgroundColor: group.color }]} />
                <Text variant="xs">{String(item[group.kindKey] || group.label)} · {formatInteger(numberValue(item.iter))}</Text>
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
        <View key={`${title(item)}-${index}`} style={[styles.rankRow, index > 0 && { borderTopColor: colors.border, borderTopWidth: StyleSheet.hairlineWidth }]}>
          <View style={styles.rankCopy}>
            <Text variant="sm" style={styles.rankTitle} numberOfLines={1}>{title(item)}</Text>
            <Text variant="xs" dim numberOfLines={2}>{detail(item)}</Text>
          </View>
          <Text variant="xs" style={{ color: colors.accent, fontVariant: ['tabular-nums'] }}>{value(item)}</Text>
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
  content: { padding: spacing.base, paddingBottom: 48 },
  loadingWrap: { padding: spacing.base },
  loadingBlock: { marginBottom: spacing.sm },
  pageHeader: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 16 },
  pageHeaderCopy: { flex: 1, paddingRight: 12 },
  pageTitle: { fontWeight: '700', letterSpacing: -0.5 },
  statusPill: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7 },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  scopeRow: { gap: 8, paddingBottom: 16 },
  scopeChip: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginBottom: 4 },
  metricCard: { width: '48%', minHeight: 104 },
  metricValue: { fontSize: 22, lineHeight: 30, fontWeight: '700', letterSpacing: -0.5, marginTop: 5, marginBottom: 2, fontVariant: ['tabular-nums'] },
  chartCard: { marginTop: 12, paddingVertical: 18 },
  chartTitle: { fontWeight: '700' },
  chartSubtitle: { marginTop: 2, marginBottom: 14 },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginBottom: 10 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  legendDot: { width: 7, height: 7, borderRadius: 4 },
  chartScroll: { minWidth: '100%', alignItems: 'flex-end', paddingTop: 4, paddingBottom: 2, gap: 4 },
  groupColumn: { width: 18, height: 136, justifyContent: 'flex-end', alignItems: 'center' },
  groupBars: { height: 112, flexDirection: 'row', alignItems: 'flex-end', gap: 2 },
  groupBar: { width: 6, borderTopLeftRadius: 2, borderTopRightRadius: 2 },
  stackColumn: { width: 13, height: 136, justifyContent: 'flex-end', alignItems: 'center' },
  stackBarArea: { height: 112, width: 9, justifyContent: 'flex-end', overflow: 'hidden', borderRadius: 3 },
  singleColumn: { width: 11, height: 136, justifyContent: 'flex-end', alignItems: 'center' },
  singleBar: { width: 8, borderTopLeftRadius: 3, borderTopRightRadius: 3 },
  latestValue: { marginBottom: 4, fontVariant: ['tabular-nums'] },
  horizontalList: { gap: 13 },
  horizontalItem: { minHeight: 48 },
  horizontalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 },
  horizontalLabel: { flex: 1, fontWeight: '600', marginRight: 12 },
  horizontalTrack: { height: 7, borderRadius: 4, overflow: 'hidden', marginBottom: 4 },
  horizontalFill: { height: 7, borderRadius: 4 },
  timeline: { gap: 14 },
  timelineGroup: { gap: 6 },
  timelineLabel: { fontWeight: '600' },
  timelineItems: { gap: 7 },
  eventPill: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 7 },
  eventDot: { width: 7, height: 7, borderRadius: 4 },
  rankRow: { minHeight: 62, flexDirection: 'row', alignItems: 'center', paddingVertical: 10 },
  rankCopy: { flex: 1, paddingRight: 12 },
  rankTitle: { fontWeight: '600', marginBottom: 2 },
  emptyChart: { paddingVertical: 18, textAlign: 'center' },
});
