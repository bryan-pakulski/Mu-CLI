import React, { useCallback, useState } from 'react';
import { Alert, RefreshControl, ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
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
import { spacing, fontSizes, fontWeights, fonts, radii, touchTarget, elevation, typography } from '../theme/tokens';
import { Button, Card, Chip, ContextRing, EmptyState, ErrorState, LayerBar, ModalFrame, Pressable, Skeleton, Text, TextField, layerTone } from '../ui';

/** Append an alpha channel to a #RRGGBB token (a tint of a viz token). */
function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255).toString(16).padStart(2, '0');
  return hex.slice(0, 7) + a;
}


type MemoryTab = 'memories' | 'context';
type ContextView = 'heatmap' | 'churn';


export function MemoryScreen() {
  const { colors, isDark } = useTheme();
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
      Alert.alert('Memory not stored', String(e));
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
      Alert.alert('Memory unavailable', String(e));
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
      Alert.alert('Memory changed', String(e));
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (
    memory: DurableMemory,
    action: 'pin' | 'unpin' | 'archive' | 'restore' | 'forget',
  ) => {
    const execute = async () => {
      try {
        await memoryApi.actionDurable(memory, action);
        setDetail(null);
        setLedger(await memoryApi.listDurable(query));
      } catch (e) {
        Alert.alert('Memory action failed', String(e));
      }
    };
    if (action === 'forget') {
      Alert.alert(
        'Forget permanently?',
        'Content, provenance excerpts and search indexes are purged. Only a content-free receipt remains.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Forget', style: 'destructive', onPress: execute },
        ],
      );
      return;
    }
    await execute();
  };

  const showLastRecall = async () => {
    try {
      const response = await memoryApi.getLastRecall();
      setReceipt(response.receipt);
    } catch (e) {
      Alert.alert('No recall receipt', String(e));
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

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <View style={{ padding: spacing.base }}>
          <Skeleton height={44} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={120} style={{ marginBottom: spacing.sm }} />
          <Skeleton height={80} />
        </View>
      </SafeAreaView>
    );
  }

  if (error && !ledger && !snapshot) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  const tabButton = (target: MemoryTab, title: string) => (
    <Pressable
      onPress={() => setTab(target)}
      style={{
        flex: 1,
        minHeight: 44,
        alignItems: 'center',
        justifyContent: 'center',
        borderBottomWidth: 2,
        borderBottomColor: tab === target ? colors.accent.ink : colors.hairline,
      }}
    >
      <Text
        variant="sm"
        style={{ color: tab === target ? colors.ink : colors.ink3, fontWeight: tab === target ? fontWeights.semibold : fontWeights.medium }}
      >
        {title}
      </Text>
    </Pressable>
  );

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

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: 'transparent' }}>
      <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
        {tabButton('memories', 'Memories')}
        {tabButton('context', 'Context')}
      </View>

      {tab === 'memories' ? (
        <ScrollView
          keyboardShouldPersistTaps="handled"
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => { setRefreshing(true); load(); }}
            />
          }
          contentContainerStyle={{ padding: spacing.base }}
        >
          <Card style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <View>
                <Text variant="base" style={{ fontWeight: fontWeights.semibold }}>Memory Plane</Text>
                <Text variant="xs" style={{ color: colors.ink3 }}>
                  Automatic model-managed capture · no approval prompts
                </Text>
              </View>
              <Chip
                label={String(ledger?.stats.total || 0)}
                tone="neutral"
              />
            </View>
            <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm }}>
              <Chip label={String(ledger?.stats.pinned || 0) + ' pinned'} />
              <Pressable onPress={showLastRecall} hitSlop={{ top: spacing.md, bottom: spacing.md, left: spacing.xs, right: spacing.xs }}>
                <Chip label="Why last recall" tone="accent" />
              </Pressable>
            </View>
          </Card>

          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="sm" style={{ fontWeight: fontWeights.semibold, marginBottom: spacing.sm }}>
              Remember explicitly
            </Text>
            <TextField
              value={newStatement}
              onChangeText={setNewStatement}
              placeholder="Decision, preference, convention or useful fact…"
              multiline
              numberOfLines={3}
              style={{ minHeight: 84, textAlignVertical: 'top' }}
            />
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.xs }}
            >
              {['auto', 'repository', 'workspace', 'branch', 'personal'].map(scope => (
                <Pressable key={scope} onPress={() => setSelectedScope(scope)} style={{ minHeight: touchTarget, justifyContent: 'center' }}>
                  <Chip label={scope} tone={selectedScope === scope ? 'accent' : 'neutral'} />
                </Pressable>
              ))}
            </ScrollView>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: spacing.sm, paddingBottom: spacing.sm }}
            >
              {['observation', 'decision', 'finding', 'constraint', 'preference', 'procedure'].map(kind => (
                <Pressable key={kind} onPress={() => setSelectedKind(kind)} style={{ minHeight: touchTarget, justifyContent: 'center' }}>
                  <Chip label={kind} tone={selectedKind === kind ? 'accent' : 'neutral'} />
                </Pressable>
              ))}
            </ScrollView>
            <Button
              title="Remember"
              onPress={createMemory}
              loading={saving}
              disabled={!newStatement.trim()}
            />
          </Card>

          <TextField
            value={query}
            onChangeText={setQuery}
            placeholder="Search durable memories…"
            style={{ marginBottom: spacing.sm }}
          />

          {(ledger?.memories || []).map(memory => (
            <Pressable
              key={memory.id}
              onPress={() => openMemory(memory)}
              activeOpacity={0.72}
            >
              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
                  <Chip label={memory.scope.type} />
                  <Chip label={memory.kind} />
                  <Chip
                    label={memory.lifecycle}
                    tone={memory.lifecycle === 'active' ? 'ok' : 'attention'}
                  />
                  {memory.pinned && <Chip label="pinned" tone="accent" />}
                </View>
                <Text variant="sm" style={{ marginTop: spacing.sm }}>
                  {memory.statement}
                </Text>
                <View
                  style={{
                    flexDirection: 'row',
                    justifyContent: 'space-between',
                    marginTop: spacing.sm,
                  }}
                >
                  <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono, fontVariant: ['tabular-nums'] }}>
                    {memory.id.slice(0, 8)} · v{memory.version}
                  </Text>
                  <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>
                    recalled {memory.recall_count || 0}
                  </Text>
                </View>
              </Card>
            </Pressable>
          ))}

          {ledger && ledger.memories.length === 0 && (
            <EmptyState
              title="No durable memories yet"
              message="The model will save useful knowledge automatically and show every write here."
            />
          )}
        </ScrollView>
      ) : (
        <ScrollView
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => { setRefreshing(true); load(); }}
            />
          }
          contentContainerStyle={{ padding: spacing.base }}
        >
          {!snapshot || !snapshot.active ? (
            <EmptyState
              title="Context observatory is ready"
              message="Send a prompt. Each provider call will appear as a new heatmap column."
            />
          ) : (
            <>
              <Card style={{ marginBottom: spacing.sm }}>
                <Text variant="xs" style={{ color: colors.ink3 }}>
                  MODEL INPUT · LIVE
                </Text>
                <Text variant="lg" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.xs }}>
                  Context Observatory
                </Text>
                <Text variant="xs" style={{ color: colors.ink3 }}>
                  Provider-visible context, captured at every model call.
                </Text>

                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.base }}>
                  {[
                    ['Calls', contextSummary.samples || 0],
                    ['Pressure', String(snapshot.fill_pct) + '%'],
                    ['Net', ((contextSummary.net_delta || 0) > 0 ? '+' : '') + String(contextSummary.net_delta || 0)],
                    ['Hottest', contextSummary.hottest_layer || 'stable'],
                  ].map(([label, value]) => (
                    <View
                      key={String(label)}
                      style={{
                        width: '47%',
                        paddingVertical: spacing.sm,
                      }}
                    >
                      <Text variant="xs" style={{ color: colors.ink3 }}>{label}</Text>
                      <Text variant="base" style={{ fontWeight: fontWeights.semibold, fontVariant: ['tabular-nums'] }}>
                        {value}
                      </Text>
                    </View>
                  ))}
                </View>

                <View style={{ marginTop: spacing.base }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
                      <ContextRing fill={snapshot.context_limit ? snapshot.total_tokens / snapshot.context_limit : 0} showLabel={false} />
                      <Text variant="xs" style={{ color: colors.ink3 }}>Current composition</Text>
                    </View>
                    <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>
                      {snapshot.total_tokens.toLocaleString()} / {snapshot.context_limit.toLocaleString()} tok
                    </Text>
                  </View>
                  {/* Wash stacked layer bar (G§9.14): layer pigments, direct labels. */}
                  <LayerBar
                    testID="memory-layer-bar"
                    layers={snapshot.layers.map(layer => ({ id: layer.id, tokens: layer.tokens, name: layer.name }))}
                    limit={snapshot.context_limit}
                  />
                </View>
              </Card>

              <View
                style={{
                  flexDirection: 'row',
                  padding: spacing.xs,
                  marginBottom: spacing.sm,
                  borderRadius: radii.sm,
                  backgroundColor: colors.surface,
                }}
              >
                {(['heatmap', 'churn'] as ContextView[]).map(view => (
                  <Pressable
                    key={view}
                    onPress={() => setContextView(view)}
                    style={{
                      flex: 1,
                      alignItems: 'center',
                      justifyContent: 'center',
                      minHeight: touchTarget,
                      borderRadius: radii.sm,
                      backgroundColor: contextView === view ? colors.accent.wash : 'transparent',
                    }}
                  >
                    <Text variant="xs" style={{ color: contextView === view ? colors.accent.ink : colors.ink3, fontWeight: contextView === view ? fontWeights.semibold : fontWeights.regular }}>
                      {view === 'heatmap' ? 'Evolution heatmap' : 'Churn pulse'}
                    </Text>
                  </Pressable>
                ))}
              </View>

              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                  <View>
                    <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>
                      {contextView === 'heatmap' ? 'Layer evolution' : 'Context churn'}
                    </Text>
                    <Text variant="xs" style={{ color: colors.ink3 }}>
                      {contextView === 'heatmap'
                        ? 'Brightness = content replaced at each provider call'
                        : 'Replacement rate; teal bars mark compaction'}
                    </Text>
                  </View>
                  <Chip label="live" tone="ok" />
                </View>

                {contextPoints.length === 0 ? (
                  <View style={{ minHeight: 140, alignItems: 'center', justifyContent: 'center' }}>
                    <Text variant="xs" style={{ color: colors.ink3 }}>
                      Waiting for the first provider call…
                    </Text>
                  </View>
                ) : contextView === 'heatmap' ? (
                  <View style={{ flexDirection: 'row' }}>
                    <View style={{ width: 38, paddingRight: spacing.xs }}>
                      {snapshot.layers.map(layer => (
                        <View key={layer.id} style={{ height: 20, justifyContent: 'center' }}>
                          <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono, fontSize: fontSizes.xs, lineHeight: typography.sm.lineHeight }}>
                            {layer.id}
                          </Text>
                        </View>
                      ))}
                    </View>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flex: 1 }}>
                      <View>
                        {snapshot.layers.map(layer => (
                          <View key={layer.id} style={{ height: 20, flexDirection: 'row', alignItems: 'center' }}>
                            {contextPoints.map(point => {
                              const measured = point.layers.find(item => item.id === layer.id);
                              const changed = !!measured?.changed;
                              const ratio = measured?.change_ratio || 0;
                              const alpha = changed
                                ? Math.min(0.96, (isDark ? 0.28 : 0.38) + ratio * (isDark ? 0.68 : 0.58))
                                : (measured?.tokens ? (isDark ? 0.12 : 0.19) : (isDark ? 0.035 : 0.055));
                              return (
                                <Pressable accessibilityLabel={`Provider call ${point.id}`}
                                  key={point.id}
                                  onPress={() => setSelectedContextPoint(point)}
                                  style={{
                                    width: 9,
                                    height: 16,
                                    marginRight: 2,
                                    borderRadius: radii.sm,
                                    borderWidth: activeContextPoint?.id === point.id ? 1 : 0,
                                    borderColor: colors.ink,
                                    backgroundColor: withAlpha(layerColor(layer.id), alpha),
                                  }}
                                />
                              );
                            })}
                          </View>
                        ))}
                      </View>
                    </ScrollView>
                  </View>
                ) : (
                  <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                    <View style={{ height: 150, flexDirection: 'row', alignItems: 'flex-end', gap: spacing.xs, paddingTop: spacing.sm }}>
                      {contextPoints.map(point => (
                        <Pressable accessibilityLabel={`Provider call ${point.id}`}
                          key={point.id}
                          onPress={() => setSelectedContextPoint(point)}
                          style={{
                            width: 8,
                            height: Math.max(3, (point.churn_score || 0) / maxChurn * 128),
                            borderRadius: radii.pill,
                            backgroundColor: point.compaction ? colors.series[0].ink : colors.danger.ink,
                            opacity: activeContextPoint?.id === point.id ? 1 : 0.68,
                          }}
                        />
                      ))}
                    </View>
                  </ScrollView>
                )}
              </Card>

              {activeContextPoint && (
                <Card style={{ marginBottom: spacing.sm }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                    <View>
                      <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>
                        Provider call {activeContextPoint.id}
                      </Text>
                      <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>
                        {new Date(activeContextPoint.at * 1000).toLocaleTimeString()}
                      </Text>
                    </View>
                    <Chip
                      label={(activeContextPoint.total_delta > 0 ? '+' : '') + activeContextPoint.total_delta + ' tok'}
                      tone={activeContextPoint.total_delta < 0 ? 'ok' : 'neutral'}
                    />
                  </View>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm }}>
                    <Chip label={activeContextPoint.total_tokens.toLocaleString() + ' total'} />
                    <Chip label={activeContextPoint.churn_score + '% churn'} />
                    <Chip label={activeContextPoint.changed_layers + ' changed'} />
                    {activeContextPoint.compaction && <Chip label="compacted" tone="ok" />}
                  </View>
                  {activeContextPoint.layers.map(layer => {
                    const currentLayer = snapshot.layers.find(item => item.id === layer.id);
                    return (
                      <Pressable
                        key={layer.id}
                        onPress={() => currentLayer && openLayer(currentLayer)}
                        style={{ flexDirection: 'row', alignItems: 'center', minHeight: touchTarget }}
                      >
                        <View
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: radii.pill,
                            marginRight: spacing.sm,
                            backgroundColor: layerColor(layer.id),
                          }}
                        />
                        <Text variant="xs" style={{ width: 34, fontFamily: fonts.mono, color: colors.ink3 }}>
                          {layer.id}
                        </Text>
                        <Text variant="xs" style={{ flex: 1 }} numberOfLines={1}>{layer.name}</Text>
                        <Text variant="xs" style={{ color: layer.changed ? colors.attention.ink : colors.ink3, fontVariant: ['tabular-nums'] }}>
                          {layer.tokens.toLocaleString()} · Δ{layer.changed_chunks}
                        </Text>
                      </Pressable>
                    );
                  })}
                </Card>
              )}

              <Text variant="xs" style={{ color: colors.ink3, textAlign: 'center', marginVertical: spacing.sm }}>
                Session-run history stores measurements and hashes, never prompt text.
              </Text>
            </>
          )}
        </ScrollView>
      )}

      <ModalFrame statusBarTranslucent={false}
        visible={!!detail}
        transparent
        animationType="slide"
        onRequestClose={() => setDetail(null)}
      >
        <View style={{ flex: 1, backgroundColor: colors.scrim, justifyContent: 'flex-end' }}>
          <View
            style={[elevation(2, colors, isDark), {
              borderTopLeftRadius: radii.lg,
              borderTopRightRadius: radii.lg,
              padding: spacing.base,
              maxHeight: '92%',
            }]}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: fontWeights.semibold }}>Memory detail</Text>
              <Pressable onPress={() => setDetail(null)} accessibilityRole="button" style={{ minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.sm }}>
                <Text variant="sm" style={{ color: colors.accent.ink }}>Close</Text>
              </Pressable>
            </View>
            {detail && (
              <ScrollView keyboardShouldPersistTaps="handled">
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm }}>
                  <Chip label={detail.memory.scope.type} />
                  <Chip label={detail.memory.kind} />
                  <Chip label={'confidence ' + detail.memory.trust.confidence.toFixed(2)} />
                </View>
                <TextField
                  value={editStatement}
                  onChangeText={setEditStatement}
                  multiline
                  numberOfLines={4}
                  style={{ minHeight: 110, textAlignVertical: 'top' }}
                />
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginVertical: spacing.sm }}>
                  <Button
                    title="Save edit"
                    onPress={saveEdit}
                    loading={saving}
                    style={{ flexGrow: 1 }}
                  />
                  <Button
                    title={detail.memory.pinned ? 'Unpin' : 'Pin'}
                    variant="secondary"
                    onPress={() => runAction(detail.memory, detail.memory.pinned ? 'unpin' : 'pin')}
                  />
                  <Button
                    title={detail.memory.lifecycle === 'archived' ? 'Restore' : 'Archive'}
                    variant="secondary"
                    onPress={() => runAction(
                      detail.memory,
                      detail.memory.lifecycle === 'archived' ? 'restore' : 'archive',
                    )}
                  />
                  <Button
                    title="Forget"
                    variant="danger"
                    onPress={() => runAction(detail.memory, 'forget')}
                  />
                </View>
                <Text variant="sm" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.sm }}>
                  Sources
                </Text>
                <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }}>
                  {JSON.stringify(detail.memory.source_refs, null, 2)}
                </Text>
                <Text variant="sm" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.base }}>
                  Relationships
                </Text>
                {(detail.graph.edges || []).map((edge, index) => (
                  <Text key={edge.source + edge.target + index} variant="xs" style={{ color: colors.ink3 }}>
                    {edge.source.slice(0, 8)} —{edge.type}→ {edge.target.slice(0, 8)}
                  </Text>
                ))}
                {!detail.graph.edges.length && (
                  <Text variant="xs" style={{ color: colors.ink3 }}>No related memories.</Text>
                )}
                <Text variant="sm" style={{ fontWeight: fontWeights.semibold, marginTop: spacing.base }}>
                  Timeline
                </Text>
                {detail.events.map((event, index) => (
                  <View key={String(event.event_id || index)} style={{ marginTop: spacing.sm, padding: spacing.md, borderRadius: radii.sm, backgroundColor: colors.surface }}>
                    <Text variant="xs" style={{ fontFamily: fonts.mono }}>
                      {String(event.type || 'event')} · {String(event.actor || 'system')}
                    </Text>
                    <Text variant="xs" style={{ color: colors.ink3 }}>
                      {JSON.stringify(event.after || {})}
                    </Text>
                  </View>
                ))}
              </ScrollView>
            )}
          </View>
        </View>
      </ModalFrame>

      <ModalFrame statusBarTranslucent={false}
        visible={!!receipt}
        transparent
        animationType="slide"
        onRequestClose={() => setReceipt(null)}
      >
        <View style={{ flex: 1, backgroundColor: colors.scrim, justifyContent: 'center' }}>
          <View style={[elevation(2, colors, isDark), { margin: spacing.base, borderRadius: radii.lg, padding: spacing.base, maxHeight: '85%' }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: fontWeights.semibold }}>Why memory was used</Text>
              <Pressable onPress={() => setReceipt(null)} accessibilityRole="button" style={{ minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.sm }}>
                <Text variant="sm" style={{ color: colors.accent.ink }}>Close</Text>
              </Pressable>
            </View>
            <ScrollView>
              <Text variant="xs" style={{ color: colors.ink3, fontFamily: fonts.mono }}>
                {receipt ? JSON.stringify(receipt, null, 2) : ''}
              </Text>
            </ScrollView>
          </View>
        </View>
      </ModalFrame>

      <ModalFrame statusBarTranslucent={false}
        visible={!!selectedLayer}
        transparent
        animationType="slide"
        onRequestClose={() => setSelectedLayer(null)}
      >
        <View style={{ flex: 1, backgroundColor: colors.scrim, justifyContent: 'center' }}>
          <View style={[elevation(2, colors, isDark), { margin: spacing.base, borderRadius: radii.lg, padding: spacing.base, maxHeight: '85%' }]}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: fontWeights.semibold }}>{selectedLayer?.name}</Text>
              <Pressable onPress={() => setSelectedLayer(null)} accessibilityRole="button" style={{ minHeight: touchTarget, justifyContent: 'center', paddingHorizontal: spacing.sm }}>
                <Text variant="sm" style={{ color: colors.accent.ink }}>Close</Text>
              </Pressable>
            </View>
            <ScrollView style={{ maxHeight: 400 }}>
              {layerLoading ? (
                <Skeleton height={40} />
              ) : (
                <Text variant="xs" style={{ color: colors.ink, fontFamily: fonts.mono }}>
                  {selectedLayer?.content}
                </Text>
              )}
            </ScrollView>
          </View>
        </View>
      </ModalFrame>
    </SafeAreaView>
  );
}
