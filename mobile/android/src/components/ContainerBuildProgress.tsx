import React, { useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  Platform,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { fontSizes, fontWeights, fonts, radii, spacing, typography, touchTarget } from '../theme/tokens';

export type ContainerProgressLog = {
  seq: number;
  stream: string;
  text: string;
};

export type ContainerBuildProgressProps = {
  message: string;
  logs: ContainerProgressLog[];
  expanded: boolean;
  onToggle: () => void;
  running?: boolean;
  failed?: boolean;
};

export function ContainerBuildProgress({
  message,
  logs,
  expanded,
  onToggle,
  running = true,
  failed = false,
}: ContainerBuildProgressProps) {
  const { colors } = useTheme();
  const outputRef = useRef<ScrollView>(null);

  useEffect(() => {
    if (!expanded) return;
    const frame = requestAnimationFrame(() => {
      outputRef.current?.scrollToEnd({ animated: logs.length > 1 });
    });
    return () => cancelAnimationFrame(frame);
  }, [expanded, logs.length]);

  return (
    <View
      style={[
        styles.container,
        {
          backgroundColor: failed ? colors.errorSubtle : colors.surface,
          borderColor: colors.border,
          borderLeftColor: failed ? colors.danger : running ? colors.running : colors.ok,
        },
      ]}
    >
      <TouchableOpacity
        onPress={onToggle}
        activeOpacity={0.72}
        accessibilityRole="button"
        accessibilityLabel={`${expanded ? 'Hide' : 'Show'} container build output`}
        accessibilityHint="Displays live stdout and stderr from Docker container creation"
        style={styles.header}
      >
        {running ? (
          <ActivityIndicator color={colors.running} accessibilityLabel="in progress" />
        ) : (
          <Text
            variant="base"
            accessibilityLabel={failed ? 'failed' : 'done'}
            style={{ color: failed ? colors.danger : colors.ok, fontWeight: fontWeights.semibold }}
          >
            {failed ? '✗' : '✓'}
          </Text>
        )}
        <View style={styles.copy}>
          <Text variant="sm" style={{ fontWeight: fontWeights.semibold }}>
            {message || 'Preparing container…'}
          </Text>
          <Text variant="xs" dim>
            {expanded ? 'Hide Docker output' : 'Tap to view Docker output'}
          </Text>
        </View>
        <View style={styles.chevron}>
          <Ionicons
            name={expanded ? 'chevron-up' : 'chevron-down'}
            size={17}
            color={colors.textDim}
          />
        </View>
      </TouchableOpacity>

      {expanded ? (
        <View style={[styles.outputShell, { borderTopColor: colors.hairline, backgroundColor: colors.codeBg }]}>
          <View style={styles.outputHeader}>
            <Text variant="xs" dim style={styles.outputTitle}>stdout / stderr</Text>
            <Text variant="xs" dim style={{ fontVariant: ['tabular-nums'] }}>{logs.length} events</Text>
          </View>
          <ScrollView
            ref={outputRef}
            nestedScrollEnabled
            keyboardShouldPersistTaps="handled"
            style={styles.outputScroll}
            contentContainerStyle={styles.outputContent}
          >
            {logs.length ? logs.map(line => (
              <View key={`${line.seq}-${line.stream}`} style={styles.logEntry}>
                <Text
                  variant="xs"
                  style={{
                    color: line.stream === 'stderr' ? colors.error : colors.textDim,
                    fontWeight: fontWeights.semibold,
                  }}
                >
                  {line.stream || 'output'}
                </Text>
                <Text
                  selectable
                  style={[
                    styles.logText,
                    { color: line.stream === 'stderr' ? colors.error : colors.text },
                  ]}
                >
                  {line.text.replace(/\s+$/, '') || ' '}
                </Text>
              </View>
            )) : (
              <Text variant="xs" dim style={styles.waitingText}>
                Waiting for Docker output…
              </Text>
            )}
          </ScrollView>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginTop: spacing.md,
    borderWidth: 1,
    borderLeftWidth: 3,
    borderRadius: radii.panel,
    overflow: 'hidden',
  },
  header: {
    minHeight: 70,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  copy: { flex: 1, gap: spacing.xs },
  chevron: {
    width: touchTarget,
    height: touchTarget,
    borderRadius: radii.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  outputShell: {
    borderTopWidth: 1,
  },
  outputHeader: {
    minHeight: 40,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  outputTitle: {
    fontWeight: fontWeights.semibold,
  },
  outputScroll: { maxHeight: 280 },
  outputContent: { paddingHorizontal: spacing.md, paddingBottom: spacing.md },
  logEntry: { marginBottom: spacing.md, gap: spacing.xs },
  logText: {
    fontFamily: fonts.mono,
    fontSize: fontSizes.xs,
    lineHeight: typography.xs.lineHeight,
  },
  waitingText: { paddingVertical: spacing.base },
});
