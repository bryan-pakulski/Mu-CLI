// Chat header (G§8.3): the μ mark opens sessions; the session name in
// title-3 plum with the provider · model line; safety states (plan mode,
// auto-approve) are always visible here (G§10.4); a compact context ring
// opens the breakdown; trace, work and settings are icon buttons. Settings
// is a real screen under More, not a sheet.

import React, { useCallback, useEffect, useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { memoryApi } from '../api/memory';
import { normaliseFill, useContextFillStore } from '../store/contextFill';
import { PlanModeBadge } from './PlanModeIndicator';
import { radii, spacing } from '../theme/tokens';
import { Chip, ContextRing, IconButton, Pressable, Text } from '../ui';

export type ModernHeaderProps = {
  onOpenSessions: () => void;
  onOpenWork: () => void;
  onOpenTraces: () => void;
  onOpenConnection: () => void;
  onOpenSettings: () => void;
  onOpenContext: () => void;
};

/**
 * Context fill for the header ring (display only; G§9.14). The SSE
 * `context_snapshot` event, reported by the chat session hook, is the
 * source; /api/memory/state is polled only until the first snapshot for the
 * open session arrives (for example a freshly opened, idle session).
 */
export const CONTEXT_POLL_MS = 20_000;

export function useContextFill(sessionName: string | null, isConnected: boolean): number | null {
  const [polledFill, setPolledFill] = useState<number | null>(null);
  const snapshotFill = useContextFillStore(s => (sessionName && s.sessionName === sessionName ? s.fill : null));
  const hasSnapshot = snapshotFill != null;
  useEffect(() => {
    setPolledFill(null);
  }, [sessionName]);
  useEffect(() => {
    if (!sessionName || !isConnected || hasSnapshot) return undefined;
    let cancelled = false;
    const load = () => {
      memoryApi.getState(8, 4)
        .then(state => {
          if (cancelled || !state || !state.active) return;
          const fill = normaliseFill(state.fill_pct);
          if (fill != null) setPolledFill(fill);
        })
        .catch(() => undefined);
    };
    load();
    const timer = setInterval(load, CONTEXT_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [hasSnapshot, isConnected, sessionName]);
  if (!sessionName || !isConnected) return null;
  return snapshotFill ?? polledFill;
}

export function ModernHeader({
  onOpenSessions,
  onOpenWork,
  onOpenTraces,
  onOpenConnection,
  onOpenSettings,
  onOpenContext,
}: ModernHeaderProps) {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(s => s.activeSessionName);
  const activeProvider = useConnectionStore(s => s.activeProvider);
  const activeModel = useConnectionStore(s => s.activeModel);
  const isConnected = useConnectionStore(s => s.isConnected);
  const yolo = useConnectionStore(s => s.yolo);
  const pendingMutations = useConnectionStore(s => s.pendingMutations);
  const lastReplay = useConnectionStore(s => s.lastReplay);
  const fill = useContextFill(activeSessionName, isConnected);

  const sessionTitle = activeSessionName || 'New session';
  const sessionMeta = [activeProvider, activeModel].filter(Boolean).join(' · ') || (isConnected ? 'Connected' : 'Connect to Mucli');

  // User-visible count of queued offline mutations, plus a conflict notice
  // when a replay dropped stale state (409 from If-Match).
  const queueBadge = pendingMutations > 0 ? ` · ${pendingMutations} queued` : '';
  useEffect(() => {
    if (lastReplay && lastReplay.conflicts > 0) {
      Alert.alert(
        'Changes applied elsewhere',
        `${lastReplay.conflicts} queued change${lastReplay.conflicts === 1 ? '' : 's'} conflicted with newer session state and was dropped.`,
      );
    }
  }, [lastReplay]);

  const openContext = useCallback(() => onOpenContext(), [onOpenContext]);
  const titleContent = (
    <>
      <View style={styles.titleRow}>
        <Text variant="title3" numberOfLines={1} style={styles.title}>{sessionTitle}</Text>
        <View accessibilityLabel={isConnected ? 'Live' : 'Offline'} style={[styles.statusDot, { backgroundColor: isConnected ? colors.ok.pigment : colors.danger.pigment }]} />
      </View>
      <Text variant="caption" numeric numberOfLines={1} style={{ color: isConnected ? colors.ink3 : colors.danger.ink }}>
        {sessionMeta}{queueBadge}
      </Text>
    </>
  );

  return (
    <View style={[styles.container, { backgroundColor: colors.canvas, borderBottomColor: colors.hairline, paddingTop: insets.top + spacing.xs }]}>
      <Pressable accessibilityLabel="Open sessions" onPress={onOpenSessions} style={styles.mark}>
        <Text variant="title2" style={{ color: colors.plum }}>μ</Text>
      </Pressable>

      {/* Connected: the title is information, not a control (a disabled
          pressable would dim it). Offline: tap to configure the connection. */}
      {isConnected ? (
        <View accessible accessibilityLabel={`Current session ${sessionTitle}, ${sessionMeta}${queueBadge}`} style={styles.titleBlock}>
          {titleContent}
        </View>
      ) : (
        <Pressable accessibilityLabel="Configure Mucli connection" onPress={onOpenConnection} scale={false} style={styles.titleBlock}>
          {titleContent}
        </Pressable>
      )}

      <View style={styles.actions}>
        <PlanModeBadge />
        {yolo ? <Chip tone="caution" testID="header-yolo" accessibilityLabel="Auto-approve is on">auto</Chip> : null}
        {fill != null ? <ContextRing fill={fill} showLabel={false} onPress={openContext} testID="header-context-ring" /> : null}
        <IconButton icon="briefcase" label="Open engineering work" onPress={onOpenWork} disabled={!isConnected} />
        <IconButton icon="activity" label="Open Trace Analyzer" onPress={onOpenTraces} disabled={!activeSessionName} />
        <IconButton icon="settings" label="Open settings" onPress={onOpenSettings} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    minHeight: 64,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.sm,
    paddingBottom: spacing.sm,
    borderBottomWidth: 1,
  },
  mark: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radii.pill },
  titleBlock: { flex: 1, alignItems: 'flex-start', paddingHorizontal: spacing.xs },
  titleRow: { maxWidth: '100%', flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  title: { flexShrink: 1 },
  statusDot: { width: 6, height: 6, borderRadius: radii.pill },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 0 },
});
