// Modes: choose the operating harness. A mode changes how the model investigates and acts; its
// panel presents the facts, evidence and controls of that workflow.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { modesApi, type ModeInfo } from '../api/modes';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { spacing } from '../theme/tokens';
import { Banner, Chip, Row, Text } from '../ui';

const MODE_SURFACES: Record<string, { title: string; lenses: string[]; purpose: string }> = {
  default: { title: 'General workspace', lenses: ['plan', 'tools', 'turn receipts'], purpose: 'Flexible coding and codebase assistance; each turn says what changed and whether a check passed after the edit.' },
  research: { title: 'Evidence desk', lenses: ['claims', 'sources', 'citations'], purpose: 'Build source-backed conclusions and expose coverage gaps.' },
  security: { title: 'Verification bench', lenses: ['risk', 'proof', 'fixes'], purpose: 'Gate security findings on reproducible proof and verified remediation.' },
  debug: { title: 'Investigation board', lenses: ['case', 'hypotheses', 'runs', 'root causes'], purpose: 'Reproduce first, test competing explanations, and close a case only on a verified fix.' },
  loop: { title: 'Mission control', lenses: ['mission', 'timeline', 'backlog'], purpose: 'A goal, what done looks like and a budget; autonomous increments you can pause, resume or stop.' },
  feature: { title: 'Delivery workspace', lenses: ['plan', 'tasks', 'reviews'], purpose: 'You approve the plan; each task completes on criterion evidence.' },
  teacher: { title: 'Learning studio', lenses: ['path', 'mastery', 'reviews'], purpose: 'You approve the curriculum; lessons pass on demonstrated understanding, with spaced reviews.' },
};

export function ModesScreen() {
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<string | null>(null);
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
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const selectMode = async (name: string) => {
    setSelecting(name);
    setSelectError(null);
    try {
      await modesApi.set(name);
      setCurrent(name);
      setModes(prev => prev.map(m => ({ ...m, is_current: m.name === name })));
    } catch (e) {
      setSelectError(String(e));
    } finally {
      setSelecting(null);
    }
  };

  const where = sessionType === 'container' ? 'container workspace' : hasWorkspace ? 'workspace attached' : 'chat only';
  const currentTitle = current ? (MODE_SURFACES[current]?.title || modes.find(m => m.name === current)?.display_name || current) : null;
  return (
    <PanelPage testID="modes-screen" title="Modes"
      state={[currentTitle ? { text: currentTitle } : null, { text: where, tone: hasExecutionWorkspace ? 'ok' : 'attention' }]}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && !modes.length ? { title: 'No modes', message: 'No agent modes available.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}>
      <Text variant="body" tone="ink2" style={styles.lede}>
        Choose the operating harness. A mode changes how the model investigates and acts; its panel presents the facts, evidence and controls that belong to that workflow.
      </Text>
      {selectError ? <Banner tone="danger" title="Couldn't switch mode" testID="modes-error">{selectError}</Banner> : null}
      <PanelSection title="Harnesses" count={modes.length}>
        {modes.map(item => {
          const active = current === item.name;
          const surface = MODE_SURFACES[item.name] || { title: item.display_name, lenses: [], purpose: item.description };
          return (
            <Row key={item.name} testID={`mode-row-${item.name}`} selected={active}
              status={active ? 'done' : selecting === item.name ? 'active' : undefined}
              title={surface.title} when={active ? 'current' : undefined}
              accessibilityLabel={`${surface.title}${active ? ', current' : ''}${item.disabled ? ', workspace required' : ''}`}
              onPress={item.disabled || selecting !== null ? undefined : () => void selectMode(item.name)}
              sub={(
                <View style={styles.sub}>
                  <Text variant="caption" tone="ink2">{surface.purpose}</Text>
                  {surface.lenses.length ? <Text variant="caption" tone="ink3">{surface.lenses.join('  ·  ')}</Text> : null}
                  {item.disabled ? <Chip variant="meta" tone="attention" label="Workspace required" /> : null}
                </View>
              )} />
          );
        })}
      </PanelSection>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  lede: { marginBottom: spacing.base },
  sub: { gap: spacing.xs, alignItems: 'flex-start' },
});
