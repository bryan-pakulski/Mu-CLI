import React, { useEffect, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import type { LiveSubagent } from '../hooks/useChatSession';
import { Text } from './Text';

type Props = {
  agents: LiveSubagent[];
};

function isActive(status: string): boolean {
  return status === 'running' || status === 'stuck' || status === 'stall';
}

function elapsedAt(agent: LiveSubagent, now: number): number {
  const liveDelta = isActive(agent.status)
    ? Math.max(0, now - agent.observed_at) / 1000
    : 0;
  return Math.max(0, agent.elapsed + liveDelta);
}

function duration(value: number): string {
  const seconds = Math.floor(value);
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${seconds}s`;
}

function tokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

function statusIcon(status: string): keyof typeof Ionicons.glyphMap {
  if (status === 'done') return 'checkmark-circle-outline';
  if (status === 'error' || status === 'killed') return 'close-circle-outline';
  if (status === 'stuck' || status === 'stall') return 'warning-outline';
  return 'ellipse-outline';
}

export function SubagentActivityPanel({ agents }: Props) {
  const { colors } = useTheme();
  const [now, setNow] = useState(Date.now());
  const activeCount = agents.filter(agent => isActive(agent.status)).length;
  const totalElapsed = useMemo(
    () => Math.max(0, ...agents.map(agent => elapsedAt(agent, now))),
    [agents, now],
  );

  useEffect(() => {
    if (activeCount === 0) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [activeCount]);

  if (agents.length === 0) return null;

  return (
    <View
      accessibilityLiveRegion="polite"
      style={[
        styles.panel,
        {
          backgroundColor: colors.glassStrong,
          borderColor: activeCount > 0 ? colors.accent : colors.hairline,
          opacity: activeCount > 0 ? 1 : 0.82,
        },
      ]}
    >
      <View style={styles.header}>
        <View style={[styles.headerIcon, { backgroundColor: colors.accentSoft }]}>
          <Ionicons name="git-network-outline" size={17} color={colors.accent} />
        </View>
        <View style={styles.headerCopy}>
          <Text variant="xs" style={{ color: colors.text, fontWeight: '700', letterSpacing: 0.45 }}>
            SUBAGENTS
          </Text>
          <Text variant="xs" style={{ color: colors.textDim }}>
            {activeCount > 0 ? `${activeCount} of ${agents.length} running` : `${agents.length} completed`}
          </Text>
        </View>
        <Text variant="xs" style={{ color: colors.textDim }}>{duration(totalElapsed)}</Text>
      </View>

      <View style={styles.rows}>
        {agents.map(agent => {
          const active = isActive(agent.status);
          const statusColor = agent.status === 'done'
            ? colors.success
            : (agent.status === 'error' || agent.status === 'killed')
              ? colors.error
              : (agent.status === 'stuck' || agent.status === 'stall')
                ? colors.warning
                : colors.accent;
          const iterationPct = agent.max_iter > 0
            ? Math.min(100, Math.max(0, (agent.iter / agent.max_iter) * 100))
            : 0;
          const contextPct = Math.min(100, Math.max(0, agent.context_pct));
          return (
            <View key={agent.task_id} style={[styles.row, { borderColor: colors.hairline, backgroundColor: colors.bgLift }]}>
              <View style={styles.rowHead}>
                <View style={[styles.depthBadge, { borderColor: colors.hairline }]}>
                  <Text variant="xs" style={{ color: colors.textDim }}>d{agent.depth}</Text>
                </View>
                <Text variant="sm" numberOfLines={2} style={{ color: colors.textSoft, flex: 1 }}>
                  {agent.task || agent.task_id}
                </Text>
                <View style={styles.status}>
                  <Ionicons name={statusIcon(agent.status)} size={14} color={statusColor} />
                  <Text variant="xs" style={{ color: statusColor }}>{active ? (agent.last_tool || agent.status) : agent.status}</Text>
                </View>
              </View>

              <View style={styles.meta}>
                <Text variant="xs" style={{ color: colors.textDim }}>{duration(elapsedAt(agent, now))}</Text>
                {agent.model ? <Text variant="xs" numberOfLines={1} style={{ color: colors.textDim, maxWidth: 110 }}>{agent.model}</Text> : null}
                {agent.tool_count > 0 ? <Text variant="xs" style={{ color: colors.textDim }}>{agent.tool_count} calls</Text> : null}
                {agent.tokens_in > 0 ? <Text variant="xs" style={{ color: colors.textDim }}>{tokens(agent.tokens_in)} tok</Text> : null}
              </View>

              {agent.max_iter > 0 ? (
                <Meter
                  label="Iteration"
                  value={`${agent.iter}/${agent.max_iter}`}
                  percent={iterationPct}
                  color={colors.accent}
                  track={colors.bgHover}
                  text={colors.textDim}
                />
              ) : null}
              {agent.context_pct > 0 ? (
                <Meter
                  label="Context"
                  value={`${Math.round(contextPct)}%`}
                  percent={contextPct}
                  color={contextPct > 80 ? colors.warning : colors.info}
                  track={colors.bgHover}
                  text={colors.textDim}
                />
              ) : null}
            </View>
          );
        })}
      </View>
    </View>
  );
}

function Meter({
  label,
  value,
  percent,
  color,
  track,
  text,
}: {
  label: string;
  value: string;
  percent: number;
  color: string;
  track: string;
  text: string;
}) {
  return (
    <View style={styles.meter}>
      <Text variant="xs" style={[styles.meterLabel, { color: text }]}>{label}</Text>
      <View style={[styles.meterTrack, { backgroundColor: track }]}>
        <View style={[styles.meterFill, { backgroundColor: color, width: `${percent}%` as `${number}%` }]} />
      </View>
      <Text variant="xs" style={[styles.meterValue, { color: text }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    marginTop: 8,
    marginBottom: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 15,
    padding: 11,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 5 },
    elevation: 3,
  },
  header: { flexDirection: 'row', alignItems: 'center' },
  headerIcon: { width: 34, height: 34, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  headerCopy: { flex: 1, marginLeft: 9 },
  rows: { gap: 7, marginTop: 9 },
  row: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 11, padding: 9 },
  rowHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 7 },
  depthBadge: { minWidth: 28, borderWidth: StyleSheet.hairlineWidth, borderRadius: 999, alignItems: 'center', paddingHorizontal: 5, paddingVertical: 1 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 4, maxWidth: 115 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', gap: 9, marginTop: 7 },
  meter: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 7 },
  meterLabel: { width: 50 },
  meterTrack: { flex: 1, height: 4, borderRadius: 999, overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: 999 },
  meterValue: { width: 42, textAlign: 'right' },
});
