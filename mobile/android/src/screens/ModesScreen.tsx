import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Card, Skeleton, ErrorState, EmptyState, Badge } from '../components';
import { modesApi, ModeInfo } from '../api/modes';
import { spacing } from '../theme/tokens';

const MODE_SURFACES: Record<string, { title: string; lenses: string[]; purpose: string }> = {
  default: { title: 'General workspace', lenses: ['plan', 'tools', 'verification'], purpose: 'Flexible coding and codebase assistance.' },
  research: { title: 'Evidence desk', lenses: ['claims', 'sources', 'citations'], purpose: 'Build source-backed conclusions and expose coverage gaps.' },
  security: { title: 'Verification bench', lenses: ['risk', 'proof', 'fixes'], purpose: 'Gate security findings on reproducible proof and verified remediation.' },
  debug: { title: 'Investigation board', lenses: ['hypotheses', 'observations', 'root cause'], purpose: 'Test competing explanations and track what changes belief.' },
  loop: { title: 'Mission control', lenses: ['queue', 'workstreams', 'checkpoints'], purpose: 'Keep autonomous work oriented, inspectable, and stoppable.' },
  feature: { title: 'Delivery workspace', lenses: ['tasks', 'criteria', 'reviews'], purpose: 'Drive an approved plan through acceptance evidence.' },
  teacher: { title: 'Learning studio', lenses: ['curriculum', 'mastery', 'reviews'], purpose: 'Separate course progress from demonstrated understanding.' },
};

export function ModesScreen() {
  const { colors } = useTheme();
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selecting, setSelecting] = useState<string | null>(null);
  const [hasWorkspace, setHasWorkspace] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await modesApi.list();
      setModes(res.modes);
      setCurrent(res.current);
      setHasWorkspace(res.has_workspace);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load();
    }, [load]),
  );

  const selectMode = async (name: string) => {
    setSelecting(name);
    try {
      await modesApi.set(name);
      setCurrent(name);
      setModes(prev => prev.map(m => ({ ...m, is_current: m.name === name })));
    } catch (e) {
      setError(String(e));
    } finally {
      setSelecting(null);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <View style={{ padding: spacing.base }}>
          {[1, 2, 3, 4].map(i => (
            <Skeleton key={i} height={64} style={{ marginBottom: spacing.sm }} />
          ))}
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <ErrorState message={error} onRetry={load} />
      </SafeAreaView>
    );
  }

  if (modes.length === 0) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
        <EmptyState title="No modes" message="No agent modes available" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <FlatList
        data={modes}
        keyExtractor={item => item.name}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
        contentContainerStyle={{ padding: spacing.base, paddingBottom: spacing['2xl'] }}
        ListHeaderComponent={
          <Card elevated style={{ marginBottom: spacing.md }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
              <Text variant="xs" style={{ color: colors.accent, fontWeight: '700', letterSpacing: 1.3 }}>MODE OS</Text>
              <Badge label={hasWorkspace ? 'workspace attached' : 'chat only'} variant={hasWorkspace ? 'success' : 'warning'} />
            </View>
            <Text variant="lg" style={{ color: colors.text, fontWeight: '600', marginTop: spacing.md }}>Choose the operating harness</Text>
            <Text variant="xs" style={{ color: colors.textSoft, marginTop: 5, lineHeight: 18 }}>
              A mode changes how the model investigates and acts. Its explorer then presents the facts, evidence, and controls that belong to that workflow.
            </Text>
          </Card>
        }
        renderItem={({ item }) => {
          const isActive = current === item.name;
          const surface = MODE_SURFACES[item.name] || { title: item.display_name, lenses: [], purpose: item.description };
          const accent = modeAccent(item.name, colors);
          return (
            <TouchableOpacity
              onPress={() => !item.disabled && selectMode(item.name)}
              disabled={item.disabled || selecting !== null}
              activeOpacity={0.7}
            >
              <Card elevated={isActive} style={{ marginBottom: spacing.sm, minHeight: 44, opacity: item.disabled ? 0.48 : 1, borderColor: isActive ? accent : colors.border }}>
                <View style={{ position: 'absolute', top: 13, bottom: 13, left: 0, width: 2, borderRadius: 1, backgroundColor: accent, opacity: isActive ? 1 : .45 }} />
                <View style={{ flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm }}>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}><Text variant="base" style={{ color: colors.text, fontWeight: '600' }}>{surface.title}</Text>{isActive && <Badge label="active" variant="accent" />}{item.disabled && <Badge label="workspace required" variant="warning" />}</View>
                    <Text variant="xs" style={{ color: colors.textSoft, marginTop: 4, lineHeight: 18 }}>{surface.purpose}</Text>
                    <View style={{ flexDirection: 'row', gap: 5, flexWrap: 'wrap', marginTop: spacing.sm }}>{surface.lenses.map(lens => <View key={lens} style={{ paddingHorizontal: 7, paddingVertical: 3, borderRadius: 999, backgroundColor: colors.bgHover }}><Text variant="xs" style={{ color: colors.textDim }}>{lens}</Text></View>)}</View>
                  </View>
                  {isActive && <Ionicons name="checkmark-circle" size={20} color={accent} />}
                  {selecting === item.name && !isActive && <Ionicons name="hourglass-outline" size={18} color={colors.textDim} />}
                </View>
              </Card>
            </TouchableOpacity>
          );
        }}
      />
    </SafeAreaView>
  );
}

function modeAccent(mode: string, colors: ReturnType<typeof useTheme>['colors']) {
  if (mode === 'research') return colors.info;
  if (mode === 'security') return colors.error;
  if (mode === 'debug') return colors.warning;
  if (mode === 'loop') return colors.success;
  if (mode === 'teacher') return colors.accentStrong;
  return colors.accent;
}
