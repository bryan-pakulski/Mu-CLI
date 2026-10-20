// Context (web memory_panel.html): the durable Memory Plane — remember explicitly, search, pin,
// archive, forget, why the last recall happened — and the Context Observatory: live composition
// by layer, the per-call evolution heatmap and churn pulse, and each provider call's layers.

import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import {
  DurableMemory,
  DurableMemoryDetail,
  DurableMemoryList,
  ContextTimeline,
  ContextTimelinePoint,
  MemoryLayer,
  MemorySnapshot,
  RecallReceipt,
  memoryApi,
} from '../api/memory';
import { radii, spacing, touchTarget } from '../theme/tokens';
import {
  Button, Chip, ContextRing, EmptyState, Field, LayerBar, Pre, Pressable, Row, Rows, Segmented, Sheet, Skeleton, Text, TextField,
  layerTone, useServerActions, useToast,
} from '../ui';

/** Append an alpha channel to a #RRGGBB token (a tint of a viz token). */
function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, '0');
  return hex.slice(0, 7) + a;
}


type MemoryTab = 'memories' | 'context';
type ContextView = 'heatmap' | 'churn';


export function MemoryScreen() {
  const { colors, isDark } = useTheme();
  const toast = useToast();
  const actions = useServerActions();
  const [tab, setTab] = useState<MemoryTab>('memories');
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [contextTimeline, setContextTimeline] = useState<ContextTimeline | null>(null);
  const [contextView, setContextView] = useState<ContextView>('heatmap');
  const [selectedContextPoint, setSelectedContextPoint] = useState<ContextTimelinePoint | null>(null);
  const [ledger, setLedger] = useState<DurableMemoryList | null>(null);
  const [query, setQuery] = useState('');
  const [newStatement, setNewStatement] = useState('');
  const [selectedScope, setSelectedScope] = useState('auto');
  const [selectedKind, setSelectedKind] = useState('observation');
  const [detail, setDetail] = useState<DurableMemoryDetail | null>(null);
  const [editStatement, setEditStatement] = useState('');
  const [receipt, setReceipt] = useState<RecallReceipt | null>(null);
  const [selectedLayer, setSelectedLayer] = useState<{
    layer: string;
    name: string;
    content: string;
  } | null>(null);
  const [layerLoading, setLayerLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [durable, context, timeline] = await Promise.all([
        memoryApi.listDurable(query),
        memoryApi.getState(),
        memoryApi.getTimeline(),
      ]);
      setLedger(durable);
      setSnapshot(context);
      setContextTimeline(timeline);
      if (timeline.points.length) {
        setSelectedContextPoint(timeline.points[timeline.points.length - 1]);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [query]);

  useFocusEffect(useCallback(() => {
    setLoading(true);
    load();
  }, [load]));

  const createMemory = async () => {
    const statement = newStatement.trim();
    if (!statement) return;
    setSaving(true);
    try {
      await memoryApi.createDurable(statement, selectedScope, selectedKind);
      setNewStatement('');
      setLedger(await memoryApi.listDurable(query));
    } catch (e) {
      toast(`Memory not stored: ${String(e)}`, 'danger');
    } finally {
      setSaving(false);
    }
  };

  const openMemory = async (memory: DurableMemory) => {
    try {
      const next = await memoryApi.getDurable(memory.id);
      setDetail(next);
      setEditStatement(next.memory.statement);
    } catch (e) {
      toast(`Memory unavailable: ${String(e)}`, 'danger');
    }
  };

  const saveEdit = async () => {
    if (!detail || !editStatement.trim()) return;
    setSaving(true);
    try {
      await memoryApi.reviseDurable(detail.memory, editStatement.trim());
      setDetail(null);
      setLedger(await memoryApi.listDurable(query));
    } catch (e) {
      toast(`Memory changed: ${String(e)}`, 'danger');
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (
    memory: DurableMemory,
    action: 'pin' | 'unpin' | 'archive' | 'restore' | 'forget',
  ) => {
    if (action === 'forget' && !(await actions.ask({
      id: 'forget', label: 'Forget', danger: true,
      confirm: 'Forget permanently? Content, provenance excerpts and search indexes are purged. Only a content-free receipt remains.',
    }))) return;
    try {
      await memoryApi.actionDurable(memory, action);
      setDetail(null);
      setLedger(await memoryApi.listDurable(query));
    } catch (e) {
      toast(`Memory action failed: ${String(e)}`, 'danger');
    }
  };

  const showLastRecall = async () => {
    try {
      const response = await memoryApi.getLastRecall();
      setReceipt(response.receipt);
    } catch (e) {
      toast(`No recall receipt: ${String(e)}`, 'attention');
    }
  };

  const openLayer = async (layer: MemoryLayer) => {
    setLayerLoading(true);
    try {
      const res = await memoryApi.getLayerContent(layer.id);
      setSelectedLayer({
        layer: layer.id,
        name: layer.name,
        content: res.content || res.error || '(empty)',
      });
    } catch (e) {
      setSelectedLayer({
        layer: layer.id,
        name: layer.name,
        content: String(e),
      });
    } finally {
      setLayerLoading(false);
    }
  };

  const contextPoints = (contextTimeline?.points || []).slice(-96);
  // Layer series colours come from the viz palette, keyed by the layer's
  // position in the live snapshot so every chart agrees.
  const layerColor = (id: string) => {
    const index = Math.max(0, (snapshot?.layers || []).findIndex(layer => layer.id === id));
    return layerTone(colors, id, index).pigment;
  };
  const contextSummary = contextTimeline?.summary || { samples: 0 };
  const activeContextPoint = selectedContextPoint
    || (contextPoints.length ? contextPoints[contextPoints.length - 1] : null);
  const maxChurn = Math.max(10, ...contextPoints.map(point => point.churn_score || 0));
  const refresh = () => { setRefreshing(true); void load(); };
  const net = contextSummary.net_delta || 0;

  const state = tab === 'memories'
    ? [{ text: `${ledger?.stats.total || 0} memories` }, ledger?.stats.pinned ? { text: `${ledger.stats.pinned} pinned` } : null, { text: 'captured automatically', tone: 'quiet' as const }]
    : snapshot?.active
      ? [{ text: `${snapshot.fill_pct}% full`, tone: snapshot.fill_pct >= 85 ? 'danger' as const : snapshot.fill_pct >= 70 ? 'attention' as const : '' as const },
        { text: `${contextSummary.samples || 0} calls` }, { text: `net ${net > 0 ? '+' : ''}${net}` },
        { text: `hottest ${contextSummary.hottest_layer || 'stable'}`, tone: 'quiet' as const }]
      : [{ text: 'Waiting for the first provider call', tone: 'quiet' as const }];

  return (
    <PanelPage testID="memory-screen" title="Context" state={state}
      tabs={[{ key: 'memories', label: 'Memories', count: ledger?.stats.total ?? null }, { key: 'context', label: 'Context' }]}
      tab={tab} onTab={key => setTab(key as MemoryTab)}
      menu={[{ key: 'recall', label: 'Why the last recall', icon: 'search', onPress: () => void showLastRecall() }]}
      loading={loading} error={error && !ledger && !snapshot ? error : null} onRetry={() => void load()}
      refreshing={refreshing} onRefresh={refresh}
      overlay={actions.element}>
      {tab === 'memories' ? (
        <>
          <PanelSection title="Remember explicitly">
            <View style={styles.form}>
              <Field label="Memory">
                <TextField value={newStatement} onChangeText={setNewStatement} placeholder="Decision, preference, convention or useful fact…"
                  multiline numberOfLines={3} style={styles.multiline} testID="memory-new" />
              </Field>
              <ChipChoice label="Scope" options={SCOPES} value={selectedScope} onChange={setSelectedScope} />
              <ChipChoice label="Kind" options={KINDS} value={selectedKind} onChange={setSelectedKind} />
              <Button label="Remember" variant="primary" onPress={() => void createMemory()} loading={saving} disabled={!newStatement.trim()} testID="memory-remember" />
            </View>
          </PanelSection>
          <PanelSection title="Durable memories" count={ledger?.memories.length || 0}>
            <TextField value={query} onChangeText={setQuery} placeholder="Search durable memories…" accessibilityLabel="Search durable memories" testID="memory-search" />
            {ledger && ledger.memories.length === 0 ? (
              <EmptyState title="No durable memories yet" message="The model will save useful knowledge automatically and show every write here." />
            ) : (
              <Rows>
                {(ledger?.memories || []).map(memory => (
                  <Row key={memory.id} testID={`memory-${memory.id}`} status={memory.pinned ? 'done' : memory.lifecycle === 'active' ? undefined : 'skipped'}
                    title={memory.statement} when={`recalled ${memory.recall_count || 0}`} onPress={() => void openMemory(memory)}
                    sub={[memory.scope.type, memory.kind, memory.lifecycle, memory.pinned ? 'pinned' : '', `${memory.id.slice(0, 8)} · v${memory.version}`].filter(Boolean).join(' · ')} />
                ))}
              </Rows>
            )}
          </PanelSection>
        </>
      ) : !snapshot || !snapshot.active ? (
        <EmptyState title="Context observatory is ready" message="Send a prompt. Each provider call will appear as a new heatmap column." />
      ) : (
        <>
          <PanelSection title="Current composition">
            <Text variant="caption" tone="ink3">Provider-visible context, captured at every model call.</Text>
            <View style={styles.composition}>
              <ContextRing fill={snapshot.context_limit ? snapshot.total_tokens / snapshot.context_limit : 0} showLabel={false} />
              <Text variant="caption" tone="ink2" numeric style={styles.flex}>
                {snapshot.total_tokens.toLocaleString()} / {snapshot.context_limit.toLocaleString()} tok
              </Text>
            </View>
            {/* Wash stacked layer bar (G§9.14): layer pigments, direct labels. */}
            <LayerBar testID="memory-layer-bar" layers={snapshot.layers.map(layer => ({ id: layer.id, tokens: layer.tokens, name: layer.name }))} limit={snapshot.context_limit} />
          </PanelSection>

          <PanelSection title={contextView === 'heatmap' ? 'Layer evolution' : 'Context churn'}>
            <Segmented<ContextView> accessibilityLabel="Context view" value={contextView} onChange={setContextView}
              options={[{ value: 'heatmap', label: 'Evolution heatmap' }, { value: 'churn', label: 'Churn pulse' }]} />
            <Text variant="caption" tone="ink3">
              {contextView === 'heatmap' ? 'Brightness = content replaced at each provider call' : 'Replacement rate; teal bars mark compaction'}
            </Text>
            {contextPoints.length === 0 ? (
              <Text variant="caption" tone="ink3" style={styles.waiting}>Waiting for the first provider call…</Text>
            ) : contextView === 'heatmap' ? (
              <View style={styles.heatmap}>
                <View style={styles.heatLabels}>
                  {snapshot.layers.map(layer => (
                    <View key={layer.id} style={styles.heatRow}><Text variant="monoSm" tone="ink3">{layer.id}</Text></View>
                  ))}
                </View>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.flex}>
                  <View>
                    {snapshot.layers.map(layer => (
                      <View key={layer.id} style={styles.heatRow}>
                        {contextPoints.map(point => {
                          const measured = point.layers.find(item => item.id === layer.id);
                          const ratio = measured?.change_ratio || 0;
                          const alpha = measured?.changed
                            ? Math.min(0.96, (isDark ? 0.28 : 0.38) + ratio * (isDark ? 0.68 : 0.58))
                            : (measured?.tokens ? (isDark ? 0.12 : 0.19) : (isDark ? 0.035 : 0.055));
                          const active = activeContextPoint?.id === point.id;
                          return (
                            <Pressable accessibilityLabel={`Provider call ${point.id}`} key={point.id} scale={false} onPress={() => setSelectedContextPoint(point)}
                              style={[styles.cell, { backgroundColor: withAlpha(layerColor(layer.id), alpha) }, active ? { borderWidth: 1, borderColor: colors.ink } : null]} />
                          );
                        })}
                      </View>
                    ))}
                  </View>
                </ScrollView>
              </View>
            ) : (
              <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                <View style={styles.churn}>
                  {contextPoints.map(point => (
                    <Pressable accessibilityLabel={`Provider call ${point.id}`} key={point.id} scale={false} onPress={() => setSelectedContextPoint(point)}
                      style={[styles.bar, {
                        height: Math.max(3, (point.churn_score || 0) / maxChurn * 128),
                        backgroundColor: point.compaction ? colors.series[0].ink : colors.danger.ink,
                        opacity: activeContextPoint?.id === point.id ? 1 : 0.68,
                      }]} />
                  ))}
                </View>
              </ScrollView>
            )}
          </PanelSection>

          {activeContextPoint ? (
            <PanelSection title={`Provider call ${activeContextPoint.id}`}
              right={<Text variant="caption" tone="ink3" numeric>{new Date(activeContextPoint.at * 1000).toLocaleTimeString()}</Text>}>
              <Text variant="caption" tone="ink2" numeric>
                {[`${activeContextPoint.total_delta > 0 ? '+' : ''}${activeContextPoint.total_delta} tok`, `${activeContextPoint.total_tokens.toLocaleString()} total`,
                  `${activeContextPoint.churn_score}% churn`, `${activeContextPoint.changed_layers} changed`, activeContextPoint.compaction ? 'compacted' : ''].filter(Boolean).join(' · ')}
              </Text>
              <Rows>
                {activeContextPoint.layers.map(layer => {
                  const currentLayer = snapshot.layers.find(item => item.id === layer.id);
                  return (
                    <Row key={layer.id} testID={`call-layer-${layer.id}`}
                      title={(
                        <View style={styles.layerTitle}>
                          <View style={[styles.dot, { backgroundColor: layerColor(layer.id) }]} />
                          <Text variant="monoSm" tone="ink3">{layer.id}</Text>
                          <Text variant="body" numberOfLines={1} style={styles.flex}>{layer.name}</Text>
                        </View>
                      )}
                      when={`${layer.tokens.toLocaleString()} · Δ${layer.changed_chunks}`}
                      status={layer.changed ? 'waiting' : undefined}
                      onPress={currentLayer ? () => void openLayer(currentLayer) : undefined} />
                  );
                })}
              </Rows>
            </PanelSection>
          ) : null}
          <Text variant="caption" tone="ink3" style={styles.footnote}>Session-run history stores measurements and hashes, never prompt text.</Text>
        </>
      )}

      <Sheet visible={!!detail} onClose={() => setDetail(null)} title="Memory detail" testID="memory-detail"
        footer={detail ? <Button label="Save edit" variant="primary" onPress={() => void saveEdit()} loading={saving} disabled={!editStatement.trim()} testID="memory-save" /> : undefined}>
        {detail ? (
          <View style={styles.form}>
            <Text variant="caption" tone="ink3">{[detail.memory.scope.type, detail.memory.kind, `confidence ${detail.memory.trust.confidence.toFixed(2)}`].join(' · ')}</Text>
            <TextField value={editStatement} onChangeText={setEditStatement} multiline numberOfLines={4} style={styles.multiline}
              accessibilityLabel="Memory statement" testID="memory-edit" />
            <View style={styles.actions}>
              <Button label={detail.memory.pinned ? 'Unpin' : 'Pin'} variant="secondary" onPress={() => void runAction(detail.memory, detail.memory.pinned ? 'unpin' : 'pin')} />
              <Button label={detail.memory.lifecycle === 'archived' ? 'Restore' : 'Archive'} variant="secondary"
                onPress={() => void runAction(detail.memory, detail.memory.lifecycle === 'archived' ? 'restore' : 'archive')} />
              <Button label="Forget" variant="danger" onPress={() => void runAction(detail.memory, 'forget')} testID="memory-forget" />
            </View>
            <Text variant="label" tone="ink2">Sources</Text>
            <Pre>{JSON.stringify(detail.memory.source_refs, null, 2)}</Pre>
            <Text variant="label" tone="ink2">Relationships</Text>
            {detail.graph.edges.length ? detail.graph.edges.map((edge, index) => (
              <Text key={edge.source + edge.target + index} variant="monoSm" tone="ink3">{edge.source.slice(0, 8)} —{edge.type}→ {edge.target.slice(0, 8)}</Text>
            )) : <Text variant="caption" tone="ink3">No related memories.</Text>}
            <Text variant="label" tone="ink2">Timeline</Text>
            <Rows>
              {detail.events.map((event, index) => (
                <Row key={String(event.event_id || index)} title={`${String(event.type || 'event')} · ${String(event.actor || 'system')}`} sub={JSON.stringify(event.after || {})} />
              ))}
            </Rows>
          </View>
        ) : null}
      </Sheet>

      <Sheet visible={!!receipt} onClose={() => setReceipt(null)} title="Why memory was used" testID="memory-receipt">
        <Pre>{receipt ? JSON.stringify(receipt, null, 2) : ''}</Pre>
      </Sheet>

      <Sheet visible={!!selectedLayer} onClose={() => setSelectedLayer(null)} title={selectedLayer?.name} full testID="memory-layer">
        {layerLoading ? <Skeleton height={40} /> : <Pre>{selectedLayer?.content}</Pre>}
      </Sheet>
    </PanelPage>
  );
}

const SCOPES = ['auto', 'repository', 'workspace', 'branch', 'personal'];
const KINDS = ['observation', 'decision', 'finding', 'constraint', 'preference', 'procedure'];

function ChipChoice({ label, options, value, onChange }: { label: string; options: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={styles.choice}>
      <Text variant="label" tone="ink2">{label}</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips} accessibilityRole="radiogroup" accessibilityLabel={label}>
        {options.map(option => (
          <Chip key={option} variant="filter" selected={value === option} label={option} onPress={() => onChange(option)} testID={`${label.toLowerCase()}-${option}`} />
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  form: { gap: spacing.sm },
  multiline: { minHeight: 96, textAlignVertical: 'top' },
  choice: { gap: spacing.xs },
  chips: { gap: spacing.sm, paddingVertical: spacing.xs },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  composition: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginVertical: spacing.sm },
  waiting: { paddingVertical: spacing.xl, textAlign: 'center' },
  heatmap: { flexDirection: 'row', marginTop: spacing.sm },
  heatLabels: { paddingRight: spacing.xs },
  heatRow: { height: 20, flexDirection: 'row', alignItems: 'center' },
  cell: { width: 9, height: 16, marginRight: 2, borderRadius: radii.xs },
  churn: { height: 150, flexDirection: 'row', alignItems: 'flex-end', gap: spacing.xs, paddingTop: spacing.sm },
  bar: { width: 8, borderRadius: radii.pill },
  layerTitle: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: touchTarget - spacing.base },
  dot: { width: 8, height: 8, borderRadius: radii.pill },
  footnote: { textAlign: 'center', marginVertical: spacing.sm },
});
