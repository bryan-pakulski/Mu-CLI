import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { ModeWorkspaceContract, ModeWorkspaceTone } from '../api/modeWorkspace';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

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

function workspaceAccent(mode: string, colors: ReturnType<typeof useTheme>['colors']) {
  if (mode === 'research') return colors.info;
  if (mode === 'security') return colors.error;
  if (mode === 'debug') return colors.warning;
  if (mode === 'loop') return colors.success;
  if (mode === 'teacher') return colors.accentStrong;
  return colors.accent;
}

function toneColor(tone: ModeWorkspaceTone, colors: ReturnType<typeof useTheme>['colors'], accent = colors.accent) {
  if (tone === 'risk') return colors.error;
  if (tone === 'warn') return colors.warning;
  if (tone === 'good') return colors.success;
  if (tone === 'active') return accent;
  return colors.textDim;
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
  const { colors, spacing, radii } = useTheme();
  const [qualityOpen, setQualityOpen] = useState(false);
  const modeAccent = workspaceAccent(workspace.mode, colors);

  return (
    <View
      style={{
        marginBottom: spacing.md,
        padding: spacing.base,
        borderRadius: radii.lg,
        borderWidth: StyleSheet.hairlineWidth,
        borderColor: colors.borderStrong,
        backgroundColor: colors.glass,
        overflow: 'hidden',
      }}
    >
      <View style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: 2, backgroundColor: modeAccent }} />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <Text variant="xs" style={{ color: modeAccent, fontWeight: '700', letterSpacing: 1.4 }}>MODE OS</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: colors.accentSoft }}>
          <View style={{ width: 5, height: 5, borderRadius: 3, backgroundColor: toneColor(workspace.status.tone, colors, modeAccent) }} />
          <Text variant="xs" style={{ color: colors.textSoft }}>{workspace.status.label}</Text>
        </View>
      </View>

      <Text variant="lg" style={{ marginTop: spacing.md, color: colors.text, fontWeight: '600', letterSpacing: -0.35 }}>
        {workspace.title}
      </Text>
      <Text variant="xs" style={{ marginTop: 5, color: colors.textSoft, lineHeight: 18 }}>
        {workspace.objective}
      </Text>

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: spacing.md, borderRadius: 12, overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border }}>
        {workspace.metrics.map((metric, index) => (
          <View
            key={metric.id}
            style={{
              width: '50%',
              paddingHorizontal: spacing.md,
              paddingVertical: 10,
              borderRightWidth: index % 2 === 0 ? StyleSheet.hairlineWidth : 0,
              borderBottomWidth: index < workspace.metrics.length - 2 ? StyleSheet.hairlineWidth : 0,
              borderColor: colors.border,
              backgroundColor: colors.bgLift,
            }}
          >
            <Text variant="base" style={{ color: toneColor(metric.tone, colors, modeAccent), fontFamily: 'monospace', fontWeight: '600' }}>
              {String(metric.value)}
            </Text>
            <Text variant="xs" numberOfLines={1} style={{ marginTop: 1, color: colors.textDim, letterSpacing: .45, textTransform: 'uppercase' }}>
              {metric.label}
            </Text>
          </View>
        ))}
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 4, paddingVertical: spacing.md }}>
        {workspace.views.map(view => {
          const active = selectedView === view.id;
          return (
            <Pressable
              key={view.id}
              onPress={() => onSelectView(view.id)}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              style={{
                minHeight: 36,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 5,
                paddingHorizontal: 11,
                borderRadius: 9,
                borderWidth: StyleSheet.hairlineWidth,
                borderColor: active ? colors.borderStrong : 'transparent',
                backgroundColor: active ? colors.accentSoft : colors.bgHover,
              }}
            >
              <Text variant="xs" style={{ color: active ? colors.text : colors.textDim, fontWeight: active ? '600' : '400' }}>{view.label}</Text>
              {view.count !== undefined && <Text variant="xs" style={{ color: modeAccent, fontFamily: 'monospace' }}>{view.count}</Text>}
            </Pressable>
          );
        })}
      </ScrollView>

      <Pressable
        onPress={() => setQualityOpen(value => !value)}
        accessibilityRole="button"
        accessibilityState={{ expanded: qualityOpen }}
        style={{ minHeight: 36, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}
      >
        <Text variant="xs" style={{ color: colors.textDim }}>How accuracy, relevance, and evidence are assessed</Text>
        <Ionicons name={qualityOpen ? 'chevron-up' : 'chevron-down'} size={15} color={colors.textDim} />
      </Pressable>
      {qualityOpen && (
        <View style={{ gap: spacing.sm, paddingTop: spacing.xs }}>
          {workspace.quality.map(item => (
            <View key={item.id} style={{ padding: spacing.md, borderRadius: 10, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border, backgroundColor: colors.bgLift }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm }}>
                <Text variant="xs" style={{ color: colors.text, fontWeight: '600' }}>{item.label}</Text>
                <Text variant="xs" style={{ color: modeAccent, textTransform: 'uppercase', letterSpacing: .5 }}>{item.state.replace(/-/g, ' ')}</Text>
              </View>
              <Text variant="xs" style={{ color: colors.textDim, marginTop: 5, lineHeight: 17 }}>{item.detail}</Text>
            </View>
          ))}
        </View>
      )}

      <Text variant="xs" numberOfLines={1} style={{ marginTop: spacing.xs, color: colors.textDim, fontFamily: 'monospace' }}>
        ↳ {workspace.provenance}
      </Text>
    </View>
  );
}
