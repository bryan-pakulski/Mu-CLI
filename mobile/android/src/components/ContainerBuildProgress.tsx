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
          backgroundColor: colors.bgLift,
          borderColor: failed ? colors.error : colors.border,
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
          <ActivityIndicator color={colors.accent} />
        ) : (
          <Ionicons
            name={failed ? 'alert-circle-outline' : 'checkmark-circle-outline'}
            size={20}
            color={failed ? colors.error : colors.accent}
          />
        )}
        <View style={styles.copy}>
          <Text variant="sm" style={{ fontWeight: '600' }}>
            {message || 'Preparing container…'}
          </Text>
          <Text variant="xs" dim>
            {expanded ? 'Hide Docker output' : 'Tap to view Docker output'}
          </Text>
        </View>
        <View style={[styles.chevron, { backgroundColor: colors.bgHover }]}>
          <Ionicons
            name={expanded ? 'chevron-up' : 'chevron-down'}
            size={17}
            color={colors.textDim}
          />
        </View>
      </TouchableOpacity>

      {expanded ? (
        <View style={[styles.outputShell, { borderTopColor: colors.border, backgroundColor: colors.bg }]}>
          <View style={styles.outputHeader}>
            <Text variant="xs" dim style={styles.outputTitle}>stdout / stderr</Text>
            <Text variant="xs" dim>{logs.length} events</Text>
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
                    fontWeight: '700',
                    textTransform: 'uppercase',
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
    marginTop: 14,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 17,
    overflow: 'hidden',
  },
  header: {
    minHeight: 70,
    paddingHorizontal: 14,
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 11,
  },
  copy: { flex: 1, gap: 3 },
  chevron: {
    width: 32,
    height: 32,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
  },
  outputShell: {
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  outputHeader: {
    minHeight: 38,
    paddingHorizontal: 13,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  outputTitle: {
    fontWeight: '700',
    letterSpacing: 0.45,
    textTransform: 'uppercase',
  },
  outputScroll: { maxHeight: 280 },
  outputContent: { paddingHorizontal: 13, paddingBottom: 13 },
  logEntry: { marginBottom: 11, gap: 4 },
  logText: {
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    fontSize: 11.5,
    lineHeight: 17,
  },
  waitingText: { paddingVertical: 14 },
});
