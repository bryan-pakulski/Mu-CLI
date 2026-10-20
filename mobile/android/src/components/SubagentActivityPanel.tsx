// Sub-agents in the transcript (web chat.html `.w-subagents`): one tool-card-like header row,
// "Sub-agents · 2 subagents · 1 running · 12.3s · 4.5k tok", that opens to a row per agent:
// title, specialist · depth · model, status sign and words, elapsed, actions / tokens / context %,
// iteration progress, and the action timeline (oldest → newest) as tool cards. Words come from
// ./chat/subagentModel.ts (ported from the web).

import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import type { LiveSubagent } from '../hooks/useChatSession';
import { radii, spacing, touchTarget } from '../theme/tokens';
import { Button, CountBadge, Icon, InkDrop, Pressable, ProgressBar, StatusSign, Text, ToolCard, useGlass } from '../ui';
import {
  fmtTok, isSubagentActive, subagentActionView, subagentElapsed, subagentSign, subagentStatusText, subagentTitle, summarizeSubagentPanel,
} from './chat/subagentModel';

/** How long a finished panel stays open before folding (web _schedulePanelDismiss). */
const DISMISS_MS = 6000;

export function SubagentActivityPanel({ agents }: { agents: LiveSubagent[] }) {
  const { colors } = useTheme();
  const glass = useGlass();
  const [now, setNow] = useState(Date.now());
  const [details, setDetails] = useState<Set<string>>(() => new Set());
  const running = agents.some(a => isSubagentActive(a.status));
  const [open, setOpen] = useState(running);

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (running) { setOpen(true); return undefined; }
    // An agent whose activity is open keeps the panel open.
    if (details.size) return undefined;
    const timer = setTimeout(() => setOpen(false), DISMISS_MS);
    return () => clearTimeout(timer);
  }, [running, details.size]);

  if (!agents.length) return null;

  const toggleDetails = (taskId: string) => setDetails(current => {
    const next = new Set(current);
    if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
    return next;
  });

  const summary = summarizeSubagentPanel(agents, running, now);
  return (
    <View testID="subagent-panel" accessibilityLiveRegion="polite" style={styles.panel}>
      <Pressable testID="subagent-panel-header" accessibilityRole="button" accessibilityLabel={`Sub-agents, ${summary}`} accessibilityState={{ expanded: open }}
        scale={false} onPress={() => setOpen(v => !v)}
        style={({ pressed }) => [glass.style('subtle'), styles.header, pressed ? { backgroundColor: colors.hairline } : null]}>
        <View style={styles.glyph}>
          {running ? <InkDrop tone="running" size={8} /> : <Text variant="label" tone="ink3" importantForAccessibility="no">✓</Text>}
        </View>
        <Icon name="bot" size="sm" color={colors.ink3} />
        <Text variant="label">Sub-agents</Text>
        <Text variant="monoSm" tone="ink3" numberOfLines={1} style={styles.flex}>{summary}</Text>
        <Text variant="caption" tone="ink3" importantForAccessibility="no">{open ? '▾' : '›'}</Text>
      </Pressable>

      {open ? (
        <ScrollView style={styles.viewport} contentContainerStyle={styles.rows} nestedScrollEnabled showsVerticalScrollIndicator={agents.length > 2}>
          {agents.map((agent, index) => (
            <AgentRow key={agent.task_id} agent={agent} series={colors.series[index % colors.series.length].pigment} now={now}
              detailsOpen={details.has(agent.task_id)} onToggle={() => toggleDetails(agent.task_id)} />
          ))}
        </ScrollView>
      ) : null}
    </View>
  );
}

