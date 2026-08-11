import React, { useCallback, useState } from 'react';
import {
  Alert,
  RefreshControl,
  ScrollView,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  Skeleton,
  Text,
} from '../components';
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
import { spacing } from '../theme/tokens';
import { SafeAreaModal } from '../components/SafeAreaModal';


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
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
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
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  const tabButton = (target: MemoryTab, title: string) => (
    <TouchableOpacity
      onPress={() => setTab(target)}
      style={{
        flex: 1,
        minHeight: 44,
        alignItems: 'center',
        justifyContent: 'center',
        borderBottomWidth: 2,
        borderBottomColor: tab === target ? colors.accent : 'transparent',
      }}
    >
      <Text
        variant="sm"
        style={{ color: tab === target ? colors.accent : colors.textDim, fontWeight: '600' }}
      >
        {title}
      </Text>
    </TouchableOpacity>
  );

  const contextPoints = (contextTimeline?.points || []).slice(-96);
  const contextSummary = contextTimeline?.summary || { samples: 0 };
  const activeContextPoint = selectedContextPoint
    || (contextPoints.length ? contextPoints[contextPoints.length - 1] : null);
  const maxChurn = Math.max(10, ...contextPoints.map(point => point.churn_score || 0));

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
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
                <Text variant="base" style={{ fontWeight: '600' }}>Memory Plane</Text>
                <Text variant="xs" style={{ color: colors.textDim }}>
                  Automatic model-managed capture · no approval prompts
                </Text>
              </View>
              <Badge
                label={String(ledger?.stats.total || 0)}
                variant="accent"
              />
            </View>
            <View style={{ flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm }}>
              <Badge label={String(ledger?.stats.pinned || 0) + ' pinned'} />
              <TouchableOpacity onPress={showLastRecall}>
                <Badge label="Why last recall" variant="accent" />
              </TouchableOpacity>
            </View>
          </Card>

          <Card style={{ marginBottom: spacing.sm }}>
            <Text variant="sm" style={{ fontWeight: '600', marginBottom: spacing.sm }}>
              Remember explicitly
            </Text>
            <Input
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
              contentContainerStyle={{ gap: spacing.sm, paddingVertical: spacing.sm }}
            >
              {['auto', 'repository', 'workspace', 'branch', 'personal'].map(scope => (
                <TouchableOpacity key={scope} onPress={() => setSelectedScope(scope)}>
                  <Badge label={scope} variant={selectedScope === scope ? 'accent' : 'neutral'} />
                </TouchableOpacity>
              ))}
            </ScrollView>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: spacing.sm, paddingBottom: spacing.sm }}
            >
              {['observation', 'decision', 'finding', 'constraint', 'preference', 'procedure'].map(kind => (
                <TouchableOpacity key={kind} onPress={() => setSelectedKind(kind)}>
                  <Badge label={kind} variant={selectedKind === kind ? 'accent' : 'neutral'} />
                </TouchableOpacity>
              ))}
            </ScrollView>
            <Button
              title="Remember"
              onPress={createMemory}
              loading={saving}
              disabled={!newStatement.trim()}
            />
          </Card>

          <Input
            value={query}
            onChangeText={setQuery}
            placeholder="Search durable memories…"
            style={{ marginBottom: spacing.sm }}
          />

          {(ledger?.memories || []).map(memory => (
            <TouchableOpacity
              key={memory.id}
              onPress={() => openMemory(memory)}
              activeOpacity={0.72}
            >
              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
                  <Badge label={memory.scope.type} variant="accent" />
                  <Badge label={memory.kind} />
                  <Badge
                    label={memory.lifecycle}
                    variant={memory.lifecycle === 'active' ? 'success' : 'warning'}
                  />
                  {memory.pinned && <Badge label="pinned" variant="accent" />}
                </View>
                <Text variant="sm" style={{ marginTop: spacing.sm, lineHeight: 21 }}>
                  {memory.statement}
                </Text>
                <View
                  style={{
                    flexDirection: 'row',
                    justifyContent: 'space-between',
                    marginTop: spacing.sm,
                  }}
                >
                  <Text variant="xs" style={{ color: colors.textDim }}>
                    {memory.id.slice(0, 8)} · v{memory.version}
                  </Text>
                  <Text variant="xs" style={{ color: colors.textDim }}>
                    recalled {memory.recall_count || 0}
                  </Text>
                </View>
              </Card>
            </TouchableOpacity>
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
                <Text variant="xs" style={{ color: colors.accent, letterSpacing: 1.2 }}>
                  MODEL INPUT · LIVE
                </Text>
                <Text variant="lg" style={{ fontWeight: '600', marginTop: spacing.xs }}>
                  Context Observatory
                </Text>
                <Text variant="xs" style={{ color: colors.textDim }}>
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
                        borderWidth: 1,
                        borderColor: colors.hairline,
                        borderRadius: 10,
                        padding: spacing.sm,
                        backgroundColor: colors.bgHover,
                      }}
                    >
                      <Text variant="xs" style={{ color: colors.textDim }}>{label}</Text>
                      <Text variant="base" style={{ fontWeight: '600', fontVariant: ['tabular-nums'] }}>
                        {value}
                      </Text>
                    </View>
                  ))}
                </View>

                <View style={{ marginTop: spacing.base }}>
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs }}>
                    <Text variant="xs" style={{ color: colors.textDim }}>Current composition</Text>
                    <Text variant="xs" style={{ color: colors.textDim, fontVariant: ['tabular-nums'] }}>
                      {snapshot.total_tokens.toLocaleString()} / {snapshot.context_limit.toLocaleString()} tok
                    </Text>
                  </View>
                  <View
                    style={{
                      height: 14,
                      flexDirection: 'row',
                      gap: 2,
                      padding: 2,
                      borderRadius: 999,
                      borderWidth: 1,
                      borderColor: colors.hairline,
                      overflow: 'hidden',
                    }}
                  >
                    {snapshot.layers.filter(layer => layer.tokens > 0).map(layer => (
                      <View
                        key={layer.id}
                        style={{
                          flex: Math.max(1, layer.tokens),
                          borderRadius: 999,
                          backgroundColor: `hsl(${layer.hue}, 72%, ${isDark ? 56 : 47}%)`,
                        }}
                      />
                    ))}
                    <View
                      style={{
                        flex: Math.max(1, snapshot.free_tokens),
                        borderRadius: 999,
                        backgroundColor: colors.borderStrong,
                      }}
                    />
                  </View>
                </View>
              </Card>

              <View
                style={{
                  flexDirection: 'row',
                  padding: 3,
                  marginBottom: spacing.sm,
                  borderRadius: 10,
                  borderWidth: 1,
                  borderColor: colors.hairline,
                }}
              >
                {(['heatmap', 'churn'] as ContextView[]).map(view => (
                  <TouchableOpacity
                    key={view}
                    onPress={() => setContextView(view)}
                    style={{
                      flex: 1,
                      alignItems: 'center',
                      paddingVertical: spacing.sm,
                      borderRadius: 7,
                      backgroundColor: contextView === view ? colors.accentSoft : 'transparent',
                    }}
                  >
                    <Text variant="xs" style={{ color: contextView === view ? colors.accent : colors.textDim }}>
                      {view === 'heatmap' ? 'Evolution heatmap' : 'Churn pulse'}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
                  <View>
                    <Text variant="sm" style={{ fontWeight: '600' }}>
                      {contextView === 'heatmap' ? 'Layer evolution' : 'Context churn'}
                    </Text>
                    <Text variant="xs" style={{ color: colors.textDim }}>
                      {contextView === 'heatmap'
                        ? 'Brightness = content replaced at each provider call'
                        : 'Replacement rate; teal bars mark compaction'}
                    </Text>
                  </View>
                  <Badge label="live" variant="success" />
                </View>

                {contextPoints.length === 0 ? (
                  <View style={{ minHeight: 140, alignItems: 'center', justifyContent: 'center' }}>
                    <Text variant="xs" style={{ color: colors.textDim }}>
                      Waiting for the first provider call…
                    </Text>
                  </View>
                ) : contextView === 'heatmap' ? (
                  <View style={{ flexDirection: 'row' }}>
                    <View style={{ width: 38, paddingRight: spacing.xs }}>
                      {snapshot.layers.map(layer => (
                        <View key={layer.id} style={{ height: 20, justifyContent: 'center' }}>
                          <Text variant="xs" style={{ color: colors.textDim, fontFamily: 'monospace', fontSize: 10 }}>
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
                                <TouchableOpacity
                                  key={point.id}
                                  onPress={() => setSelectedContextPoint(point)}
                                  style={{
                                    width: 9,
                                    height: 16,
                                    marginRight: 2,
                                    borderRadius: 2,
                                    borderWidth: activeContextPoint?.id === point.id ? 1 : 0,
                                    borderColor: colors.text,
                                    backgroundColor: `hsla(${layer.hue}, 78%, ${changed ? (isDark ? 60 : 44) : (isDark ? 46 : 67)}%, ${alpha})`,
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
                    <View style={{ height: 150, flexDirection: 'row', alignItems: 'flex-end', gap: 3, paddingTop: spacing.sm }}>
                      {contextPoints.map(point => (
                        <TouchableOpacity
                          key={point.id}
                          onPress={() => setSelectedContextPoint(point)}
                          style={{
                            width: 8,
                            height: Math.max(3, (point.churn_score || 0) / maxChurn * 128),
                            borderRadius: 4,
                            backgroundColor: point.compaction
                              ? (isDark ? '#2dd4bf' : colors.success)
                              : (isDark ? '#f471a5' : '#b83269'),
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
                      <Text variant="sm" style={{ fontWeight: '600' }}>
                        Provider call {activeContextPoint.id}
                      </Text>
                      <Text variant="xs" style={{ color: colors.textDim }}>
                        {new Date(activeContextPoint.at * 1000).toLocaleTimeString()}
                      </Text>
                    </View>
                    <Badge
                      label={(activeContextPoint.total_delta > 0 ? '+' : '') + activeContextPoint.total_delta + ' tok'}
                      variant={activeContextPoint.total_delta < 0 ? 'success' : 'neutral'}
                    />
                  </View>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm }}>
                    <Badge label={activeContextPoint.total_tokens.toLocaleString() + ' total'} />
                    <Badge label={activeContextPoint.churn_score + '% churn'} />
                    <Badge label={activeContextPoint.changed_layers + ' changed'} />
                    {activeContextPoint.compaction && <Badge label="compacted" variant="success" />}
                  </View>
                  {activeContextPoint.layers.map(layer => {
                    const currentLayer = snapshot.layers.find(item => item.id === layer.id);
                    return (
                      <TouchableOpacity
                        key={layer.id}
                        onPress={() => currentLayer && openLayer(currentLayer)}
                        style={{ flexDirection: 'row', alignItems: 'center', minHeight: 34 }}
                      >
                        <View
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: 3,
                            marginRight: spacing.sm,
                            backgroundColor: `hsl(${layer.hue}, 72%, ${isDark ? 56 : 47}%)`,
                          }}
                        />
                        <Text variant="xs" style={{ width: 34, fontFamily: 'monospace', color: colors.textDim }}>
                          {layer.id}
                        </Text>
                        <Text variant="xs" style={{ flex: 1 }} numberOfLines={1}>{layer.name}</Text>
                        <Text variant="xs" style={{ color: layer.changed ? colors.warning : colors.textDim, fontVariant: ['tabular-nums'] }}>
                          {layer.tokens.toLocaleString()} · Δ{layer.changed_chunks}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </Card>
              )}

              <Text variant="xs" style={{ color: colors.textDim, textAlign: 'center', marginVertical: spacing.sm }}>
                Session-run history stores measurements and hashes, never prompt text.
              </Text>
            </>
          )}
        </ScrollView>
      )}

      <SafeAreaModal
        visible={!!detail}
        transparent
        animationType="slide"
        onRequestClose={() => setDetail(null)}
      >
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' }}>
          <View
            style={{
              backgroundColor: colors.bgLift,
              borderTopLeftRadius: 16,
              borderTopRightRadius: 16,
              padding: spacing.base,
              maxHeight: '92%',
            }}
          >
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>Memory detail</Text>
              <TouchableOpacity onPress={() => setDetail(null)}>
                <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
              </TouchableOpacity>
            </View>
            {detail && (
              <ScrollView keyboardShouldPersistTaps="handled">
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm }}>
                  <Badge label={detail.memory.scope.type} variant="accent" />
                  <Badge label={detail.memory.kind} />
                  <Badge label={'confidence ' + detail.memory.trust.confidence.toFixed(2)} />
                </View>
                <Input
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
                <Text variant="sm" style={{ fontWeight: '600', marginTop: spacing.sm }}>
                  Sources
                </Text>
                <Text variant="xs" style={{ color: colors.textDim, fontFamily: 'monospace' }}>
                  {JSON.stringify(detail.memory.source_refs, null, 2)}
                </Text>
                <Text variant="sm" style={{ fontWeight: '600', marginTop: spacing.base }}>
                  Relationships
                </Text>
                {(detail.graph.edges || []).map((edge, index) => (
                  <Text key={edge.source + edge.target + index} variant="xs" style={{ color: colors.textDim }}>
                    {edge.source.slice(0, 8)} —{edge.type}→ {edge.target.slice(0, 8)}
                  </Text>
                ))}
                {!detail.graph.edges.length && (
                  <Text variant="xs" style={{ color: colors.textDim }}>No related memories.</Text>
                )}
                <Text variant="sm" style={{ fontWeight: '600', marginTop: spacing.base }}>
                  Timeline
                </Text>
                {detail.events.map((event, index) => (
                  <Card key={String(event.event_id || index)} style={{ marginTop: spacing.sm }}>
                    <Text variant="xs" style={{ fontFamily: 'monospace' }}>
                      {String(event.type || 'event')} · {String(event.actor || 'system')}
                    </Text>
                    <Text variant="xs" style={{ color: colors.textDim }}>
                      {JSON.stringify(event.after || {})}
                    </Text>
                  </Card>
                ))}
              </ScrollView>
            )}
          </View>
        </View>
      </SafeAreaModal>

      <SafeAreaModal
        visible={!!receipt}
        transparent
        animationType="slide"
        onRequestClose={() => setReceipt(null)}
      >
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center' }}>
          <View style={{ margin: spacing.base, backgroundColor: colors.bgLift, borderRadius: 12, padding: spacing.base, maxHeight: '85%' }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>Why memory was used</Text>
              <TouchableOpacity onPress={() => setReceipt(null)}>
                <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
              </TouchableOpacity>
            </View>
            <ScrollView>
              <Text variant="xs" style={{ color: colors.textDim, fontFamily: 'monospace' }}>
                {receipt ? JSON.stringify(receipt, null, 2) : ''}
              </Text>
            </ScrollView>
          </View>
        </View>
      </SafeAreaModal>

      <SafeAreaModal
        visible={!!selectedLayer}
        transparent
        animationType="slide"
        onRequestClose={() => setSelectedLayer(null)}
      >
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center' }}>
          <View style={{ margin: spacing.base, backgroundColor: colors.bgLift, borderRadius: 12, padding: spacing.base, maxHeight: '85%' }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm }}>
              <Text variant="base" style={{ fontWeight: '600' }}>{selectedLayer?.name}</Text>
              <TouchableOpacity onPress={() => setSelectedLayer(null)}>
                <Text variant="sm" style={{ color: colors.accent }}>Close</Text>
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 400 }}>
              {layerLoading ? (
                <Skeleton height={40} />
              ) : (
                <Text variant="xs" style={{ color: colors.text, fontFamily: 'monospace' }}>
                  {selectedLayer?.content}
                </Text>
              )}
            </ScrollView>
          </View>
        </View>
      </SafeAreaModal>
    </SafeAreaView>
  );
}
