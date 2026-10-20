// A mode panel's workspace contract (mu/gui mode workspaces; api/modeWorkspace.ts) on the Mu page:
// the title and the status + metrics go into the page header (state sentence), the views become
// its text tabs, and the objective, the assessment notes and the provenance open the body.

import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { ModeWorkspaceContract, ModeWorkspaceTone } from '../api/modeWorkspace';
import { spacing } from '../theme/tokens';
import { Pressable, Text, Icon, type HeaderTab, type StatePart } from '../ui';
import { useTheme } from '../theme/ThemeContext';

const VIEW_STORAGE_PREFIX = 'mucli.mode-workspace.view.v1.';

export function useModeWorkspaceView(workspace: ModeWorkspaceContract | null | undefined) {
  const [selectedView, setSelectedView] = useState('overview');
  const mode = workspace?.mode;
  const available = useMemo(() => new Set((workspace?.views || []).map(view => view.id)), [workspace]);

  useEffect(() => {
    if (!mode) return;
    let current = true;
    AsyncStorage.getItem(VIEW_STORAGE_PREFIX + mode)
      .then(saved => {
        if (current) setSelectedView(saved && available.has(saved) ? saved : 'overview');
      })
      .catch(() => { /* best-effort UI preference */ });
    return () => { current = false; };
  }, [mode, available]);

  const selectView = useCallback((view: string) => {
    const next = available.has(view) ? view : 'overview';
    setSelectedView(next);
    if (mode) AsyncStorage.setItem(VIEW_STORAGE_PREFIX + mode, next).catch(() => {});
  }, [available, mode]);

  const shows = useCallback((...views: string[]) => (
    selectedView === 'overview' || views.includes(selectedView)
  ), [selectedView]);

  return { selectedView, selectView, shows };
}

function partTone(tone: ModeWorkspaceTone): StatePart['tone'] {
  if (tone === 'risk') return 'danger';
  if (tone === 'warn') return 'attention';
  if (tone === 'good') return 'ok';
  return '';
}

/** The status and the metrics as state-sentence parts: "Investigating · 3 hypotheses · 1 finding". */
export function workspaceState(workspace: ModeWorkspaceContract | null | undefined): StatePart[] {
  if (!workspace) return [];
  return [
    { text: workspace.status.label, tone: partTone(workspace.status.tone) },
    ...workspace.metrics.map(m => ({ text: `${m.value} ${m.label}`, tone: partTone(m.tone) })),
  ];
}

/** The workspace views as header tabs (with counts). */
export function workspaceTabs(workspace: ModeWorkspaceContract | null | undefined): HeaderTab[] {
  return (workspace?.views || []).map(v => ({ key: v.id, label: v.label, count: v.count ?? null }));
}

/** The objective, how the evidence is assessed (a disclosure) and where the data comes from. */
export function ModeWorkspaceIntro({ workspace }: { workspace: ModeWorkspaceContract }) {
  const { colors } = useTheme();
  const [qualityOpen, setQualityOpen] = useState(false);
  return (
    <View style={styles.intro} testID="mode-workspace-intro">
      {workspace.objective ? <Text variant="body" tone="ink2">{workspace.objective}</Text> : null}
      {workspace.quality.length ? (
        <Pressable onPress={() => setQualityOpen(v => !v)} accessibilityRole="button" accessibilityState={{ expanded: qualityOpen }} scale={false} style={styles.disclosure}>
          <Text variant="caption" tone="ink3" style={styles.flex}>How accuracy, relevance and evidence are assessed</Text>
          <Icon name={qualityOpen ? 'chevron-up' : 'chevron-down'} size="sm" color={colors.ink3} />
        </Pressable>
      ) : null}
      {qualityOpen ? workspace.quality.map(item => (
        <View key={item.id} style={styles.quality}>
          <View style={styles.qualityHead}>
            <Text variant="label" style={styles.flex}>{item.label}</Text>
            <Text variant="caption" tone="ink2">{item.state.replace(/-/g, ' ')}</Text>
          </View>
          <Text variant="caption" tone="ink3">{item.detail}</Text>
        </View>
      )) : null}
      {workspace.provenance ? <Text variant="monoSm" tone="ink3" numberOfLines={1}>Source · {workspace.provenance}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  intro: { gap: spacing.sm, marginBottom: spacing.lg },
  flex: { flex: 1 },
  disclosure: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 44 },
  quality: { gap: spacing.xs, paddingVertical: spacing.xs },
  qualityHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
