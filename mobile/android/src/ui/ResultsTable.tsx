// Fleet results as one sortable table across nodes (web: mu/gui/static/js/orchestration/results.js,
// server: mu/orchestration/results.py). Takes a run's results ({steps}) or a tool's latest results
// ({tables}); a table has columns from the tool's output schema, a key metric and an order. Sort by
// any column (blanks always last), filter, switch step and view, show as a table or a bar chart of
// the key metric, and share the view as CSV. Values come from nodes: shown as text, never markup.

import React, { useMemo, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { spacing } from '../theme/tokens';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Button } from './Button';
import { Segmented } from './Tabs';
import { TextField } from './Form';
import { Meter } from './Progress';

export type ResultColumn = { key: string; label: string; kind: string; format: string };
export type ResultRow = { exec_id: string; run_id?: string; node: string; node_id?: string; state: string; labels?: Record<string, string>; values: Record<string, unknown> };
export type ResultTable = { key: string; label: string; per: string; columns: ResultColumn[]; metric: string | null; order: string; rows: ResultRow[] };
export type ResultsData = { steps?: Array<{ step: number; step_name: string; tool: string; tables: ResultTable[] }>; tables?: ResultTable[] };

export const CHART_MAX = 24;
const NODE = '__node';

export function formatBytes(n: unknown): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = Math.abs(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${n < 0 ? '−' : ''}${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

function num(n: number): string {
  return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Tone for a value: percentages fill up, days run out. */
export function cellTone(col: ResultColumn, v: unknown): 'danger' | 'attention' | '' {
  if (typeof v !== 'number') return '';
  if (col.format === 'percent') return v >= 90 ? 'danger' : v >= 75 ? 'attention' : '';
  if (col.format === 'days') return v <= 14 ? 'danger' : v <= 30 ? 'attention' : '';
  return '';
}

/** A value as text, per the column's format (as the web shows it). */
export function formatCell(col: ResultColumn, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  switch (col.format) {
    case 'bytes': return formatBytes(v);
    case 'percent': return typeof v === 'number' ? `${num(v)}%` : String(v);
    case 'days': return typeof v === 'number' ? `${num(v)} ${Math.abs(v) === 1 ? 'day' : 'days'}` : String(v);
    case 'seconds': return typeof v === 'number' ? `${num(v)} s` : String(v);
    case 'bool': return v ? 'yes' : 'no';
    case 'number': return typeof v === 'number' ? num(v) : String(v);
    default: return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }
}

const numeric = (col: ResultColumn | undefined) => !!col && (col.kind === 'integer' || col.kind === 'number');

export type SortState = { sort: string; dir: 'asc' | 'desc' };

/** Rows sorted by the chosen column (else the table's metric and order); blanks last either way. */
export function sortRows(table: ResultTable, st: SortState): ResultRow[] {
  const key = st.sort || table.metric;
  const dir = st.sort ? st.dir : (table.order === 'desc' ? 'desc' : 'asc');
  const rows = table.rows.slice();
  if (!key) return rows;
  const val = (r: ResultRow) => (key === NODE ? r.node : r.values[key]);
  rows.sort((a, b) => {
    const x = val(a);
    const y = val(b);
    const xm = x === null || x === undefined || x === '';
    const ym = y === null || y === undefined || y === '';
    if (xm || ym) return xm === ym ? 0 : xm ? 1 : -1;
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
    return dir === 'desc' ? -c : c;
  });
  return rows;
}

/** Rows whose node, state, labels or values contain the text. */
export function filterRows(rows: ResultRow[], q: string): ResultRow[] {
  const needle = String(q || '').trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter(r => [r.node, r.state, ...Object.entries(r.labels || {}).map(([k, v]) => `${k}=${v}`), ...Object.values(r.values)]
    .some(v => v !== null && v !== undefined && String(v).toLowerCase().includes(needle)));
}

/** The next sort after tapping a column: toggle the current one; a new column starts descending (node ascending). */
export function nextSort(table: ResultTable, st: SortState, key: string): SortState {
  const current = st.sort || table.metric;
  const currentDir = st.sort ? st.dir : (table.order === 'desc' ? 'desc' : 'asc');
  return { sort: key, dir: current === key ? (currentDir === 'asc' ? 'desc' : 'asc') : (key === NODE ? 'asc' : 'desc') };
}

export type ResultsTableProps = {
  data: ResultsData | null;
  empty?: string;
  /** Share the current view as CSV (the caller fetches results.csv). */
  onShareCsv?: (step: number, view: string) => void;
  /** Open a row's execution. */
  onRow?: (row: ResultRow) => void;
  /** "Ask about this": hand the current view to a chat (the AI reads it with fleet_results). */
  onAsk?: (view: AskContext) => void;
  testID?: string;
};

export type AskContext = { view: string; label: string; step: number | null; filter: string };

export function ResultsTable({ data, empty = 'No results yet.', onShareCsv, onRow, onAsk, testID = 'results' }: ResultsTableProps) {
  const { colors } = useTheme();
  const [step, setStep] = useState<number | null>(null);
  const [view, setView] = useState('summary');
  const [st, setSt] = useState<SortState>({ sort: '', dir: 'asc' });
  const [mode, setMode] = useState<'table' | 'chart'>('table');
  const [q, setQ] = useState('');

  const steps = data?.steps || null;
  const current = steps ? (steps.find(s => s.step === step) || steps[steps.length - 1]) : null;
  const tables = steps ? (current?.tables || []) : (data?.tables || []);
  const table = tables.find(t => t.key === view) || tables[0];
  const rows = useMemo(() => (table ? filterRows(sortRows(table, st), q) : []), [table, st, q]);
  const anyRows = tables.some(t => t.rows.some(r => Object.values(r.values).some(v => v !== null && v !== undefined)));

  if (!table || !anyRows) return <Text testID={testID} variant="caption" tone="ink3">{empty}</Text>;

  const sortKey = st.sort || table.metric;
  const dir = st.sort ? st.dir : (table.order === 'desc' ? 'desc' : 'asc');
  const showState = table.rows.some(r => r.state !== 'succeeded');
  const withOut = table.per === 'node' ? table.rows.filter(r => Object.values(r.values).some(v => v !== null && v !== undefined)).length : table.rows.length;
  const sortLabel = sortKey === NODE ? 'node' : (table.columns.find(c => c.key === sortKey)?.label || sortKey || 'node');
  const pick = (k: string) => setSt(prev => nextSort(table, prev, k));

  const header = (key: string, label: string, isNum: boolean) => {
    const on = sortKey === key;
    return (
      <Pressable key={key} onPress={() => pick(key)} testID={`${testID}-sort-${key}`} accessibilityRole="button" scale={false}
        accessibilityLabel={`Sort by ${label}`} accessibilityState={{ selected: on }} accessibilityHint={on ? (dir === 'desc' ? 'sorted descending' : 'sorted ascending') : undefined}
        style={[styles.cell, isNum ? styles.num : null]}>
        <Text variant="label" style={{ color: on ? colors.ink : colors.ink2 }}>{label}{on ? (dir === 'desc' ? ' ↓' : ' ↑') : ''}</Text>
      </Pressable>
    );
  };

  return (
    <View testID={testID} style={styles.wrap}>
      {steps && steps.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {steps.map(s => (
            <Button key={s.step} size="md" variant={s.step === current?.step ? 'tonal' : 'ghost'} selected={s.step === current?.step}
              label={`${s.step_name || `Step ${s.step + 1}`} · ${s.tool}`} onPress={() => { setStep(s.step); setView('summary'); setSt({ sort: '', dir: 'asc' }); }}
              testID={`${testID}-step-${s.step}`} />
          ))}
        </ScrollView>
      ) : null}
      {tables.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
          {tables.map(t => (
            <Button key={t.key} size="md" variant={t.key === table.key ? 'tonal' : 'ghost'} selected={t.key === table.key}
              label={`${t.label} ${t.rows.length}`} onPress={() => { setView(t.key); setSt({ sort: '', dir: 'asc' }); }} testID={`${testID}-view-${t.key}`} />
          ))}
        </ScrollView>
      ) : null}
      <View style={styles.bar}>
        <Segmented<'table' | 'chart'> accessibilityLabel="Show as" value={mode} onChange={setMode}
          options={[{ value: 'table', label: 'Table', testID: `${testID}-mode-table` }, { value: 'chart', label: 'Chart', testID: `${testID}-mode-chart` }]} />
        <View style={{ flex: 1 }} />
        {onAsk ? <Button size="md" variant="ghost" icon="message-circle" label="Ask about this" accessibilityHint="Opens the fleet chat with a question about these results (not sent)"
          onPress={() => onAsk({ view: table.key, label: table.label || table.key, step: current?.step ?? null, filter: q.trim() })} testID={`${testID}-ask`} /> : null}
        {onShareCsv ? <Button size="md" variant="ghost" icon="download" label="CSV" onPress={() => onShareCsv(current?.step ?? 0, table.key)} testID={`${testID}-csv`} /> : null}
      </View>
      {table.rows.length > 8 ? (
        <TextField value={q} onChangeText={setQ} placeholder="Filter" accessibilityLabel="Filter results" autoCapitalize="none" autoCorrect={false} testID={`${testID}-filter`} />
      ) : null}
      <Text variant="caption" tone="ink3" accessibilityLiveRegion="polite" testID={`${testID}-meta`}>
        {table.per === 'item'
          ? `${rows.length} ${table.label.toLowerCase()} across ${new Set(table.rows.map(r => r.node_id || r.node)).size} nodes`
          : `${withOut} of ${table.rows.length} nodes reported`}{q ? ` · ${rows.length} match` : ''} · sorted by {sortLabel}
      </Text>
      {mode === 'chart' ? <Chart table={table} rows={rows} st={st} testID={testID} /> : (
        <ScrollView horizontal showsHorizontalScrollIndicator testID={`${testID}-table`}>
          <View>
            <View style={[styles.tr, { borderBottomColor: colors.hairlineStrong }]}>
              {header(NODE, 'Node', false)}
              {showState ? <View style={styles.cell}><Text variant="label" tone="ink2">State</Text></View> : null}
              {table.columns.map(c => header(c.key, c.label, numeric(c)))}
            </View>
            {rows.map(r => (
              <View key={r.exec_id} style={[styles.tr, { borderBottomColor: colors.hairline }]} testID={`${testID}-row-${r.exec_id}`}>
                <Pressable onPress={onRow ? () => onRow(r) : undefined} disabled={!onRow} scale={false} style={styles.cell}
                  accessibilityRole={onRow ? 'link' : undefined} accessibilityLabel={`${r.node}${onRow ? ', open execution' : ''}`}>
                  <Text variant="body" style={{ color: onRow ? colors.accent.ink : colors.ink }}>{r.node}</Text>
                </Pressable>
                {showState ? (
                  <View style={styles.cell}><Text variant="caption" tone={r.state === 'succeeded' ? 'ink3' : r.state === 'failed' ? 'danger' : 'attention'}>{r.state === 'succeeded' ? 'ok' : r.state.replace(/_/g, ' ')}</Text></View>
                ) : null}
                {table.columns.map(c => {
                  const v = r.values[c.key];
                  const tone = cellTone(c, v);
                  return (
                    <View key={c.key} style={[styles.cell, numeric(c) ? styles.num : null]}>
                      <Text variant={numeric(c) ? 'mono' : 'body'} selectable tone={tone || (v == null || v === '' ? 'ink3' : 'ink')}>{formatCell(c, v)}</Text>
                    </View>
                  );
                })}
              </View>
            ))}
          </View>
        </ScrollView>
      )}
    </View>
  );
}

/** One bar per row for the chart column: the sort column when numeric, else the key metric. */
function Chart({ table, rows, st, testID }: { table: ResultTable; rows: ResultRow[]; st: SortState; testID: string }) {
  const sortCol = table.columns.find(c => c.key === st.sort);
  const key = st.sort && st.sort !== NODE && numeric(sortCol) ? st.sort : table.metric;
  const col = table.columns.find(c => c.key === key);
  if (!col) return <Text variant="caption" tone="ink3" testID={`${testID}-chart`}>Nothing numeric to chart in this view.</Text>;
  const data = rows.filter(r => typeof r.values[col.key] === 'number').slice(0, CHART_MAX);
  if (!data.length) return <Text variant="caption" tone="ink3" testID={`${testID}-chart`}>No values to chart yet.</Text>;
  const max = col.format === 'percent' ? 100 : Math.max(...data.map(r => Math.abs(r.values[col.key] as number))) || 1;
  return (
    <View testID={`${testID}-chart`} style={styles.chart}>
      <Text variant="caption" tone="ink3">{col.label} by {table.per === 'item' ? 'item' : 'node'}</Text>
      {data.map(r => {
        const v = r.values[col.key] as number;
        const tone = cellTone(col, v);
        return (
          <View key={r.exec_id} style={styles.chartRow} accessible accessibilityLabel={`${r.node}: ${formatCell(col, v)}`}>
            <Text variant="caption" numberOfLines={1} style={styles.chartLabel}>{r.node}</Text>
            <View style={{ flex: 1 }}><Meter value={Math.abs(v)} max={max} tone={tone || 'accent'} /></View>
            <Text variant="monoSm" style={styles.chartValue}>{formatCell(col, v)}</Text>
          </View>
        );
      })}
      {rows.length > data.length ? <Text variant="caption" tone="ink3">Showing the first {data.length} of {rows.length}; the table has every row.</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  chips: { gap: spacing.xs },
  bar: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  tr: { flexDirection: 'row', borderBottomWidth: StyleSheet.hairlineWidth },
  cell: { minWidth: 96, maxWidth: 240, paddingVertical: spacing.sm, paddingHorizontal: spacing.sm, justifyContent: 'center' },
  num: { alignItems: 'flex-end' },
  chart: { gap: spacing.xs },
  chartRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 28 },
  chartLabel: { width: 96 },
  chartValue: { minWidth: 64, textAlign: 'right' },
});
