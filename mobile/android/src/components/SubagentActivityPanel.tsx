import React, { useEffect, useMemo, useState } from 'react';
import { ScrollView, StyleSheet, TouchableOpacity, View } from 'react-native';
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

function taskTitle(agent: LiveSubagent): string {
  const title = agent.title.replace(/\s+/g, ' ').trim();
  if (title) return title;
  const specialist = agent.specialist_key.replace(/[_-]+/g, ' ').trim();
  return specialist ? `${specialist} task` : 'Delegated task';
}

function toolLabel(tool: string | null): string {
  const labels: Record<string, string> = {
    apply_patch: 'Edit files',
    bash: 'Run command',
    get_chunk: 'Read source chunk',
    list_dir: 'Inspect directory',
    read_file: 'Read file',
    search_for_string: 'Search code',
    spawn_agent: 'Delegate task',
    web_search: 'Search the web',
  };
  if (!tool) return 'Starting';
  return labels[tool] || tool.replace(/[_-]+/g, ' ').replace(/\b\w/g, value => value.toUpperCase());
}

function statusLabel(agent: LiveSubagent): string {
  if (agent.status === 'running') return toolLabel(agent.last_tool);
  if (agent.status === 'done') return 'Completed';
  if (agent.status === 'error') return 'Failed';
  if (agent.status === 'killed') return 'Stopped';
  if (agent.status === 'stall') return 'Stalled';
  if (agent.status === 'stuck') return 'Stuck';
  return agent.status;
}