function AgentRow({ agent, series, now, detailsOpen, onToggle }: {
  agent: LiveSubagent; series: string; now: number; detailsOpen: boolean; onToggle: () => void;
}) {
  const { colors } = useTheme();
  const role = [(agent.specialist_key || 'subagent').replace(/[_-]+/g, ' '), `Depth ${agent.depth || 1}`, agent.model].filter(Boolean).join(' · ');
  const progress = agent.max_iter > 0 ? Math.min(1, (agent.iter || 0) / Math.max(1, agent.max_iter)) : null;
  const status = subagentStatusText(agent);
  return (
    <View testID={`subagent-${agent.task_id}`} style={[styles.agent, { borderTopColor: colors.hairline }]}>
      <View style={styles.agentHead}>
        <View style={[styles.dot, { backgroundColor: series }]} importantForAccessibility="no" />
        <View style={styles.flex}>
          <Text variant="label" numberOfLines={2}>{subagentTitle(agent)}</Text>
          <Text variant="caption" tone="ink3" numberOfLines={1}>{role}</Text>
        </View>
        <View style={styles.state}>
          <View style={styles.statusLine}>
            <StatusSign status={subagentSign(agent.status)} size={12} />
            <Text variant="caption" tone="ink2" numberOfLines={1} style={styles.statusText}>{status}</Text>
          </View>
          <Text variant="caption" tone="ink3" numeric>{`${subagentElapsed(agent, now).toFixed(1)}s`}</Text>
        </View>
      </View>

      <View style={styles.meta}>
        <Fact value={String(agent.tool_count || 0)} label="actions" />
        {agent.tokens_in > 0 ? <Fact value={fmtTok(agent.tokens_in)} label="tokens" /> : null}
        {agent.context_pct > 0 ? <Fact value={`${Math.round(agent.context_pct)}%`} label="context" /> : null}
      </View>

      {progress != null ? (
        <View style={styles.progress}>
          <View style={styles.progressHead}>
            <Text variant="caption" tone="ink3">Iteration progress</Text>
            <Text variant="caption" tone="ink2" numeric>{`${agent.iter || 0} / ${agent.max_iter} · ${Math.round(progress * 100)}%`}</Text>
          </View>
          <ProgressBar value={progress} tone="accent" accessibilityLabel={`Iteration ${agent.iter || 0} of ${agent.max_iter}`} />
        </View>
      ) : null}

      {agent.status === 'error' && agent.error ? <Text variant="caption" tone="danger">{agent.error}</Text> : null}
      {agent.summary && !isSubagentActive(agent.status) ? <Text variant="caption" tone="ink2" numberOfLines={4}>{agent.summary}</Text> : null}

      <View style={styles.toggle}>
        <Button variant="ghost" size="md" icon={detailsOpen ? 'chevron-down' : 'chevron-right'} label={detailsOpen ? 'Hide activity' : 'View activity'}
          onPress={onToggle} testID={`subagent-${agent.task_id}-activity`} />
        <CountBadge>{agent.actions.length}</CountBadge>
      </View>

      {detailsOpen ? (
        <View style={styles.timeline} testID={`subagent-${agent.task_id}-timeline`}>
          <View style={styles.progressHead}>
            <Text variant="caption" tone="ink3">Action timeline</Text>
            <Text variant="caption" tone="ink3">oldest → newest</Text>
          </View>
          {agent.actions.length
            ? agent.actions.map(action => <ToolCard key={`${agent.task_id}-${action.seq}`} call={subagentActionView(action, agent)} />)
            : <Text variant="caption" tone="ink3">Waiting for the first tool action…</Text>}
        </View>
      ) : null}
    </View>
  );
}

function Fact({ value, label }: { value: string; label: string }) {
  return (
    <Text variant="caption" tone="ink3">
      <Text variant="caption" tone="ink2" numeric style={styles.strong}>{value}</Text>{` ${label}`}
    </Text>
  );
}

const styles = StyleSheet.create({
  panel: { marginTop: spacing.sm, marginBottom: spacing.md, gap: spacing.xs },
  header: { minHeight: touchTarget, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md, borderRadius: radii.md },
  glyph: { width: 14, alignItems: 'center' },
  flex: { flex: 1, minWidth: 0 },
  viewport: { maxHeight: 420 },
  rows: { paddingHorizontal: spacing.sm },
  agent: { gap: spacing.sm, paddingVertical: spacing.md, borderTopWidth: StyleSheet.hairlineWidth },
  agentHead: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  dot: { width: 8, height: 8, borderRadius: radii.pill, marginTop: spacing.sm },
  state: { alignItems: 'flex-end', gap: spacing.xs, maxWidth: '45%' },
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  statusText: { flexShrink: 1 },
  meta: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  strong: { fontWeight: '600' },
  progress: { gap: spacing.xs },
  progressHead: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  timeline: { gap: spacing.xs },
});
