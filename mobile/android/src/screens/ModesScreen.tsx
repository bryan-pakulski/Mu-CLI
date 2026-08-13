import React, { useState, useCallback } from 'react';
import { FlatList, View, RefreshControl, StyleSheet, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text, Skeleton, ErrorState, EmptyState } from '../components';
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
  const [hasExecutionWorkspace, setHasExecutionWorkspace] = useState(false);
  const [sessionType, setSessionType] = useState<'chat' | 'workspace' | 'container'>('workspace');

  const load = useCallback(async () => {
    try {
      setError(null);
      const res = await modesApi.list();
      setModes(res.modes);
      setCurrent(res.current);
      setHasWorkspace(res.has_workspace);
      setHasExecutionWorkspace(res.has_execution_workspace);
      setSessionType(res.session_type || 'workspace');
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
          <View style={[styles.header, { borderBottomColor: colors.hairline }]}>
            <View style={styles.eyebrowRow}>
              <Text variant="xs" style={{ color: colors.accent, fontWeight: '700', letterSpacing: 1.3 }}>MODES</Text>
              <Text variant="xs" style={{ color: colors.textDim }}>/</Text>
              <Text variant="xs" style={{ color: hasExecutionWorkspace ? colors.success : colors.warning }}>
                {sessionType === 'container' ? 'container workspace' : hasWorkspace ? 'workspace attached' : 'chat only'}
              </Text>
            </View>
            <Text variant="lg" style={styles.headerTitle}>Choose the operating harness</Text>
            <Text variant="xs" style={[styles.headerCopy, { color: colors.textSoft }]}>
              A mode changes how the model investigates and acts. Its explorer presents the facts, evidence, and controls that belong to that workflow.
            </Text>
          </View>
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
              style={[
                styles.modeRow,
                { borderBottomColor: colors.hairline, opacity: item.disabled ? 0.42 : 1 },
              ]}
            >
              <View style={[styles.modeRail, { backgroundColor: isActive ? accent : 'transparent' }]} />
              <View style={styles.modeCopy}>
                <View style={styles.modeTitleRow}>
                  <Text variant="base" style={{ color: colors.text, fontWeight: '600' }}>{surface.title}</Text>
                  {isActive && <Text variant="xs" style={{ color: accent }}>Current</Text>}
                  {item.disabled && <Text variant="xs" style={{ color: colors.warning }}>Workspace required</Text>}
                </View>
                <Text variant="xs" style={{ color: colors.textSoft, marginTop: 4, lineHeight: 18 }}>{surface.purpose}</Text>
                <Text variant="xs" style={[styles.lenses, { color: colors.textDim }]}>{surface.lenses.join('  ·  ')}</Text>
              </View>
              {isActive && <Ionicons name="checkmark" size={18} color={accent} />}
              {selecting === item.name && !isActive && <Ionicons name="hourglass-outline" size={17} color={colors.textDim} />}
            </TouchableOpacity>
          );
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  header: { paddingTop: spacing.sm, paddingBottom: spacing.lg, marginBottom: spacing.xs, borderBottomWidth: StyleSheet.hairlineWidth },
  eyebrowRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  headerTitle: { fontWeight: '600', marginTop: spacing.lg, letterSpacing: -0.3 },
  headerCopy: { marginTop: 5, lineHeight: 18, maxWidth: 430 },
  modeRow: { minHeight: 108, flexDirection: 'row', alignItems: 'center', paddingVertical: spacing.base, borderBottomWidth: StyleSheet.hairlineWidth },
  modeRail: { alignSelf: 'stretch', width: 1, marginRight: spacing.md },
  modeCopy: { flex: 1 },
  modeTitleRow: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm, flexWrap: 'wrap' },
  lenses: { marginTop: spacing.sm, fontFamily: 'monospace', letterSpacing: 0.15 },
});

function modeAccent(mode: string, colors: ReturnType<typeof useTheme>['colors']) {
  if (mode === 'research') return colors.info;
  if (mode === 'security') return colors.error;
  if (mode === 'debug') return colors.warning;
  if (mode === 'loop') return colors.success;
  if (mode === 'teacher') return colors.accentStrong;
  return colors.accent;
}