export function SubagentActivityPanel({ agents }: Props) {
  const { colors } = useTheme();
  const [now, setNow] = useState(Date.now());
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const activeCount = agents.filter(agent => isActive(agent.status)).length;
  const [panelOpen, setPanelOpen] = useState(activeCount > 0);
  const totalElapsed = useMemo(
    () => Math.max(0, ...agents.map(agent => elapsedAt(agent, now))),
    [agents, now],
  );

  useEffect(() => {
    if (activeCount === 0) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [activeCount]);

  useEffect(() => {
    if (activeCount > 0) {
      setPanelOpen(true);
      return undefined;
    }
    const timer = setTimeout(() => setPanelOpen(false), 6000);
    return () => clearTimeout(timer);
  }, [activeCount]);

  if (agents.length === 0) return null;

  const toggleExpanded = (taskId: string) => {
    setExpanded(current => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  return (
    <View
      accessibilityLiveRegion="polite"
      style={[
        styles.panel,
        {
          backgroundColor: colors.glassStrong,
          borderColor: activeCount > 0 ? colors.accentSoft : colors.hairline,
          opacity: activeCount > 0 ? 1 : 0.82,
        },
      ]}
    >
      <TouchableOpacity
        style={styles.header}
        activeOpacity={0.72}
        accessibilityRole="button"
        accessibilityState={{ expanded: panelOpen }}
        accessibilityLabel={panelOpen ? 'Collapse subagent history' : 'Expand subagent history'}
        onPress={() => setPanelOpen(value => !value)}
      >
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
        <Ionicons name={panelOpen ? 'chevron-down' : 'chevron-forward'} size={14} color={colors.textDim} />
      </TouchableOpacity>

      {panelOpen ? (
        <ScrollView
          style={styles.rowsViewport}
          contentContainerStyle={styles.rows}
          nestedScrollEnabled
          showsVerticalScrollIndicator={agents.length > 2}
        >
        {agents.map(agent => {
          const isExpanded = expanded.has(agent.task_id);
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
          return (
            <View key={agent.task_id} style={[styles.row, { borderColor: colors.hairline, backgroundColor: colors.bgLift }]}>
              <View style={[styles.accentRail, { backgroundColor: statusColor }]} />
              <View style={styles.rowHead}>
                <View style={styles.identity}>
                  <Text variant="sm" style={{ color: colors.text, fontWeight: '700', lineHeight: 19 }}>
                    {taskTitle(agent)}
                  </Text>
                  <Text variant="xs" numberOfLines={1} style={{ color: colors.textDim, textTransform: 'capitalize' }}>
                    {(agent.specialist_key || 'subagent').replace(/[_-]+/g, ' ')} · Depth {agent.depth}{agent.model ? ` · ${agent.model}` : ''}
                  </Text>
                </View>
                <View style={styles.stateColumn}>
                  <View style={[styles.status, { backgroundColor: colors.accentSoft }]}>
                    <Ionicons name={statusIcon(agent.status)} size={13} color={statusColor} />
                    <Text variant="xs" numberOfLines={1} style={{ color: statusColor, maxWidth: 98 }}>{statusLabel(agent)}</Text>
                  </View>
                  <Text variant="xs" style={{ color: colors.textDim }}>{duration(elapsedAt(agent, now))}</Text>
                </View>
              </View>

              <View style={styles.meta}>
                <Metric value={String(agent.tool_count)} label="actions" color={colors.textDim} strong={colors.textSoft} />
                {agent.tokens_in > 0 ? <Metric value={tokens(agent.tokens_in)} label="tokens" color={colors.textDim} strong={colors.textSoft} /> : null}
                {agent.context_pct > 0 ? <Metric value={`${Math.round(agent.context_pct)}%`} label="context" color={colors.textDim} strong={colors.textSoft} /> : null}
              </View>

              {agent.max_iter > 0 ? (
                <Meter
                  label="Iteration progress"
                  value={`${agent.iter} / ${agent.max_iter} · ${Math.round(iterationPct)}%`}
                  percent={iterationPct}
                  color={colors.accentStrong}
                  track={colors.borderStrong}
                  text={colors.textDim}
                />
              ) : null}
              <TouchableOpacity
                activeOpacity={0.72}
                accessibilityRole="button"
                accessibilityState={{ expanded: isExpanded }}
                onPress={() => toggleExpanded(agent.task_id)}
                style={styles.activityToggle}
              >
                <Ionicons name={isExpanded ? 'chevron-down' : 'chevron-forward'} size={14} color={colors.textDim} />
                <Text variant="xs" style={{ color: colors.textSoft }}>{isExpanded ? 'Hide activity' : 'View activity'}</Text>
                <View style={[styles.countBadge, { backgroundColor: colors.bgHover }]}>
                  <Text variant="xs" style={{ color: colors.textDim }}>{agent.actions.length}</Text>
                </View>
              </TouchableOpacity>

              {isExpanded ? (
                <View style={[styles.activity, { borderColor: colors.hairline, backgroundColor: colors.glass }]}>
                  <View style={[styles.activityHead, { borderBottomColor: colors.hairline }]}>
                    <Text variant="xs" style={{ color: colors.textDim, fontWeight: '700', letterSpacing: 0.4 }}>ACTION TIMELINE</Text>
                    <Text variant="xs" style={{ color: colors.textDim }}>oldest → newest</Text>
                  </View>
                  {agent.actions.length === 0 ? (
                    <Text variant="xs" style={[styles.emptyActivity, { color: colors.textDim }]}>Waiting for the first tool action…</Text>
                  ) : (
                    <ScrollView style={styles.actionScroll} nestedScrollEnabled showsVerticalScrollIndicator>
                      {agent.actions.map(action => {
                        const actionColor = action.status === 'error'
                          ? colors.error
                          : (action.status === 'done' ? colors.success : colors.accent);
                        return (
                          <View key={`${agent.task_id}-${action.seq}`} style={[styles.action, { borderBottomColor: colors.hairline }]}>
                            <Text variant="xs" style={{ color: colors.textDim, width: 22 }}>{String(action.seq).padStart(2, '0')}</Text>
                            <View style={[styles.actionDot, { backgroundColor: actionColor }]} />
                            <View style={styles.actionCopy}>
                              <Text variant="xs" style={{ color: colors.textSoft, fontWeight: '600' }}>{toolLabel(action.tool)}</Text>
                              {action.detail ? <Text variant="xs" numberOfLines={2} style={{ color: colors.textDim }}>{action.detail}</Text> : null}
                            </View>
                            <View style={styles.actionState}>
                              <Text variant="xs" style={{ color: actionColor }}>{action.status === 'done' ? 'Done' : (action.status === 'error' ? 'Failed' : 'Running')}</Text>
                              {action.elapsed > 0 ? <Text variant="xs" style={{ color: colors.textDim }}>{action.elapsed.toFixed(1)}s</Text> : null}
                            </View>
                          </View>
                        );
                      })}
                    </ScrollView>
                  )}
                </View>
              ) : null}
            </View>
          );
        })}
        </ScrollView>
      ) : null}
    </View>
  );
}

function Metric({ value, label, color, strong }: { value: string; label: string; color: string; strong: string }) {
  return (
    <View style={styles.metric}>
      <Text variant="xs" style={{ color: strong, fontWeight: '700' }}>{value}</Text>
      <Text variant="xs" style={{ color }}>{label}</Text>
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
    borderRadius: 18,
    padding: 12,
    shadowColor: '#000',
    shadowOpacity: 0.08,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 5 },
    elevation: 3,
  },
  header: { flexDirection: 'row', alignItems: 'center' },
  headerIcon: { width: 34, height: 34, borderRadius: 11, alignItems: 'center', justifyContent: 'center' },
  headerCopy: { flex: 1, marginLeft: 9 },
  rowsViewport: { maxHeight: 380, marginTop: 10 },
  rows: { gap: 9, paddingBottom: 1 },
  row: { position: 'relative', overflow: 'hidden', borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, padding: 12 },
  accentRail: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 2 },
  rowHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  identity: { flex: 1, gap: 2 },
  stateColumn: { alignItems: 'flex-end', gap: 4 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 4, maxWidth: 125, paddingHorizontal: 7, paddingVertical: 3, borderRadius: 999 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', gap: 13, marginTop: 10 },
  metric: { flexDirection: 'row', alignItems: 'baseline', gap: 4 },
  meter: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 10 },
  meterLabel: { width: 82 },
  meterTrack: { flex: 1, height: 10, borderRadius: 999, overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: 999 },
  meterValue: { width: 76, textAlign: 'right' },
  activityToggle: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 10, marginLeft: -5, alignSelf: 'flex-start', paddingVertical: 3, paddingHorizontal: 5 },
  countBadge: { minWidth: 20, height: 18, borderRadius: 99, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 5 },
  activity: { marginTop: 7, borderWidth: StyleSheet.hairlineWidth, borderRadius: 11, overflow: 'hidden' },
  activityHead: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 9, paddingVertical: 7, borderBottomWidth: StyleSheet.hairlineWidth },
  actionScroll: { maxHeight: 190 },
  action: { flexDirection: 'row', alignItems: 'flex-start', gap: 7, paddingHorizontal: 9, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth },
  actionDot: { width: 7, height: 7, borderRadius: 99, marginTop: 5 },
  actionCopy: { flex: 1, gap: 2 },
  actionState: { alignItems: 'flex-end', gap: 1 },
  emptyActivity: { padding: 12 },
});
