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
  MemoryLayer,
  MemorySnapshot,
  RecallReceipt,
  memoryApi,
} from '../api/memory';
import { spacing } from '../theme/tokens';
import { SafeAreaModal } from '../components/SafeAreaModal';


type MemoryTab = 'memories' | 'context';


export function MemoryScreen() {
  const { colors } = useTheme();
  const [tab, setTab] = useState<MemoryTab>('memories');
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
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
      const [durable, context] = await Promise.all([
        memoryApi.listDurable(query),
        memoryApi.getState(),
      ]);
      setLedger(durable);
      setSnapshot(context);
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

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
        {tabButton('memories', 'Memories')}
        {tabButton('context', 'Context Map')}
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
              title="No context map yet"
              message="Send a prompt to see exactly what was placed in the model context."
            />
          ) : (
            <>
              <Card style={{ marginBottom: spacing.sm }}>
                <Text variant="base" style={{ fontWeight: '600' }}>Context Window</Text>
                {[
                  ['Total tokens', snapshot.total_tokens],
                  ['Context limit', snapshot.context_limit],
                  ['Free tokens', snapshot.free_tokens],
                  ['Fill', String(snapshot.fill_pct) + '%'],
                ].map(([label, value]) => (
                  <View
                    key={String(label)}
                    style={{ flexDirection: 'row', justifyContent: 'space-between', minHeight: 28 }}
                  >
                    <Text variant="sm" style={{ color: colors.textDim }}>{label}</Text>
                    <Text variant="sm" style={{ fontVariant: ['tabular-nums'] }}>{value}</Text>
                  </View>
                ))}
              </Card>
              <Text variant="sm" style={{ fontWeight: '500', marginBottom: spacing.sm }}>
                Provider-visible layers
              </Text>
              {snapshot.layers.map(layer => (
                <TouchableOpacity key={layer.id} onPress={() => openLayer(layer)}>
                  <Card style={{ marginBottom: spacing.sm, minHeight: 44 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                      <View style={{ flex: 1 }}>
                        <Text variant="sm" style={{ fontWeight: '500' }}>{layer.name}</Text>
                        <Text variant="xs" style={{ color: colors.textDim }}>
                          {layer.tokens} tokens · {layer.fill_pct}%
                        </Text>
                      </View>
                      <Badge label={String(layer.change_count)} />
                    </View>
                  </Card>
                </TouchableOpacity>
              ))}
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
