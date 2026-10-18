import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { ModeWorkspaceContract, ModeWorkspaceTone } from '../api/modeWorkspace';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, fonts, spacing, touchTarget, tracking, typography } from '../theme/tokens';
import { Pressable, Text, Icon } from '../ui';

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

// Mode identity is neutral: the active mode is carried by its label, and the
// single accent marks only selection. `mode` is kept for call compatibility.
function workspaceAccent(_mode: string, colors: ReturnType<typeof useTheme>['colors']) {
  return colors.accent.ink;
}

function toneColor(tone: ModeWorkspaceTone, colors: ReturnType<typeof useTheme>['colors'], accent = colors.accent.ink) {
  if (tone === 'risk') return colors.danger.ink;
  if (tone === 'warn') return colors.attention.ink;
  if (tone === 'good') return colors.ok.ink;
  if (tone === 'active') return accent;
  return colors.ink3;
}

export function ModeWorkspaceHeader({
  workspace,
  selectedView,
  onSelectView,
}: {
  workspace: ModeWorkspaceContract;
  selectedView: string;
  onSelectView: (view: string) => void;
}) {
  const { colors, spacing } = useTheme();
  const [qualityOpen, setQualityOpen] = useState(false);
  const modeAccent = workspaceAccent(workspace.mode, colors);

  return (
    <View
      style={{
        marginBottom: spacing.xl,
        paddingBottom: spacing.lg,
        borderBottomWidth: 1,
        borderBottomColor: colors.hairline,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <Text variant="xs" style={{ color: colors.ink3, fontWeight: fontWeights.semibold }}>MODE</Text>
        <Text variant="xs" style={{ color: colors.ink3 }}>/</Text>
        <Text variant="xs" style={{ color: toneColor(workspace.status.tone, colors, modeAccent) }}>{workspace.status.label}</Text>
      </View>

      <Text variant="lg" style={{ marginTop: spacing.lg, color: colors.ink, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight }}>
        {workspace.title}
      </Text>
      <Text variant="xs" style={{ marginTop: spacing.xs, color: colors.ink2, lineHeight: typography.xs.lineHeight }}>
        {workspace.objective}
      </Text>

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: spacing.lg, marginTop: spacing.lg }}>
        {workspace.metrics.map(metric => (
          <View
            key={metric.id}
            style={{
              flexBasis: '44%',
              flexGrow: 1,
              paddingTop: spacing.sm,
              paddingBottom: spacing.md,
            }}
          >
            <Text variant="base" style={{ color: toneColor(metric.tone, colors, modeAccent), fontFamily: fonts.mono, fontWeight: fontWeights.semibold, fontVariant: ['tabular-nums'] }}>
              {String(metric.value)}
            </Text>
            <Text variant="xs" numberOfLines={1} style={{ marginTop: 1, color: colors.ink3 }}>
              {metric.label}
            </Text>
          </View>
        ))}
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.lg, paddingTop: spacing.md }}>
        {workspace.views.map(view => {
          const active = selectedView === view.id;
          return (
            <Pressable
              key={view.id}
              onPress={() => onSelectView(view.id)}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              style={({ pressed }) => ({
                minHeight: touchTarget,
                flexDirection: 'row',
                alignItems: 'center',
                gap: spacing.xs,
                paddingHorizontal: 1,
                paddingBottom: spacing.xs,
                borderBottomWidth: 2,
                borderBottomColor: active ? colors.accent.ink : 'transparent',
                opacity: pressed ? 0.7 : 1,
              })}
            >
              <Text variant="xs" style={{ color: active ? colors.ink : colors.ink3, fontWeight: active ? fontWeights.semibold : fontWeights.regular }}>{view.label}</Text>
              {view.count !== undefined && <Text variant="xs" style={{ color: colors.ink3, fontVariant: ['tabular-nums'] }}>· {view.count}</Text>}
            </Pressable>
          );
        })}
      </ScrollView>

      <Pressable
        onPress={() => setQualityOpen(value => !value)}
        accessibilityRole="button"
        accessibilityState={{ expanded: qualityOpen }}
        style={({ pressed }) => ({ minHeight: touchTarget, marginTop: spacing.md, paddingTop: spacing.sm, borderTopWidth: 1, borderTopColor: colors.hairline, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', opacity: pressed ? 0.7 : 1 })}
      >
        <Text variant="xs" style={{ color: colors.ink3 }}>How accuracy, relevance, and evidence are assessed</Text>
        <Icon name={qualityOpen ? 'chevron-up' : 'chevron-down'} size="sm" color={colors.ink3} />
      </Pressable>
      {qualityOpen && (
        <View style={{ paddingTop: spacing.sm }}>
          {workspace.quality.map(item => (
            <View key={item.id} style={{ paddingVertical: spacing.sm }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm }}>
                <Text variant="xs" style={{ color: colors.ink, fontWeight: fontWeights.semibold }}>{item.label}</Text>
                <Text variant="xs" style={{ color: colors.ink2 }}>{item.state.replace(/-/g, ' ')}</Text>
              </View>
              <Text variant="xs" style={{ color: colors.ink3, marginTop: spacing.xs, lineHeight: typography.xs.lineHeight }}>{item.detail}</Text>
            </View>
          ))}
        </View>
      )}

      <Text variant="xs" numberOfLines={1} style={{ marginTop: spacing.md, color: colors.ink3, fontFamily: fonts.mono }}>
        Source · {workspace.provenance}
      </Text>
    </View>
  );
}
